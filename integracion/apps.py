# -*- coding: utf-8 -*-
from django.apps import AppConfig


class IntegracionConfig(AppConfig):
    """
    Configuración de la aplicación Django para la épica de Integración y Redes de Pago (PSE-18).
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'integracion'
    verbose_name = 'Integración con Redes de Pago y SIPAP'
