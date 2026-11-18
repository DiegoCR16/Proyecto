# -*- coding: utf-8 -*-
"""
Reportes financieros de transacciones y ganancias (PSE-41).

Consolida en Guaraníes (PYG) las compras y ventas de divisas (online y de ventanilla),
permite filtrarlas de forma combinable y exportarlas a CSV (se abre en Excel). El PDF se obtiene
desde la vista de impresión del navegador (``window.print()``), igual que la planilla de cierre de caja.

Criterios contables aplicados:

* Solo se incluyen las operaciones con estado ``SUCCESS``. Las fallidas, pendientes,
  canceladas y anuladas (``ANULADA``, factura anulada con devolución) no generan ganancia.
* **Volumen operado** = ``total_pyg`` de la operación.
* **Ganancia neta** = comisión − IVA de la comisión, ambos expresados en PYG.

La comisión no siempre se guarda en Guaraníes, por eso se convierte en este módulo:

* Operaciones online: la comisión se calcula sobre el monto convertido, en la moneda de destino.
  Si el destino no es PYG se convierte con el factor ``total_pyg / converted_amount``.
* Operaciones de ventanilla (``EFECTIVO_VENTANILLA``): la comisión queda en la moneda de origen.
  Si el origen no es PYG se convierte con la tasa aplicada.
"""
import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Q
from django.utils import timezone

from procesamiento_operaciones.models import CurrencyPurchaseTransaction, CurrencySaleTransaction

TIPO_COMPRA = 'COMPRA'
TIPO_VENTA = 'VENTA'
TIPOS_OPERACION = [(TIPO_COMPRA, 'Compra'), (TIPO_VENTA, 'Venta')]

ESTADO_CONTABLE = 'SUCCESS'
PAGO_VENTANILLA = 'EFECTIVO_VENTANILLA'

CENTIMOS = Decimal('0.01')
CERO = Decimal('0.00')

COLUMNAS_EXPORTACION = [
    'Fecha', 'Tipo', 'Canal', 'Cliente', 'Cajero / Usuario', 'Moneda Origen', 'Moneda Destino',
    'Monto Origen', 'Tasa Aplicada', 'Volumen (PYG)', 'Comisión (PYG)', 'IVA (PYG)', 'Ganancia Neta (PYG)',
]


@dataclass
class FiltrosReporte:
    """
    Filtros combinables del reporte. Un campo vacío (``None`` o ``''``) no filtra.

    Attributes:
        fecha_desde (date | None): Fecha inicial inclusive.
        fecha_hasta (date | None): Fecha final inclusive (se toma hasta el final del día).
        tipo (str): ``COMPRA``, ``VENTA`` o vacío para ambos.
        moneda (str): Código de divisa; coincide si es la moneda de origen o la de destino.
        cajero_id (int | None): Usuario cajero del turno de caja en que se registró la operación.
        cliente_id (str | None): UUID del cliente.
    """
    fecha_desde: object = None
    fecha_hasta: object = None
    tipo: str = ''
    moneda: str = ''
    cajero_id: object = None
    cliente_id: object = None


@dataclass
class FilaReporte:
    """
    Una operación del reporte con sus importes ya consolidados en PYG.

    Attributes:
        fecha (datetime): Fecha y hora de la operación (zona horaria local).
        tipo (str): ``COMPRA`` o ``VENTA``.
        canal (str): ``Ventanilla`` u ``Online``.
        cliente (str): Nombre o razón social del cliente.
        operador (str): Cajero del turno o, si no hay turno, el usuario que operó.
        moneda_origen (str): Moneda entregada por el cliente.
        moneda_destino (str): Moneda recibida por el cliente.
        monto_origen (Decimal): Monto en la moneda de origen.
        tasa_aplicada (Decimal): Tasa de cambio aplicada.
        volumen_pyg (Decimal): Volumen operado en PYG.
        comision_pyg (Decimal): Comisión en PYG.
        iva_pyg (Decimal): IVA de la comisión en PYG.
        ganancia_neta_pyg (Decimal): Comisión menos IVA, en PYG.
    """
    fecha: datetime
    tipo: str
    canal: str
    cliente: str
    operador: str
    moneda_origen: str
    moneda_destino: str
    monto_origen: Decimal
    tasa_aplicada: Decimal
    volumen_pyg: Decimal
    comision_pyg: Decimal
    iva_pyg: Decimal
    ganancia_neta_pyg: Decimal

    def como_lista(self):
        """
        Devuelve la fila en el orden de ``COLUMNAS_EXPORTACION``.

        Returns:
            list: Valores de la fila (fecha formateada como texto).
        """
        return [
            self.fecha.strftime('%d/%m/%Y %H:%M'), self.tipo.capitalize(), self.canal, self.cliente,
            self.operador, self.moneda_origen, self.moneda_destino, self.monto_origen, self.tasa_aplicada,
            self.volumen_pyg, self.comision_pyg, self.iva_pyg, self.ganancia_neta_pyg,
        ]


