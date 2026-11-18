# -*- coding: utf-8 -*-
"""
Suite independiente de pruebas unitarias y de integración para la Historia de Usuario PSE-24:
Monitoreo y Dashboard de Ganancias Cambiarias (Épica PSE-23).

Valida:
* Datos del gráfico de barras mensual por divisa (meses, series, totales, operaciones cruzadas).
* KPIs del día y del mes consolidados en Guaraníes (PYG), solo con operaciones confirmadas.
* Cotizaciones activas (sin la moneda base PYG).
* Integración en tiempo real: la API JSON refleja nuevas operaciones y cambios de tasa.
* Renderizado del dashboard (KPIs, gráfico Chart.js, polling) y control de acceso.
"""
import json
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from authentication.models import Cliente, Role, UserProfile
from monitoreo_corporativo import dashboard, reportes
from procesamiento_operaciones.models import CurrencyPurchaseTransaction, CurrencySaleTransaction
from tasas_cambio.models import ExchangeRate

HOY = date(2026, 10, 20)


def _fecha(anio, mes, dia, hora=12):
    """Datetime con zona horaria local para fijar el ``timestamp`` de las operaciones de prueba."""
    return timezone.make_aware(datetime(anio, mes, dia, hora, 0), timezone.get_current_timezone())


def crear_operacion(modelo, fecha=None, desde='PYG', hacia='USD', convertido='0', tasa='1', comision='0',
                    iva='0', total='0', estado='SUCCESS', ventanilla=False, cliente=None):
    """
    Crea una compra o venta de prueba y, si se indica, fija su fecha (``timestamp`` es auto_now_add).

    Args:
        modelo: ``CurrencyPurchaseTransaction`` o ``CurrencySaleTransaction``.
        fecha (datetime | None): Fecha de la operación; None deja la fecha actual.
        desde (str): Moneda de origen.
        hacia (str): Moneda de destino.
        convertido (str): Monto convertido.
        tasa (str): Tasa aplicada.
        comision (str): Comisión guardada (en la moneda que corresponda al canal).
        iva (str): IVA de la comisión guardado.
        total (str): Volumen en PYG.
        estado (str): Estado de la transacción.
        ventanilla (bool): True si es una operación de ventanilla.
        cliente (Cliente | None): Cliente de la operación.

    Returns:
        Model: La transacción creada.
    """
    tx = modelo.objects.create(
        cliente=cliente, from_currency=desde, to_currency=hacia, amount=Decimal('1'),
        converted_amount=Decimal(convertido), applied_rate=Decimal(tasa), standard_rate=Decimal(tasa),
        commission_amount=Decimal(comision), tax_amount=Decimal(iva), total_pyg=Decimal(total), status=estado,
        payment_method_type=reportes.PAGO_VENTANILLA if ventanilla else 'TRANSFERENCIA',
    )
    if fecha is not None:
        modelo.objects.filter(pk=tx.pk).update(timestamp=fecha)
        tx.refresh_from_db()
    return tx


class DatosHistoricosPSE24(TestCase):
    """
    Operaciones en distintos meses (referencia: 20/10/2026).

    Ganancia neta en PYG (comisión − IVA):
        05/10  compra online  PYG→USD   3.650 − 365   = 3.285   USD
        20/10  venta online   USD→PYG  10.000 − 1.000 = 9.000   USD (hoy)
        07/10  compra cruzada USD→EUR   8.100 − 810   = 7.290   USD (se atribuye a la divisa de origen)
        10/09  venta online   EUR→PYG   4.000 − 400   = 3.600   EUR
        15/08  compra ventan. USD→PYG   1 USD × 7.300 = 7.300   USD
        01/03  venta online   EUR→PYG   2.000 − 200   = 1.800   EUR (fuera de 6 meses, dentro de 12)
        20/10  FAILED (excluida)
    """

    def setUp(self):
        """Crea las operaciones de prueba."""
        crear_operacion(CurrencyPurchaseTransaction, _fecha(2026, 10, 5), 'PYG', 'USD', convertido='100',
                        tasa='7300', comision='0.50', iva='0.05', total='730000')
        crear_operacion(CurrencySaleTransaction, _fecha(2026, 10, 20, 10), 'USD', 'PYG', convertido='2000000',
                        tasa='7400', comision='10000', iva='1000', total='2000000')
        crear_operacion(CurrencyPurchaseTransaction, _fecha(2026, 10, 7), 'USD', 'EUR', convertido='100',
                        tasa='0.9', comision='1.00', iva='0.10', total='810000')
        crear_operacion(CurrencySaleTransaction, _fecha(2026, 9, 10), 'EUR', 'PYG', convertido='800000',
                        tasa='8000', comision='4000', iva='400', total='800000')
        crear_operacion(CurrencyPurchaseTransaction, _fecha(2026, 8, 15), 'USD', 'PYG', convertido='1452700',
                        tasa='7300', comision='1', iva='0', total='1460000', ventanilla=True)
        crear_operacion(CurrencySaleTransaction, _fecha(2026, 3, 1), 'EUR', 'PYG', convertido='400000',
                        tasa='8000', comision='2000', iva='200', total='400000')
        crear_operacion(CurrencySaleTransaction, _fecha(2026, 10, 20, 11), 'USD', 'PYG', convertido='5000000',
                        tasa='7400', comision='99999', iva='0', total='5000000', estado='FAILED')


