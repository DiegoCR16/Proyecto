# -*- coding: utf-8 -*-
from django.db import models
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from authentication.models import Cliente, ClientAccreditationMethod
from tasas_cambio.models import PaymentMethod
from decimal import Decimal


MARCA_SIFEN_ESTADO = "SIFEN Estado:"
MARCA_SIFEN_CDC = "SIFEN CDC:"
MARCA_SIFEN_REINTENTO = "SIFEN Reintento"

# Estados de la factura electrónica de una operación
ESTADOS_FACTURA = [
    "En cola", "Emitida", "Pendiente", "Aprobada", "Rechazada", "Error",
    "Cancelación en proceso", "Cancelada", "Anulación en proceso", "Anulada",
]


def _segmento_sifen_actual(texto):
    """
    Devuelve la parte del desglose que corresponde al último intento de facturación.

    El desglose es un registro que solo crece: cada reintento agrega la marca
    "SIFEN Reintento" y lo anterior queda como historial.
    """
    texto = texto or ""
    if MARCA_SIFEN_REINTENTO in texto:
        return texto.rsplit(MARCA_SIFEN_REINTENTO, 1)[1]
    return texto


def _ultimo_valor(texto, marca):
    """Valor que sigue a la última aparición de la marca, hasta el próximo separador '|'."""
    if marca not in texto:
        return None
    return texto.rsplit(marca, 1)[1].split("|")[0].strip()


class SifenFacturaMixin:
    """
    Propiedades de la factura electrónica (SIFEN) de una operación de compra o venta.
    El estado vigente es el último registrado en el intento de facturación actual.
    """

    @property
    def sifen_cdc(self):
        """CDC de la factura del intento actual, o None si todavía no se emitió."""
        return _ultimo_valor(_segmento_sifen_actual(self.transparent_breakdown), MARCA_SIFEN_CDC) or None

    @property
    def sifen_tiene_registro(self):
        """Indica si la operación tiene algún registro de facturación."""
        return MARCA_SIFEN_ESTADO in (self.transparent_breakdown or "")

    @property
    def sifen_estado(self):
        """Estado vigente de la factura (ver ESTADOS_FACTURA). Sin registros se informa "Emitida"."""
        valor = _ultimo_valor(_segmento_sifen_actual(self.transparent_breakdown), MARCA_SIFEN_ESTADO)
        if not valor:
            return "Emitida"
        for estado in sorted(ESTADOS_FACTURA, key=len, reverse=True):
            if valor.startswith(estado):
                return estado
        return "Emitida"

    @property
    def sifen_error(self):
        """Detalle del error de facturación vigente, o una cadena vacía si no hay error."""
        if self.sifen_estado != "Error":
            return ""
        valor = _ultimo_valor(_segmento_sifen_actual(self.transparent_breakdown), MARCA_SIFEN_ESTADO)
        return valor[len("Error"):].strip(" :")

    @property
    def sifen_sin_confirmar(self):
        """
        True si el último envío a Factura Segura se cortó (timeout o red) y no se sabe si el
        documento se generó. Según la API ESI, hay que verificarlo antes de reenviarlo.
        """
        return self.sifen_estado == "Error" and self.sifen_error.startswith("Sin confirmar")

    @property
    def sifen_detalle(self):
        """Texto completo del último estado registrado (incluye el motivo de rechazo o anulación)."""
        return _ultimo_valor(_segmento_sifen_actual(self.transparent_breakdown), MARCA_SIFEN_ESTADO) or ""

    @property
    def tipo_operacion(self):
        """'COMPRA' o 'VENTA'."""
        return "VENTA" if self.__class__.__name__ == "CurrencySaleTransaction" else "COMPRA"



