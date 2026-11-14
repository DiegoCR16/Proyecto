# -*- coding: utf-8 -*-
"""
Gestión del ciclo de vida de la factura electrónica (SIFEN) de las operaciones de cambio.

Estados de la factura de una operación (``tx.sifen_estado``):

* ``En cola``: espera número de documento (otro documento aguarda respuesta de SIFEN).
* ``Emitida`` / ``Pendiente``: enviada a SIFEN, sin resultado final todavía.
* ``Aprobada``: aprobada por SIFEN. Es la única que puede anularse.
* ``Rechazada``: SIFEN la rechazó. El número no se consumió; se puede reintentar.
* ``Error``: no llegó a SIFEN (API caída, sin token, datos inválidos) o Factura Segura informó
  ``ERROR_SIFEN`` / ``ERROR_ENVIO_LOTE`` / ``ERROR_CONSULTA_LOTE``. Se puede reintentar.
* ``Cancelación en proceso``: se pidió el evento de cancelación (``sol_cancelacion``) y se
  espera la respuesta de SIFEN (``estado_can`` en ``get_estado_sifen``).
* ``Cancelada``: SIFEN aprobó el evento de cancelación.
* ``Anulación en proceso``: se emitió una nota de crédito que espera aprobación.
* ``Anulada``: anulada con una nota de crédito aprobada.

Reglas de SIFEN aplicadas:

* La cancelación de una factura solo se acepta dentro de ``SIFEN_PLAZO_CANCELACION_HORAS``
  (48 h) desde su aprobación. Pasado el plazo, la anulación se hace con una nota de crédito
  electrónica por el total, que se envía por correo al cliente.
* Como el sistema no guarda la hora exacta de aprobación, el plazo se cuenta desde la fecha
  de la operación (la factura se emite en ese momento y nunca se aprueba antes), lo que
  deja un margen conservador.
* No hace falta el evento de inutilización (``sol_inutilizacion``): la numeración solo avanza
  cuando SIFEN aprueba un documento, así que el número de un documento rechazado o con error
  se vuelve a usar en el siguiente envío (Factura Segura marca el anterior como "Reingresado").

Referencia: "API de Factura Segura para ESI v01" (docs/).
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction as db_transaction
from django.utils import timezone

from authentication.facturacion_service import FacturaSeguraService
from . import views as ops
from authentication.models import ClientAccreditationMethod
from tasas_cambio.models import PaymentMethod
from .models import (
    AnulacionFactura, CurrencyPurchaseTransaction, CurrencySaleTransaction, MovimientoSaldoOperacion,
    SolicitudAnulacionFactura,
)

logger = logging.getLogger(__name__)

MOTIVO_MINIMO = 5
MOTIVO_MAXIMO = 500
ESTADOS_REINTENTABLES = ("Error", "Rechazada", "En cola")
ESTADOS_CONSULTABLES = ("Emitida", "Pendiente")
ESTADOS_REFACTURABLES = ("Cancelada", "Anulada")
STATUS_ANULADA = "ANULADA"


class FacturacionError(Exception):
    """Una acción sobre la factura no se pudo realizar; el mensaje explica el motivo."""


# --------------------------------------------------------------------------- consultas

def modelo_de(tipo):
    """Modelo de transacción según el tipo ('COMPRA' o 'VENTA')."""
    return CurrencySaleTransaction if str(tipo).upper() == "VENTA" else CurrencyPurchaseTransaction


def _filtro_operacion(tx):
    """Filtro {'compra': tx} o {'venta': tx} para los modelos de facturación."""
    return {"venta": tx} if tx.tipo_operacion == "VENTA" else {"compra": tx}


def plazo_cancelacion():
    """Plazo de SIFEN para cancelar una factura aprobada."""
    return timedelta(hours=int(getattr(settings, "SIFEN_PLAZO_CANCELACION_HORAS", 48)))


def vencimiento_cancelacion(tx):
    """Fecha y hora hasta la que la factura todavía puede cancelarse."""
    return tx.timestamp + plazo_cancelacion()


def puede_cancelarse(tx, ahora=None):
    """Indica si la factura está aprobada y dentro del plazo de cancelación."""
    return tx.sifen_estado == "Aprobada" and (ahora or timezone.now()) <= vencimiento_cancelacion(tx)


def anulacion_de_la_factura(tx):
    """Última anulación de la factura vigente (el CDC actual) de la operación, o None."""
    if not tx.sifen_cdc:
        return None
    return AnulacionFactura.objects.filter(cdc_factura=tx.sifen_cdc, **_filtro_operacion(tx)).first()


def anulacion_vigente(tx):
    """Anulación emitida o aprobada de la factura vigente de la operación, o None."""
    anulacion = anulacion_de_la_factura(tx)
    if anulacion and anulacion.estado in (AnulacionFactura.EMITIDA, AnulacionFactura.APROBADA):
        return anulacion
    return None


def es_operacion_de_ventanilla(tx):
    """Las operaciones de ventanilla se pagan en efectivo en la caja física."""
    return tx.payment_method_type == "EFECTIVO_VENTANILLA"


def solicitud_pendiente(tx):
    """Solicitud de anulación pendiente de la operación, o None."""
    return SolicitudAnulacionFactura.objects.filter(
        estado=SolicitudAnulacionFactura.PENDIENTE, **_filtro_operacion(tx)
    ).first()


def opciones_factura(tx, ahora=None):
    """
    Acciones disponibles para la factura de una operación, según su estado y el plazo de SIFEN.

    Args:
        tx: Transacción de compra o venta.
        ahora (datetime, opcional): Momento de referencia (para pruebas).

    Returns:
        dict: Banderas ``reintentar``, ``refacturar``, ``consultar``, ``cancelar``,
        ``nota_credito``, ``devolver``, ``reintentar_devolucion``, ``reenviar_nota``; además
        ``vence`` (fecha límite de cancelación) y ``anulacion``.
    """
    estado = tx.sifen_estado
    anulacion = anulacion_de_la_factura(tx)
    ninguna = {"reintentar": False, "refacturar": False, "consultar": False, "cancelar": False,
               "nota_credito": False, "devolver": False, "reintentar_devolucion": False,
               "reenviar_nota": False, "vence": None, "anulacion": anulacion}
    if tx.status not in ("SUCCESS", STATUS_ANULADA):
        # Las operaciones pendientes, canceladas o fallidas no se facturan
        return {**ninguna, "anulacion": None}
    reenviar_nota = bool(
        anulacion and anulacion.tipo == AnulacionFactura.NOTA_CREDITO and anulacion.cdc_nota_credito
        and anulacion.estado == AnulacionFactura.APROBADA
    )
    if tx.status == STATUS_ANULADA:
        # Operación anulada y fondos devueltos: solo queda reenviar la nota de crédito
        return {**ninguna, "reenviar_nota": reenviar_nota}
    sin_registro = not tx.sifen_tiene_registro
    aprobada = estado == "Aprobada"
    return {
        "reintentar": estado in ESTADOS_REINTENTABLES or (sin_registro and not tx.sifen_cdc),
        # Factura anulada solo para corregirla: la operación sigue vigente y se puede volver a facturar
        "refacturar": estado in ESTADOS_REFACTURABLES and bool(anulacion) and not anulacion.con_devolucion,
        "devolver": aprobada and not es_operacion_de_ventanilla(tx),
        "reintentar_devolucion": bool(
            anulacion and anulacion.con_devolucion and anulacion.estado == AnulacionFactura.APROBADA
            and not anulacion.devuelta_en
        ),
        "consultar": estado in ESTADOS_CONSULTABLES and bool(tx.sifen_cdc),
        "cancelar": puede_cancelarse(tx, ahora),
        "nota_credito": aprobada,
        "reenviar_nota": reenviar_nota,
        "vence": vencimiento_cancelacion(tx) if aprobada else None,
        "anulacion": anulacion,
    }


def _validar_motivo(motivo):
    """Normaliza el motivo y verifica el largo que exige SIFEN."""
    motivo = " ".join((motivo or "").split())
    if len(motivo) < MOTIVO_MINIMO:
        raise FacturacionError(f"Indique el motivo de la anulación (al menos {MOTIVO_MINIMO} caracteres).")
    return motivo[:MOTIVO_MAXIMO]


def _token():
    """Token de Factura Segura o FacturacionError si la API no responde."""
    token = FacturaSeguraService.obtener_token()
    if not token:
        raise FacturacionError("No se pudo conectar con Factura Segura (SIFEN). Intente nuevamente en unos minutos.")
    return token


def _respuesta_ok(res):
    """Según la API ESI, ``code`` 0 o positivo indica que la operación se ejecutó correctamente."""
    code = res.get("code")
    return isinstance(code, int) and code >= 0


def _descripcion_error(res):
    """Texto legible del error devuelto por la API."""
    return str(res.get("description") or res.get("error") or res.get("message") or "Sin respuesta")[:300].replace("|", "/")


# --------------------------------------------------------------------------- reintento y consulta

def reintentar_facturacion(tx, usuario=None):
    """
    Vuelve a emitir la factura de una operación cuyo intento anterior falló o fue rechazado,
    o cuya factura se anuló solo para corregirla (por ejemplo, datos del receptor equivocados:
    se corrige el cliente y se vuelve a facturar con sus datos actuales).

    El intento anterior queda en el historial del desglose; el nuevo intento empieza con la
    marca "SIFEN Reintento".

    Args:
        tx: Transacción de compra o venta.
        usuario (User, opcional): Administrador que reintenta.

    Returns:
        str: Estado de la factura después del reintento.

    Raises:
        FacturacionError: Si la factura no está en un estado que permita reintentar.
    """
    if tx.status != "SUCCESS":
        raise FacturacionError("La operación no está vigente; solo se facturan operaciones exitosas.")
    opciones = opciones_factura(tx)
    if not (opciones["reintentar"] or opciones["refacturar"]):
        raise FacturacionError(
            f"La factura está {tx.sifen_estado}; solo se reintentan facturas con error, rechazadas o en cola, "
            "o anuladas para corregirlas."
        )
    autor = getattr(usuario, "username", "sistema")
    fecha = timezone.localtime().strftime("%d/%m/%Y %H:%M")
    ops._registrar_estado_sifen(tx, f"SIFEN Reintento ({autor} {fecha}) | SIFEN Estado: En cola")
    ops._generar_factura_sifen_async(tx)
    tx.refresh_from_db()
    return tx.sifen_estado


def consultar_estado(tx):
    """
    Consulta en SIFEN el estado de una factura emitida y lo guarda si ya es definitivo.

    Returns:
        str: Estado de la factura después de la consulta.
    """
    if not opciones_factura(tx)["consultar"]:
        raise FacturacionError(f"La factura está {tx.sifen_estado}; no hay un resultado pendiente que consultar.")
    ops._sincronizar_facturas_sifen(type(tx).objects.filter(pk=tx.pk), limite=1)
    tx.refresh_from_db()
    return tx.sifen_estado


# --------------------------------------------------------------------------- anulación

def anular_factura(tx, motivo, usuario=None, solicitud=None, metodo="auto", devolucion=False):
    """
    Anula una factura aprobada por el camino que corresponde según el plazo de SIFEN.

    Args:
        tx: Transacción de compra o venta.
        motivo (str): Motivo de la anulación.
        usuario (User, opcional): Administrador que anula.
        solicitud (SolicitudAnulacionFactura, opcional): Pedido del cliente que se atiende.
        metodo (str): "auto" (cancelación si está en plazo, si no nota de crédito),
            "cancelacion" o "nota_credito".
        devolucion (bool): True para además devolver los fondos (la operación queda ANULADA
            cuando SIFEN aprueba la anulación); False para solo corregir la factura.

    Returns:
        AnulacionFactura: Registro de la anulación realizada.

    Raises:
        FacturacionError: Si la factura no puede anularse o SIFEN la rechaza.
    """
    if metodo == "auto":
        metodo = "cancelacion" if puede_cancelarse(tx) else "nota_credito"
    if metodo == "cancelacion":
        return cancelar_factura(tx, motivo, usuario, solicitud, devolucion)
    return emitir_nota_credito(tx, motivo, usuario, solicitud, devolucion)


def _verificar_anulable(tx):
    """La factura debe estar aprobada y sin otra anulación en curso."""
    if tx.sifen_estado != "Aprobada" or not tx.sifen_cdc:
        raise FacturacionError(f"Solo se anulan facturas aprobadas por SIFEN (esta está {tx.sifen_estado}).")
    if anulacion_vigente(tx):
        raise FacturacionError("La factura ya tiene una anulación en curso o realizada.")


def cancelar_factura(tx, motivo, usuario=None, solicitud=None, devolucion=False):
    """
    Solicita el evento de cancelación de SIFEN (``sol_cancelacion``) para una factura aprobada
    dentro del plazo. El evento es asíncrono: la factura queda "Cancelación en proceso" y el
    resultado se obtiene con ``sincronizar_anulaciones``.

    El motivo queda registrado en el sistema y en la auditoría; la API de Factura Segura no lo recibe.

    Returns:
        AnulacionFactura: Cancelación solicitada (estado EMITIDA).

    Raises:
        FacturacionError: Si venció el plazo, la factura no está aprobada o Factura Segura no registra la solicitud.
    """
    motivo = _validar_motivo(motivo)
    _verificar_anulable(tx)
    if devolucion:
        verificar_devolucion_posible(tx)
    if not puede_cancelarse(tx):
        limite = timezone.localtime(vencimiento_cancelacion(tx)).strftime("%d/%m/%Y %H:%M")
        raise FacturacionError(
            f"Venció el plazo de SIFEN para cancelar la factura (hasta {limite}). Anúlela con una nota de crédito."
        )

    cdc = tx.sifen_cdc
    res = FacturaSeguraService.sol_cancelacion(_token(), cdc, ops.SIFEN_RUC_EMISOR) or {}
    if not _respuesta_ok(res):
        AnulacionFactura.objects.create(
            tipo=AnulacionFactura.CANCELACION, estado=AnulacionFactura.ERROR, cdc_factura=cdc, motivo=motivo,
            detalle=_descripcion_error(res), solicitud=solicitud, usuario=usuario, con_devolucion=devolucion,
            **_filtro_operacion(tx),
        )
        raise FacturacionError(f"Factura Segura no registró la cancelación: {_descripcion_error(res)}")

    anulacion = AnulacionFactura.objects.create(
        tipo=AnulacionFactura.CANCELACION, estado=AnulacionFactura.EMITIDA, cdc_factura=cdc, motivo=motivo,
        detalle="Cancelación solicitada; espera la respuesta de SIFEN.", solicitud=solicitud, usuario=usuario,
        con_devolucion=devolucion, **_filtro_operacion(tx),
    )
    ops._registrar_estado_sifen(tx, f"SIFEN Estado: Cancelación en proceso ({motivo.replace('|', '/')[:150]})")
    return anulacion


def _construir_nota_credito(tx, item, numero_documento, cdc_factura, motivo, devolucion=True):
    """
    Arma el Documento Electrónico de la nota de crédito (iTiDE 5) por el total de la factura.

    Reutiliza emisor, receptor, ítem y totales de la factura; quita los campos propios de
    la factura (condición y forma de pago) y agrega el motivo y el documento asociado.
    """
    de = ops._construir_de(tx, item, numero_documento)["DE"]
    # La nota de crédito no lleva indicador de presencia ni condición/forma de pago
    for campo in ("iIndPres", "iCondOpe", "gPaConEIni", "iCondCred", "dPlazoCre"):
        de.pop(campo, None)
    de["iTiDE"] = ops.SIFEN_TIDE_NOTA_CREDITO
    # Manual SIFEN: 2 = Devolución (se devuelven los fondos); 1 = Devolución y ajuste de
    # precios (la factura se anula para corregirla y la operación se vuelve a facturar)
    de["iMotEmi"] = "2" if devolucion else "1"
    # Factura Segura solo admite un documento asociado; para iTipDocAso = 1 (electrónico)
    # los demás campos del grupo van con valores fijos.
    de["gCamDEAsoc"] = [{
        "iTipDocAso": "1",
        "dCdCDERef": cdc_factura,
        "dNTimDI": "0",
        "dEstDocAso": "0",
        "dPExpDocAso": "0",
        "dNumDocAso": "0",
        "iTipoDocAso": "1",
        "dFecEmiDI": timezone.localtime(tx.timestamp).strftime("%Y-%m-%d"),
        "dNumComRet": "0",
        "dNumResCF": "0",
        "iTipCons": "0",
        "dNumCons": "0",
        "dNumControl": "0",
    }]
    for linea in de.get("gCamItem", []):
        linea["dDesProSer"] = f"Nota de credito: {linea.get('dDesProSer', '')}"[:120]
    de["dInfAdic"] = ops._remove_accents(f"Anulacion de la factura {cdc_factura}. Motivo: {motivo}")[:300]
    return {"DE": de}


def emitir_nota_credito(tx, motivo, usuario=None, solicitud=None, devolucion=False):
    """
    Emite una nota de crédito electrónica por el total de una factura aprobada. La factura queda
    "Anulación en proceso" hasta que SIFEN apruebe la nota; en ese momento se envía al cliente
    por correo (``sincronizar_anulaciones``).

    Returns:
        AnulacionFactura: Nota de crédito emitida.

    Raises:
        FacturacionError: Si la factura no es anulable, no hay número disponible o SIFEN la rechaza.
    """
    motivo = _validar_motivo(motivo)
    _verificar_anulable(tx)
    if devolucion:
        verificar_devolucion_posible(tx)
    cdc_factura = tx.sifen_cdc
    item = ops._calcular_item(tx)
    token = _token()
    tide = ops.SIFEN_TIDE_NOTA_CREDITO

    try:
        numero = ops._reservar_numero_documento(tide) if ops._resolver_numero_en_proceso(token, tide) else None
    except ops.SifenRangoAgotado as exc:
        raise FacturacionError(f"No hay números de nota de crédito disponibles: {exc}")
    if numero is None:
        raise FacturacionError("Otra nota de crédito espera la respuesta de SIFEN. Intente nuevamente en unos minutos.")

    emitida = False
    try:
        res = FacturaSeguraService.generar_de(token, _construir_nota_credito(tx, item, f"{numero:07d}", cdc_factura, motivo, devolucion)) or {}
        resultados = res.get("results") or []
        cdc_nc = resultados[0].get("CDC") if _respuesta_ok(res) and resultados and isinstance(resultados[0], dict) else None
        if not cdc_nc:
            AnulacionFactura.objects.create(
                tipo=AnulacionFactura.NOTA_CREDITO, estado=AnulacionFactura.ERROR, cdc_factura=cdc_factura,
                motivo=motivo, detalle=_descripcion_error(res), solicitud=solicitud, usuario=usuario,
                con_devolucion=devolucion, **_filtro_operacion(tx),
            )
            raise FacturacionError(f"SIFEN no emitió la nota de crédito: {_descripcion_error(res)}")
        ops._guardar_cdc_en_proceso(numero, cdc_nc, tide)
        emitida = True
    finally:
        if not emitida:
            ops._liberar_numero(numero, tide=tide)

    anulacion = AnulacionFactura.objects.create(
        tipo=AnulacionFactura.NOTA_CREDITO, estado=AnulacionFactura.EMITIDA, cdc_factura=cdc_factura,
        cdc_nota_credito=cdc_nc, numero_nota_credito=numero, motivo=motivo, solicitud=solicitud, usuario=usuario,
        con_devolucion=devolucion,
        detalle="Nota de crédito emitida; espera aprobación de SIFEN.", **_filtro_operacion(tx),
    )
    ops._registrar_estado_sifen(tx, f"SIFEN Estado: Anulación en proceso (nota de crédito {cdc_nc})")
    return anulacion


def _texto_resultado(resultado, campos):
    """Une los campos de detalle de get_estado_sifen que vengan informados."""
    return " - ".join(str(resultado.get(c)) for c in campos if resultado.get(c)).replace("|", "/")[:200]


def sincronizar_anulaciones(limite=10):
    """
    Consulta en SIFEN el resultado de las anulaciones en curso y actualiza la factura.

    * Cancelación (``estado_can``): "Aprobado" -> factura Cancelada; "Rechazado" o
      "ERROR_SIFEN" -> la factura vuelve a "Aprobada" con el motivo, para que el
      administrador pueda reintentar o emitir una nota de crédito.
    * Nota de crédito (``estado_sifen`` del CDC de la nota): aprobada -> factura Anulada y
      se envía la nota al cliente por correo; rechazada o con error -> la factura vuelve a "Aprobada".

    Returns:
        int: Cantidad de anulaciones con resultado definitivo.
    """
    pendientes = list(
        AnulacionFactura.objects.filter(estado=AnulacionFactura.EMITIDA).order_by("creada_en")[:limite]
    )
    if not pendientes:
        return 0
    token = FacturaSeguraService.obtener_token()
    if not token:
        return 0
    resueltas = 0
    for anulacion in pendientes:
        es_nota = anulacion.tipo == AnulacionFactura.NOTA_CREDITO
        cdc = anulacion.cdc_nota_credito if es_nota else anulacion.cdc_factura
        if not cdc:
            continue
        res = FacturaSeguraService.get_estado_sifen(token, cdc, ops.SIFEN_RUC_EMISOR) or {}
        resultados = res.get("results") or []
        if not _respuesta_ok(res) or not resultados:
            continue
        resultado = resultados[0]
        if es_nota:
            estado = ops._mapear_estado_sifen(resultado.get("estado_sifen"))
            aprobada = estado == "Aprobada"
            detalle = _texto_resultado(resultado, ("estado_sifen", "desc_sifen", "error_sifen"))
        else:
            estado_can = str(resultado.get("estado_can") or "").upper()
            if not estado_can or estado_can.startswith("SOL") or estado_can.startswith("ENVIADO"):
                continue
            estado = "Aprobada" if "APROB" in estado_can else "Rechazada"
            aprobada = estado == "Aprobada"
            detalle = _texto_resultado(resultado, ("estado_can", "desc_can", "error_can"))
        if estado == "Pendiente":
            continue

        tx = anulacion.transaccion
        with db_transaction.atomic():
            if aprobada:
                anulacion.estado = AnulacionFactura.APROBADA
                anulacion.detalle = detalle or "Aprobada por SIFEN."
                marca = (
                    f"SIFEN Estado: Anulada (nota de crédito {anulacion.cdc_nota_credito})" if es_nota
                    else f"SIFEN Estado: Cancelada ({anulacion.motivo.replace('|', '/')[:150]})"
                )
            else:
                anulacion.estado = AnulacionFactura.ERROR if estado == "Error" or "ERROR" in detalle.upper() else AnulacionFactura.RECHAZADA
                anulacion.detalle = detalle or "Rechazada por SIFEN."
                tipo = "nota de crédito" if es_nota else "cancelación"
                marca = f"SIFEN Estado: Aprobada ({tipo} rechazada: {anulacion.detalle})"
            anulacion.save(update_fields=["estado", "detalle"])
            if tx is not None:
                ops._registrar_estado_sifen(tx, marca)
        if es_nota and anulacion.numero_nota_credito:
            ops._liberar_numero(anulacion.numero_nota_credito, aprobado=aprobada, tide=ops.SIFEN_TIDE_NOTA_CREDITO)
        if tx is not None:
            if aprobada and anulacion.con_devolucion:
                devolver_fondos(tx, anulacion)
            _avisar_resultado(tx, anulacion, aprobada)
        resueltas += 1
    return resueltas


def _avisar_resultado(tx, anulacion, aprobada):
    """Avisa al cliente el resultado final de la anulación y, si es una nota aprobada, se la envía."""
    operacion = f"{tx.tipo_operacion.lower()} #{tx.pk}"
    if anulacion.tipo == AnulacionFactura.NOTA_CREDITO:
        if aprobada:
            enviar_nota_credito_por_correo(anulacion)
            _notificar_cliente(tx, "Factura anulada", f"Se emitió la nota de crédito {anulacion.cdc_nota_credito} que anula la factura de su {operacion}. Se la enviamos por correo.")
        return
    if aprobada:
        _notificar_cliente(tx, "Factura cancelada", f"La factura de su {operacion} (CDC {anulacion.cdc_factura}) fue cancelada en SIFEN. Motivo: {anulacion.motivo}")


# --------------------------------------------------------------------------- devolución de fondos

def movimientos_originales(tx):
    """
    Movimientos de saldo que produjo la operación.

    Las operaciones nuevas los tienen registrados con el monto exacto. Para las anteriores a
    ese registro se reconstruyen con los datos guardados en la operación (si la cuenta del
    cliente está en otra moneda, la conversión usa la tasa vigente).

    Returns:
        list[dict]: Movimientos con ``tipo``, ``monto`` (con signo), ``moneda`` y la cuenta afectada.
    """
    registrados = list(MovimientoSaldoOperacion.objects.filter(es_devolucion=False, **_filtro_operacion(tx)))
    if registrados:
        return [{
            "tipo": m.tipo, "monto": m.monto, "moneda": m.moneda,
            "cuenta_cliente": m.cuenta_cliente, "metodo_pago": m.metodo_pago, "cliente": m.cliente,
        } for m in registrados]

    movimientos = []
    origen, destino = tx.origin_acreditation_method, tx.destination_acreditation_method
    if tx.tipo_operacion == "VENTA":
        caja = tx.linked_account
        debito = ops._convert_amount(tx.amount, tx.from_currency, origen.moneda) if origen else None
        credito = ops._convert_amount(tx.total_pyg, "PYG", destino.moneda) if destino else None
        # Antes de la corrección, la venta sumaba los guaraníes a la caja del negocio
        monto_caja = tx.total_pyg
    else:
        caja = tx.payment_method
        debito = ops._convert_amount(tx.total_origen or tx.amount, tx.from_currency, origen.moneda) if origen else None
        credito = ops._convert_amount(tx.converted_amount, tx.to_currency, destino.moneda) if destino else None
        monto_caja = tx.total_pyg
    if origen and debito:
        movimientos.append(ops._mov(MovimientoSaldoOperacion.CUENTA_CLIENTE, -debito, origen.moneda, cuenta_cliente=origen))
    if destino and credito:
        movimientos.append(ops._mov(MovimientoSaldoOperacion.CUENTA_CLIENTE, credito, destino.moneda, cuenta_cliente=destino))
    if caja and monto_caja:
        movimientos.append(ops._mov(MovimientoSaldoOperacion.CAJA_NEGOCIO, monto_caja, metodo_pago=caja))
    if tx.cliente_id:
        volumen = tx.monto_moneda_local or (tx.amount if tx.from_currency == "PYG" else tx.amount * tx.applied_rate)
        movimientos.append(ops._mov(MovimientoSaldoOperacion.VOLUMEN_CLIENTE, volumen, cliente=tx.cliente))
    return movimientos


def _faltantes_para_devolver(movimientos):
    """Cuentas que no tienen saldo para revertir su movimiento (descripciones legibles)."""
    faltantes = []
    for m in movimientos:
        if m["monto"] <= 0 or m["tipo"] == MovimientoSaldoOperacion.VOLUMEN_CLIENTE:
            continue  # devolver un débito acredita: no requiere saldo
        if m["tipo"] == MovimientoSaldoOperacion.CUENTA_CLIENTE:
            cuenta = m["cuenta_cliente"]
            if cuenta is None:
                faltantes.append("la cuenta del cliente que recibió los fondos ya no existe")
            elif cuenta.balance < m["monto"]:
                faltantes.append(
                    f"la cuenta {cuenta.entidad_financiera} del cliente tiene {cuenta.balance:,.2f} {cuenta.moneda} "
                    f"y hay que devolver {m['monto']:,.2f} {m['moneda']}"
                )
        elif m["tipo"] == MovimientoSaldoOperacion.CAJA_NEGOCIO:
            caja = m["metodo_pago"]
            if caja is None:
                faltantes.append("el método de pago del negocio ya no existe")
            elif caja.balance < m["monto"]:
                faltantes.append(f"la caja del negocio en {caja.name} no tiene Gs. {m['monto']:,.2f}")
    return faltantes


def verificar_devolucion_posible(tx):
    """
    Verifica que se puedan devolver los fondos de la operación.

    Raises:
        FacturacionError: Si es una operación de ventanilla (la devolución se hace en caja)
            o alguna cuenta no tiene el saldo necesario.
    """
    if es_operacion_de_ventanilla(tx):
        raise FacturacionError(
            "Las operaciones de ventanilla se devuelven en efectivo en la caja. "
            "Para corregir la factura, anúlela sin devolución de fondos."
        )
    faltantes = _faltantes_para_devolver(movimientos_originales(tx))
    if faltantes:
        raise FacturacionError("No se pueden devolver los fondos: " + "; ".join(faltantes) + ".")


def devolver_fondos(tx, anulacion):
    """
    Revierte los movimientos de saldo de la operación (mismos montos, con signo inverso) y
    deja la operación en estado ANULADA. Si algún saldo ya no alcanza, no mueve nada y la
    devolución queda pendiente con el motivo, para reintentarla desde el panel.

    Returns:
        bool: True si se devolvieron los fondos.
    """
    if anulacion.devuelta_en:
        return True
    with db_transaction.atomic():
        movimientos = movimientos_originales(tx)
        # Bloquear y releer los saldos antes de verificar
        for m in movimientos:
            if m["cuenta_cliente"] is not None:
                m["cuenta_cliente"] = ClientAccreditationMethod.objects.select_for_update().get(pk=m["cuenta_cliente"].pk)
            if m["metodo_pago"] is not None:
                m["metodo_pago"] = PaymentMethod.objects.select_for_update().get(pk=m["metodo_pago"].pk)
        faltantes = _faltantes_para_devolver(movimientos)
        if faltantes:
            anulacion.detalle_devolucion = "Devolución pendiente: " + "; ".join(faltantes) + "."
            anulacion.save(update_fields=["detalle_devolucion"])
            return False

        detalle = []
        for m in movimientos:
            inverso = -m["monto"]
            if m["tipo"] == MovimientoSaldoOperacion.CUENTA_CLIENTE:
                m["cuenta_cliente"].balance += inverso
                m["cuenta_cliente"].save(update_fields=["balance"])
                detalle.append(f"{m['cuenta_cliente'].entidad_financiera}: {inverso:+,.2f} {m['moneda']}")
            elif m["tipo"] == MovimientoSaldoOperacion.CAJA_NEGOCIO:
                m["metodo_pago"].balance += inverso
                m["metodo_pago"].save(update_fields=["balance"])
                detalle.append(f"Caja {m['metodo_pago'].name}: {inverso:+,.2f} Gs")
            elif m["cliente"] is not None:
                cliente = m["cliente"]
                cliente.transaction_volume = max(Decimal("0"), Decimal(str(cliente.transaction_volume or 0)) + inverso)
                cliente.save(update_fields=["transaction_volume"])
                detalle.append(f"Volumen transaccional: {inverso:+,.2f} Gs")
            MovimientoSaldoOperacion.objects.create(
                tipo=m["tipo"], monto=inverso, moneda=m["moneda"], cuenta_cliente=m["cuenta_cliente"],
                metodo_pago=m["metodo_pago"], cliente=m["cliente"], es_devolucion=True, anulacion=anulacion,
                **_filtro_operacion(tx),
            )

        tx.status = STATUS_ANULADA
        tx.transparent_breakdown = f"{tx.transparent_breakdown or ''} | Operación anulada con devolución de fondos"
        tx.save(update_fields=["status", "transparent_breakdown"])
        anulacion.devuelta_en = timezone.now()
        anulacion.detalle_devolucion = "Fondos devueltos. " + "; ".join(detalle)
        anulacion.save(update_fields=["devuelta_en", "detalle_devolucion"])
    _notificar_cliente(
        tx, "Fondos devueltos",
        f"Se anuló su {tx.tipo_operacion.lower()} #{tx.pk} y se devolvieron los fondos a sus cuentas.",
    )
    return True


def reintentar_devolucion(tx):
    """
    Vuelve a intentar una devolución que quedó pendiente por falta de saldo.

    Raises:
        FacturacionError: Si no hay devolución pendiente o los saldos siguen sin alcanzar.
    """
    if not opciones_factura(tx)["reintentar_devolucion"]:
        raise FacturacionError("La operación no tiene una devolución de fondos pendiente.")
    anulacion = anulacion_de_la_factura(tx)
    if not devolver_fondos(tx, anulacion):
        raise FacturacionError(anulacion.detalle_devolucion)
    return anulacion


# --------------------------------------------------------------------------- correo y avisos

def destinatarios(tx):
    """Correos del cliente de la operación y del usuario que la realizó."""
    correos = set()
    if tx.cliente is not None:
        try:
            from tasas_cambio.views import destinatarios_email_cliente
            correos |= set(destinatarios_email_cliente(tx.cliente))
        except Exception:
            logger.exception("No se pudieron obtener los correos del cliente %s", tx.cliente_id)
    if tx.user is not None and tx.user.email:
        correos.add(tx.user.email)
    return sorted(c for c in correos if c and "@" in c)


def enviar_nota_credito_por_correo(anulacion):
    """
    Envía la nota de crédito al cliente. Adjunta el KuDE (PDF) si Factura Segura ya lo tiene
    disponible; si no, el correo informa el CDC para consultarla.

    Returns:
        tuple[bool, str]: Si se envió y un detalle para mostrar al administrador.
    """
    tx = anulacion.transaccion
    if tx is None:
        return False, "La operación de la nota de crédito ya no existe."
    para = destinatarios(tx)
    if not para:
        return False, "El cliente no tiene un correo electrónico registrado."

    monto = f"{ops._calcular_item(tx)[2]:,.0f}".replace(",", ".")
    cuerpo = (
        f"Estimado cliente:\n\n"
        f"Se emitió una nota de crédito electrónica que anula la factura de su "
        f"{tx.tipo_operacion.lower()} #{tx.pk}.\n\n"
        f"Factura anulada (CDC): {anulacion.cdc_factura}\n"
        f"Nota de crédito (CDC): {anulacion.cdc_nota_credito}\n"
        f"Monto: Gs. {monto}\n"
        f"Motivo: {anulacion.motivo}\n\n"
        f"Puede verificar el documento en https://ekuatia.set.gov.py/consultas/ con el CDC indicado.\n\n"
        f"Global Exchange S.A."
    )
    correo = EmailMessage(
        subject=f"Nota de crédito electrónica - {tx.tipo_operacion.capitalize()} #{tx.pk}",
        body=cuerpo, from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None), to=para,
    )
    adjunto = False
    try:
        token = FacturaSeguraService.obtener_token()
        pdf = FacturaSeguraService.descargar_kude(token, anulacion.cdc_nota_credito) if token else None
        if pdf:
            correo.attach(f"nota_credito_{anulacion.cdc_nota_credito}.pdf", pdf, "application/pdf")
            adjunto = True
    except Exception:
        logger.exception("No se pudo adjuntar el KuDE de la nota de crédito %s", anulacion.cdc_nota_credito)
    try:
        correo.send()
    except Exception as exc:
        logger.exception("No se pudo enviar la nota de crédito %s", anulacion.cdc_nota_credito)
        return False, f"No se pudo enviar el correo: {exc}"
    anulacion.correo_enviado_en = timezone.now()
    anulacion.save(update_fields=["correo_enviado_en"])
    detalle = "con el KuDE adjunto" if adjunto else "sin PDF (SIFEN todavía no lo generó; se informó el CDC)"
    return True, f"Nota de crédito enviada a {', '.join(para)} {detalle}."


def _notificar_cliente(tx, titulo, mensaje, usuario=None):
    """Deja una notificación en pantalla al usuario de la operación (o al indicado)."""
    destino = usuario or tx.user
    if destino is None:
        return
    try:
        from tasas_cambio.models import NotificationLog
        NotificationLog.objects.create(
            user=destino, title=titulo, message=mensaje, notification_type="FACTURACION", channel="PUSH",
        )
    except Exception:
        logger.exception("No se pudo notificar al cliente sobre la factura de la operación %s", tx.pk)


# --------------------------------------------------------------------------- solicitudes del cliente

def solicitar_anulacion(tx, usuario, motivo):
    """
    Registra el pedido de un cliente para anular la factura de su operación. El pedido del
    cliente implica la devolución de los fondos, por eso se verifica que sea posible.

    Raises:
        FacturacionError: Si la factura no está aprobada, ya fue anulada, ya hay un pedido
            pendiente, es de ventanilla o los fondos ya no pueden devolverse.
    """
    motivo = _validar_motivo(motivo)
    _verificar_solicitable(tx)
    if es_operacion_de_ventanilla(tx):
        raise FacturacionError("Las operaciones realizadas en ventanilla se anulan y devuelven en la sucursal.")
    verificar_devolucion_posible(tx)
    return SolicitudAnulacionFactura.objects.create(
        cdc=tx.sifen_cdc, solicitante=usuario, cliente=tx.cliente, motivo=motivo, **_filtro_operacion(tx),
    )


def solicitar_anulacion_en_caja(tx, cajero, motivo):
    """
    Registra el pedido del cajero para anular la factura de una operación de ventanilla, cuando
    el cliente lo pide en la sucursal. Solo se anula la factura: los saldos no se modifican y,
    si corresponde devolver efectivo, el cajero lo registra como movimiento de su turno.

    Args:
        tx: Transacción de compra o venta hecha en ventanilla.
        cajero (User): Cajero que registra el pedido.
        motivo (str): Motivo indicado por el cliente.

    Returns:
        SolicitudAnulacionFactura: Solicitud pendiente, sin devolución de fondos.

    Raises:
        FacturacionError: Si la operación no es de ventanilla, la factura no está aprobada,
            ya fue anulada o ya hay un pedido pendiente.
    """
    motivo = _validar_motivo(motivo)
    if not es_operacion_de_ventanilla(tx):
        raise FacturacionError(
            "Desde la caja solo se anulan facturas de operaciones de ventanilla; "
            "las operaciones en línea las solicita el cliente desde su historial."
        )
    _verificar_solicitable(tx)
    return SolicitudAnulacionFactura.objects.create(
        cdc=tx.sifen_cdc, solicitante=cajero, cliente=tx.cliente, motivo=motivo, con_devolucion=False,
        **_filtro_operacion(tx),
    )


def _verificar_solicitable(tx):
    """La factura debe estar aprobada, sin anulación en curso y sin otro pedido pendiente."""
    if tx.sifen_estado != "Aprobada" or not tx.sifen_cdc:
        raise FacturacionError(f"Solo puede pedir la anulación de facturas aprobadas (esta está {tx.sifen_estado}).")
    if anulacion_vigente(tx):
        raise FacturacionError("La factura ya tiene una anulación en curso o realizada.")
    if solicitud_pendiente(tx):
        raise FacturacionError("Ya existe una solicitud de anulación pendiente para esta factura.")


def aprobar_solicitud(solicitud, administrador, metodo="auto"):
    """
    Aprueba la solicitud y anula la factura (cancelación en plazo o nota de crédito).
    Si la anulación falla, la solicitud sigue pendiente para volver a intentarlo.

    Returns:
        AnulacionFactura: Anulación realizada.
    """
    if solicitud.estado != SolicitudAnulacionFactura.PENDIENTE:
        raise FacturacionError("La solicitud ya fue resuelta.")
    tx = solicitud.transaccion
    if tx is None:
        raise FacturacionError("La operación de la solicitud ya no existe.")
    # El pedido del cliente devuelve los fondos; el del cajero (ventanilla) solo anula la factura
    devolucion = solicitud.con_devolucion
    anulacion = anular_factura(tx, solicitud.motivo, administrador, solicitud, metodo, devolucion=devolucion)
    solicitud.estado = SolicitudAnulacionFactura.APROBADA
    if anulacion.tipo == AnulacionFactura.CANCELACION:
        solicitud.respuesta = "Se solicitó la cancelación de la factura a SIFEN." + (
            " Los fondos se devolverán cuando SIFEN la confirme." if devolucion else ""
        )
    else:
        solicitud.respuesta = f"Se emitió la nota de crédito {anulacion.cdc_nota_credito}. Cuando SIFEN la apruebe " + (
            "se devolverán los fondos y se la enviaremos por correo." if devolucion
            else "la factura quedará anulada y se enviará la nota por correo."
        )
    solicitud.resuelta_por = administrador
    solicitud.resuelta_en = timezone.now()
    solicitud.save()
    _notificar_cliente(tx, "Solicitud de anulación aprobada", solicitud.respuesta, solicitud.solicitante)
    return anulacion


def rechazar_solicitud(solicitud, administrador, respuesta):
    """
    Rechaza la solicitud con el motivo indicado y avisa al cliente.

    Raises:
        FacturacionError: Si falta el motivo o la solicitud ya fue resuelta.
    """
    if solicitud.estado != SolicitudAnulacionFactura.PENDIENTE:
        raise FacturacionError("La solicitud ya fue resuelta.")
    respuesta = " ".join((respuesta or "").split())
    if len(respuesta) < MOTIVO_MINIMO:
        raise FacturacionError("Indique al cliente el motivo del rechazo.")
    solicitud.estado = SolicitudAnulacionFactura.RECHAZADA
    solicitud.respuesta = respuesta
    solicitud.resuelta_por = administrador
    solicitud.resuelta_en = timezone.now()
    solicitud.save()
    tx = solicitud.transaccion
    if tx is not None:
        _notificar_cliente(
            tx, "Solicitud de anulación rechazada",
            f"La solicitud para anular la factura de su {tx.tipo_operacion.lower()} #{tx.pk} fue rechazada: {respuesta}",
            solicitud.solicitante,
        )
    return solicitud
