# -*- coding: utf-8 -*-
"""
Control de acceso por permisos (RBAC) de Global Exchange.

Los permisos que el administrador asigna a cada rol en "Gestión de Permisos para Roles"
son los que deciden qué puede hacer cada usuario. Este módulo:

* Define el catálogo de permisos y los permisos por defecto de cada rol.
* Resuelve el rol con el que el usuario está operando (Admin, Analista, Cajero, Cliente,
  o su rol dentro del cliente activo: Jefe, Operador o Analista corporativo).
* Expone ``permisos_de(request)``, ``tiene_permiso(request, codename)`` y el decorador
  ``requiere_permiso`` para proteger las vistas.
"""
import logging
from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect

logger = logging.getLogger(__name__)

# (codename, nombre, descripción, administrativo)
# Los permisos administrativos no pueden asignarse a los roles de cliente.
CATALOGO_PERMISOS = [
    ('can_manage_roles', 'Gestionar Roles y Permisos', 'Editar la descripción y los permisos de los roles.', True),
    ('can_manage_users', 'Gestionar Usuarios', 'Crear, editar y desactivar usuarios del sistema.', True),
    ('can_manage_clients', 'Gestionar Clientes', 'Crear, editar y asociar clientes y aprobar solicitudes.', True),
    ('can_manage_rates', 'Gestionar Tasas de Cambio', 'Actualizar cotizaciones y el historial de tasas.', True),
    ('can_manage_benefits', 'Configurar Beneficios de Clientes', 'Definir los porcentajes de beneficio por categoría.', True),
    ('can_manage_payment_config', 'Parametrizar Divisas y Métodos de Pago', 'Configurar divisas habilitadas y métodos de pago.', True),
    ('can_manage_cashboxes', 'Gestionar Cajas', 'Crear y editar cajas físicas y asignar cajeros.', True),
    ('can_view_audit', 'Ver Auditoría', 'Consultar el registro de auditoría del sistema.', True),
    ('can_operate_cashier', 'Operar Caja y Ventanilla', 'Abrir y cerrar turnos, registrar movimientos y operar en ventanilla.', True),
    ('can_view_financial_reports', 'Ver Reportes Financieros', 'Generar y exportar reportes de transacciones y ganancias (PDF, Excel/CSV).', True),
    ('can_manage_invoices', 'Gestionar Facturación Electrónica', 'Reintentar, consultar, cancelar y anular facturas con nota de crédito; resolver solicitudes de anulación.', True),
    ('can_perform_exchange', 'Realizar Transacciones', 'Comprar y vender divisas desde la interfaz de cliente.', False),
    ('can_view_history', 'Ver Historial de Operaciones', 'Consultar el historial de compras y ventas del cliente.', False),
    ('can_manage_alerts', 'Gestionar Alertas de Tasas', 'Crear y administrar alertas de tasa objetivo.', False),
    ('can_manage_accreditation', 'Gestionar Medios de Acreditación', 'Registrar y editar las cuentas para recibir fondos.', False),
    ('can_request_invoice_cancellation', 'Solicitar Anulación de Factura', 'Pedir al Administrador la anulación de la factura de una operación.', False),
    ('can_simulate', 'Simular Operaciones', 'Usar el simulador de conversión de divisas.', False),
    ('can_view_rates', 'Ver Pizarra de Tasas', 'Consultar cotizaciones y el gráfico de evolución.', False),
]

TODOS_LOS_PERMISOS = frozenset(c for c, _, _, _ in CATALOGO_PERMISOS)
PERMISOS_ADMINISTRATIVOS = frozenset(c for c, _, _, admin in CATALOGO_PERMISOS if admin)
NOMBRES_PERMISOS = {c: n for c, n, _, _ in CATALOGO_PERMISOS}

ROL_ADMIN = 'Admin'
ROL_ANALISTA = 'Analista'
ROL_CLIENTE = 'Cliente'
ROL_CAJERO = 'Cajero'
ROL_JEFE = 'Jefe (Corporativo)'
ROL_OPERADOR = 'Operador (Corporativo)'
ROL_ANALISTA_CORP = 'Analista (Corporativo)'

