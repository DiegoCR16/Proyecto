# -*- coding: utf-8 -*-
from django.urls import path
from .views import (
    public_rates_view, currency_simulator_view, rates_evolution_view,
    rates_evolution_api, client_benefit_config_view, currency_payment_config_view,
    rates_manager_view
)

app_name = 'tasas_cambio'

urlpatterns = [
    path('', public_rates_view, name='rates_board'),
    path('simulador/', currency_simulator_view, name='simulator'),
    path('evolucion/', rates_evolution_view, name='rates_evolution'),
    path('api/evolucion/', rates_evolution_api, name='rates_evolution_api'),
    path('configuracion-beneficios/', client_benefit_config_view, name='client_benefit_config'),
    path('parametrizacion-divisas-pagos/', currency_payment_config_view, name='currency_payment_config'),
    path('gestor-cotizaciones/', rates_manager_view, name='rates_manager'),
]
