# -*- coding: utf-8 -*-
from django.db import models
from django.contrib.auth.models import User
from authentication.models import Cliente
from tasas_cambio.models import PaymentMethod
from decimal import Decimal


class CurrencyPurchaseTransaction(models.Model):
    """
    Modelo que registra las operaciones de compra de divisas de forma digital (PSE-13),
    incluyendo validaciones de límites, verificación de fondos, tasas, comisiones,
    impuestos y descuentos por perfil (VIP 2%, Corporativo 4%).
    
    Attributes:
        user (ForeignKey): Usuario solicitante.
        cliente (ForeignKey): Cliente asociado.
        from_currency (CharField): Moneda de origen.
        to_currency (CharField): Moneda de destino.
        amount (DecimalField): Monto de origen.
        converted_amount (DecimalField): Monto convertido resultante.
        applied_rate (DecimalField): Tasa de cambio aplicada.
        standard_rate (DecimalField): Tasa estándar.
        payment_method (ForeignKey): Método de pago utilizado.
        benefit_percentage (DecimalField): Porcentaje de beneficio aplicado (%).
        commission_amount (DecimalField): Monto de comisión de la operación (Gs).
        tax_amount (DecimalField): Monto de impuestos (Gs).
        total_pyg (DecimalField): Monto total expresado en Guaraníes (PYG).
        status (CharField): Estado de la transacción ('SUCCESS', 'FAILED', 'PENDING').
        processing_time_ms (IntegerField): Tiempo de procesamiento medido en milisegundos.
        transparent_breakdown (TextField): Desglose transparente de la operación.
        timestamp (DateTimeField): Fecha y hora de la transacción.
    """
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Usuario")
    cliente = models.ForeignKey(Cliente, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Cliente")
    from_currency = models.CharField(max_length=10, verbose_name="Moneda Origen")
    to_currency = models.CharField(max_length=10, verbose_name="Moneda Destino")
    amount = models.DecimalField(max_digits=15, decimal_places=2, verbose_name="Monto Origen")
    converted_amount = models.DecimalField(max_digits=15, decimal_places=2, verbose_name="Monto Convertido")
    applied_rate = models.DecimalField(max_digits=12, decimal_places=4, verbose_name="Tasa Aplicada")
    standard_rate = models.DecimalField(max_digits=12, decimal_places=4, verbose_name="Tasa Estándar")
    payment_method = models.ForeignKey(PaymentMethod, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Método de Pago")
    benefit_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'), verbose_name="Descuento por Perfil (%)")
    commission_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Comisión (Gs)")
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Impuestos (Gs)")
    total_pyg = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="Total en Guaraníes (PYG)")
    status = models.CharField(max_length=20, default='SUCCESS', verbose_name="Estado de Transacción")
    processing_time_ms = models.IntegerField(default=0, verbose_name="Tiempo de Procesamiento (ms)")
    transparent_breakdown = models.TextField(blank=True, null=True, verbose_name="Desglose Transparente")
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Operación")

    class Meta:
        verbose_name = "Transacción de Compra de Divisas"
        verbose_name_plural = "Transacciones de Compra de Divisas"
        ordering = ['-timestamp']

    def __str__(self):
        """Devuelve la representación en cadena de la transacción de compra."""
        return f"Compra #{self.id or 0}: {self.amount} {self.from_currency} -> {self.converted_amount} {self.to_currency} ({self.status})"
