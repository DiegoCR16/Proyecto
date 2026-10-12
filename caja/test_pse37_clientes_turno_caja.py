# -*- coding: utf-8 -*-
"""
Pruebas Unitarias e Integración para la Historia de Usuario PSE-37:
Registro y Consulta de Operaciones y Clientes Atendidos por Turno de Caja.
Épica: PSE-19: Operativa de Caja y Arqueo.
"""

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from decimal import Decimal
from caja.models import Caja, TurnoCaja
from authentication.models import Cliente, ClientAccreditationMethod
from procesamiento_operaciones.models import CurrencyPurchaseTransaction, CurrencySaleTransaction
from tasas_cambio.models import ExchangeRate, PaymentMethod


class PSE37ClientesTurnoCajaTests(TestCase):
    """
    Suite de pruebas unitarias e integración independientes para validar la gestión de operaciones,
    clientes atendidos, consolidado, filtros y planillas de cierre por turno de caja (PSE-37).
    """

    def setUp(self):
        """
        Configuración inicial de datos de prueba para turnos, cajas, clientes y transacciones.
        """
        self.cajero = User.objects.create_user(username='cajero_pse37', password='password123')
        self.admin_user = User.objects.create_superuser(username='admin_pse37', password='password123')
        
        self.caja = Caja.objects.create(nombre='Caja PSE-37', codigo='C37', activa=True)
        self.caja.cajeros_asignados.add(self.cajero)

        self.turno = TurnoCaja.objects.create(
            caja=self.caja,
            cajero=self.cajero,
            saldo_inicial_pyg=Decimal('5000000.00'),
            saldo_inicial_usd=Decimal('1000.00'),
            estado='ABIERTO'
        )

        self.cliente = Cliente.objects.create(
            nombre_o_razon_social='Empresa Test PSE-37 S.A.',
            tipo_cliente='JURIDICA',
            documento_identidad='80012345-6',
            email='testpse37@globalexchange.py',
            categoria='VIP'
        )

        self.rate_usd = ExchangeRate.objects.get_or_create(
            currency_code='USD',
            defaults={'currency_name': 'Dólar', 'symbol': '$', 'buy_rate': Decimal('7300'), 'sell_rate': Decimal('7450')}
        )[0]
        self.rate_pyg = ExchangeRate.objects.get_or_create(
            currency_code='PYG',
            defaults={'currency_name': 'Guaraní', 'symbol': '₲', 'buy_rate': Decimal('1'), 'sell_rate': Decimal('1')}
        )[0]

        self.pm = PaymentMethod.objects.get_or_create(
            code='SIPAP',
            defaults={'name': 'Transferencia SIPAP', 'is_active': True}
        )[0]

        # Crear transacciones asociadas al turno
        self.compra_tx = CurrencyPurchaseTransaction.objects.create(
            user=self.cajero,
            cliente=self.cliente,
            turno=self.turno,
            from_currency='USD',
            to_currency='PYG',
            amount=Decimal('100.00'),
            converted_amount=Decimal('730000.00'),
            applied_rate=Decimal('7300.00'),
            standard_rate=Decimal('7300.00'),
            payment_method=self.pm,
            total_pyg=Decimal('730000.00'),
            status='SUCCESS'
        )

        self.venta_tx = CurrencySaleTransaction.objects.create(
            user=self.cajero,
            cliente=self.cliente,
            turno=self.turno,
            from_currency='PYG',
            to_currency='USD',
            amount=Decimal('1490000.00'),
            converted_amount=Decimal('200.00'),
            applied_rate=Decimal('7450.00'),
            standard_rate=Decimal('7450.00'),
            linked_account=self.pm,
            total_pyg=Decimal('1490000.00'),
            status='SUCCESS'
        )

    def test_asociacion_estricta_turno(self):
        """
        Valida que las transacciones queden estrictamente vinculadas al turno de caja seleccionado.
        """
        self.assertEqual(self.turno.purchase_transactions.count(), 1)
        self.assertEqual(self.turno.sale_transactions.count(), 1)
        self.assertEqual(self.turno.purchase_transactions.first(), self.compra_tx)
        self.assertEqual(self.turno.sale_transactions.first(), self.venta_tx)

    def test_identificacion_cliente_en_transaccion(self):
        """
        Valida que cada registro muestre correctamente el nombre completo y documento (RUC/CI) del cliente.
        """
        self.assertEqual(self.compra_tx.cliente.nombre_o_razon_social, 'Empresa Test PSE-37 S.A.')
        self.assertEqual(self.compra_tx.cliente.documento_identidad, '80012345-6')
        self.assertEqual(self.venta_tx.cliente.nombre_o_razon_social, 'Empresa Test PSE-37 S.A.')
        self.assertEqual(self.venta_tx.cliente.documento_identidad, '80012345-6')

    def test_consolidado_acumulado_por_tipo(self):
        """
        Valida el cálculo matemático del acumulado consolidado por tipo de operación (compras vs ventas).
        """
        url = reverse('caja:detalle_turno_operaciones', args=[self.turno.id])
        self.client.login(username='cajero_pse37', password='password123')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_compras_count'], 1)
        self.assertEqual(response.context['total_ventas_count'], 1)
        self.assertEqual(response.context['acumulado_compras_pyg'], Decimal('730000.00'))
        self.assertEqual(response.context['acumulado_ventas_pyg'], Decimal('1490000.00'))

    def test_filtros_y_busqueda_transaccional(self):
        """
        Valida el funcionamiento de los filtros por tipo de operación y búsqueda por documento/nombre.
        """
        self.client.login(username='cajero_pse37', password='password123')
        url = reverse('caja:detalle_turno_operaciones', args=[self.turno.id])

        # Filtrar solo compras
        response_compra = self.client.get(url, {'tx_type': 'COMPRA'})
        self.assertEqual(response_compra.status_code, 200)
        self.assertEqual(len(response_compra.context['operaciones']), 1)
        self.assertEqual(response_compra.context['operaciones'][0].tx_type, 'COMPRA')

        # Buscar por RUC del cliente
        response_search = self.client.get(url, {'q': '80012345-6'})
        self.assertEqual(response_search.status_code, 200)
        self.assertEqual(len(response_search.context['operaciones']), 2)

    def test_generacion_reporte_planilla_cierre(self):
        """
        Valida la generación de exportación CSV y vista de impresión/PDF de la planilla de cierre de turno.
        """
        self.client.login(username='admin_pse37', password='password123')
        url = reverse('caja:detalle_turno_operaciones', args=[self.turno.id])

        # Export CSV
        response_csv = self.client.get(url, {'export': 'excel'})
        self.assertEqual(response_csv.status_code, 200)
        self.assertIn('text/csv', response_csv['Content-Type'])

        # Planilla PDF / Print view
        response_pdf = self.client.get(url, {'export': 'pdf'})
        self.assertEqual(response_pdf.status_code, 200)
        self.assertTemplateUsed(response_pdf, 'caja/planilla_cierre_turno_pdf.html')
