# -*- coding: utf-8 -*-
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Q
from django.contrib import messages
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from decimal import Decimal
import csv

from .models import (
    Caja, TurnoCaja, DenominacionDivisa, DesgloseEfectivoCaja, 
    DetalleDesgloseBillete, ArqueoCaja, BitacoraArqueo, OperacionVentanilla,
    verificar_turno_activo, inicializar_denominaciones_default, procesar_arqueo_cierre
)
from authentication.models import UserProfile, Cliente
from gestion_clientes.views import get_user_interface_context
from procesamiento_operaciones.models import CurrencyPurchaseTransaction, CurrencySaleTransaction
from tasas_cambio.models import ExchangeRate, ClientBenefitRule
from tasas_cambio.views import ensure_default_benefit_rules


@login_required
def gestion_caja_view(request):
    """
    Vista web para la gestión de apertura y cierre de turnos de caja física
    y registro de ingresos/salidas con desglose detallado de billetes (PSE-21),
    incluyendo el reporte de arqueo automático y bitácora de auditoría (PSE-22).
    Estilizada con Tailwind CSS (Corporate Modern).
    """
    inicializar_denominaciones_default()

    if not Caja.objects.exists():
        Caja.objects.get_or_create(codigo="C01", defaults={'nombre': "Caja Principal 01", 'activa': True})
        Caja.objects.get_or_create(codigo="C02", defaults={'nombre': "Caja Secundaria 02", 'activa': True})

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    interface_ctx = get_user_interface_context(request, profile)
    is_admin = interface_ctx.get('is_admin', False)
    badge_text = interface_ctx.get('badge_text', '')

    if is_admin:
        cajas = Caja.objects.filter(activa=True)
    else:
        # El cajero solo ve cajas específicamente asignadas a él
        turno_actual_cajero = TurnoCaja.objects.filter(cajero=request.user, estado='ABIERTO').first()
        if turno_actual_cajero:
            cajas = [turno_actual_cajero.caja]
        else:
            cajas_asignadas = Caja.objects.filter(activa=True, cajeros_asignados=request.user)
            if cajas_asignadas.exists():
                caja_asignada_libre = cajas_asignadas.exclude(turnocaja__estado='ABIERTO').first()
                if not caja_asignada_libre:
                    caja_asignada_libre = cajas_asignadas.first()
                cajas = [caja_asignada_libre] if caja_asignada_libre else []
            else:
                # Si no tiene ninguna caja asignada, no ve ninguna caja
                cajas = []

    cajas_data = []

    denominaciones = DenominacionDivisa.objects.filter(activa=True)
    denominaciones_por_divisa = {}
    for d in denominaciones:
        if d.divisa not in denominaciones_por_divisa:
            denominaciones_por_divisa[d.divisa] = []
        denominaciones_por_divisa[d.divisa].append(d)

    for caja in cajas:
        turno_activo = verificar_turno_activo(caja)
        desgloses_turno = DesgloseEfectivoCaja.objects.filter(turno=turno_activo).order_by('-fecha') if turno_activo else []
        cajas_data.append({
            'caja': caja,
            'turno_activo': turno_activo,
            'desgloses_turno': desgloses_turno,
            'cajeros_asignados': caja.cajeros_asignados.all(),
        })

    turnos_cerrados = TurnoCaja.objects.filter(estado='CERRADO').prefetch_related('arqueos', 'bitacoras_arqueo').order_by('-fecha_cierre')[:10] if is_admin else []
    bitacoras_recientes = BitacoraArqueo.objects.all().order_by('-fecha')[:15] if is_admin else []

    if is_admin or (badge_text and 'admin' in badge_text.lower()):
        role_badge = "Administrador"
        badge_bg = "bg-blue-800"
    else:
        role_badge = badge_text if badge_text else "Cajero de Sucursal"
        badge_bg = "bg-amber-700"

    context = {
        'cajas_data': cajas_data,
        'denominaciones_por_divisa': denominaciones_por_divisa,
        'turnos_cerrados': turnos_cerrados,
        'bitacoras_recientes': bitacoras_recientes,
        'role_badge': role_badge,
        'badge_bg': badge_bg,
        'is_admin': is_admin,
    }
    return render(request, 'caja/gestion_caja.html', context)