@dataclass
class TotalesReporte:
    """
    Totales acumulados del período filtrado.

    Attributes:
        cantidad (int): Cantidad de operaciones.
        cantidad_compras (int): Cantidad de compras.
        cantidad_ventas (int): Cantidad de ventas.
        volumen_compras_pyg (Decimal): Volumen de compras en PYG.
        volumen_ventas_pyg (Decimal): Volumen de ventas en PYG.
        volumen_total_pyg (Decimal): Volumen total operado en PYG.
        comision_pyg (Decimal): Comisiones acumuladas en PYG.
        iva_pyg (Decimal): IVA acumulado en PYG.
        ganancia_neta_pyg (Decimal): Ganancia neta del período en PYG.
    """
    cantidad: int = 0
    cantidad_compras: int = 0
    cantidad_ventas: int = 0
    volumen_compras_pyg: Decimal = field(default=CERO)
    volumen_ventas_pyg: Decimal = field(default=CERO)
    volumen_total_pyg: Decimal = field(default=CERO)
    comision_pyg: Decimal = field(default=CERO)
    iva_pyg: Decimal = field(default=CERO)
    ganancia_neta_pyg: Decimal = field(default=CERO)


def _redondear(valor):
    """Redondea un importe a dos decimales (mitad hacia arriba)."""
    return Decimal(valor or 0).quantize(CENTIMOS, rounding=ROUND_HALF_UP)


def factor_comision_a_pyg(transaccion):
    """
    Factor que convierte la comisión y el IVA guardados en la transacción a Guaraníes.

    Args:
        transaccion (CurrencyPurchaseTransaction | CurrencySaleTransaction): Operación.

    Returns:
        Decimal: Multiplicador a aplicar (1 si ya están en PYG).
    """
    if transaccion.payment_method_type == PAGO_VENTANILLA:
        if transaccion.from_currency == 'PYG':
            return Decimal('1')
        return Decimal(transaccion.applied_rate or 0)
    if transaccion.to_currency == 'PYG':
        return Decimal('1')
    convertido = Decimal(transaccion.converted_amount or 0)
    if convertido == 0:
        return Decimal('0')
    return Decimal(transaccion.total_pyg or 0) / convertido


def _filtrar(queryset, filtros):
    """
    Aplica los filtros combinables a un queryset de compras o ventas.

    Args:
        queryset (QuerySet): Compras o ventas.
        filtros (FiltrosReporte): Filtros elegidos.

    Returns:
        QuerySet: Queryset filtrado (solo operaciones contabilizables).
    """
    queryset = queryset.filter(status=ESTADO_CONTABLE)
    zona = timezone.get_current_timezone()
    if filtros.fecha_desde:
        queryset = queryset.filter(timestamp__gte=timezone.make_aware(datetime.combine(filtros.fecha_desde, time.min), zona))
    if filtros.fecha_hasta:
        queryset = queryset.filter(timestamp__lte=timezone.make_aware(datetime.combine(filtros.fecha_hasta, time.max), zona))
    if filtros.moneda:
        queryset = queryset.filter(Q(from_currency=filtros.moneda) | Q(to_currency=filtros.moneda))
    if filtros.cajero_id:
        queryset = queryset.filter(turno__cajero_id=filtros.cajero_id)
    if filtros.cliente_id:
        queryset = queryset.filter(cliente_id=filtros.cliente_id)
    return queryset.select_related('cliente', 'user', 'turno__cajero')


def _a_fila(transaccion, tipo):
    """
    Convierte una transacción en una ``FilaReporte`` consolidada en PYG.

    Args:
        transaccion (CurrencyPurchaseTransaction | CurrencySaleTransaction): Operación.
        tipo (str): ``COMPRA`` o ``VENTA``.

    Returns:
        FilaReporte: Fila del reporte.
    """
    factor = factor_comision_a_pyg(transaccion)
    comision = _redondear(Decimal(transaccion.commission_amount or 0) * factor)
    iva = _redondear(Decimal(transaccion.tax_amount or 0) * factor)
    operador = transaccion.turno.cajero if transaccion.turno_id else transaccion.user
    return FilaReporte(
        fecha=timezone.localtime(transaccion.timestamp),
        tipo=tipo,
        canal='Ventanilla' if transaccion.payment_method_type == PAGO_VENTANILLA else 'Online',
        cliente=transaccion.cliente.nombre_o_razon_social if transaccion.cliente_id else '—',
        operador=operador.username if operador else '—',
        moneda_origen=transaccion.from_currency,
        moneda_destino=transaccion.to_currency,
        monto_origen=_redondear(transaccion.amount),
        tasa_aplicada=Decimal(transaccion.applied_rate or 0),
        volumen_pyg=_redondear(transaccion.total_pyg),
        comision_pyg=comision,
        iva_pyg=iva,
        ganancia_neta_pyg=comision - iva,
    )


