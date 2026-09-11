# -*- coding: utf-8 -*-
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.contrib.auth.decorators import login_required
from decimal import Decimal
from datetime import timedelta
import random
from authentication.models import UserProfile
from .models import ExchangeRate, ExchangeRateHistory, ClientBenefitRule, PaymentMethod

def ensure_default_benefit_rules():
    """
    Asegura que existan las reglas predeterminadas de beneficios por categoría en la base de datos (PSE-29).
    """
    ClientBenefitRule.objects.get_or_create(
        category_code='MINORISTA',
        defaults={
            'category_name': 'Minorista',
            'min_operation_amount': Decimal('0.00'),
            'benefit_percentage': Decimal('0.00'),
            'description': 'Clientes individuales de bajo volumen (sin beneficio / condiciones estándar).'
        }
    )
    ClientBenefitRule.objects.get_or_create(
        category_code='VIP',
        defaults={
            'category_name': 'VIP',
            'min_operation_amount': Decimal('0.00'),
            'benefit_percentage': Decimal('2.00'),
            'description': 'Clientes con operaciones superiores a 50.000.000 PYG (2% de beneficio en compra de divisas).'
        }
    )
    ClientBenefitRule.objects.get_or_create(
        category_code='CORPORATIVO',
        defaults={
            'category_name': 'Corporativo',
            'min_operation_amount': Decimal('0.00'),
            'benefit_percentage': Decimal('4.00'),
            'description': 'Clientes con operaciones superiores a 100.000.000 PYG (4% de beneficio en compra de divisas).'
        }
    )


def public_rates_view(request):
    """
    Vista pública y en tiempo real de las tasas de cambio (PSE-9).
    Muestra las divisas predeterminadas (USD, EUR, BRL, ARS, PYG).
    Si el usuario está autenticado, recupera su perfil y calcula automáticamente
    las tasas personalizadas con los beneficios aplicados (2% para VIP, 4% para CORPORATIVO).
    
    Args:
        request (HttpRequest): Solicitud HTTP del cliente (invitado o autenticado).
        
    Returns:
        HttpResponse: Página renderizada con la pizarra de cotizaciones y beneficios.
    """
    # Asegurar que existan las 5 divisas predeterminadas
    default_rates = [
        ('USD', 'Dólar Estadounidense', Decimal('7300.0000'), Decimal('7450.0000')),
        ('EUR', 'Euro', Decimal('7900.0000'), Decimal('8150.0000')),
        ('BRL', 'Real Brasileño', Decimal('1350.0000'), Decimal('1450.0000')),
        ('ARS', 'Peso Argentino', Decimal('7.5000'), Decimal('9.0000')),
        ('PYG', 'Guaraní Paraguayo', Decimal('1.0000'), Decimal('1.0000')),
    ]

    for code, name, buy, sell in default_rates:
        ExchangeRate.objects.get_or_create(
            currency_code=code,
            defaults={
                'currency_name': name,
                'buy_rate': buy,
                'sell_rate': sell
            }
        )

    ensure_default_benefit_rules()
    rates = ExchangeRate.objects.all().order_by('id')

    user_profile = None
    benefit_percentage = Decimal('0.00')
    benefit_label = 'Estándar'
    category_display = 'Invitado / Minorista'

    if request.user.is_authenticated:
        try:
            user_profile = request.user.profile
            category = user_profile.category
            rule = ClientBenefitRule.objects.filter(category_code=category).first()
            if rule:
                benefit_percentage = rule.benefit_percentage
                benefit_label = f"{benefit_percentage}% ({rule.category_name})"
                category_display = rule.category_name
        except Exception:
            pass

    # Calcular tasas personalizadas si aplica beneficio (> 0)
    personalized_rates = []
    for rate in rates:
        if benefit_percentage > 0 and rate.currency_code != 'PYG':
            factor = Decimal('1.00') - (benefit_percentage / Decimal('100.00'))
            custom_sell = (rate.sell_rate * factor).quantize(Decimal('0.0001'))
            custom_buy = (rate.buy_rate / factor).quantize(Decimal('0.0001'))
        else:
            custom_sell = rate.sell_rate
            custom_buy = rate.buy_rate

        personalized_rates.append({
            'currency_code': rate.currency_code,
            'currency_name': rate.currency_name,
            'standard_buy': rate.buy_rate,
            'standard_sell': rate.sell_rate,
            'custom_buy': custom_buy,
            'custom_sell': custom_sell,
            'last_updated': rate.last_updated,
        })

    context = {
        'rates': personalized_rates,
        'user_profile': user_profile,
        'benefit_percentage': benefit_percentage,
        'benefit_label': benefit_label,
        'category_display': category_display,
        'now': timezone.now(),
    }

    return render(request, 'tasas_cambio/rates_board.html', context)


