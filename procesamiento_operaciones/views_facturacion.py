# -*- coding: utf-8 -*-
"""Vistas de facturación electrónica: panel del Administrador y solicitudes de anulación del cliente."""
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from authentication.models import AuditLog, UsuarioClienteRelacion
from authentication.permisos import requiere_permiso
from . import facturacion
from . import views as ops
from .facturacion import FacturacionError
from .models import AnulacionFactura, CurrencyPurchaseTransaction, CurrencySaleTransaction, SolicitudAnulacionFactura

logger = logging.getLogger(__name__)

LIMITE_OPERACIONES = 300
FILTROS_ESTADO = [
    ("", "Todas"),
    ("ATENCION", "Requieren atención (error, rechazada, en cola)"),
    ("En cola", "En cola"), ("Emitida", "Emitida"), ("Pendiente", "Pendiente"), ("Aprobada", "Aprobada"),
    ("Rechazada", "Rechazada"), ("Error", "Error"),
    ("Cancelación en proceso", "Cancelación en proceso"), ("Cancelada", "Cancelada"),
    ("Anulación en proceso", "Anulación en proceso"), ("Anulada", "Anulada"),
]


def _ip(request):
    """IP del cliente de la solicitud."""
    reenviada = request.META.get("HTTP_X_FORWARDED_FOR")
    return reenviada.split(",")[0] if reenviada else request.META.get("REMOTE_ADDR")


def _auditar(request, accion, detalle):
    """Registra la acción de facturación en la auditoría."""
    AuditLog.objects.create(user=request.user, action=accion, ip_address=_ip(request), details=detalle[:1000])


def _volver(request, defecto):
    """Redirige a la pantalla anterior (si es del mismo sitio) o a la indicada."""
    anterior = request.META.get("HTTP_REFERER", "")
    if anterior and url_has_allowed_host_and_scheme(anterior, allowed_hosts={request.get_host()}):
        return redirect(anterior)
    return redirect(defecto)


def _operaciones_facturables(q=""):
    """Últimas operaciones exitosas (compras y ventas), opcionalmente filtradas por búsqueda."""
    filas = []
    for modelo in (CurrencyPurchaseTransaction, CurrencySaleTransaction):
        qs = modelo.objects.filter(status__in=["SUCCESS", facturacion.STATUS_ANULADA]).select_related("cliente", "user")
        if q:
            filtro = (
                Q(transparent_breakdown__icontains=q) | Q(cliente__nombre_o_razon_social__icontains=q)
                | Q(cliente__documento_identidad__icontains=q) | Q(user__username__icontains=q)
            )
            if q.isdigit():
                filtro |= Q(pk=int(q))
            qs = qs.filter(filtro)
        filas += list(qs.order_by("-timestamp")[:LIMITE_OPERACIONES])
    return sorted(filas, key=lambda tx: tx.timestamp, reverse=True)


