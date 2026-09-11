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


class ExchangeRateHistory(models.Model):
    """
    Modelo que representa el registro histórico de las tasas de cambio de una divisa.
    Se utiliza para almacenar las variaciones en el tiempo y permitir graficar su evolución.
    
    Attributes:
        currency_code (CharField): Código ISO de la divisa (USD, EUR, BRL, ARS, PYG).
        buy_rate (DecimalField): Tasa de compra histórica en Guaraníes (Gs).
        sell_rate (DecimalField): Tasa de venta histórica en Guaraníes (Gs).
        timestamp (DateTimeField): Fecha y hora del registro histórico.
    """
    currency_code = models.CharField(max_length=10, verbose_name="Código de Divisa")
    buy_rate = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('0.0000'), verbose_name="Tasa de Compra (Gs)")
    sell_rate = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('0.0000'), verbose_name="Tasa de Venta (Gs)")
    timestamp = models.DateTimeField(verbose_name="Fecha y Hora del Registro")

    class Meta:
        verbose_name = "Historial de Tasa de Cambio"
        verbose_name_plural = "Historiales de Tasas de Cambio"
        ordering = ['timestamp']

    def __str__(self):
        """
        Devuelve la representación en cadena del registro histórico.

        Returns:
            str: Representación descriptiva del registro histórico.
        """
        return f"{self.currency_code} ({self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}) - Compra: {self.buy_rate} | Venta: {self.sell_rate}"


class ClientBenefitRule(models.Model):
    """
    Modelo que representa las reglas de beneficio cambiario y comisiones
    parametrizables por tipo de categoría de cliente (PSE-29).
    
    Attributes:
        category_code (CharField): Código único de la categoría (MINORISTA, VIP, CORPORATIVO).
        category_name (CharField): Nombre descriptivo de la categoría.
        min_operation_amount (DecimalField): Monto mínimo de operación en Guaraníes para aplicar el beneficio.
        benefit_percentage (DecimalField): Porcentaje de beneficio/descuento aplicable (%).
        description (TextField): Descripción o notas sobre las condiciones de la regla.
        updated_at (DateTimeField): Fecha de última actualización de la regla.
    """
    category_code = models.CharField(max_length=20, unique=True, verbose_name="Código de Categoría")
    category_name = models.CharField(max_length=100, verbose_name="Nombre de Categoría")
    min_operation_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Monto Mínimo Operación (Gs)")
    benefit_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'), verbose_name="Porcentaje de Beneficio (%)")
    description = models.TextField(blank=True, null=True, verbose_name="Descripción / Condiciones")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Última Actualización")

    class Meta:
        verbose_name = "Regla de Beneficio por Categoría"
        verbose_name_plural = "Reglas de Beneficios por Categorías"

    def __str__(self):
        """
        Devuelve la representación en cadena de la regla de beneficio.

        Returns:
            str: Representación descriptiva de la regla.
        """
        return f"{self.category_name} - {self.benefit_percentage}% (Mín: {self.min_operation_amount:,.2f} Gs)"
