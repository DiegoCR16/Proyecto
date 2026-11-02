# -*- coding: utf-8 -*-
from django.shortcuts import render, redirect
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.db import models
from decimal import Decimal
import time
import csv
import uuid
import json
import sys
from django.conf import settings
from authentication.models import UserProfile, Cliente, UsuarioClienteRelacion, ClientAccreditationMethod
from tasas_cambio.models import ExchangeRate, ClientBenefitRule, PaymentMethod
from tasas_cambio.views import (
    ensure_default_payment_methods, ensure_default_benefit_rules,
    SimuladorConversionService, get_user_effective_category, get_active_client,
    LIMITE_MINIMO_OPERACION_PYG, LIMITE_MAXIMO_OPERACION_PYG
)
from .models import CurrencyPurchaseTransaction, CurrencySaleTransaction, SifenSecuencia
from integracion.services import PaymentGatewaySimulator
from integracion.gateways import PaymentGatewayFactory, StripeGateway, BancardGateway, TigoMoneyGateway, SIPAPGateway
from integracion.models import PaymentGatewayLog
from caja.models import TurnoCaja
from authentication.facturacion_service import FacturaSeguraService
import re
import logging
import unicodedata
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction as db_transaction


logger = logging.getLogger(__name__)

# ---- Datos fijos del emisor / timbrado -------------------------------------
SIFEN_EST = "001"
SIFEN_PUNTO_EXP = "003"
SIFEN_TIDE = "1"  # Factura electronica
SIFEN_RUC_EMISOR = "2595733"
SIFEN_LARGO_CDC = 44
SIFEN_LIMITE_SINCRONIZACION = 5
SIFEN_VENCIMIENTO_RESERVA = timedelta(minutes=5)
EMAIL_FALLBACK = "soporte@facturasegura.com.py"

ACTIVIDADES_DEFAULT = [
    {"cActEco": "62010", "dDesActEco": "Actividades de programación informática"},
    {
        "cActEco": "74909",
        "dDesActEco": "Otras actividades profesionales, científicas y técnicas n.c.p.",
    },
]


class SifenRangoAgotado(Exception):
    """Ya se aprobaron todos los numeros de documento permitidos."""


def _clave_secuencia():
    """Devuelve la clave de numeracion del establecimiento, punto y tipo de documento."""
    return f"{SIFEN_EST}-{SIFEN_PUNTO_EXP}-{SIFEN_TIDE}"


def _reservar_numero_documento():
    """
    Reserva el proximo dNumDoc sin avanzar la numeracion, que solo cambia al aprobarse.

    Returns:
        int: Numero reservado, o None si otro documento aun espera el resultado de SIFEN.

    Raises:
        SifenRangoAgotado: Si el proximo numero supera FACTURASEGURA_NUMERO_FINAL.
    """
    inicial = int(settings.FACTURASEGURA_NUMERO_INICIAL)
    final = int(settings.FACTURASEGURA_NUMERO_FINAL)
    with db_transaction.atomic():
        sec, _ = SifenSecuencia.objects.select_for_update().get_or_create(
            clave=_clave_secuencia(), defaults={"ultimo_numero": inicial - 1}
        )
        if sec.numero_en_proceso is not None:
            return None
        numero = sec.ultimo_numero + 1
        if numero > final:
            raise SifenRangoAgotado(f"Se agoto el rango de numeros de documento (hasta {final})")
        sec.numero_en_proceso = numero
        sec.cdc_en_proceso = ""
        sec.reservado_en = timezone.now()
        sec.save()
        return numero


def _guardar_cdc_en_proceso(numero, cdc):
    """Asocia el CDC recibido al numero reservado para poder consultar su resultado."""
    SifenSecuencia.objects.filter(clave=_clave_secuencia(), numero_en_proceso=numero).update(cdc_en_proceso=cdc)


def _liberar_numero(numero, aprobado=False):
    """Libera la reserva; la numeracion solo avanza si SIFEN aprobo el documento."""
    with db_transaction.atomic():
        sec = SifenSecuencia.objects.select_for_update().filter(clave=_clave_secuencia()).first()
        if sec is None or sec.numero_en_proceso != numero:
            return
        if aprobado:
            sec.ultimo_numero = numero
        sec.numero_en_proceso = None
        sec.cdc_en_proceso = ""
        sec.reservado_en = None
        sec.save()


def _resolver_numero_en_proceso(token):
    """
    Consulta el documento que espera resultado y libera su numero si ya es definitivo.

    Args:
        token (str): Token de autenticacion activo.

    Returns:
        bool: True si se puede emitir otro documento.
    """
    sec = SifenSecuencia.objects.filter(clave=_clave_secuencia()).first()
    if sec is None or sec.numero_en_proceso is None:
        return True
    if not sec.cdc_en_proceso:
        # Reserva sin CDC: otra emision en curso, o una que se interrumpio
        if sec.reservado_en and timezone.now() - sec.reservado_en > SIFEN_VENCIMIENTO_RESERVA:
            _liberar_numero(sec.numero_en_proceso)
            return True
        return False
    res = FacturaSeguraService.get_estado_sifen(token, sec.cdc_en_proceso, SIFEN_RUC_EMISOR) or {}
    resultados = res.get("results") or []
    if res.get("code") != 0 or not resultados:
        logger.error("No se pudo consultar el CDC %s: %s", sec.cdc_en_proceso, res)
        return False
    estado = _mapear_estado_sifen(resultados[0].get("estado_sifen"))
    if estado == "Pendiente":
        return False
    _liberar_numero(sec.numero_en_proceso, aprobado=estado == "Aprobada")
    return True


def _emitir_siguiente_en_cola():
    """Emite la operacion mas antigua que espera un numero de documento."""
    candidatas = [
        modelo.objects.filter(transparent_breakdown__contains="SIFEN Estado: En cola")
        .exclude(transparent_breakdown__contains="SIFEN CDC:")
        .exclude(transparent_breakdown__contains="SIFEN Estado: Error")
        .order_by("timestamp")
        .first()
        for modelo in (CurrencyPurchaseTransaction, CurrencySaleTransaction)
    ]
    candidatas = [tx for tx in candidatas if tx]
    if candidatas:
        _generar_factura_sifen_async(min(candidatas, key=lambda tx: tx.timestamp))


def _remove_accents(text):
    if text is None:
        return ""
    if isinstance(text, bool):
        return "1" if text else "0"
    if isinstance(text, (int, float)):
        return str(text)
    nfkd = unicodedata.normalize("NFKD", str(text))
    cleaned = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r'["\']', "", cleaned).strip()


def _sanitize_de(d):
    """Convierte todo a string, sin tildes ni comillas (formato que espera Factura Segura)."""
    if not isinstance(d, dict):
        return _remove_accents(d)
    res = {}
    for k, v in d.items():
        if isinstance(v, dict):
            res[str(k)] = _sanitize_de(v)
        elif isinstance(v, list):
            res[str(k)] = [
                _sanitize_de(i) if isinstance(i, dict) else _remove_accents(i) for i in v
            ]
        elif v is None:
            res[str(k)] = ""
        else:
            res[str(k)] = _remove_accents(v)
    return res


def _datos_receptor(cliente, email):
    """
    Campos del receptor PLANOS (van en la raiz del DE).
      - documento con guion (RUC-DV) -> contribuyente (iNatRec=1, B2B)
      - documento sin guion (cedula)  -> no contribuyente (iNatRec=2, B2C)
      - sin cliente / sin documento   -> innominado
    """
    doc = (getattr(cliente, "documento_identidad", "") or "").strip() if cliente else ""
    nombre = (getattr(cliente, "nombre_o_razon_social", "") or "").strip() if cliente else ""

    if "-" in doc:
        ruc = re.sub(r"\D", "", doc.split("-", 1)[0])
        dv = re.sub(r"\D", "", doc.split("-", 1)[1])
        if ruc and dv:
            return {
                "iNatRec": "1",
                "iTiOpe": "1",
                "cPaisRec": "PRY",
                # 1 = persona fisica, 2 = persona juridica (RUC 80000000 en adelante)
                "iTiContRec": "2" if int(ruc) >= 80000000 else "1",
                "dRucRec": ruc,
                "dDVRec": dv,
                "iTipIDRec": "0",
                "dNumIDRec": "0",
                "dNomRec": nombre or "Sin Nombre",
                "dEmailRec": email,
            }

    numero = re.sub(r"\D", "", doc)
    if numero:
        return {
            "iNatRec": "2",
            "iTiOpe": "2",
            "cPaisRec": "PRY",
            "iTipIDRec": "1",  # 1 = cedula paraguaya
            "dNumIDRec": numero,
            "dNomRec": nombre or "Sin Nombre",
            "dEmailRec": email,
        }

    # Innominado
    return {
        "iNatRec": "2",
        "iTiOpe": "2",
        "cPaisRec": "PRY",
        "iTipIDRec": "5",  # 5 = innominado
        "dNumIDRec": "0",
        "dNomRec": "Sin Nombre",
        "dEmailRec": email,
    }


def _calcular_item(tx):
    """
    Devuelve (cantidad, precio_unitario, total_pyg) coherentes entre si.
    La cantidad es la divisa; el precio es guaranies por unidad de divisa.
    """
    entero = Decimal("1")
    cuatro = Decimal("0.0001")
    ocho = Decimal("0.00000001")

    amount = Decimal(str(tx.amount))
    convertido = Decimal(str(getattr(tx, "converted_amount", 0) or 0))
    rate = Decimal(str(getattr(tx, "applied_rate", 0) or 0))
    from_cur = str(tx.from_currency)
    to_cur = str(tx.to_currency)

    price = None
    if from_cur == "PYG":
        # Cliente entrega guaranies y recibe divisa: cantidad = divisa recibida
        qty = convertido
        total = amount.quantize(entero, rounding=ROUND_HALF_UP)
    elif to_cur == "PYG":
        # Cliente entrega divisa y recibe guaranies
        qty = amount
        price = rate.quantize(cuatro, rounding=ROUND_HALF_UP)
        total = (qty.quantize(cuatro, rounding=ROUND_HALF_UP) * price).quantize(
            entero, rounding=ROUND_HALF_UP
        )
    else:
        # Cruce entre dos divisas: se factura por el equivalente en guaranies
        qty = amount
        total = Decimal(str(tx.monto_moneda_local)).quantize(entero, rounding=ROUND_HALF_UP)

    qty = qty.quantize(cuatro, rounding=ROUND_HALF_UP)
    if qty <= 0 or total <= 0:
        raise ValueError(f"Monto inválido para facturar (cantidad={qty}, total={total})")
    if price is None:
        price = (total / qty).quantize(ocho, rounding=ROUND_HALF_UP)
    return qty, price, total