@login_required
def abrir_turno_view(request, caja_id):
    """
    Procesa la apertura de un turno de caja asociando el usuario cajero,
    la fecha/hora y los saldos iniciales por tipo de divisa (USD, EUR, PYG, BRL, ARS).
    """
    caja = get_object_or_404(Caja, id=caja_id, activa=True)

    if verificar_turno_activo(caja):
        messages.error(request, f"La caja '{caja.nombre}' ya posee un turno activo sin cerrar.")
        return redirect('caja:gestion_caja')

    if request.method == 'POST':
        try:
            saldo_pyg = Decimal(request.POST.get('saldo_inicial_pyg') or '0')
            saldo_usd = Decimal(request.POST.get('saldo_inicial_usd') or '0')
            saldo_eur = Decimal(request.POST.get('saldo_inicial_eur') or '0')
            saldo_brl = Decimal(request.POST.get('saldo_inicial_brl') or '0')
            saldo_ars = Decimal(request.POST.get('saldo_inicial_ars') or '0')

            if saldo_pyg == 0 and saldo_usd == 0 and saldo_eur == 0 and saldo_brl == 0 and saldo_ars == 0:
                messages.error(request, "Debe ingresar al menos un saldo inicial mayor a 0 en alguna de las divisas para abrir el turno.")
                return redirect('caja:gestion_caja')

            turno = TurnoCaja(
                caja=caja,
                cajero=request.user,
                saldo_inicial_pyg=saldo_pyg,
                saldo_inicial_usd=saldo_usd,
                saldo_inicial_eur=saldo_eur,
                saldo_inicial_brl=saldo_brl,
                saldo_inicial_ars=saldo_ars,
                estado='ABIERTO'
            )
            turno.save()
            messages.success(request, f"Turno abierto exitosamente para la caja '{caja.nombre}' con usuario {request.user.username}.")
        except ValidationError as e:
            messages.error(request, str(e))
        except Exception as e:
            messages.error(request, f"Error al abrir turno: {str(e)}")

    return redirect('caja:gestion_caja')


@login_required
def cerrar_turno_view(request, turno_id):
    """
    Procesa el cierre de turno de caja ingresando los saldos finales físicos,
    ejecutando el arqueo automático por divisa, registrando bitácora de auditoría
    y notificando al Administrador en caso de descuadres (PSE-22).
    """
    turno = get_object_or_404(TurnoCaja, id=turno_id, estado='ABIERTO')

    if request.method == 'POST':
        try:
            turno.saldo_final_pyg = Decimal(request.POST.get('saldo_final_pyg') or '0')
            turno.saldo_final_usd = Decimal(request.POST.get('saldo_final_usd') or '0')
            turno.saldo_final_eur = Decimal(request.POST.get('saldo_final_eur') or '0')
            turno.saldo_final_brl = Decimal(request.POST.get('saldo_final_brl') or '0')
            turno.saldo_final_ars = Decimal(request.POST.get('saldo_final_ars') or '0')
            turno.fecha_cierre = timezone.now()
            turno.estado = 'CERRADO'
            turno.save()

            procesar_arqueo_cierre(turno)

            messages.success(request, f"Turno #{turno.id} cerrado y arqueo automático procesado exitosamente. Operaciones congeladas.")
        except Exception as e:
            messages.error(request, f"Error al cerrar turno y procesar arqueo: {str(e)}")

    return redirect('caja:gestion_caja')


