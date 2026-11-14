# -*- coding: utf-8 -*-
from django.urls import path
from .views import (
    currency_purchase_view, currency_purchase_history_view,
    currency_sale_view, currency_sale_history_view,
    currency_transactions_history_view
)
from .views_facturacion import admin_facturacion_view, solicitar_anulacion_factura_view

app_name = 'procesamiento_operaciones'

urlpatterns = [
    path('compra/', currency_purchase_view, name='currency_purchase'),
    path('historial-compras/', currency_purchase_history_view, name='currency_purchase_history'),
    path('venta/', currency_sale_view, name='currency_sale'),
    path('historial-ventas/', currency_sale_history_view, name='currency_sale_history'),
    path('historial-operaciones/', currency_transactions_history_view, name='currency_transactions_history'),
    path('facturacion/', admin_facturacion_view, name='admin_facturacion'),
    path('facturacion/solicitar-anulacion/', solicitar_anulacion_factura_view, name='solicitar_anulacion_factura'),
]
