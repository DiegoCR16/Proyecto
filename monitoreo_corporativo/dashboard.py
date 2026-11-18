# -*- coding: utf-8 -*-
"""
Dashboard de monitoreo de ganancias cambiarias (PSE-24).

Integra en un solo panel:

* KPIs de ganancia neta, volumen y cantidad de operaciones del día y del mes.
* Cotizaciones activas (compra y venta en Guaraníes) del catálogo de divisas.
* Ganancia neta mensual por divisa, para el gráfico de barras.

La moneda base obligatoria es el Guaraní (PYG): todos los importes se consolidan en PYG
con el mismo cálculo del reporte financiero de PSE-41 (``monitoreo_corporativo.reportes``):
solo operaciones ``SUCCESS`` y ganancia neta = comisión − IVA, convertidas a PYG.

La página se actualiza en tiempo real por polling: el navegador consulta periódicamente
la API JSON (``datos_dashboard``), igual que la API de evolución de tasas (PSE-10).
"""
from datetime import date
from decimal import Decimal

from django.utils import timezone

from tasas_cambio.models import ExchangeRate

from . import reportes

MONEDA_BASE = 'PYG'
MESES_PERMITIDOS = (6, 12)
MESES_POR_DEFECTO = 6
INTERVALO_ACTUALIZACION_SEGUNDOS = 30
NOMBRES_MES = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
CERO = Decimal('0.00')


def divisa_de_operacion(fila):
    """
    Divisa extranjera a la que se atribuye la ganancia de una operación.

    Es la moneda distinta de PYG que interviene en la operación. En las operaciones
    cruzadas (ninguna es PYG) se usa la divisa de origen.

    Args:
        fila (reportes.FilaReporte): Operación consolidada.

    Returns:
        str: Código de la divisa (ej. ``USD``).
    """
    if fila.moneda_origen != MONEDA_BASE:
        return fila.moneda_origen
    return fila.moneda_destino


def ultimos_meses(cantidad, hoy=None):
    """
    Lista de los últimos meses, del más antiguo al actual (inclusive).

    Args:
        cantidad (int): Cantidad de meses.
        hoy (date | None): Fecha de referencia (por defecto, hoy en hora local).

    Returns:
        list[tuple[int, int]]: Pares (año, mes).
    """
    hoy = hoy or timezone.localdate()
    meses = []
    anio, mes = hoy.year, hoy.month
    for _ in range(cantidad):
        meses.append((anio, mes))
        mes -= 1
        if mes == 0:
            anio, mes = anio - 1, 12
    return list(reversed(meses))


def etiqueta_mes(anio, mes):
    """
    Etiqueta corta de un mes para el eje del gráfico.

    Args:
        anio (int): Año.
        mes (int): Mes (1-12).

    Returns:
        str: Ej. ``Oct 2026``.
    """
    return f'{NOMBRES_MES[mes - 1]} {anio}'


def ganancias_mensuales_por_divisa(meses=MESES_POR_DEFECTO, hoy=None):
    """
    Ganancia neta en PYG agrupada por mes y por divisa, lista para el gráfico de barras.

    Args:
        meses (int): Cantidad de meses hacia atrás (incluye el actual).
        hoy (date | None): Fecha de referencia.

    Returns:
        dict: ``labels`` (etiquetas de mes), ``series`` (lista de ``{'divisa', 'data'}`` con un
        importe por mes, en PYG) y ``totales`` (ganancia neta total de cada mes, en PYG).
    """
    periodo = ultimos_meses(meses, hoy)
    indice = {clave: i for i, clave in enumerate(periodo)}
    anio, mes = periodo[0]
    filas = reportes.obtener_filas(reportes.FiltrosReporte(fecha_desde=date(anio, mes, 1), fecha_hasta=hoy))

    por_divisa = {}
    totales = [CERO] * len(periodo)
    for fila in filas:
        posicion = indice.get((fila.fecha.year, fila.fecha.month))
        if posicion is None:
            continue
        divisa = divisa_de_operacion(fila)
        por_divisa.setdefault(divisa, [CERO] * len(periodo))
        por_divisa[divisa][posicion] += fila.ganancia_neta_pyg
        totales[posicion] += fila.ganancia_neta_pyg

    return {
        'labels': [etiqueta_mes(a, m) for a, m in periodo],
        'series': [{'divisa': d, 'data': por_divisa[d]} for d in sorted(por_divisa)],
        'totales': totales,
    }


def kpis_periodo(desde, hasta=None):
    """
    Indicadores consolidados en PYG de un período.

    Args:
        desde (date): Fecha inicial inclusive.
        hasta (date | None): Fecha final inclusive.

    Returns:
        dict: ``ganancia_neta_pyg``, ``volumen_pyg``, ``comision_pyg`` y ``operaciones``.
    """
    _, totales = reportes.generar_reporte(reportes.FiltrosReporte(fecha_desde=desde, fecha_hasta=hasta))
    return {
        'ganancia_neta_pyg': totales.ganancia_neta_pyg,
        'volumen_pyg': totales.volumen_total_pyg,
        'comision_pyg': totales.comision_pyg,
        'operaciones': totales.cantidad,
    }


def cotizaciones_activas():
    """
    Cotizaciones vigentes del catálogo de divisas (sin la moneda base).

    Returns:
        list[dict]: ``codigo``, ``nombre``, ``simbolo``, ``compra``, ``venta`` (en PYG) y ``actualizado``.
    """
    return [
        {
            'codigo': tasa.currency_code,
            'nombre': tasa.currency_name,
            'simbolo': tasa.symbol,
            'compra': tasa.buy_rate,
            'venta': tasa.sell_rate,
            'actualizado': timezone.localtime(tasa.last_updated) if tasa.last_updated else None,
        }
        for tasa in ExchangeRate.objects.exclude(currency_code=MONEDA_BASE).order_by('currency_code')
    ]


def datos_dashboard(meses=MESES_POR_DEFECTO, hoy=None):
    """
    Todos los datos del dashboard: KPIs del día y del mes, cotizaciones y gráfico mensual.

    Args:
        meses (int): Meses del gráfico (6 o 12; otro valor usa el valor por defecto).
        hoy (date | None): Fecha de referencia.

    Returns:
        dict: ``moneda_base``, ``generado``, ``hoy``, ``mes``, ``cotizaciones`` y ``grafico``.
    """
    meses = meses if meses in MESES_PERMITIDOS else MESES_POR_DEFECTO
    hoy = hoy or timezone.localdate()
    return {
        'moneda_base': MONEDA_BASE,
        'generado': timezone.localtime(),
        'hoy': kpis_periodo(hoy, hoy),
        'mes': kpis_periodo(hoy.replace(day=1), hoy),
        'cotizaciones': cotizaciones_activas(),
        'grafico': ganancias_mensuales_por_divisa(meses, hoy),
    }


def _a_json(valor):
    """Convierte recursivamente Decimal a float y fechas a texto ISO para JsonResponse."""
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, dict):
        return {k: _a_json(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_a_json(v) for v in valor]
    if hasattr(valor, 'isoformat'):
        return valor.isoformat()
    return valor


def datos_dashboard_json(meses=MESES_POR_DEFECTO, hoy=None):
    """
    Versión serializable a JSON de ``datos_dashboard`` (para la API de polling).

    Args:
        meses (int): Meses del gráfico.
        hoy (date | None): Fecha de referencia.

    Returns:
        dict: Datos del dashboard con números como float y fechas en ISO 8601.
    """
    return _a_json(datos_dashboard(meses, hoy))
