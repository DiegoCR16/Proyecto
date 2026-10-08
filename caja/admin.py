# -*- coding: utf-8 -*-
from django.contrib import admin
from .models import (
    Caja, TurnoCaja, DenominacionDivisa, DesgloseEfectivoCaja, 
    DetalleDesgloseBillete, ArqueoCaja, BitacoraArqueo
)

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


@admin.register(DenominacionDivisa)
class DenominacionDivisaAdmin(admin.ModelAdmin):
    """
    Administración de Denominaciones de Divisas (PSE-21).
    """
    list_display = ('divisa', 'valor_facial', 'nombre', 'activa')
    list_filter = ('divisa', 'activa')
    search_fields = ('divisa', 'nombre')


@admin.register(DesgloseEfectivoCaja)
class DesgloseEfectivoCajaAdmin(admin.ModelAdmin):
    """
    Administración de Desgloses de Efectivo de Caja (PSE-21).
    """
    list_display = ('id', 'turno', 'tipo_operacion', 'divisa', 'monto_total', 'fecha')
    list_filter = ('tipo_operacion', 'divisa', 'fecha')
    search_fields = ('turno__caja__nombre', 'turno__cajero__username', 'observacion')


@admin.register(DetalleDesgloseBillete)
class DetalleDesgloseBilleteAdmin(admin.ModelAdmin):
    """
    Administración de Detalles de Billetes en Desgloses (PSE-21).
    """
    list_display = ('id', 'desglose', 'denominacion', 'cantidad', 'subtotal')
    list_filter = ('denominacion__divisa',)
    search_fields = ('denominacion__nombre', 'desglose__turno__caja__nombre')


@admin.register(ArqueoCaja)
class ArqueoCajaAdmin(admin.ModelAdmin):
    """
    Administración de Arqueos de Caja Automáticos al Cierre (PSE-22).
    """
    list_display = ('id', 'turno', 'divisa', 'saldo_teorico', 'saldo_fisico_cierre', 'diferencia', 'estado_arqueo', 'fecha')
    list_filter = ('estado_arqueo', 'divisa', 'fecha')
    search_fields = ('turno__caja__nombre', 'turno__cajero__username')


@admin.register(BitacoraArqueo)
class BitacoraArqueoAdmin(admin.ModelAdmin):
    """
    Administración de Bitácora Histórica y Alertas de Arqueo (PSE-22).
    """
    list_display = ('id', 'turno', 'divisa', 'tipo_descuadre', 'monto_diferencia', 'administrador_notificado', 'fecha')
    list_filter = ('tipo_descuadre', 'divisa', 'administrador_notificado', 'fecha')
    search_fields = ('turno__caja__nombre', 'mensaje')