class SimuladorConversionService:
    """
    Servicio de dominio para la simulación de conversión de divisas (PSE-11 / PSE-29).
    Calcula conversiones exactas aplicando tipos de cambio vigentes y beneficios dinámicos por categoría y umbrales.
    """

    @staticmethod
    def simular(from_currency, to_currency, amount, user=None):
        """
        Simula una conversión monetaria entre dos divisas aplicando reglas de beneficio parametrizables.

        Args:
            from_currency (str): Código ISO de la moneda de origen (ej. 'USD', 'PYG').
            to_currency (str): Código ISO de la moneda de destino (ej. 'PYG', 'EUR').
            amount (Decimal or float or int): Monto a convertir.
            user (User, optional): Usuario solicitante para determinar categoría y beneficios (VIP, Corporativo).

        Returns:
            dict: Diccionario con el resultado detallado de la simulación y desglose transparente.

        Raises:
            ValidationError: Si faltan campos obligatorios, el monto es inválido o no existe la tasa de cambio.
        """
        if not from_currency or not to_currency or amount is None:
            raise ValidationError("Los campos moneda origen, moneda destino y monto son obligatorios.")

        try:
            amount_dec = Decimal(str(amount))
        except (ValueError, TypeError):
            raise ValidationError("El monto ingresado debe ser un valor numérico válido.")

        if amount_dec <= Decimal('0.00'):
            raise ValidationError("El monto de la simulación debe ser mayor a cero.")

        ensure_default_benefit_rules()
        benefit_percentage = Decimal('0.00')
        category_name = 'Invitado / Minorista'
        threshold_met = False
        min_operation_amount = Decimal('0.00')

        if user and user.is_authenticated:
            try:
                profile = user.profile
                cat_code = profile.category
                rule = ClientBenefitRule.objects.filter(category_code=cat_code).first()
                if rule:
                    category_name = rule.category_name
                    min_operation_amount = rule.min_operation_amount

                    # Evaluar equivalencia en PYG para umbral transaccional
                    eval_amount = amount_dec
                    if from_currency != 'PYG':
                        try:
                            r_from = ExchangeRate.objects.get(currency_code=from_currency)
                            eval_amount = amount_dec * r_from.buy_rate
                        except Exception:
                            pass

                    if eval_amount >= min_operation_amount:
                        benefit_percentage = rule.benefit_percentage
                        threshold_met = True
                    else:
                        benefit_percentage = Decimal('0.00')
                        threshold_met = False
            except Exception:
                pass

        factor = Decimal('1.00') - (benefit_percentage / Decimal('100.00'))
        transparent_breakdown = f"Categoría: {category_name} | Umbral Mínimo: ₲ {min_operation_amount:,.2f} | Beneficio: {benefit_percentage}% {'(Aplicado)' if threshold_met else '(No alcanza umbral)'}"

        if from_currency == to_currency:
            return {
                'from_currency': from_currency,
                'to_currency': to_currency,
                'amount': amount_dec,
                'converted_amount': amount_dec,
                'applied_rate': Decimal('1.0000'),
                'standard_rate': Decimal('1.0000'),
                'operation_type': 'IDÉNTICA',
                'benefit_percentage': benefit_percentage,
                'category_name': category_name,
                'threshold_met': threshold_met,
                'min_operation_amount': min_operation_amount,
                'transparent_breakdown': transparent_breakdown,
            }

        if from_currency == 'PYG':
            try:
                rate_obj = ExchangeRate.objects.get(currency_code=to_currency)
            except ExchangeRate.DoesNotExist:
                raise ValidationError(f"El sistema no cuenta con la tasa de cambio registrada para la divisa: {to_currency}")

            standard_rate = rate_obj.sell_rate
            if standard_rate <= 0:
                raise ValidationError(f"La tasa de venta para {to_currency} no se encuentra disponible.")

            custom_rate = (standard_rate * factor).quantize(Decimal('0.0001'))
            converted_amount = (amount_dec / custom_rate).quantize(Decimal('0.01'))

            return {
                'from_currency': from_currency,
                'to_currency': to_currency,
                'amount': amount_dec,
                'converted_amount': converted_amount,
                'applied_rate': custom_rate,
                'standard_rate': standard_rate,
                'operation_type': 'COMPRA',
                'benefit_percentage': benefit_percentage,
                'category_name': category_name,
                'threshold_met': threshold_met,
                'min_operation_amount': min_operation_amount,
                'transparent_breakdown': transparent_breakdown,
            }

        elif to_currency == 'PYG':
            try:
                rate_obj = ExchangeRate.objects.get(currency_code=from_currency)
            except ExchangeRate.DoesNotExist:
                raise ValidationError(f"El sistema no cuenta con la tasa de cambio registrada para la divisa: {from_currency}")

            standard_rate = rate_obj.buy_rate
            if standard_rate <= 0:
                raise ValidationError(f"La tasa de compra para {from_currency} no se encuentra disponible.")

            if benefit_percentage > 0:
                custom_rate = (standard_rate / factor).quantize(Decimal('0.0001'))
            else:
                custom_rate = standard_rate

            converted_amount = (amount_dec * custom_rate).quantize(Decimal('0.01'))

            return {
                'from_currency': from_currency,
                'to_currency': to_currency,
                'amount': amount_dec,
                'converted_amount': converted_amount,
                'applied_rate': custom_rate,
                'standard_rate': standard_rate,
                'operation_type': 'VENTA',
                'benefit_percentage': benefit_percentage,
                'category_name': category_name,
                'threshold_met': threshold_met,
                'min_operation_amount': min_operation_amount,
                'transparent_breakdown': transparent_breakdown,
            }

        else:
            try:
                rate_from = ExchangeRate.objects.get(currency_code=from_currency)
            except ExchangeRate.DoesNotExist:
                raise ValidationError(f"El sistema no cuenta con la tasa de cambio registrada para la divisa: {from_currency}")

            try:
                rate_to = ExchangeRate.objects.get(currency_code=to_currency)
            except ExchangeRate.DoesNotExist:
                raise ValidationError(f"El sistema no cuenta con la tasa de cambio registrada para la divisa: {to_currency}")

            std_buy = rate_from.buy_rate
            std_sell = rate_to.sell_rate
            if std_buy <= 0 or std_sell <= 0:
                raise ValidationError("Tasas de cambio inválidas para la conversión cruzada.")

            if benefit_percentage > 0:
                custom_buy = (std_buy / factor).quantize(Decimal('0.0001'))
                custom_sell = (std_sell * factor).quantize(Decimal('0.0001'))
            else:
                custom_buy = std_buy
                custom_sell = std_sell

            pyg_amount = amount_dec * custom_buy
            converted_amount = (pyg_amount / custom_sell).quantize(Decimal('0.01'))
            effective_rate = (custom_buy / custom_sell).quantize(Decimal('0.0004'))

            return {
                'from_currency': from_currency,
                'to_currency': to_currency,
                'amount': amount_dec,
                'converted_amount': converted_amount,
                'applied_rate': effective_rate,
                'standard_rate': std_sell,
                'operation_type': 'CRUZADA',
                'benefit_percentage': benefit_percentage,
                'category_name': category_name,
                'threshold_met': threshold_met,
                'min_operation_amount': min_operation_amount,
                'transparent_breakdown': transparent_breakdown,
            }


