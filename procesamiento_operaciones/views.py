# -*- coding: utf-8 -*-
from django.shortcuts import render, redirect
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from decimal import Decimal
import time
import csv
from authentication.models import UserProfile, Cliente, UsuarioClienteRelacion
from tasas_cambio.models import ExchangeRate, ClientBenefitRule, PaymentMethod
from tasas_cambio.views import (
    ensure_default_payment_methods, ensure_default_benefit_rules,
    SimuladorConversionService, get_user_effective_category, get_active_client
)
from .models import CurrencyPurchaseTransaction, CurrencySaleTransaction


class CurrencySaleService:
    """
    Servicio de dominio para el procesamiento de la operación de venta de divisas (PSE-14).
    Valida límites (50.000 PYG a 1.000.000.000 PYG), verifica obligatoriamente la existencia
    de una cuenta bancaria o billetera digital vinculada, aplica la tasa de compra vigente,
    calcula comisiones, impuestos y descuentos por perfil, y mide el tiempo de respuesta (< 5 segundos).
    """

    @staticmethod
    def process_sale(user, from_currency, to_currency, amount, payment_method_id_or_code, request=None):
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

        # Simular / Calcular conversión aplicando tasa de compra vigente
        sim_result = SimuladorConversionService.simular(
            from_currency=from_currency,
            to_currency=to_currency,
            amount=amount_dec,
            user=user,
            request=request
        )

        # Determinar el monto total expresado en Guaraníes (PYG) para validación de límites
        eval_amount_pyg = amount_dec
        if from_currency != 'PYG':
            try:
                r_from = ExchangeRate.objects.get(currency_code=from_currency)
                eval_amount_pyg = amount_dec * r_from.buy_rate
            except Exception:
                eval_amount_pyg = sim_result.get('converted_amount', amount_dec)
        else:
            eval_amount_pyg = amount_dec

        # Criterio 1: Validación de Límites (Mínimo 50.000 PYG, Máximo 1.000.000.000 PYG)
        MIN_LIMIT = Decimal('50000.00')
        MAX_LIMIT = Decimal('1000000000.00')

        if eval_amount_pyg < MIN_LIMIT:
            raise ValidationError(f"El monto de la transacción (₲ {eval_amount_pyg:,.2f}) es inferior al límite mínimo permitido de ₲ 50.000 PYG.")
        if eval_amount_pyg > MAX_LIMIT:
            raise ValidationError(f"El monto de la transacción (₲ {eval_amount_pyg:,.2f}) excede el límite máximo permitido de ₲ 1.000.000.000 PYG.")

        # Calcular comisión (0.5%) e impuestos (IVA 10% sobre comisión) y neto a acreditar en cuenta vinculada
        gross_pyg = sim_result['converted_amount']
        commission_amount = (gross_pyg * Decimal('0.005')).quantize(Decimal('0.01'))
        tax_amount = (commission_amount * Decimal('0.10')).quantize(Decimal('0.01'))
        net_credit_pyg = gross_pyg - commission_amount - tax_amount

        # Acreditar fondos en la cuenta bancaria / billetera digital vinculada del cliente
        linked_account.balance += net_credit_pyg
        linked_account.save()

        # Medir tiempo de respuesta (Criterio: < 5 segundos)
        elapsed_time = time.time() - start_time
        processing_time_ms = int(elapsed_time * 1000)

        # Obtener cliente activo y actualizar su volumen transaccional
        cliente = get_active_client(user, request)
        if not cliente and user and user.is_authenticated:
            profile = getattr(user, 'profile', None)
            if profile and profile.keycloak_id:
                rel = UsuarioClienteRelacion.objects.filter(keycloak_user_id=profile.keycloak_id).select_related('cliente').first()
                if rel:
                    cliente = rel.cliente

        if not cliente:
            raise ValidationError("Debe seleccionar un cliente activo para realizar la operación de venta de divisas.")

        c_vol = cliente.transaction_volume if isinstance(cliente.transaction_volume, Decimal) else Decimal(str(cliente.transaction_volume or 0))
        cliente.transaction_volume = c_vol + eval_amount_pyg
        cliente.save()

        # Registrar transacción de venta
        transaction = CurrencySaleTransaction.objects.create(
            user=user if user and user.is_authenticated else None,
            cliente=cliente,
            from_currency=from_currency,
            to_currency=to_currency,
            amount=amount_dec,
            converted_amount=gross_pyg,
            applied_rate=sim_result['applied_rate'],
            standard_rate=sim_result['standard_rate'],
            linked_account=linked_account,
            benefit_percentage=sim_result['benefit_percentage'],
            commission_amount=commission_amount,
            tax_amount=tax_amount,
            total_pyg=net_credit_pyg,
            status='SUCCESS',
            processing_time_ms=processing_time_ms,
            transparent_breakdown=f"{sim_result['transparent_breakdown']} | Comisión: ₲ {commission_amount:,.2f} | Impuestos: ₲ {tax_amount:,.2f} | Neto Acreditado: ₲ {net_credit_pyg:,.2f} | Tiempo: {processing_time_ms}ms"
        )

        return transaction