@login_required
def registrar_movimiento_efectivo_view(request, turno_id):
    """
    Procesa un ingreso o salida física de efectivo en un turno activo,
    respaldado por el desglose detallado de billetes por denominación (PSE-21).
    """
    turno = get_object_or_404(TurnoCaja, id=turno_id, estado='ABIERTO')

    if request.method == 'POST':
        try:
            tipo_operacion = request.POST.get('tipo_operacion') # INGRESO o SALIDA
            divisa = request.POST.get('divisa', 'PYG')
            observacion = request.POST.get('observacion', '')

            if tipo_operacion not in ['INGRESO', 'SALIDA']:
                messages.error(request, "Tipo de operación no válido.")
                return redirect('caja:gestion_caja')

            desglose = DesgloseEfectivoCaja.objects.create(
                turno=turno,
                tipo_operacion=tipo_operacion,
                divisa=divisa,
                observacion=observacion,
                monto_total=Decimal('0.00')
            )

            monto_total = Decimal('0.00')
            denominaciones = DenominacionDivisa.objects.filter(divisa=divisa, activa=True)
            
            for denom in denominaciones:
                qty_str = request.POST.get(f'denom_{denom.id}', '0')
                qty = int(qty_str) if qty_str.isdigit() else 0
                if qty > 0:
                    detalle = DetalleDesgloseBillete.objects.create(
                        desglose=desglose,
                        denominacion=denom,
                        cantidad=qty
                    )
                    monto_total += detalle.subtotal

            desglose.monto_total = monto_total
            desglose.save()

            messages.success(request, f"{tipo_operacion.capitalize()} de efectivo registrado exitosamente ({divisa} {monto_total:,.2f}) con desglose detallado de billetes.")
        except Exception as e:
            messages.error(request, f"Error al registrar movimiento con desglose: {str(e)}")

    return redirect('caja:gestion_caja')


@login_required
def gestion_cajas_admin_view(request):
    """
    Vista de administración para el CRUD de Cajas Físicas y asignación de cajeros.
    Permite agregar, editar, eliminar cajas y asignar usuarios con rol cajero.
    """
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    interface_ctx = get_user_interface_context(request, profile)
    if not interface_ctx.get('is_admin', False) and not request.user.is_superuser:
        messages.error(request, "Acceso no autorizado.")
        return redirect('dashboard_redirect')

    cajeros_list = User.objects.filter(
        Q(profile__role__name__icontains='cajero') |
        Q(profile__roles__name__icontains='cajero')
    ).distinct()
    if not cajeros_list.exists():
        cajeros_list = User.objects.filter(is_superuser=False)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'crear':
            nombre = request.POST.get('nombre')
            codigo = request.POST.get('codigo')
            cajeros_ids = request.POST.getlist('cajeros_asignados')
            try:
                caja = Caja.objects.create(nombre=nombre, codigo=codigo, activa=True)
                if cajeros_ids:
                    caja.cajeros_asignados.set(cajeros_ids)
                messages.success(request, f"Caja '{nombre}' creada exitosamente.")
            except Exception as e:
                messages.error(request, f"Error al crear caja: {str(e)}")
        elif action == 'editar':
            caja_id = request.POST.get('caja_id')
            caja = get_object_or_404(Caja, id=caja_id)
            caja.nombre = request.POST.get('nombre', caja.nombre)
            caja.codigo = request.POST.get('codigo', caja.codigo)
            caja.activa = True if request.POST.get('activa') == 'on' else False
            cajeros_ids = request.POST.getlist('cajeros_asignados')
            try:
                caja.save()
                caja.cajeros_asignados.set(cajeros_ids)
                messages.success(request, f"Caja '{caja.nombre}' actualizada exitosamente.")
            except Exception as e:
                messages.error(request, f"Error al actualizar caja: {str(e)}")
        elif action == 'eliminar':
            caja_id = request.POST.get('caja_id')
            caja = get_object_or_404(Caja, id=caja_id)
            try:
                nombre_caja = caja.nombre
                caja.delete()
                messages.success(request, f"Caja '{nombre_caja}' eliminada exitosamente.")
            except Exception as e:
                messages.error(request, f"Error al eliminar caja: {str(e)}")
        return redirect('caja:gestion_cajas_admin')

    cajas = Caja.objects.all().prefetch_related('cajeros_asignados')
    context = {
        'cajas': cajas,
        'cajeros_list': cajeros_list,
    }
    return render(request, 'caja/gestion_cajas_admin.html', context)