def currency_simulator_view(request):
    """
    Vista web para el simulador de conversión de divisas (PSE-11).
    Permite ingresar montos, seleccionar monedas de origen y destino, y visualizar cotizaciones exactas.
    """
    default_rates = [
        ('USD', 'Dólar Estadounidense', Decimal('7300.0000'), Decimal('7450.0000')),
        ('EUR', 'Euro', Decimal('7900.0000'), Decimal('8150.0000')),
        ('BRL', 'Real Brasileño', Decimal('1350.0000'), Decimal('1450.0000')),
        ('ARS', 'Peso Argentino', Decimal('7.5000'), Decimal('9.0000')),
        ('PYG', 'Guaraní Paraguayo', Decimal('1.0000'), Decimal('1.0000')),
    ]
    for code, name, buy, sell in default_rates:
        ExchangeRate.objects.get_or_create(
            currency_code=code,
            defaults={'currency_name': name, 'buy_rate': buy, 'sell_rate': sell}
        )

    currencies = ExchangeRate.objects.all().order_by('currency_code')
    simulation_result = None
    error_message = None

    from_currency = request.GET.get('from_currency', 'PYG')
    to_currency = request.GET.get('to_currency', 'USD')
    amount_str = request.GET.get('amount', '100000')

    if request.method == 'POST':
        from_currency = request.POST.get('from_currency')
        to_currency = request.POST.get('to_currency')
        amount_str = request.POST.get('amount')

        try:
            if not from_currency or not to_currency or not amount_str:
                raise ValidationError("Todos los campos (moneda origen, moneda destino y monto) son obligatorios.")
            
            simulation_result = SimuladorConversionService.simular(
                from_currency=from_currency,
                to_currency=to_currency,
                amount=amount_str,
                user=request.user
            )
        except ValidationError as e:
            error_message = e.messages[0] if hasattr(e, 'messages') else str(e)
        except Exception as e:
            error_message = f"Error en la simulación: {str(e)}"
    else:
        if request.GET.get('amount'):
            try:
                simulation_result = SimuladorConversionService.simular(
                    from_currency=from_currency,
                    to_currency=to_currency,
                    amount=amount_str,
                    user=request.user
                )
            except ValidationError as e:
                error_message = e.messages[0] if hasattr(e, 'messages') else str(e)
            except Exception:
                pass

    ensure_default_benefit_rules()
    user_profile = None
    benefit_percentage = Decimal('0.00')
    category_display = 'Invitado / Minorista'
    if request.user.is_authenticated:
        try:
            user_profile = request.user.profile
            rule = ClientBenefitRule.objects.filter(category_code=user_profile.category).first()
            if rule:
                benefit_percentage = rule.benefit_percentage
                category_display = rule.category_name
        except Exception:
            pass

    rates = ExchangeRate.objects.all().order_by('id')
    personalized_rates = []
    for rate in rates:
        if benefit_percentage > 0 and rate.currency_code != 'PYG':
            factor = Decimal('1.00') - (benefit_percentage / Decimal('100.00'))
            custom_sell = (rate.sell_rate * factor).quantize(Decimal('0.0001'))
            custom_buy = (rate.buy_rate / factor).quantize(Decimal('0.0001'))
        else:
            custom_sell = rate.sell_rate
            custom_buy = rate.buy_rate

        personalized_rates.append({
            'currency_code': rate.currency_code,
            'currency_name': rate.currency_name,
            'standard_buy': rate.buy_rate,
            'standard_sell': rate.sell_rate,
            'custom_buy': custom_buy,
            'custom_sell': custom_sell,
            'last_updated': rate.last_updated,
        })

    context = {
        'currencies': currencies,
        'rates': personalized_rates,
        'from_currency': from_currency,
        'to_currency': to_currency,
        'amount': amount_str,
        'simulation_result': simulation_result,
        'error_message': error_message,
        'user_profile': user_profile,
        'benefit_percentage': benefit_percentage,
        'category_display': category_display,
        'now': timezone.now(),
    }

    return render(request, 'tasas_cambio/simulator.html', context)


