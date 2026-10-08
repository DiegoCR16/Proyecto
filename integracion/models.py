# -*- coding: utf-8 -*-
from django.db import models
from django.contrib.auth.models import User
from authentication.models import Cliente, ClientAccreditationMethod
from decimal import Decimal


class PaymentGatewayLog(models.Model):
    """
    Registra las llamadas simuladas a las pasarelas de pago y redes externas (Bancard, Tigo Money, SIPAP).
    
    Attributes:
        gateway_name (CharField): Nombre de la pasarela o red (BANCARD, TIGO_MONEY, SIPAP).
        transaction_type (CharField): Tipo de operación (PAYMENT, COLLECTION, TRANSFER, ACCREDITATION).
        reference_code (CharField): Código de referencia externo o transacción.
        amount (DecimalField): Monto involucrado.
        currency (CharField): Moneda (PYG, USD, etc.).
        status (CharField): Estado de respuesta (SUCCESS, FAILED, TIMEOUT, PENDING).
        request_payload (TextField): Datos enviados en la solicitud simulada.
        response_payload (TextField): Respuesta simulada de la pasarela.
        error_message (TextField): Mensaje de error si falló.
        timestamp (DateTimeField): Fecha y hora de la transacción.
    """
    GATEWAY_CHOICES = [
        ('BANCARD', 'Bancard API'),
        ('TIGO_MONEY', 'Tigo Money API'),
        ('SIPAP', 'SIPAP Red Interbancaria'),
    ]

    STATUS_CHOICES = [
        ('SUCCESS', 'Exitoso'),
        ('FAILED', 'Fallido'),
        ('TIMEOUT', 'Tiempo Agotado'),
        ('PENDING', 'Pendiente'),
    ]

    gateway_name = models.CharField(max_length=30, choices=GATEWAY_CHOICES, verbose_name="Pasarela / Red")
    transaction_type = models.CharField(max_length=30, default='TRANSFER', verbose_name="Tipo de Operación")
    reference_code = models.CharField(max_length=100, unique=True, verbose_name="Código de Referencia")
    amount = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="Monto")
    currency = models.CharField(max_length=10, default='PYG', verbose_name="Moneda")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='SUCCESS', verbose_name="Estado")
    request_payload = models.TextField(blank=True, null=True, verbose_name="Payload de Solicitud")
    response_payload = models.TextField(blank=True, null=True, verbose_name="Payload de Respuesta")
    error_message = models.TextField(blank=True, null=True, verbose_name="Mensaje de Error")
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="Fecha y Hora")

    class Meta:
        verbose_name = "Log de Pasarela de Pago"
        verbose_name_plural = "Logs de Pasarelas de Pago"
        ordering = ['-timestamp']

    def __str__(self):
        """Devuelve la representación en cadena del log de pasarela."""
        return f"[{self.gateway_name}] Ref: {self.reference_code} - {self.amount} {self.currency} ({self.status})"


class WithdrawalRequest(models.Model):
    """
    Gestiona la configuración y ejecución de retiros y acreditaciones automáticas (SIPAP, Bancard, Tigo Money, Western Union / EuroTransfer, Pickup).
    
    Attributes:
        cliente (ForeignKey): Cliente solicitante.
        user (ForeignKey): Usuario que gestiona.
        channel (CharField): Canal seleccionado (SIPAP, BANCARD, TIGO_MONEY, WESTERN_UNION, EURO_TRANSFER, PICKUP_CAJA).
        currency (CharField): Moneda del retiro/acreditación.
        amount (DecimalField): Monto solicitado.
        destination_detail (CharField): Número de cuenta, teléfono, código de transferencia o sucursal de pickup.
        status (CharField): Estado del retiro (PROCESSING, COMPLETED, FAILED, PENDING_APPROVAL).
        gateway_ref (CharField): Referencia de la pasarela externa asociada.
        timestamp (DateTimeField): Fecha de solicitud.
    """
    CHANNEL_CHOICES = [
        ('SIPAP', 'SIPAP Transferencia Interbancaria 24/7'),
        ('BANCARD', 'Bancard Pago/Cobro Digital'),
        ('TIGO_MONEY', 'Billetera Tigo Money'),
        ('WESTERN_UNION', 'Western Union Internacional'),
        ('EURO_TRANSFER', 'EuroTransfer Internacional'),
        ('PICKUP_CAJA', 'Retiro Físico en Caja (Pickup)'),
    ]

    STATUS_CHOICES = [
        ('PROCESSING', 'Procesando 24/7'),
        ('COMPLETED', 'Completado y Acreditado'),
        ('FAILED', 'Fallido / Rechazado'),
        ('PENDING_APPROVAL', 'Pendiente de Aprobación'),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, verbose_name="Cliente")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Usuario")
    channel = models.CharField(max_length=30, choices=CHANNEL_CHOICES, verbose_name="Canal de Acreditación/Retiro")
    currency = models.CharField(max_length=10, default='PYG', verbose_name="Moneda")
    amount = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="Monto")
    destination_detail = models.CharField(max_length=255, verbose_name="Detalle de Cuenta / Destino / Teléfono")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PROCESSING', verbose_name="Estado de Acreditación")
    gateway_ref = models.CharField(max_length=100, blank=True, null=True, verbose_name="Referencia Externa Gateway")
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Solicitud")

    class Meta:
        verbose_name = "Solicitud de Retiro / Acreditación"
        verbose_name_plural = "Solicitudes de Retiros y Acreditaciones"
        ordering = ['-timestamp']

    def __str__(self):
        """Devuelve la representación en cadena de la solicitud de retiro."""
        return f"Retiro #{self.id or 0} - {self.cliente.nombre_o_razon_social}: {self.amount} {self.currency} vía {self.channel} ({self.status})"
