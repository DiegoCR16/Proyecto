# -*- coding: utf-8 -*-
"""Rutas del módulo de Monitoreo Corporativo (PSE-41: reportes financieros; PSE-24: dashboard de ganancias)."""
from django.urls import path

from .views import dashboard_ganancias_api, dashboard_ganancias_view, reporte_financiero_view

app_name = 'monitoreo_corporativo'

urlpatterns = [
    path('reportes-financieros/', reporte_financiero_view, name='reporte_financiero'),
    path('dashboard-ganancias/', dashboard_ganancias_view, name='dashboard_ganancias'),
    path('api/dashboard-ganancias/', dashboard_ganancias_api, name='dashboard_ganancias_api'),
]