def populate_mock_history():
    """
    Pobla datos históricos simulados de tasas de cambio si la tabla ExchangeRateHistory está vacía.
    Genera registros diarios para el último año para las principales divisas (USD, EUR, BRL, ARS).
    """
    if ExchangeRateHistory.objects.exists():
        return

    base_rates = {
        'USD': (Decimal('7300.0000'), Decimal('7450.0000')),
        'EUR': (Decimal('7900.0000'), Decimal('8150.0000')),
        'BRL': (Decimal('1350.0000'), Decimal('1450.0000')),
        'ARS': (Decimal('7.5000'), Decimal('9.0000')),
        'PYG': (Decimal('1.0000'), Decimal('1.0000')),
    }

    now = timezone.now()
    random.seed(42)  # Semilla fija para consistencia en pruebas
    for code, (base_buy, base_sell) in base_rates.items():
        current_buy = base_buy
        current_sell = base_sell
        for i in range(365, -1, -1):
            day_time = now - timedelta(days=i)
            if code != 'PYG':
                var_buy = float(current_buy) * random.uniform(-0.005, 0.005)
                var_sell = float(current_sell) * random.uniform(-0.005, 0.005)
                current_buy = (current_buy + Decimal(str(var_buy))).quantize(Decimal('0.0001'))
                current_sell = (current_sell + Decimal(str(var_sell))).quantize(Decimal('0.0001'))

            ExchangeRateHistory.objects.create(
                currency_code=code,
                buy_rate=current_buy,
                sell_rate=current_sell,
                timestamp=day_time
            )


