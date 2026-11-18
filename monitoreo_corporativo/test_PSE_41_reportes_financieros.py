# -*- coding: utf-8 -*-
"""
Suite independiente de pruebas unitarias y de integración para la Historia de Usuario PSE-41:
Generación y Exportación de Reportes de Transacciones y Ganancias (Épica PSE-23).

Valida:
* Filtros combinables: rango de fechas, tipo de operación, moneda, cajero y cliente.
* Consolidación en PYG de volúmenes, comisiones, IVA y ganancia neta (comisión − IVA),
  incluida la conversión de comisiones que no se guardaron en Guaraníes.
* Exclusión de operaciones no confirmadas (FAILED, PENDING, ANULADA).
* Generación y estructura del CSV (Excel/CSV) y de la vista de impresión para PDF (window.print()).
* Control de acceso: Administrador y Analista sí; Cliente no.
"""
import csv
import io
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from authentication.models import Cliente, Role, UserProfile
from authentication.permisos import PERMISOS_POR_DEFECTO, ROL_ANALISTA
from caja.models import Caja, TurnoCaja
from monitoreo_corporativo import reportes
from procesamiento_operaciones.models import CurrencyPurchaseTransaction, CurrencySaleTransaction
from tasas_cambio.models import ExchangeRate


def _fecha(anio, mes, dia, hora=12):
    """Datetime con zona horaria local para fijar el ``timestamp`` de las operaciones de prueba."""
    return timezone.make_aware(datetime(anio, mes, dia, hora, 0), timezone.get_current_timezone())


