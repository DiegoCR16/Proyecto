# -*- coding: utf-8 -*-
"""
Estado de la interfaz para la actualización automática de los dashboards.

Los dashboards del Administrador, del Cliente (también en modo usuario) y del Analista
consultan cada 30 segundos ``estado_interfaz_api``. Si la firma del estado cambió
(llegó una notificación, una solicitud de anulación o se actualizó una cotización),
la página se recarga para mostrarlo sin que el usuario tenga que actualizar a mano.

La firma inicial se calcula con la misma función al renderizar el dashboard, así la
comparación es siempre coherente y no se producen recargas en bucle.
"""
from django.db.models import Max

from .permisos import tiene_permiso

INTERVALO_SEGUNDOS = 30


def estado_interfaz(request):
    """
    Resume lo que, si cambia, obliga a refrescar el dashboard del usuario.

    Incluye:
        * Notificaciones en pantalla visibles para el usuario y su cliente activo:
          cantidad sin leer y el id de la más reciente.
        * Solicitudes de anulación de factura pendientes (solo si el usuario tiene
          el permiso ``can_manage_invoices``): cantidad y id de la más reciente.
        * Última actualización de las cotizaciones.

    Args:
        request (HttpRequest): Solicitud de un usuario autenticado.

    Returns:
        dict: Datos del estado y ``firma`` (texto que cambia cuando cambia cualquiera de ellos).
    """
    from procesamiento_operaciones.models import SolicitudAnulacionFactura
    from tasas_cambio.models import ExchangeRate, NotificationLog
    from tasas_cambio.views import get_active_client, notificaciones_visibles_q

    cliente_activo = get_active_client(request.user, request)
    visibles = NotificationLog.objects.filter(notificaciones_visibles_q(request.user, cliente_activo))
    datos = {
        'notificaciones_no_leidas': visibles.filter(is_read=False).count(),
        'ultima_notificacion_id': visibles.aggregate(m=Max('id'))['m'],
        'solicitudes_anulacion_pendientes': None,
        'ultima_solicitud_anulacion_id': None,
    }
    if tiene_permiso(request, 'can_manage_invoices'):
        pendientes = SolicitudAnulacionFactura.objects.filter(estado=SolicitudAnulacionFactura.PENDIENTE)
        datos['solicitudes_anulacion_pendientes'] = pendientes.count()
        datos['ultima_solicitud_anulacion_id'] = pendientes.aggregate(m=Max('id'))['m']
    ultima_tasa = ExchangeRate.objects.aggregate(m=Max('last_updated'))['m']
    datos['cotizaciones_actualizadas'] = ultima_tasa.isoformat() if ultima_tasa else None
    datos['firma'] = '|'.join(str(datos[clave]) for clave in (
        'notificaciones_no_leidas', 'ultima_notificacion_id', 'solicitudes_anulacion_pendientes',
        'ultima_solicitud_anulacion_id', 'cotizaciones_actualizadas',
    ))
    return datos
