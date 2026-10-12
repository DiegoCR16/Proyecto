# -*- coding: utf-8 -*-
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from decimal import Decimal
from caja.models import Caja, TurnoCaja, OperacionVentanilla, DesgloseEfectivoCaja, verificar_turno_activo, inicializar_denominaciones_default
from authentication.models import Cliente
from tasas_cambio.models import ExchangeRate, ClientBenefitRule

class VentanillaPOSPSE38Tests(TestCase):
    """
    Suite de pruebas unitarias e integración independiente y exclusiva para la Historia de Usuario PSE-38:
    Procesamiento Operativo en Ventanilla (Epic: PSE-19 Operativa de Caja y Arqueo).

    Valida:
    - Validación de turno abierto (bloqueo si no hay turno activo con acento rojo).
    - Identificación del cliente presencial (búsqueda por CI/RUC o alta rápida de cliente ocasional).
    - Ingreso de la operación (compra/venta, moneda origen/destino, monto).
    - Cálculo de cotización, comisiones y vuelto en efectivo.
    - Actualización automática e inmediata en tiempo real del saldo por divisa en la caja activa tras la emisión del comprobante.
    """

    def setUp(self):
        """Configuración inicial para las pruebas de ventanilla POS PSE-38."""
        inicializar_denominaciones_default()
        self.client = Client()
        self.cajero = User.objects.create_user(username='cajero_pse38', password='password123')
        self.caja = Caja.objects.create(nombre='Caja Ventanilla 01', codigo='CV01', activa=True)
        self.caja.cajeros_asignados.add(self.cajero)

        ExchangeRate.objects.get_or_create(
            currency_code='USD',
            defaults={
                'currency_name': 'Dólar Estadounidense',
                'symbol': '$',
                'buy_rate': Decimal('7300.0000'),
                'sell_rate': Decimal('7400.0000')
            }
        )
        ExchangeRate.objects.get_or_create(
            currency_code='EUR',
            defaults={
                'currency_name': 'Euro',
                'symbol': '€',
                'buy_rate': Decimal('7800.0000'),
                'sell_rate': Decimal('8000.0000')
            }
        )

        self.cliente = Cliente.objects.create(
            nombre_o_razon_social='Juan Pérez Presencial',
            documento_identidad='1234567-PY',
            email='juan.perez@test.com',
            tipo_cliente='FISICA',
            categoria='MINORISTA'
        )

    def test_validacion_turno_abierto_bloqueo(self):
        """Valida que sin turno abierto el sistema bloquee el procesamiento en ventanilla."""
        self.client.login(username='cajero_pse38', password='password123')
        
        response = self.client.get(reverse('caja:ventanilla_pos'))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['turno_activo'])

        response_post = self.client.post(reverse('caja:procesar_ventanilla'), {
            'tipo_operacion': 'COMPRA',
            'moneda_origen': 'USD',
            'moneda_destino': 'PYG',
            'monto_origen': '100.00',
            'efectivo_recibido': '730000.00',
            'cliente_id': self.cliente.id
        })
        self.assertEqual(OperacionVentanilla.objects.count(), 0)

    def test_procesamiento_exitoso_compra_divisas_y_actualizacion_saldo(self):
        """Valida la apertura de turno, ejecución de compra de divisas, cálculo de vuelto y actualización en tiempo real de saldo en caja."""
        turno = TurnoCaja.objects.create(
            caja=self.caja,
            cajero=self.cajero,
            saldo_inicial_pyg=Decimal('10000000.00'),
            saldo_inicial_usd=Decimal('500.00'),
            estado='ABIERTO'
        )

        self.client.login(username='cajero_pse38', password='password123')

        response = self.client.post(reverse('caja:procesar_ventanilla'), {
            'tipo_operacion': 'COMPRA',
            'moneda_origen': 'USD',
            'moneda_destino': 'PYG',
            'monto_origen': '100.00',
            'efectivo_recibido': '100.00',
            'cliente_id': self.cliente.id
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(OperacionVentanilla.objects.count(), 1)

        operacion = OperacionVentanilla.objects.first()
        self.assertEqual(operacion.tipo_operacion, 'COMPRA')
        self.assertEqual(operacion.monto_origen, Decimal('100.00'))
        self.assertIsNotNone(operacion.comprobante_numero)

        desgloses = DesgloseEfectivoCaja.objects.filter(turno=turno)
        self.assertEqual(desgloses.count(), 2)
        
        ingreso_usd = desgloses.filter(tipo_operacion='INGRESO', divisa='USD').first()
        self.assertIsNotNone(ingreso_usd)
        self.assertEqual(ingreso_usd.monto_total, Decimal('100.00'))

    def test_operacion_cruzada_usd_eur(self):
        """Valida una operación cruzada entre divisas extranjeras (ej. USD a EUR)."""
        TurnoCaja.objects.create(
            caja=self.caja,
            cajero=self.cajero,
            saldo_inicial_pyg=Decimal('10000000.00'),
            estado='ABIERTO'
        )
        self.client.login(username='cajero_pse38', password='password123')

        response = self.client.post(reverse('caja:procesar_ventanilla'), {
            'tipo_operacion': 'COMPRA',
            'moneda_origen': 'USD',
            'moneda_destino': 'EUR',
            'monto_origen': '200.00',
            'efectivo_recibido': '200.00',
            'cliente_id': self.cliente.id
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(OperacionVentanilla.objects.count(), 1)
        op = OperacionVentanilla.objects.first()
        self.assertEqual(op.moneda_origen, 'USD')
        self.assertEqual(op.moneda_destino, 'EUR')

    def test_registro_cliente_ocasional_presencial(self):
        """Valida el alta rápida y automática de un cliente presencial ocasional si no está registrado."""
        TurnoCaja.objects.create(
            caja=self.caja,
            cajero=self.cajero,
            saldo_inicial_pyg=Decimal('5000000.00'),
            estado='ABIERTO'
        )
        self.client.login(username='cajero_pse38', password='password123')

        response = self.client.post(reverse('caja:procesar_ventanilla'), {
            'tipo_operacion': 'VENTA',
            'moneda_origen': 'PYG',
            'moneda_destino': 'USD',
            'monto_origen': '740000.00',
            'efectivo_recibido': '800000.00',
            'nombre_cliente': 'Cliente Ocasional Test',
            'documento_cliente': 'DOC-OCASIONAL-999'
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(OperacionVentanilla.objects.count(), 1)
        
        op = OperacionVentanilla.objects.first()
        self.assertIsNotNone(op.cliente)
        self.assertEqual(op.cliente.documento_identidad, 'DOC-OCASIONAL-999')

    def _crear_cliente_vip(self):
        """Crea regla VIP y cliente VIP para pruebas de beneficio por categoría."""
        ClientBenefitRule.objects.get_or_create(
            category_code='VIP',
            defaults={
                'category_name': 'VIP',
                'min_operation_amount': Decimal('0.00'),
                'benefit_percentage': Decimal('2.00')
            }
        )
        return Cliente.objects.create(
            nombre_o_razon_social='Cliente VIP PSE38',
            documento_identidad='VIP-001-PY',
            email='vip@test.com',
            tipo_cliente='FISICA',
            categoria='VIP'
        )

    def test_beneficio_categoria_vip_en_compra(self):
        """Valida que un cliente VIP reciba la tasa preferencial (compra +2%) en ventanilla."""
        cliente_vip = self._crear_cliente_vip()

        TurnoCaja.objects.create(
            caja=self.caja,
            cajero=self.cajero,
            saldo_inicial_pyg=Decimal('10000000.00'),
            saldo_inicial_usd=Decimal('500.00'),
            estado='ABIERTO'
        )
        self.client.login(username='cajero_pse38', password='password123')

        response = self.client.post(reverse('caja:procesar_ventanilla'), {
            'tipo_operacion': 'COMPRA',
            'moneda_origen': 'USD',
            'moneda_destino': 'PYG',
            'monto_origen': '100.00',
            'efectivo_recibido': '100.00',
            'cliente_id': cliente_vip.id
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        op = OperacionVentanilla.objects.first()
        # Tasa preferencial: 7300.00 * 1.02 = 7446.00
        self.assertEqual(op.tasa_aplicada, Decimal('7446.0000'))
        self.assertEqual(op.benefit_percentage, Decimal('2.00'))
        # valor_pyg = 744600, comision 0.5% USD (3723 PYG), neto = 740877
        self.assertEqual(op.monto_destino, Decimal('740877.00'))

    def test_busqueda_cliente_vip_muestra_beneficio_en_contexto(self):
        """Valida que la búsqueda por documento de un cliente VIP exponga el beneficio en el contexto de la vista."""
        cliente_vip = self._crear_cliente_vip()
        self.client.login(username='cajero_pse38', password='password123')

        response = self.client.get(reverse('caja:ventanilla_pos') + f'?documento_busqueda={cliente_vip.documento_identidad}')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['benefit_percentage'], Decimal('2.00'))
        self.assertEqual(response.context['category_name'], 'VIP')
        self.assertEqual(response.context['cliente_encontrado'].id, cliente_vip.id)