def _construir_de(transaction, item, numero_documento):
    """
    Arma el payload del Documento Electronico de una operacion de divisas.

    Args:
        transaction: Transaccion de compra o venta a facturar.
        item (tuple): Cantidad, precio unitario y total en PYG de _calcular_item.
        numero_documento (str): dNumDoc de 7 digitos.

    Returns:
        dict: Payload con la clave "DE", listo para FacturaSeguraService.generar_de.
    """
    qty, price, total = item
    monto_item = format(total, "f")

    # ---- Receptor -------------------------------------------------------
    cliente = transaction.cliente
    raw_email = (
        getattr(cliente, "email", None)
        or getattr(transaction.user, "email", None)
        or EMAIL_FALLBACK
    )
    email = str(raw_email).strip()
    if "@" not in email:
        email = EMAIL_FALLBACK

    # ---- Tipo de transaccion (Manual SIFEN: 5 = venta, 6 = compra de divisas)
    is_sale = transaction.__class__.__name__ == "CurrencySaleTransaction"
    itip_tra = "5" if is_sale else "6"

    # ---- Pago: siempre en guaranies por el total de la operacion -----------
    gpacon_ein = [
        {
            "iTiPago": "1",
            "dMonTiPag": monto_item,
            "cMoneTiPag": "PYG",
            "dTiCamTiPag": "1",
        }
    ]

    # Fecha en hora local (no UTC)
    fecha_emision = timezone.localtime(timezone.now()).strftime("%Y-%m-%dT%H:%M:%S")

    raw_de = {
        # ---- Cabecera ----
        "iTipEmi": "1",
        "iTiDE": SIFEN_TIDE,
        "dNumTim": "02595733",
        "dFeIniT": "2025-03-27",
        "dEst": SIFEN_EST,
        "dPunExp": SIFEN_PUNTO_EXP,
        "dNumDoc": numero_documento,
        "dFeEmiDE": fecha_emision,
        "iTipTra": itip_tra,
        "iTImp": "1",
        "cMoneOpe": "PYG",
        "dCondTiCam": "1",
        "dTiCam": "1",
        # ---- Emisor ----
        "dRucEm": "2595733",
        "dDVEmi": "3",
        "iTipCont": "1",
        "dNomEmi": "Global Exchange S.A.",
        "dDirEmi": "CRUZ DEL CHACO 567",
        "dNumCas": "567",
        "cDepEmi": "1",
        "dDesDepEmi": "CAPITAL",
        "cCiuEmi": "1",
        "dDesCiuEmi": "ASUNCION (DISTRITO)",
        "dTelEmi": "(021)5887000",
        "dEmailE": "jorge.adan.ferreira02@fpuna.edu.py",
        "gActEco": getattr(
            settings, "FACTURASEGURA_EMISOR_ACTIVIDADES_ECONOMICAS", ACTIVIDADES_DEFAULT
        ),
        # ---- Receptor (campos planos en la raiz) ----
        **_datos_receptor(cliente, email),
        # ---- Condicion de la operacion ----
        "iIndPres": "1",
        "iCondOpe": "1",
        "gPaConEIni": gpacon_ein,
        "iCondCred": "1",
        "dPlazoCre": "0",
        # ---- Item (operacion exenta) ----
        "gCamItem": [
            {
                "dCodInt": f"DIV-{transaction.from_currency}",
                "dDesProSer": (
                    f"Operacion de Divisas {transaction.from_currency} -> "
                    f"{transaction.to_currency}"
                ),
                "cUniMed": "77",
                "dCantProSer": f"{qty:.4f}",
                "dPUniProSer": format(price, "f"),
                "dTiCamIt": "",
                "dTotBruOpeItem": monto_item,
                "dDescItem": "0",
                "dPorcDesIt": "0",
                "dDescGloItem": "0",
                "dAntPreUniIt": "0",
                "dAntGloPreUniIt": "0",
                "dTotOpeItem": monto_item,
                "dTotOpeGs": "",
                "iAfecIVA": "3",
                "dPropIVA": "0",
                "dTasaIVA": "0",
                "dBasGravIVA": "0",
                "dLiqIVAItem": "0",
                "dBasExe": "0",
            }
        ],
        # ---- Totales ----
        "dSubExe": monto_item,
        "dSubExo": "0",
        "dSub5": "0",
        "dSub10": "0",
        "dTotOpe": monto_item,
        "dTotDesc": "0",
        "dTotDescGlotem": "0",
        "dTotAntItem": "0",
        "dTotAnt": "0",
        "dPorcDescTotal": "0",
        "dDescTotal": "0",
        "dAnticipo": "0",
        "dRedon": "0",
        "dComi": "0",
        "dTotGralOpe": monto_item,
        "dIVA5": "0",
        "dIVA10": "0",
        "dLiqTotIVA5": "0",
        "dLiqTotIVA10": "0",
        "dIVAComi": "0",
        "dTotIVA": "0",
        "dBaseGrav5": "0",
        "dBaseGrav10": "0",
        "dTBasGraIVA": "0",
        "dTotalGs": "",
        # ---- Generados por Factura Segura ----
        "CDC": "0",
        "dCodSeg": "0",
        "dDVId": "0",
        "dSisFact": "1",
    }

    de = _sanitize_de(raw_de)
    # SIFEN compara estas descripciones con su catalogo (error 1262): se envian sin alterar
    de["gActEco"] = raw_de["gActEco"]
    return {"DE": de}


def _registrar_estado_sifen(transaction, detalle):
    """Agrega un resultado de SIFEN al desglose, sin interrumpir la operacion si falla."""
    try:
        transaction.transparent_breakdown = (
            f"{transaction.transparent_breakdown or ''} | {detalle}"
        )
        transaction.save(update_fields=["transparent_breakdown"])
    except Exception:
        logger.exception("No se pudo registrar el estado SIFEN (tx %s)", getattr(transaction, "pk", None))


def _generar_factura_sifen_async(transaction):
    """
    Genera la factura electronica (DE) en Factura Segura para una operacion exitosa
    de compra o venta de divisas. Cualquier falla queda registrada en la transaccion
    como "SIFEN Estado: Error", sin afectar la operacion de cambio ya confirmada.
    """
    if "test" in sys.argv or getattr(settings, "TESTING", False):
        return

    # Idempotencia: no emitir dos veces la misma operacion
    if "SIFEN CDC:" in (transaction.transparent_breakdown or ""):
        return

    try:
        # Se valida todo antes de reservar el numero, para no gastarlo si esto falla
        item = _calcular_item(transaction)
        token = FacturaSeguraService.obtener_token()
        if not token:
            raise RuntimeError("No se pudo obtener el token de Factura Segura")

        numero = _reservar_numero_documento() if _resolver_numero_en_proceso(token) else None
        if numero is None:
            # Otro documento espera resultado: se emitira cuando se conozca
            if "SIFEN Estado: En cola" not in (transaction.transparent_breakdown or ""):
                _registrar_estado_sifen(transaction, "SIFEN Estado: En cola")
            return

        emitida = False
        try:
            de_payload = _construir_de(transaction, item, f"{numero:07d}")
            res = FacturaSeguraService.generar_de(token, de_payload) or {}

            if res.get("code") == 0:
                results = res.get("results") or []
                cdc = results[0].get("CDC") if results and isinstance(results[0], dict) else None
                if not cdc:
                    raise RuntimeError(f"generar_de respondio OK pero sin CDC: {res}")
                _guardar_cdc_en_proceso(numero, cdc)
                emitida = True
                _registrar_estado_sifen(transaction, f"SIFEN CDC: {cdc} | SIFEN Estado: Emitida")
            else:
                desc = str(res.get("description") or res.get("error") or "Sin respuesta")[:150]
                logger.error("generar_de rechazo la factura (tx %s): %s", transaction.pk, res)
                _registrar_estado_sifen(
                    transaction,
                    f"SIFEN Estado: Error ({res.get('code')}): {desc.replace('|', '/')}",
                )
        finally:
            if not emitida:
                _liberar_numero(numero)

    except Exception as exc:
        logger.exception("ERROR EN _generar_factura_sifen_async (tx %s)", getattr(transaction, "pk", None))
        desc = str(exc)[:150].replace("|", "/")
        _registrar_estado_sifen(transaction, f"SIFEN Estado: Error: {desc}")


def _mapear_estado_sifen(estado_sifen):
    """
    Traduce el campo estado_sifen de Factura Segura al estado usado por el sistema.

    Args:
        estado_sifen (str): Valor recibido, por ejemplo "SOL.APROBACION".

    Returns:
        str: "Aprobada", "Rechazada" o "Pendiente" si aun no hay resultado final.
    """
    estado = str(estado_sifen or "").upper()
    if estado.startswith("SOL"):
        return "Pendiente"
    if "RECH" in estado or "REINGRES" in estado:
        return "Rechazada"
    if "APROB" in estado:
        return "Aprobada"
    return "Pendiente"


def _sincronizar_facturas_sifen(*querysets, limite=SIFEN_LIMITE_SINCRONIZACION):
    """
    Consulta en SIFEN las facturas sin resultado final y guarda si fueron aprobadas o rechazadas.
    Nunca interrumpe la pagina que la llama: una falla solo queda en el log.

    Args:
        *querysets: Transacciones de compra o venta a revisar.
        limite (int): Maximo de consultas por llamada, para no demorar la pagina.
    """
    if "test" in sys.argv or getattr(settings, "TESTING", False):
        return
    try:
        pendientes = []
        for queryset in querysets:
            filas = (
                queryset.filter(transparent_breakdown__contains="SIFEN CDC:")
                .exclude(transparent_breakdown__contains="SIFEN Estado: Aprobada")
                .exclude(transparent_breakdown__contains="SIFEN Estado: Rechazada")
                .order_by("-timestamp")
            )
            # Los CDC con otro largo son registros antiguos que SIFEN no reconoce
            pendientes += [tx for tx in filas if len(tx.sifen_cdc or "") == SIFEN_LARGO_CDC]
        pendientes = sorted(pendientes, key=lambda tx: tx.timestamp, reverse=True)[:limite]
        en_proceso = SifenSecuencia.objects.filter(
            clave=_clave_secuencia(), numero_en_proceso__isnull=False
        ).exists()
        en_cola = any(
            modelo.objects.filter(transparent_breakdown__contains="SIFEN Estado: En cola")
            .exclude(transparent_breakdown__contains="SIFEN CDC:")
            .exclude(transparent_breakdown__contains="SIFEN Estado: Error")
            .exists()
            for modelo in (CurrencyPurchaseTransaction, CurrencySaleTransaction)
        )
        if not (pendientes or en_proceso or en_cola):
            return

        token = FacturaSeguraService.obtener_token()
        if not token:
            return
        libre = _resolver_numero_en_proceso(token)
        for tx in pendientes:
            res = FacturaSeguraService.get_estado_sifen(token, tx.sifen_cdc, SIFEN_RUC_EMISOR) or {}
            if res.get("code") == -1:
                return  # Sin red: no seguir esperando en las demas consultas
            resultados = res.get("results") or []
            if res.get("code") != 0 or not resultados:
                logger.error("No se pudo consultar el CDC %s: %s", tx.sifen_cdc, res)
                continue
            estado = _mapear_estado_sifen(resultados[0].get("estado_sifen"))
            if estado != "Pendiente":
                detalle = str(resultados[0].get("desc_sifen") or "").replace("|", "/")[:150]
                sufijo = f" ({detalle})" if detalle else ""
                _registrar_estado_sifen(tx, f"SIFEN Estado: {estado}{sufijo}")
        if libre:
            _emitir_siguiente_en_cola()
    except Exception:
        logger.exception("Error al sincronizar facturas SIFEN")


def _get_user_acreditation_methods(request, active_client):
    """Obtiene estrictamente los medios de acreditación del cliente activo."""
    if not request.user.is_authenticated or is_user_analyst(request.user, request):
        return ClientAccreditationMethod.objects.none()

    client = active_client or _resolve_active_client(request.user, request)
    if client:
        return ClientAccreditationMethod.objects.filter(cliente=client)

    return ClientAccreditationMethod.objects.none()


def _resolve_active_client(user, request):
    """Obtiene el cliente activo del usuario según sesión o relaciones existentes (sin auto-creación)."""
    cliente = get_active_client(user, request)
    if not cliente and user and user.is_authenticated:
        profile = getattr(user, 'profile', None)
        kc_id = profile.keycloak_id if profile else None
        q_filter = models.Q(keycloak_user_id=str(user.id)) | models.Q(keycloak_user_id=user.username)
        if kc_id:
            q_filter |= models.Q(keycloak_user_id=kc_id)
        rel = UsuarioClienteRelacion.objects.filter(q_filter).select_related('cliente').first()
        if rel:
            cliente = rel.cliente
    return cliente


def _resumen_desde_simulador(request, active_client, from_currency, to_currency, amount):
    """
    Recalcula con la cotización vigente la simulación con la que el cliente llegó a la operación.

    Los valores se recalculan en el servidor porque la URL solo transporta monedas y monto.

    Args:
        request (HttpRequest): Solicitud HTTP actual.
        active_client (Cliente or None): Cliente con el que se ejecutará la operación.
        from_currency (str): Moneda de origen.
        to_currency (str): Moneda de destino.
        amount (str): Monto de origen.

    Returns:
        dict or None: Resultado de la simulación, o None si no se llegó desde el simulador
        o los datos recibidos no son válidos.
    """
    if request.method != 'GET' or request.GET.get('desde_simulador') != '1':
        return None
    try:
        return SimuladorConversionService.simular(
            from_currency=from_currency,
            to_currency=to_currency,
            amount=amount,
            user=request.user,
            request=request,
            client=active_client,
            category_override=active_client.categoria if active_client else None
        )
    except (ValidationError, ArithmeticError):
        return None


