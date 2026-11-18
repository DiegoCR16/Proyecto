# -*- coding: utf-8 -*-
"""Rutas del módulo de Monitoreo Corporativo (PSE-41: reportes financieros)."""
from django.urls import path

from .views import reporte_financiero_view

app_name = 'monitoreo_corporativo'

urlpatterns = [
    path('reportes-financieros/', reporte_financiero_view, name='reporte_financiero'),
]
