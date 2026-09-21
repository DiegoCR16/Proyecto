# -*- coding: utf-8 -*-
from django.db import models
from decimal import Decimal

class ExchangeRate(models.Model):
    """
    Modelo que representa la tasa de cambio vigente para una divisa autorizada.
    
    Attributes:
        currency_code (CharField): Código ISO de la divisa (USD, EUR, BRL, ARS, PYG).
        currency_name (CharField): Nombre descriptivo de la divisa.
        symbol (CharField): Símbolo cambiario ($, €, R$, ₲).
        buy_rate (DecimalField): Tasa de compra estándar en Guaraníes (Gs).
        sell_rate (DecimalField): Tasa de venta estándar en Guaraníes (Gs).
        last_updated (DateTimeField): Fecha y hora de última actualización en tiempo real.
    """
    currency_code = models.CharField(max_length=10, unique=True, verbose_name="Código de Divisa")
    currency_name = models.CharField(max_length=100, verbose_name="Nombre de Divisa")
    symbol = models.CharField(max_length=10, default='$', verbose_name="Símbolo Cambiario")
    buy_rate = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('0.0000'), verbose_name="Tasa de Compra (Gs)")
    sell_rate = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('0.0000'), verbose_name="Tasa de Venta (Gs)")
    last_updated = models.DateTimeField(auto_now=True, verbose_name="Última Actualización")

    def __str__(self):
        """Devuelve la representación en cadena de la tasa de cambio."""
        return f"{self.currency_code} ({self.symbol}) - Compra: {self.buy_rate} | Venta: {self.sell_rate}"


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


class PaymentMethod(models.Model):
    """
    Modelo que representa un método de pago parametrizable (tarjeta de crédito, débito, transferencia, billeteras electrónicas, etc.)
    con sus campos específicos por tipo (PSE-25).
    
    Attributes:
        code (CharField): Código único del método de pago.
        name (CharField): Nombre descriptivo del método de pago.
        method_type (CharField): Tipo de método de pago (TARJETA_CREDITO, TARJETA_DEBITO, TRANSFERENCIA, BILLETERA, EFECTIVO, OTRO).
        account_number (CharField): Número de cuenta, número de tarjeta o identificador.
        bank_name (CharField): Nombre del banco o entidad emisora.
        account_type (CharField): Tipo de cuenta (Corriente, Ahorro, etc.).
        holder_name (CharField): Titular de la cuenta o tarjeta.
        description (TextField): Descripción o detalles del método de pago.
        is_active (BooleanField): Estado de habilitación (True = Activo, False = Inactivo).
        updated_at (DateTimeField): Fecha de última actualización.
    """
    METHOD_TYPES = [
        ('TARJETA_CREDITO', 'Tarjeta de Crédito'),
        ('TARJETA_DEBITO', 'Tarjeta de Débito'),
        ('TRANSFERENCIA', 'Transferencia Bancaria'),
        ('BILLETERA', 'Billetera Electrónica'),
        ('EFECTIVO', 'Efectivo en Sucursal'),
        ('OTRO', 'Otro / Personalizado'),
    ]

    code = models.CharField(max_length=50, unique=True, verbose_name="Código de Método de Pago")
    name = models.CharField(max_length=100, verbose_name="Nombre del Método de Pago")
    method_type = models.CharField(max_length=30, choices=METHOD_TYPES, default='TRANSFERENCIA', verbose_name="Tipo de Método de Pago")
    account_number = models.CharField(max_length=100, blank=True, null=True, verbose_name="Número de Cuenta / Tarjeta")
    bank_name = models.CharField(max_length=100, blank=True, null=True, verbose_name="Banco / Emisor")
    account_type = models.CharField(max_length=50, blank=True, null=True, verbose_name="Tipo de Cuenta")
    holder_name = models.CharField(max_length=150, blank=True, null=True, verbose_name="Titular")
    description = models.TextField(blank=True, null=True, verbose_name="Descripción")
    balance = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('5000000000.00'), verbose_name="Saldo Disponible (Gs)")
    is_active = models.BooleanField(default=True, verbose_name="Habilitado / Activo")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Última Actualización")

    class Meta:
        verbose_name = "Método de Pago"
        verbose_name_plural = "Métodos de Pago"

    def __str__(self):
        """
        Devuelve la representación en cadena del método de pago.

        Returns:
            str: Representación descriptiva del método de pago.
        """
        acc_info = f" - N°: {self.account_number}" if self.account_number else ""
        return f"{self.name} ({self.get_method_type_display()}){acc_info} ({'Activo' if self.is_active else 'Inactivo'})"