@login_required
def detalle_turno_operaciones_view(request, turno_id):
    """
    Vista web para consultar el detalle transaccional de todas las operaciones procesadas
    durante un turno activo o cerrado de caja (PSE-37).
    Muestra clientes atendidos, tipos de operación (Compra/Venta), consolidado acumulado,
    filtros, búsqueda y opción de exportar/imprimir planilla de cierre.
    """
    turno = get_object_or_404(TurnoCaja, id=turno_id)
    
    tx_type_filter = request.GET.get('tx_type', '')
    search_query = request.GET.get('q', '').strip()
    export_format = request.GET.get('export', '')

    purchases_qs = CurrencyPurchaseTransaction.objects.filter(
        Q(turno=turno) | Q(turno__isnull=True, user=turno.cajero, timestamp__gte=turno.fecha_apertura)
    )
    sales_qs = CurrencySaleTransaction.objects.filter(
        Q(turno=turno) | Q(turno__isnull=True, user=turno.cajero, timestamp__gte=turno.fecha_apertura)
    )

    if turno.fecha_cierre:
        purchases_qs = purchases_qs.filter(timestamp__lte=turno.fecha_cierre)
        sales_qs = sales_qs.filter(timestamp__lte=turno.fecha_cierre)

    purchases = []
    if tx_type_filter in ['', 'COMPRA']:
        for p in purchases_qs:
            p.tx_type = 'COMPRA'
            p.comprobante = f"COMPRA-{p.id}"
            p.cliente_nombre = p.cliente.nombre_o_razon_social if p.cliente else 'N/A'
            p.cliente_doc = p.cliente.documento_identidad if p.cliente else 'N/A'
            p.moneda_str = f"{p.from_currency} -> {p.to_currency}"
            p.monto_str = p.amount
            p.medio_pago = str(p.payment_method) if p.payment_method else (p.payment_method_type or 'Efectivo / Caja')
            purchases.append(p)

    sales = []
    if tx_type_filter in ['', 'VENTA']:
        for s in sales_qs:
            s.tx_type = 'VENTA'
            s.comprobante = f"VENTA-{s.id}"
            s.cliente_nombre = s.cliente.nombre_o_razon_social if s.cliente else 'N/A'
            s.cliente_doc = s.cliente.documento_identidad if s.cliente else 'N/A'
            s.moneda_str = f"{s.from_currency} -> {s.to_currency}"
            s.monto_str = s.amount
            s.medio_pago = str(s.linked_account) if s.linked_account else (s.payment_method_type or 'Efectivo / Cuenta')
            sales.append(s)

    all_ops = sorted(purchases + sales, key=lambda x: x.timestamp, reverse=True)

    if search_query:
        q_lower = search_query.lower()
        filtered_ops = []
        for op in all_ops:
            if q_lower in op.cliente_nombre.lower() or q_lower in op.cliente_doc.lower() or q_lower in str(op.id):
                filtered_ops.append(op)
        all_ops = filtered_ops

    total_compras_count = len([op for op in all_ops if op.tx_type == 'COMPRA'])
    total_ventas_count = len([op for op in all_ops if op.tx_type == 'VENTA'])
    
    acumulado_compras_pyg = sum([op.total_pyg for op in all_ops if op.tx_type == 'COMPRA' and hasattr(op, 'total_pyg') and op.total_pyg], Decimal('0.00'))
    acumulado_ventas_pyg = sum([op.total_pyg for op in all_ops if op.tx_type == 'VENTA' and hasattr(op, 'total_pyg') and op.total_pyg], Decimal('0.00'))

    if export_format == 'excel':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="planilla_turno_{turno.id}.csv"'
        writer = csv.writer(response)
        writer.writerow(['Hora', 'Comprobante', 'Tipo Operacion', 'Cliente', 'Documento RUC/CI', 'Moneda', 'Monto', 'Medio de Pago', 'Total PYG', 'Estado'])
        for op in all_ops:
            writer.writerow([
                op.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                op.comprobante,
                op.tx_type,
                op.cliente_nombre,
                op.cliente_doc,
                op.moneda_str,
                op.monto_str,
                op.medio_pago,
                getattr(op, 'total_pyg', 0),
                op.status
            ])
        return response

    if export_format == 'pdf' or export_format == 'print':
        context = {
            'turno': turno,
            'operaciones': all_ops,
            'total_compras_count': total_compras_count,
            'total_ventas_count': total_ventas_count,
            'acumulado_compras_pyg': acumulado_compras_pyg,
            'acumulado_ventas_pyg': acumulado_ventas_pyg,
            'is_print': True,
            'now': timezone.now(),
        }
        return render(request, 'caja/planilla_cierre_turno_pdf.html', context)

    context = {
        'turno': turno,
        'operaciones': all_ops,
        'total_compras_count': total_compras_count,
        'total_ventas_count': total_ventas_count,
        'acumulado_compras_pyg': acumulado_compras_pyg,
        'acumulado_ventas_pyg': acumulado_ventas_pyg,
        'tx_type': tx_type_filter,
        'search_query': search_query,
        'now': timezone.now(),
    }
    return render(request, 'caja/detalle_turno_operaciones.html', context)