class GraficoMensualPorDivisaTests(DatosHistoricosPSE24):
    """Pruebas unitarias de los datos del gráfico de barras mensual por divisa."""

    def test_ultimos_meses_cruza_el_anio(self):
        """Los últimos meses se calculan del más antiguo al actual, cruzando el cambio de año."""
        self.assertEqual(dashboard.ultimos_meses(3, date(2026, 2, 10)), [(2025, 12), (2026, 1), (2026, 2)])
        self.assertEqual(len(dashboard.ultimos_meses(12, HOY)), 12)

    def test_etiqueta_mes(self):
        """La etiqueta del eje es el mes abreviado y el año."""
        self.assertEqual(dashboard.etiqueta_mes(2026, 10), 'Oct 2026')

    def test_divisa_de_operacion(self):
        """Se atribuye a la divisa distinta de PYG; en cruzadas, a la de origen."""
        fila = reportes.FilaReporte(timezone.now(), 'COMPRA', 'Online', '', '', 'PYG', 'USD',
                                    Decimal(0), Decimal(0), Decimal(0), Decimal(0), Decimal(0), Decimal(0))
        self.assertEqual(dashboard.divisa_de_operacion(fila), 'USD')
        fila.moneda_origen, fila.moneda_destino = 'EUR', 'PYG'
        self.assertEqual(dashboard.divisa_de_operacion(fila), 'EUR')
        fila.moneda_origen, fila.moneda_destino = 'USD', 'EUR'
        self.assertEqual(dashboard.divisa_de_operacion(fila), 'USD')

    def test_series_por_divisa_seis_meses(self):
        """Seis meses (may–oct): una serie por divisa con la ganancia neta de cada mes en PYG."""
        datos = dashboard.ganancias_mensuales_por_divisa(6, HOY)
        self.assertEqual(datos['labels'], ['May 2026', 'Jun 2026', 'Jul 2026', 'Ago 2026', 'Sep 2026', 'Oct 2026'])
        series = {s['divisa']: s['data'] for s in datos['series']}
        self.assertEqual(sorted(series), ['EUR', 'USD'])
        self.assertEqual(series['USD'], [Decimal('0.00')] * 3 + [Decimal('7300.00'), Decimal('0.00'), Decimal('19575.00')])
        self.assertEqual(series['EUR'], [Decimal('0.00')] * 4 + [Decimal('3600.00'), Decimal('0.00')])

    def test_totales_por_mes(self):
        """El total de cada mes suma todas las divisas."""
        totales = dashboard.ganancias_mensuales_por_divisa(6, HOY)['totales']
        self.assertEqual(totales[-3:], [Decimal('7300.00'), Decimal('3600.00'), Decimal('19575.00')])

    def test_doce_meses_incluye_operaciones_antiguas(self):
        """Con 12 meses (nov 2025–oct 2026) entra la operación de marzo."""
        datos = dashboard.ganancias_mensuales_por_divisa(12, HOY)
        self.assertEqual(datos['labels'][0], 'Nov 2025')
        eur = next(s['data'] for s in datos['series'] if s['divisa'] == 'EUR')
        self.assertEqual(eur[datos['labels'].index('Mar 2026')], Decimal('1800.00'))

    def test_operacion_fallida_no_suma(self):
        """La operación FAILED del 20/10 no aparece en el gráfico."""
        usd = next(s['data'] for s in dashboard.ganancias_mensuales_por_divisa(6, HOY)['series'] if s['divisa'] == 'USD')
        self.assertEqual(usd[-1], Decimal('19575.00'))

    def test_sin_operaciones(self):
        """Sin operaciones, no hay series y los totales son cero."""
        datos = dashboard.ganancias_mensuales_por_divisa(6, date(2020, 1, 31))
        self.assertEqual(datos['series'], [])
        self.assertEqual(datos['totales'], [Decimal('0.00')] * 6)