def rates_evolution_api(request):
    """
    API JSON para obtener el historial de evolución de tasas de cambio (PSE-10).
    
    Args:
        request (HttpRequest): Solicitud GET con parámetros 'currency' (ej. USD) y 'range' (1D, 7D, 1M, 1Y).
        
    Returns:
        JsonResponse: Datos estructurados con etiquetas, tasas de compra, venta, estado y mensajes.
    """
    populate_mock_history()

    currency = request.GET.get('currency', 'USD').upper()
    time_range = request.GET.get('range', '30D').upper()

    now = timezone.now()
    
    if time_range == '1D':
        start_date = now - timedelta(days=1)
    elif time_range == '7D':
        start_date = now - timedelta(days=7)
    elif time_range == '1M' or time_range == '30D':
        start_date = now - timedelta(days=30)
    elif time_range == '1Y':
        start_date = now - timedelta(days=365)
    else:
        start_date = now - timedelta(days=30)

    history_qs = ExchangeRateHistory.objects.filter(
        currency_code=currency,
        timestamp__gte=start_date
    ).order_by('timestamp')

    if not history_qs.exists():
        return JsonResponse({
            'has_data': False,
            'currency': currency,
            'range': time_range,
            'message': f'No se registran datos históricos suficientes para la divisa {currency} en el rango seleccionado ({time_range}).',
            'labels': [],
            'buy_rates': [],
            'sell_rates': [],
        })

    labels = []
    buy_rates = []
    sell_rates = []

    for record in history_qs:
        if time_range == '1D':
            label = record.timestamp.strftime('%H:%M')
        elif time_range in ['7D', '1M', '30D']:
            label = record.timestamp.strftime('%d/%m/%Y')
        else:
            label = record.timestamp.strftime('%m/%Y')

        labels.append(label)
        buy_rates.append(float(record.buy_rate))
        sell_rates.append(float(record.sell_rate))

    return JsonResponse({
        'has_data': True,
        'currency': currency,
        'range': time_range,
        'message': '',
        'labels': labels,
        'buy_rates': buy_rates,
        'sell_rates': sell_rates,
    })