def ventanilla_pos_view(request):
    """
    Vista web para el procesamiento operativo en ventanilla (POS) (PSE-38).
    Valida que el cajero tenga un turno de caja activo, permite buscar/registrar cliente presencial,
    seleccionar tipo de operación (Compra/Venta), par de divisas, monto, calcular tasa/comisión/vuelto
    y procesar la transacción con emisión de comprobante y actualización en tiempo real del saldo en caja.

    Args:
        request (HttpRequest): Objeto HttpRequest.

    Returns:
        HttpResponse: Respuesta HTTP renderizada con la plantilla del POS de ventanilla.
    """
    inicializar_denominaciones_default()

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    interface_ctx = get_user_interface_context(request, profile)
    is_admin = interface_ctx.get('is_admin', False)

    # Verificar si el cajero tiene un turno activo
    turno_activo = TurnoCaja.objects.filter(cajero=request.user, estado='ABIERTO').first()

    tasas = ExchangeRate.objects.all()
    clientes = Cliente.objects.all().order_by('-creado_en')[:50]

    cliente_encontrado = None
    benefit_percentage = Decimal('0.00')
    category_name = 'Minorista'
    ensure_default_benefit_rules()

    doc_busqueda = request.GET.get('documento_busqueda', '').strip()
    if doc_busqueda:
        cliente_encontrado = Cliente.objects.filter(documento_identidad__icontains=doc_busqueda).first()
        if cliente_encontrado:
            cat = cliente_encontrado.categoria or 'MINORISTA'
            rule = ClientBenefitRule.objects.filter(category_code__iexact=cat).first()
            if rule:
                benefit_percentage = rule.benefit_percentage
                category_name = rule.category_name
            elif str(cat).upper() == 'VIP':
                benefit_percentage = Decimal('2.00')
                category_name = 'VIP'
            elif str(cat).upper() == 'CORPORATIVO':
                benefit_percentage = Decimal('4.00')
                category_name = 'Corporativo'

    operaciones_recientes = OperacionVentanilla.objects.filter(cajero=request.user).order_by('-fecha')[:10]

    context = {
        'turno_activo': turno_activo,
        'tasas': tasas,
        'clientes': clientes,
        'cliente_encontrado': cliente_encontrado,
        'benefit_percentage': benefit_percentage,
        'category_name': category_name,
        'doc_busqueda': doc_busqueda,
        'operaciones_recientes': operaciones_recientes,
        'is_admin': is_admin,
    }
    return render(request, 'caja/ventanilla_pos.html', context)