def obtener_filas(filtros):
    """
    Devuelve las operaciones del reporte, de la más reciente a la más antigua.

    Args:
        filtros (FiltrosReporte): Filtros elegidos.

    Returns:
        list[FilaReporte]: Filas consolidadas en PYG.
    """
    filas = []
    if filtros.tipo in ('', None, TIPO_COMPRA):
        filas += [_a_fila(t, TIPO_COMPRA) for t in _filtrar(CurrencyPurchaseTransaction.objects.all(), filtros)]
    if filtros.tipo in ('', None, TIPO_VENTA):
        filas += [_a_fila(t, TIPO_VENTA) for t in _filtrar(CurrencySaleTransaction.objects.all(), filtros)]
    filas.sort(key=lambda f: f.fecha, reverse=True)
    return filas


def calcular_totales(filas):
    """
    Acumula volúmenes, comisiones, IVA y ganancia neta de las filas.

    Args:
        filas (list[FilaReporte]): Filas del reporte.

    Returns:
        TotalesReporte: Totales del período.
    """
    totales = TotalesReporte()
    for fila in filas:
        totales.cantidad += 1
        if fila.tipo == TIPO_COMPRA:
            totales.cantidad_compras += 1
            totales.volumen_compras_pyg += fila.volumen_pyg
        else:
            totales.cantidad_ventas += 1
            totales.volumen_ventas_pyg += fila.volumen_pyg
        totales.volumen_total_pyg += fila.volumen_pyg
        totales.comision_pyg += fila.comision_pyg
        totales.iva_pyg += fila.iva_pyg
        totales.ganancia_neta_pyg += fila.ganancia_neta_pyg
    return totales


def generar_reporte(filtros):
    """
    Genera las filas y los totales del reporte.

    Args:
        filtros (FiltrosReporte): Filtros elegidos.

    Returns:
        tuple[list[FilaReporte], TotalesReporte]: Filas y totales.
    """
    filas = obtener_filas(filtros)
    return filas, calcular_totales(filas)


def descripcion_filtros(filtros, cajero=None, cliente=None):
    """
    Texto legible de los filtros aplicados, para el encabezado de los archivos exportados.

    Args:
        filtros (FiltrosReporte): Filtros elegidos.
        cajero (User | None): Cajero filtrado, para mostrar su nombre.
        cliente (Cliente | None): Cliente filtrado, para mostrar su nombre.

    Returns:
        str: Descripción de los filtros, o "Sin filtros".
    """
    partes = []
    if filtros.fecha_desde:
        partes.append(f"Desde {filtros.fecha_desde.strftime('%d/%m/%Y')}")
    if filtros.fecha_hasta:
        partes.append(f"Hasta {filtros.fecha_hasta.strftime('%d/%m/%Y')}")
    if filtros.tipo:
        partes.append(f"Tipo: {filtros.tipo.capitalize()}")
    if filtros.moneda:
        partes.append(f"Moneda: {filtros.moneda}")
    if cajero is not None:
        partes.append(f"Cajero: {cajero.username}")
    if cliente is not None:
        partes.append(f"Cliente: {cliente.nombre_o_razon_social}")
    return ' | '.join(partes) if partes else 'Sin filtros'


def _filas_resumen(totales):
    """Filas (etiqueta, valor) del resumen de totales que se agregan al pie de cada archivo."""
    return [
        ('Cantidad de operaciones', totales.cantidad),
        ('Volumen compras (PYG)', totales.volumen_compras_pyg),
        ('Volumen ventas (PYG)', totales.volumen_ventas_pyg),
        ('Volumen total operado (PYG)', totales.volumen_total_pyg),
        ('Comisiones (PYG)', totales.comision_pyg),
        ('IVA sobre comisiones (PYG)', totales.iva_pyg),
        ('Ganancia neta (PYG)', totales.ganancia_neta_pyg),
    ]


def exportar_csv(filas, totales, descripcion=''):
    """
    Genera el reporte en CSV (separador ``;`` y BOM UTF-8 para que Excel lo abra bien).

    Args:
        filas (list[FilaReporte]): Filas del reporte.
        totales (TotalesReporte): Totales del período.
        descripcion (str): Filtros aplicados.

    Returns:
        bytes: Contenido del archivo.
    """
    salida = io.StringIO()
    escritor = csv.writer(salida, delimiter=';')
    escritor.writerow(['Global Exchange - Reporte de Transacciones y Ganancias (PYG)'])
    escritor.writerow([f'Filtros: {descripcion or "Sin filtros"}'])
    escritor.writerow([])
    escritor.writerow(COLUMNAS_EXPORTACION)
    for fila in filas:
        escritor.writerow(fila.como_lista())
    escritor.writerow([])
    escritor.writerow(['RESUMEN DE TOTALES'])
    for etiqueta, valor in _filas_resumen(totales):
        escritor.writerow([etiqueta, valor])
    return ('\ufeff' + salida.getvalue()).encode('utf-8')
