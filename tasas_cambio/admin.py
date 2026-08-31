# -*- coding: utf-8 -*-
from django.contrib import admin
from .models import ExchangeRate

@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    """
    Panel de administración para las tasas de cambio de Global Exchange.
    """
    list_display = ('currency_code', 'currency_name', 'buy_rate', 'sell_rate', 'last_updated')
    search_fields = ('currency_code', 'currency_name')