class BaseReportePSE41(TestCase):
    """
    Datos comunes: dos clientes, un cajero con turno, y operaciones online y de ventanilla
    en distintas fechas, tipos y monedas.

    Operaciones confirmadas (SUCCESS):
        c1  Compra online   PYG→USD  01/10  cliente A  volumen 730.000   comisión 0,50 USD → 3.650 PYG, IVA 365
        c2  Compra ventan.  USD→PYG  05/10  cliente B  cajero  volumen 1.460.000  comisión 1 USD → 7.300, IVA 0
        v1  Venta online    USD→PYG  10/10  cliente A  volumen 2.000.000  comisión 10.000, IVA 1.000
        v2  Venta ventan.   EUR→PYG  15/10  cliente B  cajero  volumen 800.000  comisión 4.000 PYG*, IVA 0
    No confirmadas (deben excluirse): una compra FAILED, una venta PENDING y una venta ANULADA.
    """

    def setUp(self):
        """Crea usuarios, clientes, turno de caja y las operaciones de prueba."""
        self.http = Client()
        self.url = reverse('monitoreo_corporativo:reporte_financiero')

        ExchangeRate.objects.create(currency_code='USD', currency_name='Dólar', buy_rate=Decimal('7300'), sell_rate=Decimal('7400'))
        ExchangeRate.objects.create(currency_code='EUR', currency_name='Euro', buy_rate=Decimal('8000'), sell_rate=Decimal('8100'))

        self.admin = User.objects.create_superuser(username='admin_pse41', password='x')
        UserProfile.objects.create(user=self.admin, role=Role.objects.get_or_create(name='Admin')[0])
        self.analista = User.objects.create_user(username='analista_pse41', password='x')
        UserProfile.objects.create(user=self.analista, role=Role.objects.get_or_create(name='Analista')[0])
        self.usuario_cliente = User.objects.create_user(username='cliente_pse41', password='x')
        UserProfile.objects.create(user=self.usuario_cliente, role=Role.objects.get_or_create(name='Cliente')[0])
        self.cajero = User.objects.create_user(username='cajero_pse41', password='x')
        UserProfile.objects.create(user=self.cajero, role=Role.objects.get_or_create(name='Cajero')[0])

        self.cliente_a = Cliente.objects.create(nombre_o_razon_social='Cliente Alfa', documento_identidad='4141001', email='a@x.com')
        self.cliente_b = Cliente.objects.create(nombre_o_razon_social='Cliente Beta', documento_identidad='4141002', email='b@x.com')

        caja = Caja.objects.create(nombre='Caja PSE41', codigo='C41')
        self.turno = TurnoCaja.objects.create(caja=caja, cajero=self.cajero)

        self.c1 = self._compra(_fecha(2026, 10, 1), cliente=self.cliente_a, user=self.usuario_cliente,
                               desde='PYG', hacia='USD', monto='730000', convertido='100', tasa='7300',
                               comision='0.50', iva='0.05', total='730000')
        self.c2 = self._compra(_fecha(2026, 10, 5), cliente=self.cliente_b, user=self.cajero, turno=self.turno,
                               desde='USD', hacia='PYG', monto='200', convertido='1452700', tasa='7300',
                               comision='1.00', iva='0', total='1460000', ventanilla=True)
        self.v1 = self._venta(_fecha(2026, 10, 10), cliente=self.cliente_a, user=self.usuario_cliente,
                              desde='USD', hacia='PYG', monto='270.27', convertido='2000000', tasa='7400',
                              comision='10000', iva='1000', total='2000000')
        self.v2 = self._venta(_fecha(2026, 10, 15), cliente=self.cliente_b, user=self.cajero, turno=self.turno,
                              desde='PYG', hacia='EUR', monto='800000', convertido='98.77', tasa='8100',
                              comision='4000', iva='0', total='800000', ventanilla=True)
        # No confirmadas: no deben sumar.
        self._compra(_fecha(2026, 10, 2), cliente=self.cliente_a, desde='PYG', hacia='USD', monto='73000',
                     convertido='10', tasa='7300', comision='0.05', iva='0', total='73000', estado='FAILED')
        self._venta(_fecha(2026, 10, 3), cliente=self.cliente_a, desde='USD', hacia='PYG', monto='10',
                    convertido='74000', tasa='7400', comision='370', iva='37', total='74000', estado='PENDING')
        self._venta(_fecha(2026, 10, 4), cliente=self.cliente_b, desde='USD', hacia='PYG', monto='10',
                    convertido='74000', tasa='7400', comision='370', iva='37', total='74000', estado='ANULADA')

    def _crear(self, modelo, fecha, cliente=None, user=None, turno=None, desde='PYG', hacia='USD', monto='0',
               convertido='0', tasa='1', comision='0', iva='0', total='0', estado='SUCCESS', ventanilla=False):
        """Crea una transacción y fija su fecha (``timestamp`` es auto_now_add)."""
        tx = modelo.objects.create(
            user=user, cliente=cliente, turno=turno, from_currency=desde, to_currency=hacia,
            amount=Decimal(monto), converted_amount=Decimal(convertido), applied_rate=Decimal(tasa),
            standard_rate=Decimal(tasa), commission_amount=Decimal(comision), tax_amount=Decimal(iva),
            total_pyg=Decimal(total), status=estado,
            payment_method_type=reportes.PAGO_VENTANILLA if ventanilla else 'TRANSFERENCIA',
        )
        modelo.objects.filter(pk=tx.pk).update(timestamp=fecha)
        tx.refresh_from_db()
        return tx

    def _compra(self, fecha, **kwargs):
        """Crea una compra de prueba."""
        return self._crear(CurrencyPurchaseTransaction, fecha, **kwargs)

    def _venta(self, fecha, **kwargs):
        """Crea una venta de prueba."""
        return self._crear(CurrencySaleTransaction, fecha, **kwargs)