def _get_active_turn(user):
    """Retorna el turno de caja abierto para el usuario o su caja asignada."""
    if not user or not user.is_authenticated:
        return None
    turn = TurnoCaja.objects.filter(cajero=user, estado='ABIERTO').first()
    if not turn and hasattr(user, 'cajas_asignadas'):
        cajas = user.cajas_asignadas.all()
        turn = TurnoCaja.objects.filter(caja__in=cajas, estado='ABIERTO').first()
    return turn


def _process_financial_instrument(payment_method, total_amount, currency='PYG', origin_method=None, initial_status='SUCCESS'):
    """
    Valida campos requeridos y enruta el procesamiento al servicio de pasarela correspondiente según el tipo
    (SIPAP, Bancard, Tigo Money, Stripe), registrando el log inmutable en la base de datos (PaymentGatewayLog).
    """
    if not payment_method:
        res = SIPAPGateway().process_payment(amount=total_amount, currency=currency, source_account=getattr(origin_method, 'numero_cuenta', 'GX-MAIN'), destination_account='GX-DEFAULT')
        ref = res.get('reference_code', f"SIPAP-{uuid.uuid4().hex.upper()}")
        log_status = initial_status
        PaymentGatewayLog.objects.update_or_create(
            reference_code=ref,
            defaults={
                'gateway_name': 'SIPAP',
                'transaction_type': 'PAYMENT',
                'amount': total_amount,
                'currency': currency,
                'status': log_status,
                'request_payload': json.dumps({'amount': str(total_amount)}),
                'response_payload': json.dumps(res),
                'error_message': ''
            }
        )
        return 'CUENTA_BANCARIA_LOCAL', ref, log_status, log_status

    pm_type = getattr(payment_method, 'method_type', '') or getattr(payment_method, 'tipo_medio', '') or 'CUENTA_BANCARIA_LOCAL'
    gateway_name = 'SIPAP'
    if pm_type in ['TARJETA_CREDITO']:
        gateway_name = 'STRIPE'
    elif pm_type in ['TARJETA_DEBITO']:
        gateway_name = 'BANCARD'
    elif pm_type in ['BILLETERA_ELECTRONICA', 'BILLETERA']:
        gateway_name = 'TIGO_MONEY'
    else:
        code_str = getattr(payment_method, 'code', '') or getattr(payment_method, 'entidad_financiera', '') or 'SIPAP'
        if 'BANCARD' in code_str.upper():
            gateway_name = 'BANCARD'
        elif 'TIGO' in code_str.upper():
            gateway_name = 'TIGO_MONEY'
        elif 'STRIPE' in code_str.upper():
            gateway_name = 'STRIPE'
        else:
            gateway_name = 'SIPAP'

    gateway_ref = f"GX-{uuid.uuid4().hex.upper()}"
    gateway_status = initial_status
    tx_status = initial_status

    if pm_type == 'CUENTA_BANCARIA_EXTRANJERA':
        swift = getattr(payment_method, 'swift_iban', '') or getattr(payment_method, 'account_number', '') or getattr(payment_method, 'alias_transferencia', '')
        if not swift:
            raise ValidationError("La cuenta bancaria extranjera requiere código SWIFT / IBAN.")
        gateway_ref = f"SWIFT-{uuid.uuid4().hex.upper()}"
        gateway_status = 'PENDING'
        tx_status = 'PENDING'
        PaymentGatewayLog.objects.update_or_create(
            reference_code=gateway_ref,
            defaults={
                'gateway_name': 'SIPAP',
                'transaction_type': 'TRANSFER',
                'amount': total_amount,
                'currency': currency,
                'status': 'PENDING',
                'request_payload': json.dumps({'swift': swift}),
                'response_payload': json.dumps({'status': 'PENDING'}),
                'error_message': ''
            }
        )
    else:
        card_num = getattr(payment_method, 'account_number', '') or getattr(payment_method, 'numero_tarjeta', '') or '1234'
        phone = getattr(payment_method, 'account_number', '') or getattr(payment_method, 'numero_telefono', '') or '0981000000'
        acc_num = getattr(payment_method, 'account_number', '') or getattr(payment_method, 'swift_iban', '') or 'ACC-001'

        res = PaymentGatewaySimulator.process_gateway_payment(
            gateway_name=gateway_name,
            amount=total_amount,
            currency=currency,
            card_number=card_num,
            phone_number=phone,
            source_account=getattr(origin_method, 'numero_cuenta', 'GX-MAIN'),
            destination_account=acc_num
        )
        gateway_ref = res.get('reference_code', gateway_ref)
        if initial_status == 'PENDING':
            gateway_status = 'PENDING'
            tx_status = 'PENDING'
        else:
            gateway_status = res.get('status', 'SUCCESS')
            if gateway_status not in ['SUCCESS', 'APPROVED']:
                tx_status = 'FAILED'
            else:
                tx_status = 'SUCCESS'

        PaymentGatewayLog.objects.update_or_create(
            reference_code=gateway_ref,
            defaults={
                'gateway_name': gateway_name,
                'transaction_type': 'PAYMENT',
                'amount': total_amount,
                'currency': currency,
                'status': gateway_status,
                'request_payload': json.dumps({'amount': str(total_amount)}),
                'response_payload': json.dumps(res),
                'error_message': ''
            }
        )

    return pm_type, gateway_ref, gateway_status, tx_status


def _resolve_client_and_methods(user, request, origin_method_id, destination_method_id, from_currency=None, to_currency=None):
    """Resuelve el cliente activo y sus medios de acreditación de origen y destino según divisa."""
    cliente = _resolve_active_client(user, request)
    if not cliente:
        raise ValidationError("Debe seleccionar un cliente activo para realizar la operación.")

    origin_method = None
    destination_method = None
    if origin_method_id:
        origin_method = ClientAccreditationMethod.objects.filter(id=origin_method_id, cliente=cliente).first()
    if not origin_method and from_currency:
        origin_method = ClientAccreditationMethod.objects.filter(cliente=cliente, moneda__iexact=from_currency).first()
    if not origin_method:
        origin_method = ClientAccreditationMethod.objects.filter(cliente=cliente).first()

    if destination_method_id:
        destination_method = ClientAccreditationMethod.objects.filter(id=destination_method_id, cliente=cliente).first()
    if not destination_method and to_currency:
        destination_method = ClientAccreditationMethod.objects.filter(cliente=cliente, moneda__iexact=to_currency).first()
    if not destination_method:
        destination_method = ClientAccreditationMethod.objects.filter(cliente=cliente).exclude(id=getattr(origin_method, 'id', None)).first() or origin_method

    if not origin_method or not destination_method:
        origin_method = ClientAccreditationMethod.objects.create(
            cliente=cliente,
            user=user if user and user.is_authenticated else None,
            tipo_medio='CUENTA_BANCARIA',
            entidad_financiera='Banco Global',
            numero_cuenta='123456789',
            tipo_cuenta='AHORRO',
            moneda=from_currency if from_currency else 'PYG',
            titularidad=cliente.nombre_o_razon_social,
            estado='VERIFICADO',
            es_predeterminado=True
        )
        destination_method = origin_method

    return cliente, origin_method, destination_method


def _convert_amount(amount, cur_from, cur_to):
    """Convierte un monto entre dos divisas utilizando el servicio de simulación."""
    if cur_from == cur_to or amount is None:
        return amount
    try:
        res = SimuladorConversionService.simular(cur_from, cur_to, amount)
        return res['converted_amount']
    except Exception:
        return amount


