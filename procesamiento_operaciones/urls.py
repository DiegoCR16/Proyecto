# -*- coding: utf-8 -*-
from django.urls import path
from .views import (
    currency_purchase_view, currency_purchase_history_view,
    currency_sale_view, currency_sale_history_view,
    currency_transactions_history_view
)

app_name = 'procesamiento_operaciones'

urlpatterns = [
    path('compra/', currency_purchase_view, name='currency_purchase'),
    path('historial-compras/', currency_purchase_history_view, name='currency_purchase_history'),
    path('venta/', currency_sale_view, name='currency_sale'),
    path('historial-ventas/', currency_sale_history_view, name='currency_sale_history'),
    path('historial-operaciones/', currency_transactions_history_view, name='currency_transactions_history'),
]