class ConsolidacionPYGTests(BaseReportePSE41):
    """Pruebas unitarias del cálculo consolidado en PYG."""

    def test_factor_comision_online_con_destino_extranjero(self):
        """Online con destino USD: la comisión (en USD) se convierte con total_pyg / converted_amount."""
        self.assertEqual(reportes.factor_comision_a_pyg(self.c1), Decimal('7300'))

    def test_factor_comision_ventanilla_con_origen_extranjero(self):
        """Ventanilla con origen USD: la comisión (en USD) se convierte con la tasa aplicada."""
        self.assertEqual(reportes.factor_comision_a_pyg(self.c2), Decimal('7300'))

    def test_factor_comision_ya_en_pyg(self):
        """Online con destino PYG y ventanilla con origen PYG: la comisión ya está en Guaraníes."""
        self.assertEqual(reportes.factor_comision_a_pyg(self.v1), Decimal('1'))
        self.assertEqual(reportes.factor_comision_a_pyg(self.v2), Decimal('1'))

    def test_fila_convierte_comision_iva_y_ganancia(self):
        """La fila de la compra c1 queda con comisión 3.650, IVA 365 y ganancia 3.285 PYG."""
        fila = reportes._a_fila(self.c1, reportes.TIPO_COMPRA)
        self.assertEqual(fila.comision_pyg, Decimal('3650.00'))
        self.assertEqual(fila.iva_pyg, Decimal('365.00'))
        self.assertEqual(fila.ganancia_neta_pyg, Decimal('3285.00'))
        self.assertEqual(fila.canal, 'Online')

    def test_operador_es_cajero_del_turno(self):
        """En ventanilla el operador es el cajero del turno; online, el usuario que operó."""
        self.assertEqual(reportes._a_fila(self.c2, reportes.TIPO_COMPRA).operador, 'cajero_pse41')
        self.assertEqual(reportes._a_fila(self.v1, reportes.TIPO_VENTA).operador, 'cliente_pse41')

    def test_totales_sin_filtros_excluyen_no_confirmadas(self):
        """Sin filtros se suman solo las 4 operaciones SUCCESS."""
        filas, totales = reportes.generar_reporte(reportes.FiltrosReporte())
        self.assertEqual(totales.cantidad, 4)
        self.assertEqual(totales.cantidad_compras, 2)
        self.assertEqual(totales.cantidad_ventas, 2)
        self.assertEqual(totales.volumen_compras_pyg, Decimal('2190000.00'))
        self.assertEqual(totales.volumen_ventas_pyg, Decimal('2800000.00'))
        self.assertEqual(totales.volumen_total_pyg, Decimal('4990000.00'))
        self.assertEqual(totales.comision_pyg, Decimal('24950.00'))  # 3.650 + 7.300 + 10.000 + 4.000
        self.assertEqual(totales.iva_pyg, Decimal('1365.00'))        # 365 + 0 + 1.000 + 0
        self.assertEqual(totales.ganancia_neta_pyg, Decimal('23585.00'))

    def test_filas_ordenadas_de_la_mas_reciente(self):
        """Las filas se listan de la más reciente a la más antigua."""
        filas = reportes.obtener_filas(reportes.FiltrosReporte())
        self.assertEqual([f.fecha.day for f in filas], [15, 10, 5, 1])

    def test_totales_de_lista_vacia(self):
        """Sin filas, todos los totales son cero."""
        totales = reportes.calcular_totales([])
        self.assertEqual(totales.cantidad, 0)
        self.assertEqual(totales.ganancia_neta_pyg, Decimal('0.00'))


class FiltrosReporteTests(BaseReportePSE41):
    """Pruebas de los filtros combinables del servicio de reportes."""

    def _cantidad(self, **kwargs):
        """Cantidad de operaciones del reporte con los filtros dados."""
        return reportes.calcular_totales(reportes.obtener_filas(reportes.FiltrosReporte(**kwargs))).cantidad

    def test_filtro_rango_de_fechas_inclusivo(self):
        """El rango incluye los días extremos completos."""
        self.assertEqual(self._cantidad(fecha_desde=date(2026, 10, 5), fecha_hasta=date(2026, 10, 10)), 2)
        self.assertEqual(self._cantidad(fecha_desde=date(2026, 10, 15)), 1)
        self.assertEqual(self._cantidad(fecha_hasta=date(2026, 10, 1)), 1)

    def test_filtro_tipo_operacion(self):
        """COMPRA solo trae compras y VENTA solo ventas."""
        filas = reportes.obtener_filas(reportes.FiltrosReporte(tipo=reportes.TIPO_COMPRA))
        self.assertEqual({f.tipo for f in filas}, {reportes.TIPO_COMPRA})
        self.assertEqual(self._cantidad(tipo=reportes.TIPO_VENTA), 2)

    def test_filtro_moneda_en_origen_o_destino(self):
        """La moneda coincide tanto en el origen como en el destino de la operación."""
        self.assertEqual(self._cantidad(moneda='USD'), 3)
        self.assertEqual(self._cantidad(moneda='EUR'), 1)

    def test_filtro_cajero(self):
        """El cajero filtra por el turno de caja en que se registró la operación."""
        filas = reportes.obtener_filas(reportes.FiltrosReporte(cajero_id=self.cajero.pk))
        self.assertEqual({f.canal for f in filas}, {'Ventanilla'})
        self.assertEqual(len(filas), 2)

    def test_filtro_cliente(self):
        """El cliente filtra sus operaciones."""
        self.assertEqual(self._cantidad(cliente_id=self.cliente_a.pk), 2)

    def test_filtros_combinados(self):
        """Fechas + tipo + moneda + cliente se aplican en conjunto."""
        filas, totales = reportes.generar_reporte(reportes.FiltrosReporte(
            fecha_desde=date(2026, 10, 1), fecha_hasta=date(2026, 10, 31),
            tipo=reportes.TIPO_VENTA, moneda='USD', cliente_id=self.cliente_a.pk,
        ))
        self.assertEqual(len(filas), 1)
        self.assertEqual(totales.ganancia_neta_pyg, Decimal('9000.00'))

    def test_filtros_sin_resultados(self):
        """Una combinación sin coincidencias devuelve una lista vacía."""
        self.assertEqual(self._cantidad(moneda='EUR', cliente_id=self.cliente_a.pk), 0)

    def test_descripcion_filtros(self):
        """La descripción legible de los filtros se usa en el encabezado de los archivos."""
        filtros = reportes.FiltrosReporte(fecha_desde=date(2026, 10, 1), tipo='VENTA', moneda='USD')
        texto = reportes.descripcion_filtros(filtros, cajero=self.cajero, cliente=self.cliente_a)
        self.assertIn('Desde 01/10/2026', texto)
        self.assertIn('Tipo: Venta', texto)
        self.assertIn('Moneda: USD', texto)
        self.assertIn('Cajero: cajero_pse41', texto)
        self.assertIn('Cliente: Cliente Alfa', texto)
        self.assertEqual(reportes.descripcion_filtros(reportes.FiltrosReporte()), 'Sin filtros')