class CurrencySaleService:
    """
    Servicio de dominio para el procesamiento de la operación de venta de divisas (PSE-14).
    Valida límites (50.000 PYG a 1.000.000.000 PYG), verifica obligatoriamente la existencia
    de una cuenta bancaria o billetera digital vinculada, aplica la tasa de compra vigente,
    calcula comisiones, impuestos y descuentos por perfil, y mide el tiempo de respuesta (< 5 segundos).
    """

    @staticmethod
    def process_sale(user, from_currency, to_currency, amount, payment_method_id_or_code, origin_method_id=None, destination_method_id=None, request=None):
        """
        Procesa la venta de divisas con validaciones estrictas, tasa de compra y cuenta vinculada obligatoria.

        Args:
            user (User): Usuario solicitante.
            from_currency (str): Moneda de origen (divisa a vender).
            to_currency (str): Moneda de destino (PYG).
            amount (Decimal or float): Monto de origen.
            payment_method_id_or_code (str or int): ID o código de la cuenta bancaria o billetera digital vinculada.
            request (HttpRequest, optional): Solicitud HTTP para contexto de sesión.

        Returns:
            CurrencySaleTransaction: Instancia de la transacción de venta guardada exitosamente.

        Raises:
            ValidationError: Si se violan límites, falta cuenta vinculada o datos inválidos.
        """
        start_time = time.time()

        if is_user_analyst(user, request):
            raise ValidationError("Error de validación: El rol Analista no tiene permisos para realizar operaciones de venta de divisas.")

        if not from_currency or not to_currency or amount is None:
            raise ValidationError("Los campos moneda origen, moneda destino y monto son obligatorios para la venta.")

        try:
            amount_dec = Decimal(str(amount))
        except (ValueError, TypeError):
            raise ValidationError("El monto ingresado debe ser un valor numérico válido.")

        if amount_dec <= Decimal('0.00'):
            raise ValidationError("El monto de la transacción debe ser mayor a cero.")

        # Criterio 3: Cuenta o Billetera Vinculada Obligatoria
        ensure_default_payment_methods()
        linked_account = None
        if payment_method_id_or_code:
            if isinstance(payment_method_id_or_code, int) or str(payment_method_id_or_code).isdigit():
                linked_account = PaymentMethod.objects.filter(id=int(payment_method_id_or_code)).first()
            if not linked_account:
                linked_account = PaymentMethod.objects.filter(code=str(payment_method_id_or_code)).first()

        if not linked_account or not linked_account.is_active:
            raise ValidationError("El cliente debe contar obligatoriamente con una cuenta bancaria o billetera digital vinculada y activa en el sistema para proceder con la venta de divisas.")

        cliente, origin_method, destination_method = _resolve_client_and_methods(user, request, origin_method_id, destination_method_id, from_currency=from_currency, to_currency=to_currency)

        # Simular / Calcular conversión aplicando tasa de compra vigente y categoría de cliente
        sim_result = SimuladorConversionService.simular(
            from_currency=from_currency,
            to_currency=to_currency,
            amount=amount_dec,
            user=user,
            request=request,
            client=cliente,
            category_override=cliente.categoria if cliente else None
        )

        eval_amount_pyg = amount_dec
        if from_currency != 'PYG':
            try:
                r_from = ExchangeRate.objects.get(currency_code=from_currency)
                benefit_pct = sim_result.get('benefit_percentage', Decimal('0.00'))
                custom_buy = r_from.buy_rate * (Decimal('1.00') + (benefit_pct / Decimal('100.00')))
                eval_amount_pyg = amount_dec * custom_buy
            except Exception:
                eval_amount_pyg = amount_dec * Decimal('7300.00')
        else:
            eval_amount_pyg = amount_dec

        # Criterio 1: Validación de Límites (Mínimo 50.000 PYG, Máximo 1.000.000.000 PYG)
        MIN_LIMIT = LIMITE_MINIMO_OPERACION_PYG
        MAX_LIMIT = LIMITE_MAXIMO_OPERACION_PYG

        if eval_amount_pyg < MIN_LIMIT:
            raise ValidationError(f"El monto de la transacción (₲ {eval_amount_pyg:,.2f}) es inferior al límite mínimo permitido de ₲ 50.000 PYG.")
        if eval_amount_pyg > MAX_LIMIT:
            raise ValidationError(f"El monto de la transacción (₲ {eval_amount_pyg:,.2f}) excede el límite máximo permitido de ₲ 1.000.000.000 PYG.")

        # Calcular comisión (0.5%) e impuestos (IVA 10% sobre comisión) y neto a acreditar en cuenta vinculada
        gross_pyg = sim_result['converted_amount']
        tipo_cambio_local_origen = Decimal('1.0000')
        if from_currency != 'PYG':
            tipo_cambio_local_origen = sim_result.get('applied_rate', Decimal('1.0000'))

        tipo_cambio_local_destino = Decimal('1.0000')
        if to_currency != 'PYG':
            tipo_cambio_local_destino = sim_result.get('applied_rate', Decimal('1.0000'))

        tipo_cambio_cruzado = sim_result.get('applied_rate') if (from_currency != 'PYG' and to_currency != 'PYG') else None

        if to_currency == 'PYG':
            commission_amount = (gross_pyg * Decimal('0.005')).quantize(Decimal('0.01'))
            tax_amount = (commission_amount * Decimal('0.10')).quantize(Decimal('0.01'))
            total_pyg = gross_pyg
            total_origen = amount_dec
        else:
            commission_amount = (gross_pyg * Decimal('0.005')).quantize(Decimal('0.01'))
            tax_amount = (commission_amount * Decimal('0.10')).quantize(Decimal('0.01'))
            total_pyg = (gross_pyg * tipo_cambio_local_destino).quantize(Decimal('0.01'))
            total_origen = amount_dec

        cliente, origin_method, destination_method = _resolve_client_and_methods(user, request, origin_method_id, destination_method_id, from_currency=from_currency, to_currency=to_currency)
        pm_type, gateway_ref, gateway_status, tx_status = _process_financial_instrument(linked_account, total_pyg, 'PYG', origin_method)

        # Descontar de la cuenta origen
        if origin_method:
            required_debit = _convert_amount(amount_dec, from_currency, origin_method.moneda)
            if origin_method.balance < required_debit:
                raise ValidationError(f"Fondos insuficientes en la cuenta origen '{origin_method.entidad_financiera}'. Saldo disponible: {origin_method.balance:,.2f} {origin_method.moneda}, requerido: {required_debit:,.2f} {origin_method.moneda}.")
            origin_method.balance -= required_debit
            origin_method.save()

        # Acreditar en la cuenta destino
        if destination_method:
            required_credit = _convert_amount(total_pyg, 'PYG', destination_method.moneda)
            destination_method.balance += required_credit
            destination_method.save()

        # Acreditar fondos en la cuenta bancaria / billetera digital vinculada del sistema
        linked_account.balance += total_pyg
        linked_account.save()

        # Ejecutar pasarela de pago (PSE-18 Server-to-Server)
        try:
            PaymentGatewaySimulator.process_gateway_payment(
                gateway_name=linked_account.code or 'SIPAP',
                amount=total_pyg,
                currency='PYG',
                source_account=getattr(origin_method, 'numero_cuenta', 'GX-MAIN'),
                destination_account=getattr(destination_method, 'numero_cuenta', 'GX-DEST')
            )
        except Exception:
            pass

        # Medir tiempo de respuesta (Criterio: < 5 segundos)
        elapsed_time = time.time() - start_time
        processing_time_ms = int(elapsed_time * 1000)

        c_vol = cliente.transaction_volume if isinstance(cliente.transaction_volume, Decimal) else Decimal(str(cliente.transaction_volume or 0))
        cliente.transaction_volume = c_vol + eval_amount_pyg
        cliente.save()

        # Registrar transacción de venta
        transaction = CurrencySaleTransaction.objects.create(
            user=user if user and user.is_authenticated else None,
            cliente=cliente,
            turno=_get_active_turn(user),
            from_currency=from_currency,
            to_currency=to_currency,
            amount=amount_dec,
            converted_amount=gross_pyg,
            applied_rate=sim_result['applied_rate'],
            standard_rate=sim_result['standard_rate'],
            linked_account=linked_account,
            origin_acreditation_method=origin_method,
            destination_acreditation_method=destination_method,
            acreditation_method=destination_method,
            benefit_percentage=sim_result['benefit_percentage'],
            commission_amount=commission_amount,
            tax_amount=tax_amount,
            total_pyg=total_pyg,
            status=tx_status,
            payment_method_type=pm_type,
            gateway_reference=gateway_ref,
            gateway_status=gateway_status,
            processing_time_ms=processing_time_ms,
            transparent_breakdown=f"{sim_result['transparent_breakdown']} | Neto Acreditado: ₲ {total_pyg:,.2f} | Tiempo: {processing_time_ms}ms",
            moneda_origen_id=from_currency,
            monto_origen=amount_dec,
            moneda_destino_id=to_currency,
            monto_destino=gross_pyg,
            tipo_cambio_cruzado=tipo_cambio_cruzado,
            tipo_cambio_local_origen=tipo_cambio_local_origen,
            monto_moneda_local=eval_amount_pyg,
            total_origen=total_origen,
        )

        if transaction.status == 'SUCCESS':
            _generar_factura_sifen_async(transaction)

        return transaction

    @staticmethod
    def initiate_sale(user, from_currency, to_currency, amount, payment_method_id_or_code, origin_method_id=None, destination_method_id=None, request=None):
        """
        Inicia una operación de venta de divisas dejando la transacción en estado PENDIENTE,
        capturando la tasa de cambio vigente al momento de la solicitud (PSE-31).
        """
        if is_user_analyst(user, request):
            raise ValidationError("Error de validación: El rol Analista no tiene permisos para realizar operaciones de venta de divisas.")

        if not from_currency or not to_currency or amount is None:
            raise ValidationError("Los campos moneda origen, moneda destino y monto son obligatorios para la venta.")

        try:
            amount_dec = Decimal(str(amount))
        except (ValueError, TypeError):
            raise ValidationError("El monto ingresado debe ser un valor numérico válido.")

        if amount_dec <= Decimal('0.00'):
            raise ValidationError("El monto de la transacción debe ser mayor a cero.")

        ensure_default_payment_methods()
        linked_account = None
        if payment_method_id_or_code:
            if isinstance(payment_method_id_or_code, int) or str(payment_method_id_or_code).isdigit():
                linked_account = PaymentMethod.objects.filter(id=int(payment_method_id_or_code)).first()
            if not linked_account:
                linked_account = PaymentMethod.objects.filter(code=str(payment_method_id_or_code)).first()

        if not linked_account or not linked_account.is_active:
            raise ValidationError("El cliente debe contar obligatoriamente con una cuenta bancaria o billetera digital vinculada y activa en el sistema para proceder con la venta de divisas.")

        cliente, origin_method, destination_method = _resolve_client_and_methods(user, request, origin_method_id, destination_method_id, from_currency=from_currency, to_currency=to_currency)

        sim_result = SimuladorConversionService.simular(
            from_currency=from_currency,
            to_currency=to_currency,
            amount=amount_dec,
            user=user,
            request=request,
            client=cliente,
            category_override=cliente.categoria if cliente else None
        )

        eval_amount_pyg = amount_dec
        if from_currency != 'PYG':
            try:
                r_from = ExchangeRate.objects.get(currency_code=from_currency)
                benefit_pct = sim_result.get('benefit_percentage', Decimal('0.00'))
                custom_buy = r_from.buy_rate * (Decimal('1.00') + (benefit_pct / Decimal('100.00')))
                eval_amount_pyg = amount_dec * custom_buy
            except Exception:
                eval_amount_pyg = amount_dec * Decimal('7300.00')
        else:
            eval_amount_pyg = amount_dec

        MIN_LIMIT = LIMITE_MINIMO_OPERACION_PYG
        MAX_LIMIT = LIMITE_MAXIMO_OPERACION_PYG

        if eval_amount_pyg < MIN_LIMIT:
            raise ValidationError(f"El monto de la transacción (₲ {eval_amount_pyg:,.2f}) es inferior al límite mínimo permitido de ₲ 50.000 PYG.")
        if eval_amount_pyg > MAX_LIMIT:
            raise ValidationError(f"El monto de la transacción (₲ {eval_amount_pyg:,.2f}) excede el límite máximo permitido de ₲ 1.000.000.000 PYG.")

        gross_pyg = sim_result['converted_amount']
        tipo_cambio_local_origen = Decimal('1.0000')
        if from_currency != 'PYG':
            tipo_cambio_local_origen = sim_result.get('applied_rate', Decimal('1.0000'))

        tipo_cambio_local_destino = Decimal('1.0000')
        if to_currency != 'PYG':
            tipo_cambio_local_destino = sim_result.get('applied_rate', Decimal('1.0000'))

        tipo_cambio_cruzado = sim_result.get('applied_rate') if (from_currency != 'PYG' and to_currency != 'PYG') else None

        if to_currency == 'PYG':
            commission_amount = (gross_pyg * Decimal('0.005')).quantize(Decimal('0.01'))
            tax_amount = (commission_amount * Decimal('0.10')).quantize(Decimal('0.01'))
            total_pyg = gross_pyg
            total_origen = amount_dec
        else:
            commission_amount = (gross_pyg * Decimal('0.005')).quantize(Decimal('0.01'))
            tax_amount = (commission_amount * Decimal('0.10')).quantize(Decimal('0.01'))
            total_pyg = (gross_pyg * tipo_cambio_local_destino).quantize(Decimal('0.01'))
            total_origen = amount_dec

        cliente, origin_method, destination_method = _resolve_client_and_methods(user, request, origin_method_id, destination_method_id, from_currency=from_currency, to_currency=to_currency)
        pm_type, gateway_ref, gateway_status, tx_status = _process_financial_instrument(linked_account, total_pyg, 'PYG', origin_method, initial_status='PENDING')

        transaction = CurrencySaleTransaction.objects.create(
            user=user if user and user.is_authenticated else None,
            cliente=cliente,
            turno=_get_active_turn(user),
            from_currency=from_currency,
            to_currency=to_currency,
            amount=amount_dec,
            converted_amount=gross_pyg,
            applied_rate=sim_result['applied_rate'],
            standard_rate=sim_result['standard_rate'],
            linked_account=linked_account,
            payment_method_type=pm_type,
            gateway_reference=gateway_ref,
            gateway_status=gateway_status,
            origin_acreditation_method=origin_method,
            destination_acreditation_method=destination_method,
            acreditation_method=destination_method,
            benefit_percentage=sim_result['benefit_percentage'],
            commission_amount=commission_amount,
            tax_amount=tax_amount,
            total_pyg=total_pyg,
            status='PENDING',
            processing_time_ms=0,
            transparent_breakdown=f"Iniciado en PENDIENTE. Pasarela: {pm_type} ({gateway_ref}) - Estado: {gateway_status}. Tasa aplicada: {sim_result['applied_rate']}",
            moneda_origen_id=from_currency,
            monto_origen=amount_dec,
            moneda_destino_id=to_currency,
            monto_destino=gross_pyg,
            tipo_cambio_cruzado=tipo_cambio_cruzado,
            tipo_cambio_local_origen=tipo_cambio_local_origen,
            monto_moneda_local=eval_amount_pyg,
            total_origen=total_origen,
        )
        return transaction

    @staticmethod
    def confirm_sale(transaction_id, user=None, request=None):
        """
        Confirma la venta pendiente, verificando en tiempo real si la tasa de cambio ha variado (PSE-31).
        Si cambió, cancela automáticamente sin acreditar fondos. Si no cambió, acredita netos y marca SUCCESS.
        """
        try:
            transaction = CurrencySaleTransaction.objects.get(id=transaction_id)
        except CurrencySaleTransaction.DoesNotExist:
            raise ValidationError("La transacción de venta especificada no existe.")

        if transaction.status != 'PENDING':
            raise ValidationError(f"La transacción ya se encuentra en estado '{transaction.status}' y no puede ser confirmada.")

        sim_check = SimuladorConversionService.simular(
            from_currency=transaction.from_currency,
            to_currency=transaction.to_currency,
            amount=transaction.amount,
            user=user,
            request=request
        )
        current_applied_rate = sim_check['applied_rate']

        if current_applied_rate != transaction.applied_rate:
            transaction.status = 'CANCELLED'
            transaction.transparent_breakdown += f" | CANCELADO AUTOMÁTICAMENTE por variación de tasa (Inicial: {transaction.applied_rate}, Actual: {current_applied_rate}). Sin acreditación de fondos."
            transaction.save()
            raise ValidationError("Alerta de Variación de Tasa: La cotización de la divisa ha variado en el sistema mientras la operación estaba pendiente. La transacción se ha cancelado de forma segura sin acreditar fondos.")

        linked_account = transaction.linked_account
        if not linked_account or not linked_account.is_active:
            transaction.status = 'FAILED'
            transaction.transparent_breakdown += " | Falló por cuenta vinculada inactiva al confirmar."
            transaction.save()
            raise ValidationError("La cuenta o billetera vinculada se encuentra inactiva al confirmar la operación.")

        start_time = time.time()
        origin_method = transaction.origin_acreditation_method
        destination_method = transaction.destination_acreditation_method

        if origin_method:
            required_debit = _convert_amount(transaction.amount, transaction.from_currency, origin_method.moneda)
            if origin_method.balance < required_debit:
                transaction.status = 'FAILED'
                transaction.transparent_breakdown += " | Falló por fondos insuficientes en cuenta origen al confirmar."
                transaction.save()
                raise ValidationError(f"Fondos insuficientes en la cuenta origen '{origin_method.entidad_financiera}'. Saldo disponible: {origin_method.balance:,.2f} {origin_method.moneda}, requerido: {required_debit:,.2f} {origin_method.moneda}.")
            origin_method.balance -= required_debit
            origin_method.save()

        if destination_method:
            required_credit = _convert_amount(transaction.total_pyg, 'PYG', destination_method.moneda)
            destination_method.balance += required_credit
            destination_method.save()

        linked_account.balance += transaction.total_pyg
        linked_account.save()

        cliente = transaction.cliente
        if cliente:
            c_vol = cliente.transaction_volume if isinstance(cliente.transaction_volume, Decimal) else Decimal(str(cliente.transaction_volume or 0))
            eval_amount_pyg = transaction.amount * transaction.applied_rate if transaction.from_currency != 'PYG' else transaction.amount
            cliente.transaction_volume = c_vol + eval_amount_pyg
            cliente.save()

        elapsed_time = time.time() - start_time
        processing_time_ms = int(elapsed_time * 1000)

        if transaction.gateway_reference:
            PaymentGatewayLog.objects.filter(reference_code=transaction.gateway_reference).update(status='SUCCESS')
        transaction.gateway_status = 'SUCCESS'
        transaction.status = 'SUCCESS'
        transaction.processing_time_ms = processing_time_ms
        transaction.transparent_breakdown += f" | Venta confirmada exitosamente. Tiempo: {processing_time_ms}ms"
        transaction.save()
        _generar_factura_sifen_async(transaction)
        transaction.refresh_from_db()

        return transaction

    @staticmethod
    def cancel_sale_manual(transaction_id, user=None, request=None):
        """
        Cancela manualmente una venta pendiente antes de confirmar, sin acreditación de fondos (PSE-31).
        """
        try:
            transaction = CurrencySaleTransaction.objects.get(id=transaction_id)
        except CurrencySaleTransaction.DoesNotExist:
            raise ValidationError("La transacción de venta especificada no existe.")

        if transaction.status != 'PENDING':
            raise ValidationError(f"La transacción se encuentra en estado '{transaction.status}' y no puede ser cancelada manualmente.")

        if transaction.gateway_reference:
            PaymentGatewayLog.objects.filter(reference_code=transaction.gateway_reference).update(status='CANCELLED')
        transaction.gateway_status = 'CANCELLED'
        transaction.status = 'CANCELLED'
        transaction.transparent_breakdown += " | Cancelado manualmente por el usuario antes de confirmar. Sin acreditación de fondos."
        transaction.save()
        return transaction


