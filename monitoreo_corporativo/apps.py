# -*- coding: utf-8 -*-
from django.apps import AppConfig

class MonitoreoCorporativoConfig(AppConfig):
    """
    Configuración de la aplicación de Monitoreo Corporativo (PSE-25).
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'monitoreo_corporativo'
    verbose_name = 'Monitoreo Corporativo y Parametrización'
