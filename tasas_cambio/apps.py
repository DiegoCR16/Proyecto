# -*- coding: utf-8 -*-
from django.apps import AppConfig

class TasasCambioConfig(AppConfig):
    """
    Configuración de la aplicación de Tasas de Cambio (PSE-9).
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tasas_cambio'
    verbose_name = 'Consulta de Tasas de Cambio'
