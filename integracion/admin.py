# -*- coding: utf-8 -*-
from django.contrib import admin
from .models import PaymentGatewayLog, WithdrawalRequest


@admin.register(PaymentGatewayLog)
class PaymentGatewayLogAdmin(admin.ModelAdmin):
    """Administración de Logs de Pasarelas de Pago y SIPAP."""
    list_display = ('gateway_name', 'transaction_type', 'reference_code', 'amount', 'currency', 'status', 'timestamp')
    list_filter = ('gateway_name', 'status', 'transaction_type')
    search_fields = ('reference_code', 'gateway_name')
    readonly_fields = ('timestamp', 'request_payload', 'response_payload')


@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):
    """Administración de Solicitudes de Retiro y Acreditación."""
    list_display = ('id', 'cliente', 'channel', 'amount', 'currency', 'status', 'gateway_ref', 'timestamp')
    list_filter = ('channel', 'status')
    search_fields = ('cliente__nombre_o_razon_social', 'gateway_ref', 'destination_detail')
    readonly_fields = ('timestamp', 'gateway_ref')
