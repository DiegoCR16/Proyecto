# -*- coding: utf-8 -*-
from django.shortcuts import render
from django.utils import timezone
from decimal import Decimal
from .models import ExchangeRate

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

    rates = ExchangeRate.objects.all().order_by('id')

    user_profile = None
    benefit_percentage = Decimal('0.00')
    benefit_label = 'Estándar'
    category_display = 'Invitado / Minorista'

    if request.user.is_authenticated:
        try:
            user_profile = request.user.profile
            category = user_profile.category
            if category == 'VIP':
                benefit_percentage = Decimal('2.00')
                benefit_label = '2% (VIP)'
                category_display = 'VIP'
            elif category == 'CORPORATIVO':
                benefit_percentage = Decimal('4.00')
                benefit_label = '4% (Corporativo)'
                category_display = 'Corporativo'
            else:
                category_display = 'Minorista'
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
