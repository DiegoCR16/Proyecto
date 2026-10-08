# -*- coding: utf-8 -*-
from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from decimal import Decimal

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


class DenominacionDivisa(models.Model):
    """
    Modelo que representa una denominación de papel moneda autorizada por divisa (PSE-21).
    
    Attributes:
        divisa (CharField): Código ISO de la divisa (USD, PYG, EUR, BRL, ARS).
        valor_facial (DecimalField): Valor nominal del billete (ej. 100000 para PYG, 100 para USD).
        nombre (CharField): Nombre descriptivo de la denominación (ej. Billetes de 100.000 PYG).
        activa (BooleanField): Estado de habilitación.
    """
    divisa = models.CharField(max_length=10, verbose_name="Código de Divisa")
    valor_facial = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Valor Facial")
    nombre = models.CharField(max_length=100, verbose_name="Nombre de Denominación")
    activa = models.BooleanField(default=True, verbose_name="Activa")

    class Meta:
        verbose_name = "Denominación de Divisa"
        verbose_name_plural = "Denominaciones de Divisas"
        ordering = ['divisa', '-valor_facial']

    def __str__(self):
        """Devuelve la representación en cadena de la denominación."""
        return f"{self.nombre} ({self.divisa})"


class DesgloseEfectivoCaja(models.Model):
    """
    Modelo que representa un registro de conteo físico o movimiento de efectivo
    respaldado por el desglose detallado individual de billetes (PSE-21).
    
    Attributes:
        turno (TurnoCaja): Turno de caja asociado.
        tipo_operacion (CharField): APERTURA, CIERRE, INGRESO, SALIDA.
        divisa (CharField): Divisa del desglose.
        monto_total (DecimalField): Sumatoria total calculada del desglose.
        observacion (TextField): Notas u observaciones.
        fecha (DateTimeField): Fecha y hora del registro.
    """
    TIPO_OPERACION_CHOICES = [
        ('APERTURA', 'Apertura de Turno'),
        ('CIERRE', 'Cierre de Turno'),
        ('INGRESO', 'Ingreso Físico de Efectivo'),
        ('SALIDA', 'Salida Física de Efectivo'),
    ]

    turno = models.ForeignKey(TurnoCaja, on_delete=models.CASCADE, related_name='desgloses_efectivo', verbose_name="Turno de Caja")
    tipo_operacion = models.CharField(max_length=20, choices=TIPO_OPERACION_CHOICES, verbose_name="Tipo de Operación")
    divisa = models.CharField(max_length=10, verbose_name="Divisa")
    monto_total = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Monto Total")
    observacion = models.TextField(blank=True, null=True, verbose_name="Observación")
    fecha = models.DateTimeField(auto_now_add=True, verbose_name="Fecha y Hora")

    class Meta:
        verbose_name = "Desglose de Efectivo de Caja"
        verbose_name_plural = "Desgloses de Efectivo de Caja"

    def __str__(self):
        """Devuelve la representación en cadena del desglose."""
        return f"{self.get_tipo_operacion_display()} - {self.divisa} {self.monto_total:,.2f} (Turno #{self.turno.id})"


class DetalleDesgloseBillete(models.Model):
    """
    Modelo que representa el detalle de cantidad de billetes por denominación
    en un desglose físico de efectivo (PSE-21).
    
    Attributes:
        desglose (DesgloseEfectivoCaja): Registro de desglose padre.
        denominacion (DenominacionDivisa): Denominación del billete.
        cantidad (IntegerField): Cantidad de billetes de dicha denominación.
        subtotal (DecimalField): Subtotal calculado (cantidad * valor_facial).
    """
    desglose = models.ForeignKey(DesgloseEfectivoCaja, on_delete=models.CASCADE, related_name='detalles', verbose_name="Desglose")
    denominacion = models.ForeignKey(DenominacionDivisa, on_delete=models.CASCADE, verbose_name="Denominación")
    cantidad = models.IntegerField(default=0, verbose_name="Cantidad de Billetes")
    subtotal = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Subtotal")

    class Meta:
        verbose_name = "Detalle de Billete"
        verbose_name_plural = "Detalles de Billetes"

    def save(self, *args, **kwargs):
        """Calcula automáticamente el subtotal multiplicando cantidad por valor facial antes de guardar."""
        self.subtotal = Decimal(str(self.cantidad)) * self.denominacion.valor_facial
        super().save(*args, **kwargs)

    def __str__(self):
        """Devuelve la representación en cadena del detalle de billete."""
        return f"{self.cantidad} x {self.denominacion.nombre} = {self.subtotal:,.2f}"


def inicializar_denominaciones_default():
    """Crea las denominaciones estándar por divisa si no existen."""
    denominaciones_data = [
        # PYG
        ('PYG', Decimal('100000'), 'Billete de 100.000 Guaraníes'),
        ('PYG', Decimal('50000'), 'Billete de 50.000 Guaraníes'),
        ('PYG', Decimal('20000'), 'Billete de 20.000 Guaraníes'),
        ('PYG', Decimal('10000'), 'Billete de 10.000 Guaraníes'),
        ('PYG', Decimal('5000'), 'Billete de 5.000 Guaraníes'),
        ('PYG', Decimal('2000'), 'Billete de 2.000 Guaraníes'),
        ('PYG', Decimal('1000'), 'Billete de 1.000 Guaraníes'),
        # USD
        ('USD', Decimal('100'), 'Billete de 100 USD'),
        ('USD', Decimal('50'), 'Billete de 50 USD'),
        ('USD', Decimal('20'), 'Billete de 20 USD'),
        ('USD', Decimal('10'), 'Billete de 10 USD'),
        ('USD', Decimal('5'), 'Billete de 5 USD'),
        ('USD', Decimal('1'), 'Billete de 1 USD'),
        # EUR
        ('EUR', Decimal('500'), 'Billete de 500 EUR'),
        ('EUR', Decimal('200'), 'Billete de 200 EUR'),
        ('EUR', Decimal('100'), 'Billete de 100 EUR'),
        ('EUR', Decimal('50'), 'Billete de 50 EUR'),
        ('EUR', Decimal('20'), 'Billete de 20 EUR'),
        ('EUR', Decimal('10'), 'Billete de 10 EUR'),
        ('EUR', Decimal('5'), 'Billete de 5 EUR'),
        # BRL
        ('BRL', Decimal('100'), 'Billete de 100 BRL'),
        ('BRL', Decimal('50'), 'Billete de 50 BRL'),
        ('BRL', Decimal('20'), 'Billete de 20 BRL'),
        ('BRL', Decimal('10'), 'Billete de 10 BRL'),
        ('BRL', Decimal('5'), 'Billete de 5 BRL'),
        ('BRL', Decimal('2'), 'Billete de 2 BRL'),
        # ARS
        ('ARS', Decimal('1000'), 'Billete de 1000 ARS'),
        ('ARS', Decimal('500'), 'Billete de 500 ARS'),
        ('ARS', Decimal('200'), 'Billete de 200 ARS'),
        ('ARS', Decimal('100'), 'Billete de 100 ARS'),
        ('ARS', Decimal('50'), 'Billete de 50 ARS'),
    ]
    for divisa, valor, nombre in denominaciones_data:
        DenominacionDivisa.objects.get_or_create(
            divisa=divisa,
            valor_facial=valor,
            defaults={'nombre': nombre, 'activa': True}
        )



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
