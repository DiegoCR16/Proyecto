# -*- coding: utf-8 -*-
from django.urls import path
from .views import public_rates_view

app_name = 'tasas_cambio'

urlpatterns = [
    path('', public_rates_view, name='rates_board'),
]
