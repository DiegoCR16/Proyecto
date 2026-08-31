# -*- coding: utf-8 -*-
from django.db import models
from decimal import Decimal

class ExchangeRate(models.Model):
    """
    Modelo que representa la tasa de cambio vigente para una divisa autorizada.
    
    Attributes:
        currency_code (CharField): Código ISO de la divisa (USD, EUR, BRL, ARS, PYG).
        currency_name (CharField): Nombre descriptivo de la divisa.
        buy_rate (DecimalField): Tasa de compra estándar en Guaraníes (Gs).
        sell_rate (DecimalField): Tasa de venta estándar en Guaraníes (Gs).
        last_updated (DateTimeField): Fecha y hora de última actualización en tiempo real.
    """
    currency_code = models.CharField(max_length=10, unique=True, verbose_name="Código de Divisa")
    currency_name = models.CharField(max_length=100, verbose_name="Nombre de Divisa")
    buy_rate = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('0.0000'), verbose_name="Tasa de Compra (Gs)")
    sell_rate = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('0.0000'), verbose_name="Tasa de Venta (Gs)")
    last_updated = models.DateTimeField(auto_now=True, verbose_name="Última Actualización")

    def __str__(self):
        """Devuelve la representación en cadena de la tasa de cambio."""
        return f"{self.currency_code} - Compra: {self.buy_rate} | Venta: {self.sell_rate}"
