# -*- coding: utf-8 -*-
from django.urls import path
from .views import currency_purchase_view, currency_purchase_history_view

app_name = 'procesamiento_operaciones'

urlpatterns = [
    path('compra/', currency_purchase_view, name='currency_purchase'),
    path('historial-compras/', currency_purchase_history_view, name='currency_purchase_history'),
]