@login_required
def currency_sale_view(request):
    """
    Vista web para la Operación de Venta de Divisas (PROCESAMIENTO DE OPERACIONES / PSE-14).
    Permite seleccionar divisa origen, moneda destino (PYG), cuenta/billetera vinculada y monto,
    aplicando la tasa de compra vigente y mostrando el resumen transparente con comisiones e impuestos.
    
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

    success_transaction = None
    error_message = None

    from_currency = request.GET.get('from_currency', 'USD')
    to_currency = request.GET.get('to_currency', 'PYG')
    amount_str = request.GET.get('amount', '100')
    payment_method_code = request.GET.get('payment_method', '')

    if request.method == 'POST':
        from_currency = request.POST.get('from_currency')
        to_currency = request.POST.get('to_currency')
        amount_str = request.POST.get('amount')
        payment_method_code = request.POST.get('payment_method')

        try:
            success_transaction = CurrencySaleService.process_sale(
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
            error_message = f"Error al procesar la venta: {str(e)}"

    active_client = get_active_client(request.user, request=request)
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
        'success_transaction': success_transaction,
        'error_message': error_message,
        'user_profile': user_profile,
        'active_client': active_client,
        'clientes_asociados': clientes_asociados,
        'benefit_percentage': benefit_percentage,
        'category_display': category_display,
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
            p.display_total = p.total_pyg
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
            s.display_total = s.total_pyg
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
        writer.writerow(['ID Operacion', 'Tipo', 'Fecha y Hora', 'Cliente', 'Moneda Origen', 'Moneda Destino', 'Monto Origen', 'Monto Convertido', 'Tasa Aplicada', 'Total (PYG)', 'Estado'])
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
                getattr(tx, 'applied_rate', ''),
                tx.display_total,
                getattr(tx, 'status_display', tx.status)
            ])
        return response

    if export_format == 'pdf':
        context = {
            'transactions': all_transactions,
            'active_client': active_client,
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
    def process_purchase(user, from_currency, to_currency, amount, payment_method_id_or_code, request=None):
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

        # Simular / Calcular conversión para obtener equivalencia en PYG y valores
        sim_result = SimuladorConversionService.simular(
            from_currency=from_currency,
            to_currency=to_currency,
            amount=amount_dec,
            user=user,
            request=request
        )

        # Determinar el monto total expresado en Guaraníes (PYG) para validación de límites y fondos
        eval_amount_pyg = amount_dec
        if from_currency != 'PYG':
            try:
                r_from = ExchangeRate.objects.get(currency_code=from_currency)
                eval_amount_pyg = amount_dec * r_from.buy_rate
            except Exception:
                eval_amount_pyg = sim_result.get('converted_amount', amount_dec)
        else:
            eval_amount_pyg = amount_dec

        # Criterio 1: Validación de Límites (Mínimo 50.000 PYG, Máximo 1.000.000.000 PYG)
        MIN_LIMIT = Decimal('50000.00')
        MAX_LIMIT = Decimal('1000000000.00')

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

        # Calcular comisión (0.5%) e impuestos (IVA 10% sobre comisión)
        commission_amount = (eval_amount_pyg * Decimal('0.005')).quantize(Decimal('0.01'))
        tax_amount = (commission_amount * Decimal('0.10')).quantize(Decimal('0.01'))
        total_cost_pyg = eval_amount_pyg + commission_amount + tax_amount

        if payment_method.balance < total_cost_pyg:
            raise ValidationError(f"Fondos insuficientes en el método de pago '{payment_method.name}'. Saldo disponible: ₲ {payment_method.balance:,.2f}, requerido: ₲ {total_cost_pyg:,.2f}.")

        # Descontar fondos del método de pago
        payment_method.balance -= total_cost_pyg
        payment_method.save()

        # Medir tiempo de respuesta (Criterio 4: < 5 segundos)
        elapsed_time = time.time() - start_time
        processing_time_ms = int(elapsed_time * 1000)

        # Obtener cliente activo y actualizar su volumen transaccional específico
        cliente = get_active_client(user, request)
        if not cliente and user and user.is_authenticated:
            profile = getattr(user, 'profile', None)
            if profile and profile.keycloak_id:
                rel = UsuarioClienteRelacion.objects.filter(keycloak_user_id=profile.keycloak_id).select_related('cliente').first()
                if rel:
                    cliente = rel.cliente

        if not cliente:
            raise ValidationError("Debe seleccionar un cliente activo para realizar la operación de compra de divisas. Los usuarios regulares no pueden operar directamente.")

        c_vol = cliente.transaction_volume if isinstance(cliente.transaction_volume, Decimal) else Decimal(str(cliente.transaction_volume or 0))
        cliente.transaction_volume = c_vol + eval_amount_pyg
        cliente.save()

        # Registrar transacción
        transaction = CurrencyPurchaseTransaction.objects.create(
            user=user if user and user.is_authenticated else None,
            cliente=cliente,
            from_currency=from_currency,
            to_currency=to_currency,
            amount=amount_dec,
            converted_amount=sim_result['converted_amount'],
            applied_rate=sim_result['applied_rate'],
            standard_rate=sim_result['standard_rate'],
            payment_method=payment_method,
            benefit_percentage=sim_result['benefit_percentage'],
            commission_amount=commission_amount,
            tax_amount=tax_amount,
            total_pyg=total_cost_pyg,
            status='SUCCESS',
            processing_time_ms=processing_time_ms,
            transparent_breakdown=f"{sim_result['transparent_breakdown']} | Comisión: ₲ {commission_amount:,.2f} | Impuestos: ₲ {tax_amount:,.2f} | Total Cobrado: ₲ {total_cost_pyg:,.2f} | Tiempo: {processing_time_ms}ms"
        )

        return transaction


@login_required
def currency_purchase_view(request):
    """
    Vista web para la Operación de Compra de Divisas (PROCESAMIENTO DE OPERACIONES / PSE-13).
    Permite seleccionar cuenta origen, destino, divisa, monto y método de pago,
    visualizando el resumen transparente con tasas, comisiones, impuestos y descuentos (VIP 2%, Corporativo 4%).
    
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

    success_transaction = None
    error_message = None

    from_currency = request.GET.get('from_currency', 'PYG')
    to_currency = request.GET.get('to_currency', 'USD')
    amount_str = request.GET.get('amount', '100000')
    payment_method_code = request.GET.get('payment_method', 'TRANSFERENCIA')

    if request.method == 'POST':
        from_currency = request.POST.get('from_currency')
        to_currency = request.POST.get('to_currency')
        amount_str = request.POST.get('amount')
        payment_method_code = request.POST.get('payment_method')

        try:
            success_transaction = CurrencyPurchaseService.process_purchase(
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
            error_message = f"Error al procesar la compra: {str(e)}"

    active_client = get_active_client(request.user, request=request)
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
        'success_transaction': success_transaction,
        'error_message': error_message,
        'user_profile': user_profile,
        'active_client': active_client,
        'clientes_asociados': clientes_asociados,
        'benefit_percentage': benefit_percentage,
        'category_display': category_display,
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
    else:
        transactions = CurrencyPurchaseTransaction.objects.none()

    context = {
        'transactions': transactions,
        'active_client': active_client,
        'now': timezone.now(),
    }
    return render(request, 'procesamiento_operaciones/currency_purchase_history.html', context)