ROLES_DE_CLIENTE = frozenset({ROL_CLIENTE, ROL_JEFE, ROL_OPERADOR, ROL_ANALISTA_CORP})

_PERMISOS_CLIENTE = {
    'can_perform_exchange', 'can_view_history', 'can_manage_alerts',
    'can_manage_accreditation', 'can_request_invoice_cancellation', 'can_simulate', 'can_view_rates',
}

PERMISOS_POR_DEFECTO = {
    ROL_ADMIN: set(TODOS_LOS_PERMISOS),
    ROL_ANALISTA: {'can_view_audit', 'can_view_financial_reports', 'can_simulate', 'can_view_rates'},
    ROL_CAJERO: {'can_operate_cashier', 'can_view_rates'},
    ROL_CLIENTE: set(_PERMISOS_CLIENTE),
    ROL_JEFE: set(_PERMISOS_CLIENTE),
    ROL_OPERADOR: {
        'can_perform_exchange', 'can_view_history', 'can_manage_alerts',
        'can_request_invoice_cancellation', 'can_simulate', 'can_view_rates',
    },
    ROL_ANALISTA_CORP: {'can_view_history', 'can_manage_alerts', 'can_simulate', 'can_view_rates'},
}


def asegurar_catalogo_permisos():
    """
    Crea en la base de datos los permisos del catálogo que todavía no existan.

    Returns:
        QuerySet: Todos los permisos registrados.
    """
    from .models import Permission
    for codename, nombre, descripcion, _ in CATALOGO_PERMISOS:
        if not Permission.objects.filter(codename=codename).exists():
            if Permission.objects.filter(name=nombre).exists():
                nombre = f"{nombre} ({codename})"
            Permission.objects.create(codename=codename, name=nombre, description=descripcion)
    return Permission.objects.all()


def permisos_por_defecto(nombre_rol):
    """
    Devuelve los permisos por defecto de un rol fijo del sistema.

    Args:
        nombre_rol (str): Nombre del rol.

    Returns:
        set[str]: Codenames de los permisos por defecto (vacío si el rol no es conocido).
    """
    return set(PERMISOS_POR_DEFECTO.get(nombre_rol, set()))


def es_rol_admin(nombre_rol):
    """Indica si el nombre corresponde al rol de Administrador."""
    return 'admin' in (nombre_rol or '').lower()


def permisos_del_rol(nombre_rol):
    """
    Devuelve los permisos efectivos de un rol.

    El rol Administrador siempre tiene todos los permisos. Para los demás, si el
    administrador ya configuró el rol se usan los permisos guardados; si nunca se
    configuró, se usan los permisos por defecto.

    Args:
        nombre_rol (str): Nombre del rol.

    Returns:
        set[str]: Codenames de los permisos del rol.
    """
    if es_rol_admin(nombre_rol):
        return set(TODOS_LOS_PERMISOS)
    from .models import Role
    rol = Role.objects.filter(name=nombre_rol).first()
    if rol is None:
        return permisos_por_defecto(nombre_rol)
    if not rol.is_active:
        return set()
    if rol.permisos_configurados:
        return set(rol.permissions.values_list('codename', flat=True))
    return permisos_por_defecto(nombre_rol)


def _rol_de_sistema(user, profile, request):
    """Rol fijo del sistema con el que opera el usuario (respeta el rol activo elegido)."""
    rol = profile.role if profile else None
    if request is not None and profile is not None:
        rol_activo_id = request.session.get('active_role_id')
        if rol_activo_id:
            disponible = profile.roles.filter(id=rol_activo_id).first()
            if disponible is None and profile.role_id == rol_activo_id:
                disponible = profile.role
            if disponible is not None:
                rol = disponible
    return rol