class KPIsConsolidadosPYGTests(DatosHistoricosPSE24):
    """Pruebas de los indicadores consolidados en Guaraníes."""

    def test_kpis_del_dia(self):
        """Hoy solo cuenta la venta confirmada (la FAILED se excluye)."""
        kpis = dashboard.kpis_periodo(HOY, HOY)
        self.assertEqual(kpis['operaciones'], 1)
        self.assertEqual(kpis['ganancia_neta_pyg'], Decimal('9000.00'))
        self.assertEqual(kpis['volumen_pyg'], Decimal('2000000.00'))

    def test_kpis_del_mes(self):
        """El mes suma las tres operaciones confirmadas de octubre, todo en PYG."""
        kpis = dashboard.kpis_periodo(date(2026, 10, 1), HOY)
        self.assertEqual(kpis['operaciones'], 3)
        self.assertEqual(kpis['ganancia_neta_pyg'], Decimal('19575.00'))
        self.assertEqual(kpis['volumen_pyg'], Decimal('3540000.00'))
        self.assertEqual(kpis['comision_pyg'], Decimal('21750.00'))

    def test_kpis_coinciden_con_reporte_financiero(self):
        """El dashboard y el reporte de PSE-41 dan la misma ganancia neta para el mismo período."""
        _, totales = reportes.generar_reporte(reportes.FiltrosReporte(fecha_desde=date(2026, 10, 1), fecha_hasta=HOY))
        self.assertEqual(dashboard.kpis_periodo(date(2026, 10, 1), HOY)['ganancia_neta_pyg'], totales.ganancia_neta_pyg)

    def test_datos_dashboard_moneda_base_pyg(self):
        """La moneda base del dashboard es obligatoriamente PYG."""
        datos = dashboard.datos_dashboard(6, HOY)
        self.assertEqual(datos['moneda_base'], 'PYG')
        self.assertEqual(datos['hoy']['ganancia_neta_pyg'], Decimal('9000.00'))
        self.assertEqual(datos['mes']['ganancia_neta_pyg'], Decimal('19575.00'))

    def test_meses_invalidos_usan_valor_por_defecto(self):
        """Un período distinto de 6 o 12 usa 6 meses."""
        self.assertEqual(len(dashboard.datos_dashboard(99, HOY)['grafico']['labels']), 6)

    def test_serializacion_json(self):
        """La versión JSON convierte Decimal a float y fechas a ISO 8601."""
        datos = dashboard.datos_dashboard_json(6, HOY)
        json.dumps(datos)
        self.assertEqual(datos['mes']['ganancia_neta_pyg'], 19575.0)
        self.assertIsInstance(datos['generado'], str)


class CotizacionesActivasTests(TestCase):
    """Pruebas de las cotizaciones activas del dashboard."""

    def test_cotizaciones_sin_moneda_base(self):
        """Lista las divisas con compra y venta en PYG, sin incluir PYG."""
        ExchangeRate.objects.create(currency_code='USD', currency_name='Dólar', buy_rate=Decimal('7300'), sell_rate=Decimal('7400'))
        ExchangeRate.objects.create(currency_code='PYG', currency_name='Guaraní', buy_rate=Decimal('1'), sell_rate=Decimal('1'))
        cotizaciones = dashboard.cotizaciones_activas()
        self.assertEqual([c['codigo'] for c in cotizaciones], ['USD'])
        self.assertEqual(cotizaciones[0]['compra'], Decimal('7300.0000'))
        self.assertEqual(cotizaciones[0]['venta'], Decimal('7400.0000'))
        self.assertIsNotNone(cotizaciones[0]['actualizado'])