class ExportacionArchivosTests(BaseReportePSE41):
    """Pruebas de generación y estructura de los archivos CSV, Excel y PDF."""

    def setUp(self):
        """Genera el reporte completo una vez por prueba."""
        super().setUp()
        self.filas, self.totales = reportes.generar_reporte(reportes.FiltrosReporte())

    def test_csv_estructura_y_totales(self):
        """El CSV tiene encabezado, una línea por operación y el resumen de totales al pie."""
        contenido = reportes.exportar_csv(self.filas, self.totales, 'Sin filtros')
        self.assertTrue(contenido.startswith(b'\xef\xbb\xbf'))
        lineas = list(csv.reader(io.StringIO(contenido.decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(lineas[3], reportes.COLUMNAS_EXPORTACION)
        self.assertEqual(len(lineas[4:8]), 4)
        resumen = {l[0]: l[1] for l in lineas if len(l) == 2}
        self.assertEqual(resumen['Volumen total operado (PYG)'], '4990000.00')
        self.assertEqual(resumen['Ganancia neta (PYG)'], '23585.00')

    def test_csv_importes_por_operacion(self):
        """Cada línea del CSV trae el volumen, la comisión, el IVA y la ganancia en PYG."""
        contenido = reportes.exportar_csv(self.filas, self.totales)
        lineas = list(csv.reader(io.StringIO(contenido.decode('utf-8-sig')), delimiter=';'))
        compra_online = [l for l in lineas[4:8] if l[3] == 'Cliente Alfa' and l[1] == 'Compra'][0]
        self.assertEqual(compra_online[9:], ['730000.00', '3650.00', '365.00', '3285.00'])

    def test_csv_sin_operaciones(self):
        """Sin operaciones el CSV igual trae las columnas y el resumen en cero."""
        contenido = reportes.exportar_csv([], reportes.calcular_totales([])).decode('utf-8-sig')
        self.assertIn(';'.join(reportes.COLUMNAS_EXPORTACION), contenido)
        self.assertIn('Ganancia neta (PYG);0.00', contenido)


class VistaReporteFinancieroTests(BaseReportePSE41):
    """Pruebas de integración de la vista web, la exportación HTTP y el control de acceso."""

    def test_admin_ve_panel_con_totales(self):
        """El Administrador ve el panel, la tabla y el pie con la ganancia neta."""
        self.http.force_login(self.admin)
        respuesta = self.http.get(self.url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, 'monitoreo_corporativo/reporte_financiero.html')
        self.assertContains(respuesta, '<tfoot')
        self.assertEqual(respuesta.context['totales'].ganancia_neta_pyg, Decimal('23585.00'))
        self.assertContains(respuesta, 'Cliente Alfa')

    def test_analista_tiene_acceso(self):
        """El rol Analista tiene por defecto el permiso de reportes financieros."""
        self.assertIn('can_view_financial_reports', PERMISOS_POR_DEFECTO[ROL_ANALISTA])
        self.http.force_login(self.analista)
        self.assertEqual(self.http.get(self.url).status_code, 200)

    def test_cliente_sin_acceso(self):
        """Un cliente es redirigido a su panel con acceso denegado."""
        self.http.force_login(self.usuario_cliente)
        respuesta = self.http.get(self.url)
        self.assertEqual(respuesta.status_code, 302)
        self.assertNotIn('/monitoreo/', respuesta['Location'])

    def test_anonimo_redirige_a_login(self):
        """Sin sesión se redirige al login."""
        respuesta = self.http.get(self.url)
        self.assertEqual(respuesta.status_code, 302)

    def test_filtros_por_get(self):
        """Los filtros enviados por GET se aplican en la vista."""
        self.http.force_login(self.admin)
        respuesta = self.http.get(self.url, {'tipo': 'COMPRA', 'cajero': self.cajero.pk})
        self.assertEqual(respuesta.context['totales'].cantidad, 1)
        self.assertEqual(respuesta.context['totales'].ganancia_neta_pyg, Decimal('7300.00'))

    def test_rango_de_fechas_invalido(self):
        """Si 'Desde' es posterior a 'Hasta' se muestra el error y no se listan operaciones."""
        self.http.force_login(self.admin)
        respuesta = self.http.get(self.url, {'fecha_desde': '2026-10-20', 'fecha_hasta': '2026-10-01'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context['totales'].cantidad, 0)
        self.assertContains(respuesta, 'no puede ser posterior')

    def test_descarga_csv_respeta_filtros(self):
        """La descarga CSV usa los mismos filtros de la vista."""
        self.http.force_login(self.admin)
        respuesta = self.http.get(self.url, {'moneda': 'EUR', 'formato': 'csv'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('text/csv', respuesta['Content-Type'])
        self.assertIn('attachment; filename="reporte_financiero_', respuesta['Content-Disposition'])
        texto = respuesta.content.decode('utf-8-sig')
        self.assertIn('Moneda: EUR', texto)
        self.assertIn('Cliente Beta', texto)
        self.assertNotIn('Cliente Alfa', texto)

    def test_vista_impresion_pdf(self):
        """formato=pdf muestra la vista de impresión con window.print(), detalle y totales."""
        self.http.force_login(self.analista)
        respuesta = self.http.get(self.url, {'formato': 'pdf', 'tipo': 'VENTA'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, 'monitoreo_corporativo/reporte_financiero_imprimir.html')
        self.assertContains(respuesta, 'window.print()')
        self.assertContains(respuesta, '@media print')
        self.assertContains(respuesta, 'Tipo: Venta')
        self.assertContains(respuesta, '<tfoot')
        self.assertContains(respuesta, 'Resumen de Totales')
        self.assertEqual(respuesta.context['totales'].cantidad, 2)
        self.assertEqual(respuesta.context['totales'].ganancia_neta_pyg, Decimal('13000.00'))

    def test_vista_impresion_respeta_filtros(self):
        """La vista de impresión lista solo las operaciones filtradas."""
        self.http.force_login(self.admin)
        respuesta = self.http.get(self.url, {'formato': 'pdf', 'cliente': self.cliente_b.pk})
        self.assertContains(respuesta, 'Cliente Beta')
        self.assertNotContains(respuesta, 'Cliente Alfa')

    def test_formato_desconocido_muestra_panel(self):
        """Un formato no soportado muestra el panel normal."""
        self.http.force_login(self.admin)
        respuesta = self.http.get(self.url, {'formato': 'xlsx'})
        self.assertTemplateUsed(respuesta, 'monitoreo_corporativo/reporte_financiero.html')

    def test_cliente_no_accede_a_exportaciones(self):
        """Sin permiso tampoco se puede descargar el CSV ni abrir la vista de impresión."""
        self.http.force_login(self.usuario_cliente)
        self.assertEqual(self.http.get(self.url, {'formato': 'csv'}).status_code, 302)
        self.assertEqual(self.http.get(self.url, {'formato': 'pdf'}).status_code, 302)

    def test_enlaces_de_descarga_conservan_filtros(self):
        """Los botones de descarga conservan los filtros de la consulta actual."""
        self.http.force_login(self.admin)
        respuesta = self.http.get(self.url, {'moneda': 'USD'})
        self.assertContains(respuesta, 'moneda=USD&amp;formato=pdf')
        self.assertContains(respuesta, 'moneda=USD&amp;formato=csv')

    def test_tarjeta_en_dashboard_admin(self):
        """El dashboard del Administrador muestra el acceso a Reportes Financieros."""
        self.http.force_login(self.admin)
        respuesta = self.http.get(reverse('dashboard_redirect'), follow=True)
        self.assertContains(respuesta, reverse('monitoreo_corporativo:reporte_financiero'))
