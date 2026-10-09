# -*- coding: utf-8 -*-
from django.contrib import admin
from .models import CurrencyPurchaseTransaction, CurrencySaleTransaction


@admin.register(CurrencyPurchaseTransaction)
class CurrencyPurchaseTransactionAdmin(admin.ModelAdmin):
    """Administración de Transacciones de Compra de Divisas con API de Pago."""
    list_display = ('id', 'cliente', 'from_currency', 'to_currency', 'amount', 'total_pyg', 'payment_method_type', 'gateway_reference', 'gateway_status', 'status', 'timestamp')
    list_filter = ('status', 'from_currency', 'to_currency', 'payment_method_type', 'gateway_status')
    search_fields = ('id', 'cliente__nombre_o_razon_social', 'gateway_reference')
    readonly_fields = ('timestamp', 'processing_time_ms', 'transparent_breakdown', 'gateway_reference', 'gateway_status')


@admin.register(CurrencySaleTransaction)
class CurrencySaleTransactionAdmin(admin.ModelAdmin):
    """Administración de Transacciones de Venta de Divisas con API de Pago."""
    list_display = ('id', 'cliente', 'from_currency', 'to_currency', 'amount', 'total_pyg', 'payment_method_type', 'gateway_reference', 'gateway_status', 'status', 'timestamp')
    list_filter = ('status', 'from_currency', 'to_currency', 'payment_method_type', 'gateway_status')
    search_fields = ('id', 'cliente__nombre_o_razon_social', 'gateway_reference')
    readonly_fields = ('timestamp', 'processing_time_ms', 'transparent_breakdown', 'gateway_reference', 'gateway_status')