class DashboardGananciasVistaTests(TestCase):
    """Pruebas de integración de la vista, la API de tiempo real y el control de acceso."""

    def setUp(self):
        """Crea usuarios por rol, una cotización y una venta confirmada de hoy."""
        self.http = Client()
        self.url = reverse('monitoreo_corporativo:dashboard_ganancias')
        self.api = reverse('monitoreo_corporativo:dashboard_ganancias_api')
        self.admin = User.objects.create_superuser(username='admin_pse24', password='x')
        UserProfile.objects.create(user=self.admin, role=Role.objects.get_or_create(name='Admin')[0])
        self.analista = User.objects.create_user(username='analista_pse24', password='x')
        UserProfile.objects.create(user=self.analista, role=Role.objects.get_or_create(name='Analista')[0])
        self.usuario_cliente = User.objects.create_user(username='cliente_pse24', password='x')
        UserProfile.objects.create(user=self.usuario_cliente, role=Role.objects.get_or_create(name='Cliente')[0])
        self.usd = ExchangeRate.objects.create(currency_code='USD', currency_name='Dólar', buy_rate=Decimal('7300'), sell_rate=Decimal('7400'))
        cliente = Cliente.objects.create(nombre_o_razon_social='Cliente Gamma', documento_identidad='2424001', email='g@x.com')
        crear_operacion(CurrencySaleTransaction, None, 'USD', 'PYG', convertido='2000000', tasa='7400',
                        comision='10000', iva='1000', total='2000000', cliente=cliente)

    def test_admin_ve_dashboard(self):
        """El Administrador ve KPIs, cotizaciones, el gráfico y la indicación de PYG."""
        self.http.force_login(self.admin)
        respuesta = self.http.get(self.url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, 'monitoreo_corporativo/dashboard_ganancias.html')
        self.assertEqual(respuesta.context['datos']['hoy']['ganancia_neta_pyg'], Decimal('9000.00'))
        self.assertContains(respuesta, 'id="grafico-ganancias"')
        self.assertContains(respuesta, 'cdn.jsdelivr.net/npm/chart.js')
        self.assertContains(respuesta, 'Consolidación contable en Guaraníes (PYG)')
        self.assertContains(respuesta, 'Cotizaciones Activas')
        self.assertContains(respuesta, 'USD')

    def test_dashboard_incluye_datos_iniciales_y_polling(self):
        """La página trae los datos iniciales en JSON y consulta la API periódicamente."""
        self.http.force_login(self.admin)
        respuesta = self.http.get(self.url)
        self.assertContains(respuesta, 'id="datos-dashboard"')
        self.assertContains(respuesta, 'setInterval(actualizar, INTERVALO_MS)')
        self.assertContains(respuesta, self.api)
        self.assertEqual(respuesta.context['intervalo_segundos'], dashboard.INTERVALO_ACTUALIZACION_SEGUNDOS)

    def test_analista_tiene_acceso(self):
        """El Analista accede al dashboard y a la API."""
        self.http.force_login(self.analista)
        self.assertEqual(self.http.get(self.url).status_code, 200)
        self.assertEqual(self.http.get(self.api).status_code, 200)

    def test_cliente_sin_acceso(self):
        """Un cliente no accede ni al dashboard ni a la API."""
        self.http.force_login(self.usuario_cliente)
        self.assertEqual(self.http.get(self.url).status_code, 302)
        self.assertEqual(self.http.get(self.api).status_code, 302)

    def test_anonimo_redirige_a_login(self):
        """Sin sesión se redirige al login."""
        self.assertEqual(self.http.get(self.api).status_code, 302)

    def test_api_estructura_json_en_pyg(self):
        """La API devuelve KPIs, cotizaciones y gráfico con la moneda base PYG."""
        self.http.force_login(self.admin)
        datos = self.http.get(self.api).json()
        self.assertEqual(datos['moneda_base'], 'PYG')
        self.assertEqual(datos['hoy']['operaciones'], 1)
        self.assertEqual(datos['hoy']['ganancia_neta_pyg'], 9000.0)
        self.assertEqual(datos['cotizaciones'][0]['codigo'], 'USD')
        self.assertEqual(datos['grafico']['series'][0]['divisa'], 'USD')
        self.assertEqual(datos['grafico']['series'][0]['data'][-1], 9000.0)

    def test_api_refleja_nuevas_operaciones_y_tasas(self):
        """Tiempo real: una operación nueva y un cambio de tasa se ven en la siguiente consulta."""
        self.http.force_login(self.admin)
        antes = self.http.get(self.api).json()
        crear_operacion(CurrencyPurchaseTransaction, None, 'PYG', 'USD', convertido='100', tasa='7300',
                        comision='0.50', iva='0.05', total='730000')
        self.usd.buy_rate = Decimal('7350')
        self.usd.save()
        despues = self.http.get(self.api).json()
        self.assertEqual(antes['hoy']['operaciones'], 1)
        self.assertEqual(despues['hoy']['operaciones'], 2)
        self.assertEqual(despues['hoy']['ganancia_neta_pyg'], 12285.0)
        self.assertEqual(despues['cotizaciones'][0]['compra'], 7350.0)

    def test_parametro_meses(self):
        """El parámetro meses acepta 6 o 12; otro valor usa 6."""
        self.http.force_login(self.admin)
        self.assertEqual(len(self.http.get(self.api, {'meses': 12}).json()['grafico']['labels']), 12)
        self.assertEqual(len(self.http.get(self.api, {'meses': 'abc'}).json()['grafico']['labels']), 6)
        self.assertEqual(self.http.get(self.url, {'meses': 12}).context['meses'], 12)

    def test_accesos_en_dashboards(self):
        """El dashboard del Admin y el del Analista muestran el acceso al dashboard de ganancias."""
        for usuario in (self.admin, self.analista):
            self.http.force_login(usuario)
            respuesta = self.http.get(reverse('dashboard_redirect'), follow=True)
            self.assertContains(respuesta, self.url)