@login_required
@requiere_permiso("can_manage_invoices")
def admin_facturacion_view(request):
    """
    Panel de facturación electrónica del Administrador.

    Lista las facturas de las operaciones con su estado y las acciones que permite SIFEN:
    reintentar (error, rechazada o en cola), consultar estado (emitida o pendiente),
    solicitar la cancelación (aprobada, dentro de 48 h; operación ``sol_cancelacion``),
    anular con nota de crédito (aprobada) y reenviar la nota aprobada por correo. También resuelve las solicitudes de anulación de los clientes.
    """
    if request.method == "POST":
        return _procesar_accion(request)

    # Actualiza los resultados de SIFEN antes de mostrar el panel
    ops._sincronizar_facturas_sifen(
        CurrencyPurchaseTransaction.objects.all(), CurrencySaleTransaction.objects.all(), limite=10
    )
    try:
        facturacion.sincronizar_anulaciones()
    except Exception:
        logger.exception("No se pudieron sincronizar las anulaciones")

    estado = request.GET.get("estado", "")
    tipo = request.GET.get("tipo", "")
    q = request.GET.get("q", "").strip()
    ahora = timezone.now()

    operaciones = []
    resumen = {}
    for tx in _operaciones_facturables(q):
        if tipo and tx.tipo_operacion != tipo:
            continue
        tx_estado = tx.sifen_estado
        resumen[tx_estado] = resumen.get(tx_estado, 0) + 1
        if estado == "ATENCION" and tx_estado not in facturacion.ESTADOS_REINTENTABLES:
            continue
        if estado and estado != "ATENCION" and tx_estado != estado:
            continue
        tx.opciones = facturacion.opciones_factura(tx, ahora)
        operaciones.append(tx)

    contexto = {
        "operaciones": operaciones,
        "resumen": sorted(resumen.items()),
        "con_error": sum(resumen.get(e, 0) for e in facturacion.ESTADOS_REINTENTABLES),
        "solicitudes": SolicitudAnulacionFactura.objects.filter(estado=SolicitudAnulacionFactura.PENDIENTE)
        .select_related("compra", "venta", "cliente", "solicitante"),
        "solicitudes_resueltas": SolicitudAnulacionFactura.objects.exclude(estado=SolicitudAnulacionFactura.PENDIENTE)
        .select_related("compra", "venta", "cliente", "resuelta_por")[:15],
        "anulaciones": AnulacionFactura.objects.select_related("compra", "venta", "usuario")[:20],
        "devoluciones_pendientes": AnulacionFactura.objects.filter(
            con_devolucion=True, estado=AnulacionFactura.APROBADA, devuelta_en__isnull=True,
        ).select_related("compra", "venta"),
        "filtros_estado": FILTROS_ESTADO,
        "estado": estado, "tipo": tipo, "q": q,
        "plazo_horas": int(facturacion.plazo_cancelacion().total_seconds() // 3600),
        "now": ahora,
    }
    for solicitud in contexto["solicitudes"]:
        tx = solicitud.transaccion
        solicitud.metodo_sugerido = (
            "Cancelación (dentro del plazo)" if tx is not None and facturacion.puede_cancelarse(tx, ahora)
            else "Nota de crédito (fuera del plazo de cancelación)"
        )
    return render(request, "procesamiento_operaciones/admin_facturacion.html", contexto)


def _procesar_accion(request):
    """Ejecuta una acción del panel de facturación y vuelve al panel con el resultado."""
    accion = request.POST.get("accion", "")
    destino = "procesamiento_operaciones:admin_facturacion"
    try:
        if accion in ("aprobar_solicitud", "rechazar_solicitud"):
            solicitud = get_object_or_404(SolicitudAnulacionFactura, pk=request.POST.get("solicitud_id"))
            if accion == "aprobar_solicitud":
                anulacion = facturacion.aprobar_solicitud(solicitud, request.user, request.POST.get("metodo", "auto"))
                messages.success(request, f"Solicitud #{solicitud.pk} aprobada. {solicitud.respuesta}")
                _auditar(request, "FACTURA_ANULADA", f"Solicitud #{solicitud.pk}: {anulacion}")
            else:
                facturacion.rechazar_solicitud(solicitud, request.user, request.POST.get("respuesta", ""))
                messages.info(request, f"Solicitud #{solicitud.pk} rechazada; se avisó al cliente.")
                _auditar(request, "SOLICITUD_ANULACION_RECHAZADA", f"Solicitud #{solicitud.pk}: {solicitud.respuesta}")
            return redirect(destino)

        if accion == "reintentar_todas":
            return _reintentar_todas(request, destino)

        modelo = facturacion.modelo_de(request.POST.get("tipo", ""))
        tx = get_object_or_404(modelo, pk=request.POST.get("id"))
        nombre = f"{tx.tipo_operacion.capitalize()} #{tx.pk}"
        motivo = request.POST.get("motivo", "")

        devolucion = request.POST.get("devolucion") == "1"
        if accion in ("reintentar", "refacturar"):
            estado = facturacion.reintentar_facturacion(tx, request.user)
            _mensaje_estado(request, nombre, estado, tx)
            _auditar(request, "FACTURA_REINTENTO", f"{nombre}: {'nueva factura (corrección)' if accion == 'refacturar' else 'reintento'}, estado {estado}.")
        elif accion == "reintentar_devolucion":
            facturacion.reintentar_devolucion(tx)
            messages.success(request, f"{nombre}: fondos devueltos; la operación quedó anulada.")
            _auditar(request, "DEVOLUCION_FONDOS", f"{nombre}: devolución realizada.")
        elif accion == "consultar":
            estado = facturacion.consultar_estado(tx)
            messages.info(request, f"{nombre}: la factura está {estado}.")
        elif accion == "cancelar":
            facturacion.cancelar_factura(tx, motivo, request.user, devolucion=devolucion)
            fondos = " Cuando SIFEN la confirme se devolverán los fondos." if devolucion else " Los fondos no se modifican."
            messages.success(
                request,
                f"{nombre}: cancelación solicitada a SIFEN. El resultado se actualiza en este panel en uno o dos minutos.{fondos}",
            )
            _auditar(request, "FACTURA_CANCELADA", f"{nombre} (CDC {tx.sifen_cdc}, {'con' if devolucion else 'sin'} devolución): {motivo}")
        elif accion == "nota_credito":
            anulacion = facturacion.emitir_nota_credito(tx, motivo, request.user, devolucion=devolucion)
            fondos = "se devolverán los fondos, " if devolucion else ""
            messages.success(
                request,
                f"{nombre}: nota de crédito {anulacion.cdc_nota_credito} emitida. "
                f"Cuando SIFEN la apruebe, la factura quedará Anulada, {fondos}y la nota se enviará al cliente por correo.",
            )
            _auditar(request, "NOTA_CREDITO_EMITIDA", f"{nombre}: NC {anulacion.cdc_nota_credito} ({'con' if devolucion else 'sin'} devolución). Motivo: {motivo}")
        elif accion == "reenviar_nota":
            anulacion = facturacion.opciones_factura(tx)["anulacion"]
            if anulacion is None or not anulacion.cdc_nota_credito:
                raise FacturacionError("La operación no tiene una nota de crédito para reenviar.")
            enviado, detalle = facturacion.enviar_nota_credito_por_correo(anulacion)
            (messages.success if enviado else messages.error)(request, detalle)
        else:
            messages.error(request, "Acción de facturación no reconocida.")
    except FacturacionError as exc:
        messages.error(request, str(exc))
    return redirect(destino)


def _mensaje_estado(request, nombre, estado, tx):
    """Notificación según el resultado de un reintento."""
    if estado == "Error":
        messages.error(request, f"{nombre}: la factura volvió a fallar. Detalle: {tx.sifen_error}")
    elif estado == "En cola":
        messages.info(request, f"{nombre}: la factura quedó en cola; se emitirá cuando SIFEN responda el documento anterior.")
    else:
        messages.success(request, f"{nombre}: factura enviada a SIFEN (estado {estado}).")


def _reintentar_todas(request, destino):
    """Reintenta todas las facturas con error, rechazadas o en cola (por ejemplo, tras una caída de la API)."""
    resultados = {}
    candidatas = [
        tx for tx in reversed(_operaciones_facturables())  # de la más antigua a la más nueva
        if facturacion.opciones_factura(tx)["reintentar"] and tx.sifen_tiene_registro
    ]
    # Las que quedaron sin confirmar se revisan una por una: reenviarlas a ciegas puede duplicarlas
    sin_confirmar = [tx for tx in candidatas if tx.sifen_sin_confirmar]
    pendientes = [tx for tx in candidatas if not tx.sifen_sin_confirmar]
    detenido = False
    for tx in pendientes:
        try:
            estado = facturacion.reintentar_facturacion(tx, request.user)
        except FacturacionError:
            continue
        resultados[estado] = resultados.get(estado, 0) + 1
        if estado == "Error":
            # Si la API sigue caída, no tiene sentido esperar el tiempo de espera de cada factura
            detenido = True
            break
    if sin_confirmar:
        messages.warning(
            request,
            f"{len(sin_confirmar)} factura(s) quedaron sin confirmar porque la API no respondió; no se reenviaron. "
            "Verifíquelas en Factura Segura y reinténtelas una por una.",
        )
    if not resultados:
        if not sin_confirmar:
            messages.info(request, "No hay facturas para reintentar.")
    else:
        resumen = ", ".join(f"{cantidad} {estado.lower()}" for estado, cantidad in sorted(resultados.items()))
        if detenido:
            messages.warning(
                request,
                f"Reintento detenido: Factura Segura sigue respondiendo con error ({tx.sifen_error}). "
                f"Resultado: {resumen}. Vuelva a intentar más tarde.",
            )
        else:
            messages.success(request, f"Reintento masivo terminado: {resumen}.")
        _auditar(request, "FACTURA_REINTENTO", f"Reintento masivo: {resumen}.")
    return redirect(destino)


def _es_operacion_del_usuario(request, tx):
    """El usuario realizó la operación o pertenece al cliente de la operación."""
    if tx.user_id == request.user.id:
        return True
    if tx.cliente_id is None:
        return False
    profile = getattr(request.user, "profile", None)
    ids = [str(request.user.id)] + ([profile.keycloak_id] if profile and profile.keycloak_id else [])
    return UsuarioClienteRelacion.objects.filter(cliente_id=tx.cliente_id, keycloak_user_id__in=ids).exists()


@login_required
@requiere_permiso("can_request_invoice_cancellation")
def solicitar_anulacion_factura_view(request):
    """
    El cliente pide la anulación de la factura de una de sus operaciones.
    La solicitud queda pendiente hasta que el Administrador la apruebe o la rechace.
    """
    defecto = "procesamiento_operaciones:currency_transactions_history"
    if request.method != "POST":
        return redirect(defecto)
    modelo = facturacion.modelo_de(request.POST.get("tipo", ""))
    tx = get_object_or_404(modelo, pk=request.POST.get("id"))
    if not _es_operacion_del_usuario(request, tx):
        messages.error(request, "Solo puede pedir la anulación de facturas de sus propias operaciones.")
        return _volver(request, defecto)
    try:
        solicitud = facturacion.solicitar_anulacion(tx, request.user, request.POST.get("motivo", ""))
    except FacturacionError as exc:
        messages.error(request, str(exc))
        return _volver(request, defecto)
    _auditar(request, "SOLICITUD_ANULACION_FACTURA", f"{solicitud.descripcion_operacion} (CDC {solicitud.cdc}): {solicitud.motivo}")
    messages.success(request, "Solicitud de anulación enviada. Le avisaremos cuando el área de facturación la resuelva.")
    return _volver(request, defecto)