@login_required
def procesar_ventanilla_view(request):
    """
    Procesa la operación presencial de compra o venta de divisas en ventanilla (PSE-38),
    validando turno activo, buscando o registrando cliente ocasional, calculando cotización/comisión/vuelto,
    actualizando el saldo en caja mediante desglose físico y emitiendo comprobante.

    Args:
        request (HttpRequest): Objeto HttpRequest con datos POST de la operación de ventanilla.

    Returns:
        HttpResponseRedirect: Redirección a la vista de ventanilla POS.
    """
    if request.method != 'POST':
        return redirect('caja:ventanilla_pos')

    turno_activo = TurnoCaja.objects.filter(cajero=request.user, estado='ABIERTO').first()
    if not turno_activo:
        messages.error(request, "Acceso bloqueado: No cuenta con un turno de caja activo para procesar operaciones en ventanilla.")
        return redirect('caja:ventanilla_pos')

    try:
        tipo_operacion = request.POST.get('tipo_operacion', 'COMPRA')
        moneda_origen = request.POST.get('moneda_origen', 'USD')
        moneda_destino = request.POST.get('moneda_destino', 'PYG')
        monto_origen = Decimal(request.POST.get('monto_origen') or '0')
        efectivo_recibido = Decimal(request.POST.get('efectivo_recibido') or '0')

        cliente_id = request.POST.get('cliente_id')
        cliente = None
        if cliente_id:
            cliente = Cliente.objects.filter(id=cliente_id).first()

        if not cliente:
            nombre_cliente = request.POST.get('nombre_cliente', 'Cliente Ocasional Ventanilla')
            doc_cliente = request.POST.get('documento_cliente', f'OCASIONAL-{uuid.uuid4().hex[:8].upper()}' if 'uuid' in globals() else 'OCASIONAL-001')
            email_cliente = request.POST.get('email_cliente', 'ocasional@globalexchange.com')
            cliente, _ = Cliente.objects.get_or_create(
                documento_identidad=doc_cliente,
                defaults={
                    'nombre_o_razon_social': nombre_cliente,
                    'email': email_cliente,
                    'tipo_cliente': 'FISICA',
                    'categoria': 'MINORISTA'
                }
            )

        if monto_origen <= 0:
            messages.error(request, "El monto de origen debe ser mayor a cero.")
            return redirect('caja:ventanilla_pos')

        # Resolver beneficio por categoría del cliente (VIP / Corporativo)
        ensure_default_benefit_rules()
        benefit_percentage = Decimal('0.00')
        category_name = 'Minorista'
        if cliente:
            cat = cliente.categoria or 'MINORISTA'
            rule = ClientBenefitRule.objects.filter(category_code__iexact=cat).first()
            if rule:
                benefit_percentage = rule.benefit_percentage
                category_name = rule.category_name
            elif str(cat).upper() == 'VIP':
                benefit_percentage = Decimal('2.00')
                category_name = 'VIP'
            elif str(cat).upper() == 'CORPORATIVO':
                benefit_percentage = Decimal('4.00')
                category_name = 'Corporativo'

        def get_db_rate(cur):
            """Obtiene la tasa de base de datos ajustada por beneficio de categoría."""
            if cur == 'PYG':
                return Decimal('1.0000')
            r = ExchangeRate.objects.filter(currency_code=cur).first()
            if not r:
                defaults = {'USD': Decimal('7300'), 'EUR': Decimal('7800'), 'BRL': Decimal('1400'), 'ARS': Decimal('7.5')}
                return defaults.get(cur, Decimal('7300'))
            base = r.buy_rate if tipo_operacion == 'COMPRA' else r.sell_rate
            if benefit_percentage > 0:
                if tipo_operacion == 'COMPRA':
                    return (base * (Decimal('1.0000') + (benefit_percentage / Decimal('100.00')))).quantize(Decimal('0.0001'))
                return (base * (Decimal('1.0000') - (benefit_percentage / Decimal('100.00')))).quantize(Decimal('0.0001'))
            return base

        def get_standard_rate(cur):
            """Obtiene la tasa estándar (sin beneficio) para trazabilidad en comprobantes."""
            if cur == 'PYG':
                return Decimal('1.0000')
            r = ExchangeRate.objects.filter(currency_code=cur).first()
            if not r:
                defaults = {'USD': Decimal('7300'), 'EUR': Decimal('7800'), 'BRL': Decimal('1400'), 'ARS': Decimal('7.5')}
                return defaults.get(cur, Decimal('7300'))
            return r.buy_rate if tipo_operacion == 'COMPRA' else r.sell_rate

        tasa_origen = get_db_rate(moneda_origen)
        tasa_destino = get_db_rate(moneda_destino)
        std_origen = get_standard_rate(moneda_origen)
        std_destino = get_standard_rate(moneda_destino)

        valor_pyg = monto_origen if moneda_origen == 'PYG' else (monto_origen * tasa_origen)
        comision = monto_origen * Decimal('0.005') # 0.5%
        comision_pyg = comision if moneda_origen == 'PYG' else (comision * tasa_origen)

        neto_pyg = valor_pyg - comision_pyg if tipo_operacion == 'COMPRA' else valor_pyg + comision_pyg
        monto_destino = neto_pyg if moneda_destino == 'PYG' else (neto_pyg / tasa_destino if tasa_destino > 0 else neto_pyg)
        tasa = tasa_origen if moneda_origen != 'PYG' else tasa_destino
        tasa_std = std_origen if moneda_origen != 'PYG' else std_destino
        total_a_cobrar_entregar = monto_destino
        vuelto = efectivo_recibido - monto_destino if efectivo_recibido > monto_destino else Decimal('0.00')

        import uuid as uuid_mod
        comprobante_num = f"TICKET-VNT-{uuid_mod.uuid4().hex[:8].upper()}"

        operacion = OperacionVentanilla.objects.create(
            turno=turno_activo,
            cajero=request.user,
            cliente=cliente,
            tipo_operacion=tipo_operacion,
            moneda_origen=moneda_origen,
            moneda_destino=moneda_destino,
            monto_origen=monto_origen,
            monto_destino=monto_destino,
            tasa_aplicada=tasa,
            benefit_percentage=benefit_percentage,
            comision=comision,
            total_a_cobrar_entregar=total_a_cobrar_entregar,
            efectivo_recibido=efectivo_recibido,
            vuelto=vuelto,
            comprobante_numero=comprobante_num
        )

        if tipo_operacion == 'COMPRA':
            CurrencyPurchaseTransaction.objects.create(
                user=request.user,
                cliente=cliente,
                turno=turno_activo,
                from_currency=moneda_origen,
                to_currency=moneda_destino,
                amount=monto_origen,
                converted_amount=monto_destino,
                applied_rate=tasa,
                standard_rate=tasa_std,
                commission_amount=comision,
                total_pyg=total_a_cobrar_entregar if moneda_destino == 'PYG' else (monto_origen * tasa),
                status='SUCCESS',
                payment_method_type='EFECTIVO_VENTANILLA'
            )
            DesgloseEfectivoCaja.objects.create(
                turno=turno_activo,
                tipo_operacion='INGRESO',
                divisa=moneda_origen,
                monto_total=monto_origen,
                observacion=f"Ventanilla Compra #{comprobante_num} - Cliente {cliente.nombre_o_razon_social}"
            )
            DesgloseEfectivoCaja.objects.create(
                turno=turno_activo,
                tipo_operacion='SALIDA',
                divisa=moneda_destino,
                monto_total=total_a_cobrar_entregar,
                observacion=f"Ventanilla Compra Pago Net #{comprobante_num} - Cliente {cliente.nombre_o_razon_social}"
            )
        else:
            CurrencySaleTransaction.objects.create(
                user=request.user,
                cliente=cliente,
                turno=turno_activo,
                from_currency=moneda_origen,
                to_currency=moneda_destino,
                amount=monto_origen,
                converted_amount=monto_destino,
                applied_rate=tasa,
                standard_rate=tasa_std,
                commission_amount=comision,
                total_pyg=total_a_cobrar_entregar if moneda_origen == 'PYG' else (monto_origen * tasa),
                status='SUCCESS',
                payment_method_type='EFECTIVO_VENTANILLA'
            )
            DesgloseEfectivoCaja.objects.create(
                turno=turno_activo,
                tipo_operacion='INGRESO',
                divisa=moneda_origen,
                monto_total=total_a_cobrar_entregar,
                observacion=f"Ventanilla Venta Cobro Net #{comprobante_num} - Cliente {cliente.nombre_o_razon_social}"
            )
            DesgloseEfectivoCaja.objects.create(
                turno=turno_activo,
                tipo_operacion='SALIDA',
                divisa=moneda_destino,
                monto_total=monto_destino,
                observacion=f"Ventanilla Venta Entrega Divisa #{comprobante_num} - Cliente {cliente.nombre_o_razon_social}"
            )

        if cliente:
            vol_add = monto_origen if moneda_origen == 'PYG' else (monto_origen * tasa)
            cliente.transaction_volume = Decimal(str(cliente.transaction_volume or 0)) + Decimal(str(vol_add))
            cliente.save()

        messages.success(request, f"¡Operación procesada con éxito! Comprobante N° {comprobante_num} emitido. Saldo de caja actualizado en tiempo real.")
        return redirect('caja:ventanilla_pos')

    except Exception as e:
        messages.error(request, f"Error al procesar operación en ventanilla: {str(e)}")
        return redirect('caja:ventanilla_pos')