class CurrencyPurchaseTransaction(SifenFacturaMixin, models.Model):
    """
    Modelo que registra las operaciones de compra de divisas de forma digital (PSE-13),
    incluyendo validaciones de límites, verificación de fondos, tasas, comisiones,
    impuestos y descuentos por perfil (VIP 2%, Corporativo 4%).
    
    Attributes:
        user (ForeignKey): Usuario solicitante.
        cliente (ForeignKey): Cliente asociado.
        turno (ForeignKey): Turno de caja asociado.
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
    turno = models.ForeignKey('caja.TurnoCaja', on_delete=models.SET_NULL, null=True, blank=True, related_name='purchase_transactions', verbose_name="Turno de Caja")
    from_currency = models.CharField(max_length=10, verbose_name="Moneda Origen")
    to_currency = models.CharField(max_length=10, verbose_name="Moneda Destino")
    amount = models.DecimalField(max_digits=15, decimal_places=2, verbose_name="Monto Origen")
    converted_amount = models.DecimalField(max_digits=15, decimal_places=2, verbose_name="Monto Convertido")
    applied_rate = models.DecimalField(max_digits=12, decimal_places=4, verbose_name="Tasa Aplicada")
    standard_rate = models.DecimalField(max_digits=12, decimal_places=4, verbose_name="Tasa Estándar")
    payment_method = models.ForeignKey(PaymentMethod, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Método de Pago")
    payment_method_type = models.CharField(max_length=50, blank=True, null=True, verbose_name="Tipo de Instrumento / Método de Pago")
    gateway_reference = models.CharField(max_length=100, blank=True, null=True, verbose_name="Referencia de Pasarela")
    gateway_status = models.CharField(max_length=30, blank=True, null=True, verbose_name="Estado de Pasarela")
    origin_acreditation_method = models.ForeignKey(ClientAccreditationMethod, on_delete=models.SET_NULL, null=True, blank=True, related_name='purchase_origins', verbose_name="Medio de Acreditación Origen")
    destination_acreditation_method = models.ForeignKey(ClientAccreditationMethod, on_delete=models.SET_NULL, null=True, blank=True, related_name='purchase_destinations', verbose_name="Medio de Acreditación Destino")
    benefit_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'), verbose_name="Descuento por Perfil (%)")
    commission_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Comisión (Gs)")
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Impuestos (Gs)")
    total_pyg = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="Total en Guaraníes (PYG)")
    status = models.CharField(max_length=20, default='SUCCESS', verbose_name="Estado de Transacción")
    processing_time_ms = models.IntegerField(default=0, verbose_name="Tiempo de Procesamiento (ms)")
    transparent_breakdown = models.TextField(blank=True, null=True, verbose_name="Desglose Transparente")
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Operación")
    moneda_origen_id = models.CharField(max_length=10, blank=True, null=True, verbose_name="Moneda Origen ID")
    monto_origen = models.DecimalField(max_digits=15, decimal_places=2, blank=True, null=True, verbose_name="Monto Origen")
    moneda_destino_id = models.CharField(max_length=10, blank=True, null=True, verbose_name="Moneda Destino ID")
    monto_destino = models.DecimalField(max_digits=15, decimal_places=2, blank=True, null=True, verbose_name="Monto Destino")
    tipo_cambio_cruzado = models.DecimalField(max_digits=12, decimal_places=4, blank=True, null=True, verbose_name="Tipo de Cambio Cruzado")
    tipo_cambio_local_origen = models.DecimalField(max_digits=12, decimal_places=4, blank=True, null=True, verbose_name="Tipo de Cambio Local Origen")
    monto_moneda_local = models.DecimalField(max_digits=18, decimal_places=2, blank=True, null=True, verbose_name="Monto Moneda Local (Audit)")
    total_origen = models.DecimalField(max_digits=18, decimal_places=2, blank=True, null=True, verbose_name="Total en Moneda Origen")

    class Meta:
        verbose_name = "Transacción de Compra de Divisas"
        verbose_name_plural = "Transacciones de Compra de Divisas"
        ordering = ['-timestamp']

    def __str__(self):
        """Devuelve la representación en cadena de la transacción de compra."""
        return f"Compra #{self.id or 0}: {self.amount} {self.from_currency} -> {self.converted_amount} {self.to_currency} ({self.status})"

    def save(self, *args, **kwargs):
        """
        Garantiza la inmutabilidad del registro transaccional. Una vez guardado en la base de datos,
        no se permiten modificaciones posteriores, excepto transiciones permitidas desde PENDIENTE a SUCCESS o CANCELLED (PSE-31) y de SUCCESS a ANULADA (anulación con devolución).
        """
        if self.pk and self.__class__.objects.filter(pk=self.pk).exists():
            old_obj = self.__class__.objects.get(pk=self.pk)
            update_fields = kwargs.get('update_fields')
            if update_fields and list(update_fields) == ['transparent_breakdown']:
                pass
            elif old_obj.status == 'PENDING' and self.status in ['SUCCESS', 'CANCELLED', 'FAILED']:
                pass
            elif (old_obj.status == 'SUCCESS' and self.status == 'ANULADA' and update_fields
                  and set(update_fields) <= {'status', 'transparent_breakdown'}):
                pass  # Anulación con devolución de fondos (procesamiento_operaciones.facturacion)
            else:
                raise ValidationError("Las transacciones de compra son registros inmutables y no pueden ser modificadas.")
        super().save(*args, **kwargs)


class CurrencySaleTransaction(SifenFacturaMixin, models.Model):
    """
    Modelo que registra las operaciones de venta de divisas de forma digital (PSE-14),
    incluyendo validaciones de límites, verificación de cuenta/billetera digital vinculada obligatoria,
    tasas de compra vigentes, comisiones, impuestos y descuentos por perfil.
    
    Attributes:
        user (ForeignKey): Usuario solicitante.
        cliente (ForeignKey): Cliente asociado.
        from_currency (CharField): Moneda de origen (divisa a vender).
        to_currency (CharField): Moneda de destino (moneda local PYG).
        amount (DecimalField): Monto de origen.
        converted_amount (DecimalField): Monto convertido resultante en moneda local.
        applied_rate (DecimalField): Tasa de compra aplicada.
        standard_rate (DecimalField): Tasa estándar.
        linked_account (ForeignKey): Cuenta bancaria o billetera digital vinculada obligatoria.
        benefit_percentage (DecimalField): Porcentaje de beneficio aplicado (%).
        commission_amount (DecimalField): Monto de comisión de la operación (Gs).
        tax_amount (DecimalField): Monto de impuestos (Gs).
        total_pyg (DecimalField): Monto total neto acreditado en Guaraníes (PYG).
        status (CharField): Estado de la transacción ('SUCCESS', 'FAILED', 'PENDING').
        processing_time_ms (IntegerField): Tiempo de procesamiento medido en milisegundos.
        transparent_breakdown (TextField): Desglose transparente de la operación.
        timestamp (DateTimeField): Fecha y hora de la transacción.
    """
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Usuario")
    cliente = models.ForeignKey(Cliente, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Cliente")
    turno = models.ForeignKey('caja.TurnoCaja', on_delete=models.SET_NULL, null=True, blank=True, related_name='sale_transactions', verbose_name="Turno de Caja")
    from_currency = models.CharField(max_length=10, verbose_name="Moneda Origen")
    to_currency = models.CharField(max_length=10, default='PYG', verbose_name="Moneda Destino")
    amount = models.DecimalField(max_digits=15, decimal_places=2, verbose_name="Monto Origen")
    converted_amount = models.DecimalField(max_digits=15, decimal_places=2, verbose_name="Monto Convertido")
    applied_rate = models.DecimalField(max_digits=12, decimal_places=4, verbose_name="Tasa Aplicada")
    standard_rate = models.DecimalField(max_digits=12, decimal_places=4, verbose_name="Tasa Estándar")
    linked_account = models.ForeignKey(PaymentMethod, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Cuenta o Billetera Vinculada")
    payment_method_type = models.CharField(max_length=50, blank=True, null=True, verbose_name="Tipo de Instrumento / Método de Pago")
    gateway_reference = models.CharField(max_length=100, blank=True, null=True, verbose_name="Referencia de Pasarela")
    gateway_status = models.CharField(max_length=30, blank=True, null=True, verbose_name="Estado de Pasarela")
    acreditation_method = models.ForeignKey(ClientAccreditationMethod, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Medio de Acreditación del Cliente")
    origin_acreditation_method = models.ForeignKey(ClientAccreditationMethod, on_delete=models.SET_NULL, null=True, blank=True, related_name='sale_origins', verbose_name="Medio de Acreditación Origen")
    destination_acreditation_method = models.ForeignKey(ClientAccreditationMethod, on_delete=models.SET_NULL, null=True, blank=True, related_name='sale_destinations', verbose_name="Medio de Acreditación Destino")
    benefit_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'), verbose_name="Descuento por Perfil (%)")
    commission_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Comisión (Gs)")
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name="Impuestos (Gs)")
    total_pyg = models.DecimalField(max_digits=18, decimal_places=2, verbose_name="Total Neto en Guaraníes (PYG)")
    status = models.CharField(max_length=20, default='SUCCESS', verbose_name="Estado de Transacción")
    processing_time_ms = models.IntegerField(default=0, verbose_name="Tiempo de Procesamiento (ms)")
    transparent_breakdown = models.TextField(blank=True, null=True, verbose_name="Desglose Transparente")
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Operación")
    moneda_origen_id = models.CharField(max_length=10, blank=True, null=True, verbose_name="Moneda Origen ID")
    monto_origen = models.DecimalField(max_digits=15, decimal_places=2, blank=True, null=True, verbose_name="Monto Origen")
    moneda_destino_id = models.CharField(max_length=10, blank=True, null=True, verbose_name="Moneda Destino ID")
    monto_destino = models.DecimalField(max_digits=15, decimal_places=2, blank=True, null=True, verbose_name="Monto Destino")
    tipo_cambio_cruzado = models.DecimalField(max_digits=12, decimal_places=4, blank=True, null=True, verbose_name="Tipo de Cambio Cruzado")
    tipo_cambio_local_origen = models.DecimalField(max_digits=12, decimal_places=4, blank=True, null=True, verbose_name="Tipo de Cambio Local Origen")
    monto_moneda_local = models.DecimalField(max_digits=18, decimal_places=2, blank=True, null=True, verbose_name="Monto Moneda Local (Audit)")
    total_origen = models.DecimalField(max_digits=18, decimal_places=2, blank=True, null=True, verbose_name="Total en Moneda Origen")

    class Meta:
        verbose_name = "Transacción de Venta de Divisas"
        verbose_name_plural = "Transacciones de Venta de Divisas"
        ordering = ['-timestamp']

    def __str__(self):
        """Devuelve la representación en cadena de la transacción de venta."""
        return f"Venta #{self.id or 0}: {self.amount} {self.from_currency} -> {self.converted_amount} {self.to_currency} ({self.status})"

    def save(self, *args, **kwargs):
        """
        Garantiza la inmutabilidad del registro transaccional de venta.
        Una vez guardado, no se permiten modificaciones posteriores, excepto transiciones desde PENDIENTE a SUCCESS o CANCELLED (PSE-31) y de SUCCESS a ANULADA (anulación con devolución).
        """
        if self.pk and self.__class__.objects.filter(pk=self.pk).exists():
            old_obj = self.__class__.objects.get(pk=self.pk)
            update_fields = kwargs.get('update_fields')
            if update_fields and list(update_fields) == ['transparent_breakdown']:
                pass
            elif old_obj.status == 'PENDING' and self.status in ['SUCCESS', 'CANCELLED', 'FAILED']:
                pass
            elif (old_obj.status == 'SUCCESS' and self.status == 'ANULADA' and update_fields
                  and set(update_fields) <= {'status', 'transparent_breakdown'}):
                pass  # Anulación con devolución de fondos (procesamiento_operaciones.facturacion)
            else:
                raise ValidationError("Las transacciones de venta son registros inmutables y no pueden ser modificadas.")
        super().save(*args, **kwargs)


class SifenSecuencia(models.Model):
    """
    Numeracion de documentos electronicos por establecimiento/punto/tipo.
    ultimo_numero es el ultimo aprobado por SIFEN; numero_en_proceso es el enviado que espera resultado.
    """
    clave = models.CharField(max_length=30, unique=True)  # ej: "001-003-1"
    ultimo_numero = models.PositiveIntegerField(default=0)
    numero_en_proceso = models.PositiveIntegerField(null=True, blank=True)
    cdc_en_proceso = models.CharField(max_length=44, blank=True, default="")
    reservado_en = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.clave}: {self.ultimo_numero}"



class _OperacionFacturadaMixin:
    """Acceso común a la operación (compra o venta) a la que se refiere un registro de facturación."""

    @property
    def transaccion(self):
        """Operación de compra o venta asociada."""
        return self.compra or self.venta

    @property
    def descripcion_operacion(self):
        """Texto breve de la operación, por ejemplo "Compra #12"."""
        tx = self.transaccion
        if tx is None:
            return "Operación eliminada"
        return f"{'Venta' if self.venta_id else 'Compra'} #{tx.pk}"


class SolicitudAnulacionFactura(_OperacionFacturadaMixin, models.Model):
    """
    Pedido de un cliente para anular la factura electrónica de una de sus operaciones.
    El Administrador la aprueba (y el sistema cancela la factura o emite una nota de crédito)
    o la rechaza indicando el motivo.

    Attributes:
        compra / venta (ForeignKey): Operación cuya factura se pide anular (una de las dos).
        cdc (CharField): CDC de la factura al momento de la solicitud.
        solicitante (ForeignKey): Usuario que hizo el pedido.
        cliente (ForeignKey): Cliente de la operación.
        motivo (TextField): Motivo indicado por el cliente.
        estado (CharField): PENDIENTE, APROBADA o RECHAZADA.
        respuesta (TextField): Resultado o motivo del rechazo informado por el Administrador.
        resuelta_por (ForeignKey): Administrador que resolvió la solicitud.
    """
    PENDIENTE = 'PENDIENTE'
    APROBADA = 'APROBADA'
    RECHAZADA = 'RECHAZADA'
    ESTADOS = [(PENDIENTE, 'Pendiente'), (APROBADA, 'Aprobada'), (RECHAZADA, 'Rechazada')]

    compra = models.ForeignKey(CurrencyPurchaseTransaction, on_delete=models.CASCADE, null=True, blank=True, related_name='solicitudes_anulacion')
    venta = models.ForeignKey(CurrencySaleTransaction, on_delete=models.CASCADE, null=True, blank=True, related_name='solicitudes_anulacion')
    cdc = models.CharField(max_length=44, verbose_name="CDC de la Factura")
    solicitante = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='solicitudes_anulacion', verbose_name="Solicitante")
    cliente = models.ForeignKey(Cliente, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Cliente")
    motivo = models.TextField(verbose_name="Motivo")
    estado = models.CharField(max_length=20, choices=ESTADOS, default=PENDIENTE, verbose_name="Estado")
    respuesta = models.TextField(blank=True, default="", verbose_name="Respuesta")
    resuelta_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='solicitudes_anulacion_resueltas', verbose_name="Resuelta por")
    creada_en = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Solicitud")
    resuelta_en = models.DateTimeField(null=True, blank=True, verbose_name="Fecha de Resolución")

    class Meta:
        verbose_name = "Solicitud de Anulación de Factura"
        verbose_name_plural = "Solicitudes de Anulación de Factura"
        ordering = ['-creada_en']

    def __str__(self):
        return f"Solicitud #{self.pk} - {self.descripcion_operacion} ({self.estado})"


class AnulacionFactura(_OperacionFacturadaMixin, models.Model):
    """
    Anulación de una factura electrónica aprobada, por uno de los dos caminos de SIFEN:

    * CANCELACION: evento de cancelación (``sol_cancelacion``), permitido dentro del plazo
      (48 h desde la aprobación). Es asíncrono: queda EMITIDA hasta que SIFEN responde.
    * NOTA_CREDITO: nota de crédito electrónica por el total, fuera de ese plazo. Cuando
      SIFEN la aprueba se envía por correo al cliente.

    Attributes:
        tipo (CharField): CANCELACION o NOTA_CREDITO.
        estado (CharField): EMITIDA, APROBADA, RECHAZADA o ERROR.
        cdc_factura (CharField): CDC de la factura anulada.
        cdc_nota_credito (CharField): CDC de la nota de crédito (solo NOTA_CREDITO).
        numero_nota_credito (PositiveIntegerField): dNumDoc de la nota de crédito.
        motivo (TextField): Motivo de la anulación.
        detalle (TextField): Respuesta de SIFEN o descripción del error.
        solicitud (ForeignKey): Solicitud del cliente que originó la anulación, si la hubo.
        usuario (ForeignKey): Administrador que la ejecutó.
        correo_enviado_en (DateTimeField): Último envío de la nota de crédito al cliente.
        con_devolucion (BooleanField): True si además de anular la factura se devuelven los fondos
            (la operación queda ANULADA); False si solo se corrige la factura (se puede refacturar).
        devuelta_en (DateTimeField): Momento en que se devolvieron los fondos.
        detalle_devolucion (TextField): Resultado de la devolución o motivo por el que quedó pendiente.
    """
    CANCELACION = 'CANCELACION'
    NOTA_CREDITO = 'NOTA_CREDITO'
    TIPOS = [(CANCELACION, 'Cancelación (evento SIFEN)'), (NOTA_CREDITO, 'Nota de Crédito')]

    EMITIDA = 'EMITIDA'
    APROBADA = 'APROBADA'
    RECHAZADA = 'RECHAZADA'
    ERROR = 'ERROR'
    ESTADOS = [(EMITIDA, 'Emitida'), (APROBADA, 'Aprobada'), (RECHAZADA, 'Rechazada'), (ERROR, 'Error')]

    compra = models.ForeignKey(CurrencyPurchaseTransaction, on_delete=models.CASCADE, null=True, blank=True, related_name='anulaciones')
    venta = models.ForeignKey(CurrencySaleTransaction, on_delete=models.CASCADE, null=True, blank=True, related_name='anulaciones')
    tipo = models.CharField(max_length=20, choices=TIPOS, verbose_name="Tipo")
    estado = models.CharField(max_length=20, choices=ESTADOS, default=EMITIDA, verbose_name="Estado")
    cdc_factura = models.CharField(max_length=44, verbose_name="CDC de la Factura")
    cdc_nota_credito = models.CharField(max_length=44, blank=True, default="", verbose_name="CDC de la Nota de Crédito")
    numero_nota_credito = models.PositiveIntegerField(null=True, blank=True, verbose_name="Número de Nota de Crédito")
    motivo = models.TextField(verbose_name="Motivo")
    detalle = models.TextField(blank=True, default="", verbose_name="Detalle / Respuesta SIFEN")
    solicitud = models.ForeignKey(SolicitudAnulacionFactura, on_delete=models.SET_NULL, null=True, blank=True, related_name='anulaciones')
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Ejecutada por")
    creada_en = models.DateTimeField(auto_now_add=True, verbose_name="Fecha")
    correo_enviado_en = models.DateTimeField(null=True, blank=True, verbose_name="Correo Enviado")
    con_devolucion = models.BooleanField(default=False, verbose_name="Con Devolución de Fondos")
    devuelta_en = models.DateTimeField(null=True, blank=True, verbose_name="Fondos Devueltos")
    detalle_devolucion = models.TextField(blank=True, default="", verbose_name="Detalle de la Devolución")

    class Meta:
        verbose_name = "Anulación de Factura"
        verbose_name_plural = "Anulaciones de Factura"
        ordering = ['-creada_en']

    def __str__(self):
        return f"{self.get_tipo_display()} - {self.descripcion_operacion} ({self.estado})"


class MovimientoSaldoOperacion(_OperacionFacturadaMixin, models.Model):
    """
    Movimiento de saldo producido por una operación de compra o venta, o por su devolución.

    Se registra en el momento de la operación con el monto exacto que se movió, para que la
    devolución (anulación con devolución de fondos) aplique el inverso sin recalcular con la
    tasa del día.

    Attributes:
        tipo (CharField): CUENTA_CLIENTE (medio de acreditación), CAJA_NEGOCIO (método de pago
            del negocio, en Gs) o VOLUMEN_CLIENTE (volumen transaccional, en Gs).
        monto (DecimalField): Monto con signo (+ acredita, - debita).
        es_devolucion (BooleanField): True si el movimiento revierte uno anterior.
    """
    CUENTA_CLIENTE = 'CUENTA_CLIENTE'
    CAJA_NEGOCIO = 'CAJA_NEGOCIO'
    VOLUMEN_CLIENTE = 'VOLUMEN_CLIENTE'
    TIPOS = [
        (CUENTA_CLIENTE, 'Cuenta del cliente'),
        (CAJA_NEGOCIO, 'Caja del negocio'),
        (VOLUMEN_CLIENTE, 'Volumen transaccional del cliente'),
    ]

    compra = models.ForeignKey(CurrencyPurchaseTransaction, on_delete=models.CASCADE, null=True, blank=True, related_name='movimientos_saldo')
    venta = models.ForeignKey(CurrencySaleTransaction, on_delete=models.CASCADE, null=True, blank=True, related_name='movimientos_saldo')
    tipo = models.CharField(max_length=20, choices=TIPOS)
    cuenta_cliente = models.ForeignKey(ClientAccreditationMethod, on_delete=models.SET_NULL, null=True, blank=True, related_name='movimientos_saldo')
    metodo_pago = models.ForeignKey(PaymentMethod, on_delete=models.SET_NULL, null=True, blank=True, related_name='movimientos_saldo')
    cliente = models.ForeignKey(Cliente, on_delete=models.SET_NULL, null=True, blank=True, related_name='movimientos_saldo')
    moneda = models.CharField(max_length=10)
    monto = models.DecimalField(max_digits=18, decimal_places=2)
    es_devolucion = models.BooleanField(default=False)
    anulacion = models.ForeignKey(AnulacionFactura, on_delete=models.SET_NULL, null=True, blank=True, related_name='movimientos_saldo')
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Movimiento de Saldo de Operación"
        verbose_name_plural = "Movimientos de Saldo de Operaciones"
        ordering = ['creado_en', 'id']

    def __str__(self):
        signo = '+' if self.monto >= 0 else ''
        return f"{self.descripcion_operacion}: {self.get_tipo_display()} {signo}{self.monto} {self.moneda}"