@login_required
def currency_sale_view(request):
    """
    Vista web para la Operación de Venta de Divisas (PROCESAMIENTO DE OPERACIONES / PSE-14 y PSE-31).
    Permite seleccionar divisa origen, moneda destino (PYG), cuenta/billetera vinculada y monto,
    iniciando un flujo pendiente con validación de variación de tasa y opciones de confirmación o cancelación manual.
    
    Args:
        request (HttpRequest): Solicitud HTTP del cliente.
        
    Returns:
        HttpResponse: Página renderizada del flujo de venta de divisas.
    """
    ensure_default_payment_methods()
    ensure_default_benefit_rules()

    default_rates = [
        ('USD', 'Dólar Estadounidense', '$', Decimal('7300.0000'), Decimal('7450.0000')),
        ('EUR', 'Euro', '€', Decimal('7900.0000'), Decimal('8150.0000')),
        ('BRL', 'Real Brasileño', 'R$', Decimal('1350.0000'), Decimal('1450.0000')),
        ('ARS', 'Peso Argentino', '$', Decimal('7.5000'), Decimal('9.0000')),
        ('PYG', 'Guaraní Paraguayo', '₲', Decimal('1.0000'), Decimal('1.0000')),
    ]
    for code, name, symbol, buy, sell in default_rates:
        ExchangeRate.objects.get_or_create(
            currency_code=code,
            defaults={'currency_name': name, 'symbol': symbol, 'buy_rate': buy, 'sell_rate': sell}
        )

    currencies = ExchangeRate.objects.all().order_by('currency_code')
    payment_methods = PaymentMethod.objects.filter(is_active=True).order_by('id')

    active_client = _resolve_active_client(request.user, request)

    acreditation_methods = _get_user_acreditation_methods(request, active_client)
    if not is_user_analyst(request.user, request) and acreditation_methods.count() == 0:
        return redirect('client_acreditation_methods')

    pending_transaction = None
    success_transaction = None
    cancelled_transaction = None
    error_message = None

    from_currency = request.GET.get('from_currency', 'USD')
    to_currency = request.GET.get('to_currency', 'PYG')
    amount_str = request.GET.get('amount', '100')
    payment_method_code = request.GET.get('payment_method', '')

    if request.method == 'POST':
        action = request.POST.get('action', 'process')
        tx_id = request.POST.get('transaction_id')

        if action == 'confirm_sale' and tx_id:
            try:
                success_transaction = CurrencySaleService.confirm_sale(
                    transaction_id=int(tx_id),
                    user=request.user,
                    request=request
                )
            except ValidationError as e:
                error_message = e.messages[0] if hasattr(e, 'messages') else str(e)
                try:
                    cancelled_transaction = CurrencySaleTransaction.objects.get(id=int(tx_id), status='CANCELLED')
                except Exception:
                    pass
            except Exception as e:
                error_message = f"Error al confirmar la venta: {str(e)}"
        elif action == 'cancel_sale' and tx_id:
            try:
                cancelled_transaction = CurrencySaleService.cancel_sale_manual(
                    transaction_id=int(tx_id),
                    user=request.user,
                    request=request
                )
            except ValidationError as e:
                error_message = e.messages[0] if hasattr(e, 'messages') else str(e)
            except Exception as e:
                error_message = f"Error al cancelar la operación de venta: {str(e)}"
        else:
            from_currency = request.POST.get('from_currency')
            to_currency = request.POST.get('to_currency')
            amount_str = request.POST.get('amount')
            origin_account = request.POST.get('origin_account')
            destination_account = request.POST.get('destination_account')
            payment_method_code = request.POST.get('payment_method')

            try:
                pending_transaction = CurrencySaleService.initiate_sale(
                    user=request.user,
                    from_currency=from_currency,
                    to_currency=to_currency,
                    amount=amount_str,
                    payment_method_id_or_code=payment_method_code,
                    origin_method_id=origin_account,
                    destination_method_id=destination_account,
                    request=request
                )
            except ValidationError as e:
                error_message = e.messages[0] if hasattr(e, 'messages') else str(e)
            except Exception as e:
                error_message = f"Error al iniciar la venta: {str(e)}"

    clientes_asociados = []
    if request.user.is_authenticated and hasattr(request.user, 'profile') and request.user.profile.keycloak_id:
        rels = UsuarioClienteRelacion.objects.filter(keycloak_user_id=request.user.profile.keycloak_id).select_related('cliente')
        clientes_asociados = [r.cliente for r in rels]
    if not clientes_asociados and active_client:
        clientes_asociados = [active_client]

    user_profile = None
    benefit_percentage = Decimal('0.00')
    category_display = active_client.categoria if active_client else 'Invitado / Minorista'
    cat_code = get_user_effective_category(request.user, request=request)
    if request.user.is_authenticated:
        try:
            user_profile = request.user.profile
            rule = ClientBenefitRule.objects.filter(category_code=cat_code).first()
            if rule:
                benefit_percentage = rule.benefit_percentage
                if not active_client:
                    category_display = rule.category_name
        except Exception:
            pass

    context = {
        'currencies': currencies,
        'payment_methods': payment_methods,
        'from_currency': from_currency,
        'to_currency': to_currency,
        'amount': amount_str,
        'selected_payment_method': payment_method_code,
        'pending_transaction': pending_transaction,
        'success_transaction': success_transaction,
        'cancelled_transaction': cancelled_transaction,
        'error_message': error_message,
        'user_profile': user_profile,
        'active_client': active_client,
        'acreditation_methods': acreditation_methods,
        'clientes_asociados': clientes_asociados,
        'benefit_percentage': benefit_percentage,
        'category_display': category_display,
        'simulacion_origen': _resumen_desde_simulador(request, active_client, from_currency, to_currency, amount_str),
        'now': timezone.now(),
    }

    return render(request, 'procesamiento_operaciones/currency_sale.html', context)


@login_required
def currency_sale_history_view(request):
    """
    Vista web para consultar el historial de operaciones de venta de divisas
    del cliente activo actual (PROCESAMIENTO DE OPERACIONES / PSE-14).
    Muestra únicamente las ventas realizadas para ese cliente.
    
    Args:
        request (HttpRequest): Solicitud HTTP del cliente.
        
    Returns:
        HttpResponse: Página renderizada con el historial de ventas de divisas del cliente activo.
    """
    active_client = get_active_client(request.user, request=request)
    
    if active_client:
        transactions = CurrencySaleTransaction.objects.filter(cliente=active_client).order_by('-timestamp')
        _sincronizar_facturas_sifen(transactions)
    else:
        transactions = CurrencySaleTransaction.objects.none()

    context = {
        'transactions': transactions,
        'active_client': active_client,
        'now': timezone.now(),
    }
    return render(request, 'procesamiento_operaciones/currency_sale_history.html', context)