def _rol_en_cliente_activo(user, profile, request):
    """
    Rol del usuario dentro del cliente con el que está operando.

    Returns:
        str | None: ROL_JEFE, ROL_OPERADOR, ROL_ANALISTA_CORP o ROL_CLIENTE; None si no opera para un cliente.
    """
    from .models import UsuarioClienteRelacion, GroupMembership

    if request is not None and request.session.get('user_mode'):
        return None

    ids_usuario = [str(user.id)]
    if profile and profile.keycloak_id:
        ids_usuario.append(profile.keycloak_id)
    relaciones = UsuarioClienteRelacion.objects.filter(keycloak_user_id__in=ids_usuario).select_related('cliente')

    relacion = None
    cliente_activo_id = request.session.get('active_client_id') if request is not None else None
    if cliente_activo_id:
        relacion = relaciones.filter(cliente_id=cliente_activo_id).first() if _es_uuid(cliente_activo_id) else None
    if relacion is None:
        relacion = relaciones.first()

    if relacion is not None:
        rol = (relacion.rol_en_cliente or '').upper()
        if rol == 'OPERADOR':
            return ROL_OPERADOR
        if rol == 'ANALISTA':
            return ROL_ANALISTA_CORP
        if (relacion.cliente.tipo_cliente or '').upper().startswith('JURIDIC'):
            return ROL_JEFE
        return ROL_CLIENTE

    if profile is not None:
        membresia = GroupMembership.objects.filter(fisica_profile=profile).first()
        if membresia is not None:
            rol = (membresia.role_in_group or '').upper()
            if rol == 'OPERADOR':
                return ROL_OPERADOR
            if rol == 'ANALISTA':
                return ROL_ANALISTA_CORP
            return ROL_JEFE
        if profile.is_corporate:
            return ROL_JEFE
    return None


def _asignado_a_caja(user):
    """Indica si el usuario fue asignado como cajero a alguna caja activa o tiene un turno abierto."""
    try:
        from caja.models import Caja, TurnoCaja
    except ImportError:
        return False
    return (
        Caja.objects.filter(activa=True, cajeros_asignados=user).exists()
        or TurnoCaja.objects.filter(cajero=user, estado='ABIERTO').exists()
    )


def _es_uuid(valor):
    """Indica si el valor es un UUID válido (los clientes usan UUID como id)."""
    import uuid
    try:
        uuid.UUID(str(valor))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def rol_efectivo(request_o_user):
    """
    Determina el nombre del rol con el que el usuario está operando.

    Orden: superusuario o Admin -> Admin; rol Cajero; Analista del sistema; usuario asignado
    a una caja -> Cajero; rol dentro del cliente activo (Jefe, Operador o Analista corporativo);
    en cualquier otro caso, Cliente.

    Args:
        request_o_user (HttpRequest | User): Solicitud actual o usuario.

    Returns:
        str | None: Nombre del rol, o None si no hay un usuario autenticado.
    """
    request = request_o_user if hasattr(request_o_user, 'session') else None
    if request is not None and hasattr(request, '_rol_gx'):
        return request._rol_gx
    nombre = _calcular_rol_efectivo(request, request.user if request is not None else request_o_user)
    if request is not None:
        request._rol_gx = nombre
    return nombre


def _calcular_rol_efectivo(request, user):
    """Calcula el rol efectivo (ver ``rol_efectivo``)."""
    if not getattr(user, 'is_authenticated', False):
        return None
    if user.is_superuser:
        return ROL_ADMIN

    from .models import UserProfile
    profile = UserProfile.objects.filter(user=user).select_related('role').first()
    rol = _rol_de_sistema(user, profile, request)
    nombre = (rol.name if rol else '').lower()

    if es_rol_admin(nombre):
        return ROL_ADMIN
    if 'cajero' in nombre:
        return ROL_CAJERO
    if 'analista' in nombre and 'corporativo' not in nombre:
        return ROL_ANALISTA
    if _asignado_a_caja(user):
        return ROL_CAJERO

    return _rol_en_cliente_activo(user, profile, request) or (rol.name if rol and rol.name in ROLES_DE_CLIENTE else ROL_CLIENTE)