def rates_evolution_view(request):
    """
    Vista web para el gráfico interactivo de evolución de tasas de cambio (PSE-10).
    Renderiza la interfaz con panel de filtros y soporte para Chart.js.
    
    Args:
        request (HttpRequest): Solicitud HTTP del cliente.
        
    Returns:
        HttpResponse: Página renderizada del módulo de analítica y gráficos.
    """
    populate_mock_history()
    
    currencies = ExchangeRate.objects.all().order_by('currency_code')
    
    context = {
        'currencies': currencies,
        'selected_currency': request.GET.get('currency', 'USD'),
        'selected_range': request.GET.get('range', '30D'),
        'now': timezone.now(),
    }
    return render(request, 'tasas_cambio/rates_evolution.html', context)


@login_required
def client_benefit_config_view(request):
    """
    Vista del panel de administración para configurar dinámicamente los porcentajes de beneficio
    y umbrales por categoría de cliente (PSE-29).
    
    Args:
        request (HttpRequest): Solicitud HTTP del administrador.
        
    Returns:
        HttpResponse: Página renderizada del panel de configuración de beneficios.
    """
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not (request.user.is_superuser or (profile.role and profile.role.name.lower() in ['admin', 'administrador'])):
        return redirect('tasas_cambio:rates_board')

    ensure_default_benefit_rules()
    success_message = None
    error_message = None

    if request.method == 'POST':
        try:
            for rule in ClientBenefitRule.objects.all():
                min_amt_str = request.POST.get(f'min_amount_{rule.category_code}')
                benefit_str = request.POST.get(f'benefit_{rule.category_code}')
                if min_amt_str is not None and benefit_str is not None:
                    rule.min_operation_amount = Decimal(min_amt_str)
                    rule.benefit_percentage = Decimal(benefit_str)
                    rule.save()
            success_message = "Reglas de beneficio y umbrales actualizados exitosamente sin modificar código fuente."
        except Exception as e:
            error_message = f"Error al actualizar las reglas: {str(e)}"

    rules = ClientBenefitRule.objects.all().order_by('id')
    context = {
        'rules': rules,
        'success_message': success_message,
        'error_message': error_message,
        'now': timezone.now(),
    }
    return render(request, 'tasas_cambio/client_benefit_config.html', context)


def ensure_default_payment_methods():
    """
    Asegura que existan los métodos de pago predeterminados en la base de datos (PSE-25).
    """
    PaymentMethod.objects.get_or_create(
        code='TRANSFERENCIA',
        defaults={
            'name': 'Transferencia Bancaria',
            'description': 'Transferencia directa entre cuentas bancarias autorizadas.',
            'is_active': True
        }
    )
    PaymentMethod.objects.get_or_create(
        code='TARJETA',
        defaults={
            'name': 'Tarjeta de Crédito / Débito',
            'description': 'Cobro mediante pasarela de tarjetas Visa, Mastercard, etc.',
            'is_active': True
        }
    )
    PaymentMethod.objects.get_or_create(
        code='BILLETERA',
        defaults={
            'name': 'Billeteras Electrónicas',
            'description': 'Pagos a través de billeteras móviles (Zimple, Tigo Money, etc.).',
            'is_active': True
        }
    )
    PaymentMethod.objects.get_or_create(
        code='EFECTIVO',
        defaults={
            'name': 'Efectivo en Sucursal',
            'description': 'Pago presencial en ventanilla de caja.',
            'is_active': True
        }
    )