@login_required
def currency_transactions_history_view(request):
    """
    Vista web para consultar el historial unificado y detallado de operaciones (compras y ventas de divisas)
    del cliente activo actual (PROCESAMIENTO DE OPERACIONES / PSE-13, PSE-14, PSE-15).
    Permite filtrar por tipo de operación, estado operativo, rango de fechas, y exportar a Excel (CSV) o PDF.
    
    Args:
        request (HttpRequest): Solicitud HTTP del cliente.
        
    Returns:
        HttpResponse: Página renderizada con el historial filtrado, o archivo CSV/PDF descargable.
    """
    active_client = get_active_client(request.user, request=request)
    acreditation_methods = ClientAccreditationMethod.objects.filter(cliente=active_client) if active_client else ClientAccreditationMethod.objects.none()
    
    tx_type_filter = request.GET.get('tx_type', '')
    status_filter = request.GET.get('status', '')
    date_from_str = request.GET.get('date_from', '')
    date_to_str = request.GET.get('date_to', '')
    export_format = request.GET.get('export', '')

    purchases_qs = CurrencyPurchaseTransaction.objects.all()
    sales_qs = CurrencySaleTransaction.objects.all()

    if active_client:
        purchases_qs = purchases_qs.filter(cliente=active_client)
        sales_qs = sales_qs.filter(cliente=active_client)
        _sincronizar_facturas_sifen(purchases_qs, sales_qs)
    else:
        purchases_qs = purchases_qs.none()
        sales_qs = sales_qs.none()

    if date_from_str:
        try:
            d_from = timezone.datetime.strptime(date_from_str, '%Y-%m-%d').date()
            purchases_qs = purchases_qs.filter(timestamp__date__gte=d_from)
            sales_qs = sales_qs.filter(timestamp__date__gte=d_from)
        except ValueError:
            pass

    if date_to_str:
        try:
            d_to = timezone.datetime.strptime(date_to_str, '%Y-%m-%d').date()
            purchases_qs = purchases_qs.filter(timestamp__date__lte=d_to)
            sales_qs = sales_qs.filter(timestamp__date__lte=d_to)
        except ValueError:
            pass

    if status_filter:
        purchases_qs = purchases_qs.filter(status__iexact=status_filter)
        sales_qs = sales_qs.filter(status__iexact=status_filter)

    purchases = []
    if tx_type_filter in ['', 'COMPRA']:
        purchases = list(purchases_qs)
        for p in purchases:
            p.tx_type = 'COMPRA'
            p.display_amount = p.amount
            p.display_converted = p.converted_amount
            p.display_from = p.from_currency
            p.display_to = p.to_currency
            p.display_total = p.converted_amount
            s_up = str(p.status).upper()
            if s_up in ['SUCCESS', 'PAGADA']:
                p.status_display = 'Pagada'
            elif s_up in ['PENDING', 'PENDIENTE']:
                p.status_display = 'Pendiente'
            elif s_up in ['CANCELLED', 'CANCELADA']:
                p.status_display = 'Cancelada'
            elif s_up in ['ANNULLED', 'ANULADA']:
                p.status_display = 'Anulada'
            else:
                p.status_display = p.status

    sales = []
    if tx_type_filter in ['', 'VENTA']:
        sales = list(sales_qs)
        for s in sales:
            s.tx_type = 'VENTA'
            s.display_amount = s.amount
            s.display_converted = s.converted_amount
            s.display_from = s.from_currency
            s.display_to = s.to_currency
            s.display_total = s.converted_amount
            s_up = str(s.status).upper()
            if s_up in ['SUCCESS', 'PAGADA']:
                s.status_display = 'Pagada'
            elif s_up in ['PENDING', 'PENDIENTE']:
                s.status_display = 'Pendiente'
            elif s_up in ['CANCELLED', 'CANCELADA']:
                s.status_display = 'Cancelada'
            elif s_up in ['ANNULLED', 'ANULADA']:
                s.status_display = 'Anulada'
            else:
                s.status_display = s.status

    all_transactions = sorted(purchases + sales, key=lambda x: x.timestamp, reverse=True)

    if export_format == 'excel':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="historial_transacciones.csv"'
        writer = csv.writer(response)
        writer.writerow(['ID Operacion', 'Tipo', 'Fecha y Hora', 'Cliente', 'Moneda Origen', 'Moneda Destino', 'Monto Origen', 'Monto Convertido', 'Total Recibido', 'Estado'])
        for tx in all_transactions:
            cliente_nombre = tx.cliente.nombre_o_razon_social if tx.cliente else 'N/A'
            writer.writerow([
                tx.id,
                tx.tx_type,
                tx.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                cliente_nombre,
                tx.display_from,
                tx.display_to,
                tx.display_amount,
                tx.display_converted,
                f"{tx.display_total} {tx.display_to}",
                getattr(tx, 'status_display', tx.status)
            ])
        return response

    if export_format == 'pdf':
        context = {
            'transactions': all_transactions,
        'active_client': active_client,
        'acreditation_methods': acreditation_methods,
            'now': timezone.now(),
        }
        return render(request, 'procesamiento_operaciones/currency_transactions_history_pdf.html', context)

    context = {
        'transactions': all_transactions,
        'active_client': active_client,
        'tx_type': tx_type_filter,
        'status': status_filter,
        'date_from': date_from_str,
        'date_to': date_to_str,
        'now': timezone.now(),
    }
    return render(request, 'procesamiento_operaciones/currency_transactions_history.html', context)




def is_user_analyst(user, request=None):
    """
    Determina si el usuario actual o el rol activo en el cliente es Analista (prohibido realizar operaciones de compra).
    """
    if not user or not user.is_authenticated:
        return False
    try:
        profile = getattr(user, 'profile', None) or UserProfile.objects.filter(user=user).first()
        if profile and profile.role and 'analista' in profile.role.name.lower():
            return True
        
        active_client = get_active_client(user, request)
        if active_client and profile and profile.keycloak_id:
            from django.db import models
            rel = UsuarioClienteRelacion.objects.filter(
                models.Q(keycloak_user_id=profile.keycloak_id) | models.Q(keycloak_user_id=str(user.id)),
                cliente=active_client
            ).first()
            if rel and rel.rol_en_cliente.upper() == 'ANALISTA':
                return True
    except Exception:
        pass
    return False


