# -*- coding: utf-8 -*-
from django.apps import AppConfig


class CajaConfig(AppConfig):
    """
    Configuración de la aplicación Caja para la gestión de aperturas y cierres de turnos.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'caja'
    verbose_name = 'Gestión de Turnos de Caja'
