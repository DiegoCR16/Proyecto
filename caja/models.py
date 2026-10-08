# -*- coding: utf-8 -*-
from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

class Caja(models.Model):
    """
    Modelo que representa una caja física en la casa de cambios.
    """
    nombre = models.CharField(max_length=100, unique=True, verbose_name="Nombre de Caja")
    codigo = models.CharField(max_length=50, unique=True, verbose_name="Código")
    activa = models.BooleanField(default=True, verbose_name="Activa")

    def __str__(self):
        """
        Retorna la representación en cadena de la caja.
        """
        return f"{self.nombre} ({self.codigo})"

    class Meta:
        verbose_name = "Caja"
        verbose_name_plural = "Cajas"


class TurnoCaja(models.Model):
    """
    Modelo que gestiona la apertura y cierre de turnos de caja física,
    registrando saldos iniciales y finales detallados por divisa (USD, EUR, PYG, BRL, ARS)
    y controlando el estado activo para bloquear transacciones sin turno abierto.
    """
    ESTADO_CHOICES = [
        ('ABIERTO', 'Abierto'),
        ('CERRADO', 'Cerrado'),
    ]

    caja = models.ForeignKey(Caja, on_delete=models.CASCADE, verbose_name="Caja")
    cajero = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Cajero")
    fecha_apertura = models.DateTimeField(auto_now_add=True, verbose_name="Fecha y Hora de Apertura")
    fecha_cierre = models.DateTimeField(blank=True, null=True, verbose_name="Fecha y Hora de Cierre")
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='ABIERTO', verbose_name="Estado de Turno")

    # Saldos Iniciales por Divisa
    saldo_inicial_pyg = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Saldo Inicial PYG")
    saldo_inicial_usd = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Saldo Inicial USD")
    saldo_inicial_eur = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Saldo Inicial EUR")
    saldo_inicial_brl = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Saldo Inicial BRL")
    saldo_inicial_ars = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Saldo Inicial ARS")

    # Saldos Finales por Divisa (al cerrar turno)
    saldo_final_pyg = models.DecimalField(max_digits=15, decimal_places=2, blank=True, null=True, verbose_name="Saldo Final PYG")
    saldo_final_usd = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True, verbose_name="Saldo Final USD")
    saldo_final_eur = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True, verbose_name="Saldo Final EUR")
    saldo_final_brl = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True, verbose_name="Saldo Final BRL")
    saldo_final_ars = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True, verbose_name="Saldo Final ARS")

    def __str__(self):
        """
        Retorna la representación descriptiva del turno de caja.
        """
        return f"Turno {self.id} - Caja: {self.caja.nombre} - Cajero: {self.cajero.username} ({self.estado})"

    def clean(self):
        """
        Valida que no exista otro turno abierto para la misma caja al intentar abrir.
        """
        super().clean()
        if self.estado == 'ABIERTO':
            qs = TurnoCaja.objects.filter(caja=self.caja, estado='ABIERTO')
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError(f"La caja '{self.caja.nombre}' ya cuenta con un turno activo sin cerrar.")

    def save(self, *args, **kwargs):
        """
        Ejecuta clean antes de guardar.
        """
        self.full_clean()
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "Turno de Caja"
        verbose_name_plural = "Turnos de Caja"


def verificar_turno_activo(caja):
    """
    Verifica si una caja física posee un turno activo (abierto).
    Retorna el objeto TurnoCaja abierto o None.
    """
    return TurnoCaja.objects.filter(caja=caja, estado='ABIERTO').first()


def puede_realizar_transaccion(caja):
    """
    Retorna True si la caja posee un turno abierto, permitiendo la creación de transacciones.
    Caso contrario, retorna False (congelando/bloqueando operaciones).
    """
    if not caja:
        return False
    return TurnoCaja.objects.filter(caja=caja, estado='ABIERTO').exists()