class CurrencyPurchaseService:
    """
    Servicio de dominio para el procesamiento de la operación de compra de divisas (PSE-13).
    Valida límites (50.000 PYG a 1.000.000.000 PYG), verifica fondos suficientes en el método de pago,
    calcula tasas, comisiones, impuestos y descuentos por perfil (VIP 2%, Corporativo 4%),
    y mide el tiempo de procesamiento (< 5 segundos).
    """

    @staticmethod
    def process_purchase(user, from_currency, to_currency, amount, payment_method_id_or_code, origin_method_id=None, destination_method_id=None, request=None):
        """
        Procesa la compra de divisas con validaciones estrictas y cálculo transparente.

        Args:
            user (User): Usuario solicitante.
            from_currency (str): Moneda de origen.
            to_currency (str): Moneda de destino.
            amount (Decimal or float): Monto de origen.
            payment_method_id_or_code (str or int): ID o código del método de pago.
            request (HttpRequest, optional): Solicitud HTTP para contexto de sesión.

        Returns:
            CurrencyPurchaseTransaction: Instancia de la transacción guardada exitosamente.

        Raises:
            ValidationError: Si se violan límites, fondos insuficientes o datos inválidos.
        """
        start_time = time.time()

        if is_user_analyst(user, request):
            raise ValidationError("Error de validación: El rol Analista no tiene permisos para realizar operaciones de compra de divisas.")

        if not from_currency or not to_currency or amount is None:
            raise ValidationError("Los campos moneda origen, moneda destino y monto son obligatorios para la compra.")

        try:
            amount_dec = Decimal(str(amount))
        except (ValueError, TypeError):
            raise ValidationError("El monto ingresado debe ser un valor numérico válido.")

        if amount_dec <= Decimal('0.00'):
            raise ValidationError("El monto de la transacción debe ser mayor a cero.")

        cliente, origin_method, destination_method = _resolve_client_and_methods(user, request, origin_method_id, destination_method_id, from_currency=from_currency, to_currency=to_currency)

        # Simular / Calcular conversión para obtener equivalencia en PYG y valores aplicando categoría de cliente
        sim_result = SimuladorConversionService.simular(
            from_currency=from_currency,
            to_currency=to_currency,
            amount=amount_dec,
            user=user,
            request=request,
            client=cliente,
            category_override=cliente.categoria if cliente else None
        )

        eval_amount_pyg = amount_dec
        if from_currency != 'PYG':
            try:
                r_from = ExchangeRate.objects.get(currency_code=from_currency)
                benefit_pct = sim_result.get('benefit_percentage', Decimal('0.00'))
                custom_buy = r_from.buy_rate * (Decimal('1.00') + (benefit_pct / Decimal('100.00')))
                eval_amount_pyg = amount_dec * custom_buy
            except Exception:
                eval_amount_pyg = amount_dec * Decimal('7300.00')
        else:
            eval_amount_pyg = amount_dec
        if from_currency != 'PYG':
            if to_currency == 'PYG':
                eval_amount_pyg = sim_result.get('converted_amount', amount_dec)
            else:
                try:
                    r_from = ExchangeRate.objects.get(currency_code=from_currency)
                    benefit_pct = sim_result.get('benefit_percentage', Decimal('0.00'))
                    custom_buy = r_from.buy_rate * (Decimal('1.00') + (benefit_pct / Decimal('100.00')))
                    eval_amount_pyg = amount_dec * custom_buy
                except Exception:
                    eval_amount_pyg = amount_dec * r_from.buy_rate
        else:
            eval_amount_pyg = amount_dec

        # Criterio 1: Validación de Límites (Mínimo 50.000 PYG, Máximo 1.000.000.000 PYG)
        MIN_LIMIT = LIMITE_MINIMO_OPERACION_PYG
        MAX_LIMIT = LIMITE_MAXIMO_OPERACION_PYG

        if eval_amount_pyg < MIN_LIMIT:
            raise ValidationError(f"El monto de la transacción (₲ {eval_amount_pyg:,.2f}) es inferior al límite mínimo permitido de ₲ 50.000 PYG.")
        if eval_amount_pyg > MAX_LIMIT:
            raise ValidationError(f"El monto de la transacción (₲ {eval_amount_pyg:,.2f}) excede el límite máximo permitido de ₲ 1.000.000.000 PYG.")

        # Criterio 2: Validación de Fondos (Comprobar de manera estricta saldo suficiente en el método de pago)
        ensure_default_payment_methods()
        payment_method = None
        if isinstance(payment_method_id_or_code, int) or str(payment_method_id_or_code).isdigit():
            payment_method = PaymentMethod.objects.filter(id=int(payment_method_id_or_code)).first()
        if not payment_method:
            payment_method = PaymentMethod.objects.filter(code=str(payment_method_id_or_code)).first()
        if not payment_method:
            payment_method = PaymentMethod.objects.filter(is_active=True).first()

        if not payment_method or not payment_method.is_active:
            raise ValidationError("El método de pago seleccionado no es válido o se encuentra inactivo.")

        tipo_cambio_cruzado = sim_result.get('applied_rate') if (from_currency != 'PYG' and to_currency != 'PYG') else None
        tipo_cambio_local_origen = Decimal('1.0000')
        if from_currency != 'PYG':
            tipo_cambio_local_origen = sim_result.get('applied_rate', Decimal('1.0000'))

        if from_currency == 'PYG':
            commission_amount = (eval_amount_pyg * Decimal('0.005')).quantize(Decimal('0.01'))
            tax_amount = (commission_amount * Decimal('0.10')).quantize(Decimal('0.01'))
            total_cost_pyg = eval_amount_pyg
            total_origen = amount_dec
        else:
            commission_amount = (amount_dec * Decimal('0.005')).quantize(Decimal('0.01'))
            tax_amount = (commission_amount * Decimal('0.10')).quantize(Decimal('0.01'))
            total_origen = amount_dec
            total_pyg = (total_origen * tipo_cambio_local_origen).quantize(Decimal('0.01'))
            total_cost_pyg = total_pyg

        if payment_method.balance < total_cost_pyg:
            raise ValidationError(f"Fondos insuficientes en el método de pago '{payment_method.name}'. Saldo disponible: ₲ {payment_method.balance:,.2f}, requerido: ₲ {total_cost_pyg:,.2f}.")

        cliente, origin_method, destination_method = _resolve_client_and_methods(user, request, origin_method_id, destination_method_id, from_currency=from_currency, to_currency=to_currency)
        pm_type, gateway_ref, gateway_status, tx_status = _process_financial_instrument(payment_method, total_cost_pyg, 'PYG', origin_method)

        # Descontar de la cuenta origen del cliente
        if origin_method:
            required_debit = _convert_amount(total_origen, from_currency, origin_method.moneda)
            if origin_method.balance < required_debit:
                raise ValidationError(f"Fondos insuficientes en la cuenta origen '{origin_method.entidad_financiera}'. Saldo disponible: {origin_method.balance:,.2f} {origin_method.moneda}, requerido: {required_debit:,.2f} {origin_method.moneda}.")
            origin_method.balance -= required_debit
            origin_method.save()

        # Acreditar en la cuenta destino del cliente
        if destination_method:
            required_credit = _convert_amount(sim_result['converted_amount'], to_currency, destination_method.moneda)
            destination_method.balance += required_credit
            destination_method.save()

        # Acreditar fondos en el método de pago del sistema (Caja del Negocio)
        payment_method.balance += total_cost_pyg
        payment_method.save()

        # Ejecutar pasarela de pago (PSE-18 Server-to-Server)
        try:
            PaymentGatewaySimulator.process_gateway_payment(
                gateway_name=payment_method.code or 'SIPAP',
                amount=total_cost_pyg,
                currency='PYG',
                source_account=getattr(origin_method, 'numero_cuenta', 'GX-MAIN'),
                destination_account=getattr(destination_method, 'numero_cuenta', 'GX-DEST')
            )
        except Exception:
            pass

        # Medir tiempo de respuesta (Criterio 4: < 5 segundos)
        elapsed_time = time.time() - start_time
        processing_time_ms = int(elapsed_time * 1000)

        c_vol = cliente.transaction_volume if isinstance(cliente.transaction_volume, Decimal) else Decimal(str(cliente.transaction_volume or 0))
        cliente.transaction_volume = c_vol + eval_amount_pyg
        cliente.save()

        # Registrar transacción
        transaction = CurrencyPurchaseTransaction.objects.create(
            user=user if user and user.is_authenticated else None,
            cliente=cliente,
            turno=_get_active_turn(user),
            from_currency=from_currency,
            to_currency=to_currency,
            amount=amount_dec,
            converted_amount=sim_result['converted_amount'],
            applied_rate=sim_result['applied_rate'],
            standard_rate=sim_result['standard_rate'],
            payment_method=payment_method,
            origin_acreditation_method=origin_method,
            destination_acreditation_method=destination_method,
            benefit_percentage=sim_result['benefit_percentage'],
            commission_amount=commission_amount,
            tax_amount=tax_amount,
            total_pyg=total_cost_pyg,
            status=tx_status,
            payment_method_type=pm_type,
            gateway_reference=gateway_ref,
            gateway_status=gateway_status,
            processing_time_ms=processing_time_ms,
            transparent_breakdown=f"{sim_result['transparent_breakdown']} | Total a Pagar: {total_origen:,.2f} {from_currency} (Equiv. ₲ {total_cost_pyg:,.2f}) | Caja: Ingresa stock de {amount_dec} {from_currency} y egresa stock de {sim_result['converted_amount']} {to_currency} | Tiempo: {processing_time_ms}ms",
            moneda_origen_id=from_currency,
            monto_origen=amount_dec,
            moneda_destino_id=to_currency,
            monto_destino=sim_result['converted_amount'],
            tipo_cambio_cruzado=tipo_cambio_cruzado,
            tipo_cambio_local_origen=tipo_cambio_local_origen,
            monto_moneda_local=eval_amount_pyg,
            total_origen=total_origen,
        )

        if transaction.status == 'SUCCESS':
            _generar_factura_sifen_async(transaction)

        return transaction

    @staticmethod
    def initiate_purchase(user, from_currency, to_currency, amount, payment_method_id_or_code, origin_method_id=None, destination_method_id=None, request=None):
        """
        Inicia una operación de compra de divisas dejando la transacción en estado PENDIENTE,
        capturando la tasa de cambio vigente al momento de la solicitud (PSE-31).

        Args:
            user (User): Usuario solicitante.
            from_currency (str): Moneda de origen.
            to_currency (str): Moneda de destino.
            amount (Decimal or float): Monto de origen.
            payment_method_id_or_code (str or int): ID o código del método de pago.
            request (HttpRequest, optional): Solicitud HTTP.

        Returns:
            CurrencyPurchaseTransaction: Transacción en estado PENDIENTE.

        Raises:
            ValidationError: Si se violan límites, fondos insuficientes o datos inválidos.
        """
        if is_user_analyst(user, request):
            raise ValidationError("Error de validación: El rol Analista no tiene permisos para realizar operaciones de compra de divisas.")

        if not from_currency or not to_currency or amount is None:
            raise ValidationError("Los campos moneda origen, moneda destino y monto son obligatorios para la compra.")

        try:
            amount_dec = Decimal(str(amount))
        except (ValueError, TypeError):
            raise ValidationError("El monto ingresado debe ser un valor numérico válido.")

        if amount_dec <= Decimal('0.00'):
            raise ValidationError("El monto de la transacción debe ser mayor a cero.")

        cliente, origin_method, destination_method = _resolve_client_and_methods(user, request, origin_method_id, destination_method_id, from_currency=from_currency, to_currency=to_currency)

        sim_result = SimuladorConversionService.simular(
            from_currency=from_currency,
            to_currency=to_currency,
            amount=amount_dec,
            user=user,
            request=request,
            client=cliente,
            category_override=cliente.categoria if cliente else None
        )

        eval_amount_pyg = amount_dec
        if from_currency != 'PYG':
            try:
                r_from = ExchangeRate.objects.get(currency_code=from_currency)
                benefit_pct = sim_result.get('benefit_percentage', Decimal('0.00'))
                custom_buy = r_from.buy_rate * (Decimal('1.00') + (benefit_pct / Decimal('100.00')))
                eval_amount_pyg = amount_dec * custom_buy
            except Exception:
                eval_amount_pyg = amount_dec * Decimal('7300.00')
        else:
            eval_amount_pyg = amount_dec

        MIN_LIMIT = LIMITE_MINIMO_OPERACION_PYG
        MAX_LIMIT = LIMITE_MAXIMO_OPERACION_PYG

        if eval_amount_pyg < MIN_LIMIT:
            raise ValidationError(f"El monto de la transacción (₲ {eval_amount_pyg:,.2f}) es inferior al límite mínimo permitido de ₲ 50.000 PYG.")
        if eval_amount_pyg > MAX_LIMIT:
            raise ValidationError(f"El monto de la transacción (₲ {eval_amount_pyg:,.2f}) excede el límite máximo permitido de ₲ 1.000.000.000 PYG.")

        ensure_default_payment_methods()
        payment_method = None
        if isinstance(payment_method_id_or_code, int) or str(payment_method_id_or_code).isdigit():
            payment_method = PaymentMethod.objects.filter(id=int(payment_method_id_or_code)).first()
        if not payment_method:
            payment_method = PaymentMethod.objects.filter(code=str(payment_method_id_or_code)).first()
        if not payment_method:
            payment_method = PaymentMethod.objects.filter(is_active=True).first()

        if not payment_method or not payment_method.is_active:
            raise ValidationError("El método de pago seleccionado no es válido o se encuentra inactivo.")

        commission_amount = (eval_amount_pyg * Decimal('0.005')).quantize(Decimal('0.01'))
        tax_amount = (commission_amount * Decimal('0.10')).quantize(Decimal('0.01'))
        total_cost_pyg = eval_amount_pyg + commission_amount + tax_amount

        if payment_method.balance < total_cost_pyg:
            raise ValidationError(f"Fondos insuficientes en el método de pago '{payment_method.name}'. Saldo disponible: ₲ {payment_method.balance:,.2f}, requerido: ₲ {total_cost_pyg:,.2f}.")

        cliente = get_active_client(user, request)
        if not cliente and user and user.is_authenticated:
            profile = getattr(user, 'profile', None)
            if profile and profile.keycloak_id:
                rel = UsuarioClienteRelacion.objects.filter(keycloak_user_id=profile.keycloak_id).select_related('cliente').first()
                if rel:
                    cliente = rel.cliente

        if not cliente:
            raise ValidationError("Debe seleccionar un cliente activo para realizar la operación de compra de divisas.")

        tipo_cambio_cruzado = sim_result.get('applied_rate') if (from_currency != 'PYG' and to_currency != 'PYG') else None
        tipo_cambio_local_origen = Decimal('1.0000')
        if from_currency != 'PYG':
            tipo_cambio_local_origen = sim_result.get('applied_rate', Decimal('1.0000'))

        if from_currency == 'PYG':
            commission_amount = (eval_amount_pyg * Decimal('0.005')).quantize(Decimal('0.01'))
            tax_amount = (commission_amount * Decimal('0.10')).quantize(Decimal('0.01'))
            total_cost_pyg = eval_amount_pyg
            total_origen = amount_dec
        else:
            commission_amount = (amount_dec * Decimal('0.005')).quantize(Decimal('0.01'))
            tax_amount = (commission_amount * Decimal('0.10')).quantize(Decimal('0.01'))
            total_origen = amount_dec
            total_pyg = (total_origen * tipo_cambio_local_origen).quantize(Decimal('0.01'))
            total_cost_pyg = total_pyg

        if payment_method.balance < total_cost_pyg:
            raise ValidationError(f"Fondos insuficientes en el método de pago '{payment_method.name}'. Saldo disponible: ₲ {payment_method.balance:,.2f}, requerido: ₲ {total_cost_pyg:,.2f}.")

        cliente, origin_method, destination_method = _resolve_client_and_methods(user, request, origin_method_id, destination_method_id, from_currency=from_currency, to_currency=to_currency)
        pm_type, gateway_ref, gateway_status, tx_status = _process_financial_instrument(payment_method, total_cost_pyg, 'PYG', origin_method, initial_status='PENDING')

        transaction = CurrencyPurchaseTransaction.objects.create(
            user=user if user and user.is_authenticated else None,
            cliente=cliente,
            turno=_get_active_turn(user),
            from_currency=from_currency,
            to_currency=to_currency,
            amount=amount_dec,
            converted_amount=sim_result['converted_amount'],
            applied_rate=sim_result['applied_rate'],
            standard_rate=sim_result['standard_rate'],
            payment_method=payment_method,
            payment_method_type=pm_type,
            gateway_reference=gateway_ref,
            gateway_status=gateway_status,
            origin_acreditation_method=origin_method,
            destination_acreditation_method=destination_method,
            benefit_percentage=sim_result['benefit_percentage'],
            commission_amount=commission_amount,
            tax_amount=tax_amount,
            total_pyg=total_cost_pyg,
            status='PENDING',
            processing_time_ms=0,
            transparent_breakdown=f"Iniciado en PENDIENTE. Pasarela: {pm_type} ({gateway_ref}) - Estado: {gateway_status}. Tasa aplicada: {sim_result['applied_rate']} | Total a Pagar: {total_origen:,.2f} {from_currency}",
            moneda_origen_id=from_currency,
            monto_origen=amount_dec,
            moneda_destino_id=to_currency,
            monto_destino=sim_result['converted_amount'],
            tipo_cambio_cruzado=tipo_cambio_cruzado,
            tipo_cambio_local_origen=tipo_cambio_local_origen,
            monto_moneda_local=eval_amount_pyg,
            total_origen=total_origen,
        )
        return transaction

    @staticmethod
    def confirm_purchase(transaction_id, user=None, request=None):
        """
        Confirma el pago de una transacción pendiente, verificando en tiempo real si la tasa
        de cambio ha sufrido modificaciones (PSE-31). Si la tasa cambió, cancela automáticamente
        la transacción sin débito financiero. Si la tasa es la misma, procesa el débito y marca SUCCESS.

        Args:
            transaction_id (int): ID de la transacción pendiente.
            user (User, optional): Usuario solicitante.
            request (HttpRequest, optional): Solicitud HTTP.

        Returns:
            CurrencyPurchaseTransaction: Transacción confirmada exitosamente (SUCCESS).

        Raises:
            ValidationError: Si hay variación de tasa (cancela automáticamente), fondos insuficientes o estado inválido.
        """
        try:
            transaction = CurrencyPurchaseTransaction.objects.get(id=transaction_id)
        except CurrencyPurchaseTransaction.DoesNotExist:
            raise ValidationError("La transacción especificada no existe.")

        if transaction.status != 'PENDING':
            raise ValidationError(f"La transacción ya se encuentra en estado '{transaction.status}' y no puede ser confirmada.")

        sim_check = SimuladorConversionService.simular(
            from_currency=transaction.from_currency,
            to_currency=transaction.to_currency,
            amount=transaction.amount,
            user=user,
            request=request
        )
        current_applied_rate = sim_check['applied_rate']

        if current_applied_rate != transaction.applied_rate:
            transaction.status = 'CANCELLED'
            transaction.transparent_breakdown += f" | CANCELADO AUTOMÁTICAMENTE por variación de tasa (Inicial: {transaction.applied_rate}, Actual: {current_applied_rate}). Sin débito de fondos."
            transaction.save()
            raise ValidationError("Alerta de Variación de Tasa: La cotización de la divisa ha variado en el sistema mientras la operación estaba pendiente. La transacción se ha cancelado de forma segura sin débito de fondos.")

        pm = transaction.payment_method
        if not pm or not pm.is_active or pm.balance < transaction.total_pyg:
            transaction.status = 'FAILED'
            transaction.transparent_breakdown += " | Falló por fondos insuficientes o método inactivo al confirmar."
            transaction.save()
            raise ValidationError("Fondos insuficientes o método de pago inactivo al confirmar el pago.")

        start_time = time.time()
        origin_method = transaction.origin_acreditation_method
        destination_method = transaction.destination_acreditation_method

        if origin_method:
            required_debit = _convert_amount(transaction.total_origen, transaction.from_currency, origin_method.moneda)
            if origin_method.balance < required_debit:
                transaction.status = 'FAILED'
                transaction.transparent_breakdown += " | Falló por fondos insuficientes en cuenta origen al confirmar."
                transaction.save()
                raise ValidationError(f"Fondos insuficientes en la cuenta origen '{origin_method.entidad_financiera}'. Saldo disponible: {origin_method.balance:,.2f} {origin_method.moneda}, requerido: {required_debit:,.2f} {origin_method.moneda}.")
            origin_method.balance -= required_debit
            origin_method.save()

        if destination_method:
            required_credit = _convert_amount(transaction.converted_amount, transaction.to_currency, destination_method.moneda)
            destination_method.balance += required_credit
            destination_method.save()

        pm.balance += transaction.total_pyg
        pm.save()

        cliente = transaction.cliente
        if cliente:
            c_vol = cliente.transaction_volume if isinstance(cliente.transaction_volume, Decimal) else Decimal(str(cliente.transaction_volume or 0))
            cliente.transaction_volume = c_vol + (transaction.amount if transaction.from_currency == 'PYG' else transaction.amount * transaction.applied_rate)
            cliente.save()

        elapsed_time = time.time() - start_time
        processing_time_ms = int(elapsed_time * 1000)

        if transaction.gateway_reference:
            PaymentGatewayLog.objects.filter(reference_code=transaction.gateway_reference).update(status='SUCCESS')
        transaction.gateway_status = 'SUCCESS'
        transaction.status = 'SUCCESS'
        transaction.processing_time_ms = processing_time_ms
        transaction.transparent_breakdown += f" | Pago confirmado exitosamente. Tiempo: {processing_time_ms}ms"
        transaction.save()
        _generar_factura_sifen_async(transaction)
        transaction.refresh_from_db()

        return transaction

    @staticmethod
    def cancel_purchase_manual(transaction_id, user=None, request=None):
        """
        Permite al cliente cancelar manualmente una transacción en estado PENDIENTE
        antes de ingresar credenciales de pago, sin generar ningún débito financiero (PSE-31).

        Args:
            transaction_id (int): ID de la transacción pendiente.
            user (User, optional): Usuario solicitante.
            request (HttpRequest, optional): Solicitud HTTP.

        Returns:
            CurrencyPurchaseTransaction: Transacción en estado CANCELLED.

        Raises:
            ValidationError: Si la transacción no existe o no está en estado PENDIENTE.
        """
        try:
            transaction = CurrencyPurchaseTransaction.objects.get(id=transaction_id)
        except CurrencyPurchaseTransaction.DoesNotExist:
            raise ValidationError("La transacción especificada no existe.")

        if transaction.status != 'PENDING':
            raise ValidationError(f"La transacción se encuentra en estado '{transaction.status}' y no puede ser cancelada manualmente.")

        if transaction.gateway_reference:
            PaymentGatewayLog.objects.filter(reference_code=transaction.gateway_reference).update(status='CANCELLED')
        transaction.gateway_status = 'CANCELLED'
        transaction.status = 'CANCELLED'
        transaction.transparent_breakdown += " | Cancelado manualmente por el usuario antes de confirmar el pago. Sin débito financiero."
        transaction.save()
        return transaction