def permisos_de(request_o_user):
    """
    Devuelve los permisos del usuario según el rol con el que está operando.

    El resultado se guarda en la solicitud para no recalcularlo en la misma petición.

    Args:
        request_o_user (HttpRequest | User): Solicitud actual o usuario.

    Returns:
        set[str]: Codenames de los permisos habilitados.
    """
    cache = getattr(request_o_user, '_permisos_gx', None)
    if cache is not None:
        return cache
    nombre = rol_efectivo(request_o_user)
    permisos = permisos_del_rol(nombre) if nombre else set()
    if hasattr(request_o_user, 'session'):
        request_o_user._permisos_gx = permisos
    return permisos


def tiene_permiso(request_o_user, codename):
    """
    Indica si el usuario tiene el permiso indicado.

    Args:
        request_o_user (HttpRequest | User): Solicitud actual o usuario.
        codename (str): Código del permiso (ej. ``can_perform_exchange``).

    Returns:
        bool: True si el permiso está habilitado.
    """
    return codename in permisos_de(request_o_user)


def requiere_permiso(codename, permitir_anonimo=False):
    """
    Decorador de vistas que exige un permiso del rol del usuario.

    Si el usuario no lo tiene, registra el intento en la auditoría y lo devuelve a su
    panel con una notificación que indica qué permiso le falta.

    Args:
        codename (str): Código del permiso requerido.
        permitir_anonimo (bool): Si es True, las páginas públicas siguen abiertas para
            visitantes sin sesión; el permiso solo se exige a usuarios autenticados.

    Returns:
        Callable: Vista protegida.
    """
    def decorador(vista):
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            if not request.user.is_authenticated:
                if permitir_anonimo:
                    return vista(request, *args, **kwargs)
                from django.contrib.auth.views import redirect_to_login
                return redirect_to_login(request.get_full_path())
            if tiene_permiso(request, codename):
                return vista(request, *args, **kwargs)
            return denegar_acceso(request, codename)
        return envoltura
    return decorador


def denegar_acceso(request, codename):
    """
    Registra el acceso denegado y redirige al panel del usuario con una notificación.

    Args:
        request (HttpRequest): Solicitud actual.
        codename (str): Permiso que faltó.

    Returns:
        HttpResponseRedirect: Redirección al panel principal.
    """
    from .models import AuditLog
    nombre_permiso = NOMBRES_PERMISOS.get(codename, codename)
    rol = rol_efectivo(request) or 'Sin rol'
    try:
        AuditLog.objects.create(
            user=request.user,
            action="RBAC_ACCESS_DENIED",
            ip_address=_ip(request),
            details=f"Acceso denegado a {request.path}: el rol {rol} no tiene el permiso '{nombre_permiso}'.",
        )
    except Exception:
        logger.exception("No se pudo registrar el acceso denegado en la auditoría")
    messages.error(request, f"Acceso denegado: tu rol ({rol}) no tiene el permiso «{nombre_permiso}».")
    return redirect('dashboard_redirect')


def _ip(request):
    """IP del cliente de la solicitud."""
    reenviada = request.META.get('HTTP_X_FORWARDED_FOR')
    return reenviada.split(',')[0] if reenviada else request.META.get('REMOTE_ADDR')


def contexto_permisos(request):
    """
    Context processor: expone ``permisos`` (set de codenames) y ``rol_operativo`` en las plantillas,
    para mostrar solo las opciones que el rol del usuario tiene habilitadas.
    """
    if not getattr(getattr(request, 'user', None), 'is_authenticated', False):
        return {'permisos': set(), 'rol_operativo': None}
    try:
        return {'permisos': permisos_de(request), 'rol_operativo': rol_efectivo(request)}
    except Exception:
        logger.exception("No se pudieron calcular los permisos del usuario")
        return {'permisos': set(), 'rol_operativo': None}