@login_required
def currency_payment_config_view(request):
    """
    Vista de administración con funcionalidad CRUD completa para Divisas y Métodos de Pago (PSE-25).
    
    Args:
        request (HttpRequest): Solicitud HTTP del administrador.
        
    Returns:
        HttpResponse: Página renderizada del panel CRUD de divisas y métodos de pago.
    """
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not (request.user.is_superuser or (profile.role and profile.role.name.lower() in ['admin', 'administrador'])):
        return redirect('tasas_cambio:rates_board')

    ensure_default_payment_methods()
    
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

    success_message = None
    error_message = None

    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'add_currency':
                code = request.POST.get('currency_code', '').strip().upper()
                name = request.POST.get('currency_name', '').strip()
                symbol = request.POST.get('currency_symbol', '').strip()
                buy_str = request.POST.get('buy_rate', '').strip()
                sell_str = request.POST.get('sell_rate', '').strip()

                if not code or not name or not symbol or not buy_str or not sell_str:
                    raise ValidationError("Todos los campos de la divisa (código, nombre, símbolo, compra y venta) son obligatorios.")
                
                buy_val = Decimal(buy_str)
                sell_val = Decimal(sell_str)
                if buy_val <= 0 or sell_val <= 0:
                    raise ValidationError("Las tasas de compra y venta deben ser mayores a cero.")

                ExchangeRate.objects.update_or_create(
                    currency_code=code,
                    defaults={
                        'currency_name': name,
                        'symbol': symbol,
                        'buy_rate': buy_val,
                        'sell_rate': sell_val
                    }
                )
                success_message = f"Divisa {code} ({name}) guardada / actualizada exitosamente."

            elif action == 'delete_currency':
                code = request.POST.get('currency_code')
                if code == 'PYG':
                    raise ValidationError("No se puede eliminar la divisa base del sistema (PYG).")
                ExchangeRate.objects.filter(currency_code=code).delete()
                success_message = f"Divisa {code} eliminada exitosamente."

            elif action == 'add_payment_method':
                code = request.POST.get('pm_code', '').strip().upper()
                name = request.POST.get('pm_name', '').strip()
                description = request.POST.get('pm_description', '').strip()
                is_active = request.POST.get('pm_is_active') == 'on'

                if not code or not name:
                    raise ValidationError("Código y nombre del método de pago son obligatorios.")

                PaymentMethod.objects.update_or_create(
                    code=code,
                    defaults={
                        'name': name,
                        'description': description,
                        'is_active': is_active
                    }
                )
                success_message = f"Método de pago '{name}' guardado / actualizado exitosamente."

            elif action == 'toggle_payment_method':
                pm_code = request.POST.get('payment_method_code')
                pm = PaymentMethod.objects.get(code=pm_code)
                pm.is_active = not pm.is_active
                pm.save()
                success_message = f"Estado del método de pago '{pm.name}' actualizado a {'Habilitado' if pm.is_active else 'Deshabilitado'}."

            elif action == 'delete_payment_method':
                pm_code = request.POST.get('payment_method_code')
                PaymentMethod.objects.filter(code=pm_code).delete()
                success_message = "Método de pago eliminado exitosamente."

        except ValidationError as e:
            error_message = e.messages[0] if hasattr(e, 'messages') else str(e)
        except Exception as e:
            error_message = f"Error en la operación: {str(e)}"

    currencies = ExchangeRate.objects.all().order_by('currency_code')
    payment_methods = PaymentMethod.objects.all().order_by('id')

    context = {
        'currencies': currencies,
        'payment_methods': payment_methods,
        'success_message': success_message,
        'error_message': error_message,
        'now': timezone.now(),
    }
    return render(request, 'tasas_cambio/currency_payment_config.html', context)