@login_required
def currency_purchase_view(request):
    """
    Vista web para la Operación de Compra de Divisas (PROCESAMIENTO DE OPERACIONES / PSE-13 y PSE-31).
    Permite seleccionar cuenta origen, destino, divisa, monto y método de pago,
    iniciando un flujo pendiente con validación de variación de tasa y opciones de confirmación o cancelación manual.
    
    Args:
        request (HttpRequest): Solicitud HTTP del cliente.
        
    Returns:
        HttpResponse: Página renderizada del flujo de compra de divisas.
    """
    ensure_default_payment_methods()
    ensure_default_benefit_rules()

    default_rates = [
        ('USD', 'Dólar Estadounidense', '$', Decimal('7300.0000'), Decimal('7450.0000')),
        ('EUR', 'Euro', '€', Decimal('7900.0000'), Decimal('8150.0000')),
        ('BRL', 'Real Brasileño', 'R$', Decimal('1350.0000'), Decimal('1450.0000')),
        ('ARS', 'Peso Argentino', '$', Decimal('7.5000'), Decimal('9.0000')),
        ('PYG', 'Guaraní Paraguayo', '₲', Decimal('1.0000'), Decimal('1.0000')),
    ]
    for code, name, symbol, buy, sell in default_rates:
        ExchangeRate.objects.get_or_create(
            currency_code=code,
            defaults={'currency_name': name, 'symbol': symbol, 'buy_rate': buy, 'sell_rate': sell}
        )

    currencies = ExchangeRate.objects.all().order_by('currency_code')
    payment_methods = PaymentMethod.objects.filter(is_active=True).order_by('id')

    pending_transaction = None
    success_transaction = None
    cancelled_transaction = None
    error_message = None

    from_currency = request.GET.get('from_currency', 'PYG')
    to_currency = request.GET.get('to_currency', 'USD')
    amount_str = request.GET.get('amount', '100000')
    payment_method_code = request.GET.get('payment_method', 'TRANSFERENCIA')

    if request.method == 'POST':
        action = request.POST.get('action', 'process')
        tx_id = request.POST.get('transaction_id')

        if action == 'confirm_purchase' and tx_id:
            try:
                success_transaction = CurrencyPurchaseService.confirm_purchase(
                    transaction_id=int(tx_id),
                    user=request.user,
                    request=request
                )
            except ValidationError as e:
                error_message = e.messages[0] if hasattr(e, 'messages') else str(e)
                try:
                    cancelled_transaction = CurrencyPurchaseTransaction.objects.get(id=int(tx_id), status='CANCELLED')
                except Exception:
                    pass
            except Exception as e:
                error_message = f"Error al confirmar el pago: {str(e)}"
        elif action == 'cancel_purchase' and tx_id:
            try:
                cancelled_transaction = CurrencyPurchaseService.cancel_purchase_manual(
                    transaction_id=int(tx_id),
                    user=request.user,
                    request=request
                )
            except ValidationError as e:
                error_message = e.messages[0] if hasattr(e, 'messages') else str(e)
            except Exception as e:
                error_message = f"Error al cancelar la operación: {str(e)}"
        elif action == 'initiate_pending':
            from_currency = request.POST.get('from_currency')
            to_currency = request.POST.get('to_currency')
            amount_str = request.POST.get('amount')
            payment_method_code = request.POST.get('payment_method')

            try:
                pending_transaction = CurrencyPurchaseService.initiate_purchase(
                    user=request.user,
                    from_currency=from_currency,
                    to_currency=to_currency,
                    amount=amount_str,
                    payment_method_id_or_code=payment_method_code,
                    request=request
                )
            except ValidationError as e:
                error_message = e.messages[0] if hasattr(e, 'messages') else str(e)
            except Exception as e:
                error_message = f"Error al iniciar la compra: {str(e)}"
        else:
            from_currency = request.POST.get('from_currency')
            to_currency = request.POST.get('to_currency')
            amount_str = request.POST.get('amount')
            origin_account = request.POST.get('origin_account')
            destination_account = request.POST.get('destination_account')
            payment_method_code = request.POST.get('payment_method')

            try:
                pending_transaction = CurrencyPurchaseService.initiate_purchase(
                    user=request.user,
                    from_currency=from_currency,
                    to_currency=to_currency,
                    amount=amount_str,
                    payment_method_id_or_code=payment_method_code,
                    origin_method_id=origin_account,
                    destination_method_id=destination_account,
                    request=request
                )
            except ValidationError as e:
                error_message = e.messages[0] if hasattr(e, 'messages') else str(e)
            except Exception as e:
                error_message = f"Error al iniciar la compra: {str(e)}"

    active_client = _resolve_active_client(request.user, request)

    acreditation_methods = _get_user_acreditation_methods(request, active_client)
    if not is_user_analyst(request.user, request) and acreditation_methods.count() == 0:
        return redirect('client_acreditation_methods')
    clientes_asociados = []
    if request.user.is_authenticated and hasattr(request.user, 'profile') and request.user.profile.keycloak_id:
        rels = UsuarioClienteRelacion.objects.filter(keycloak_user_id=request.user.profile.keycloak_id).select_related('cliente')
        clientes_asociados = [r.cliente for r in rels]
    if not clientes_asociados and active_client:
        clientes_asociados = [active_client]

    user_profile = None
    benefit_percentage = Decimal('0.00')
    category_display = active_client.categoria if active_client else 'Invitado / Minorista'
    cat_code = get_user_effective_category(request.user, request=request)
    if request.user.is_authenticated:
        try:
            user_profile = request.user.profile
            rule = ClientBenefitRule.objects.filter(category_code=cat_code).first()
            if rule:
                benefit_percentage = rule.benefit_percentage
                if not active_client:
                    category_display = rule.category_name
        except Exception:
            pass

    context = {
        'currencies': currencies,
        'payment_methods': payment_methods,
        'from_currency': from_currency,
        'to_currency': to_currency,
        'amount': amount_str,
        'selected_payment_method': payment_method_code,
        'pending_transaction': pending_transaction,
        'success_transaction': success_transaction,
        'cancelled_transaction': cancelled_transaction,
        'error_message': error_message,
        'user_profile': user_profile,
        'active_client': active_client,
        'clientes_asociados': clientes_asociados,
        'benefit_percentage': benefit_percentage,
        'category_display': category_display,
        'acreditation_methods': acreditation_methods,
        'simulacion_origen': _resumen_desde_simulador(request, active_client, from_currency, to_currency, amount_str),
        'now': timezone.now(),
    }

    return render(request, 'procesamiento_operaciones/currency_purchase.html', context)


@login_required
def currency_purchase_history_view(request):
    """
    Vista web para consultar el historial de operaciones de compra de divisas
    del cliente activo actual (PROCESAMIENTO DE OPERACIONES / PSE-13).
    Muestra únicamente las compras realizadas para ese cliente. Si no hay cliente activo, el historial está vacío.
    
    Args:
        request (HttpRequest): Solicitud HTTP del cliente.
        
    Returns:
        HttpResponse: Página renderizada con el historial de compras de divisas del cliente activo.
    """
    active_client = get_active_client(request.user, request=request)
    
    if active_client:
        transactions = CurrencyPurchaseTransaction.objects.filter(cliente=active_client).order_by('-timestamp')
        _sincronizar_facturas_sifen(transactions)
    else:
        transactions = CurrencyPurchaseTransaction.objects.none()

    context = {
        'transactions': transactions,
        'active_client': active_client,
        'now': timezone.now(),
    }
    return render(request, 'procesamiento_operaciones/currency_purchase_history.html', context)
