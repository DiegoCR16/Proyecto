# -*- coding: utf-8 -*-
from django.contrib import admin
from .models import Caja, TurnoCaja

@admin.register(Caja)
class CajaAdmin(admin.ModelAdmin):
    """
    Administración de Cajas Físicas en Django Admin.
    """
    list_display = ('nombre', 'codigo', 'activa')
    search_fields = ('nombre', 'codigo')
    list_filter = ('activa', )


@admin.register(TurnoCaja)
class TurnoCajaAdmin(admin.ModelAdmin):
    """
    Administración de Turnos de Caja en Django Admin.
    """
    list_display = ('id', 'caja', 'cajero', 'fecha_apertura', 'fecha_cierre', 'estado')
    list_filter = ('estado', 'caja', 'fecha_apertura')
    search_fields = ('caja__nombre', 'cajero__username')
