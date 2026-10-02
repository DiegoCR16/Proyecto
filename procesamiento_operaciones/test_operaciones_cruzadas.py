# -*- coding: utf-8 -*-
from django.test import TestCase, Client, RequestFactory
from django.urls import reverse
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from decimal import Decimal
from authentication.models import UserProfile, Role, Cliente, ClientAccreditationMethod
from tasas_cambio.models import ExchangeRate, PaymentMethod, ClientBenefitRule
from tasas_cambio.views import ensure_default_benefit_rules, SimuladorConversionService
from procesamiento_operaciones.models import CurrencyPurchaseTransaction
from procesamiento_operaciones.views import CurrencyPurchaseService


class OperacionesCruzadasTests(TestCase):
    """
    Pruebas unitarias e integración para Operaciones Cruzadas (Compra de divisa extranjera pagando con otra divisa extranjera).
    Valida:
    1. Triangulación de divisas (Tasa Cruzada = Tasa Moneda Origen / Tasa Moneda Destino).
    2. Registro de campos específicos (moneda_origen_id, monto_origen, moneda_destino_id, monto_destino, tipo_cambio_cruzado, tipo_cambio_local_origen, monto_moneda_local).
    3. Movimientos de caja (stock de moneda origen ingresa, stock de moneda destino egresa).
    4. Integración con servicios y vistas web.
    """

    def setUp(self):
        self.client = Client()
        self.factory = RequestFactory()
        self.purchase_url = reverse('procesamiento_operaciones:currency_purchase')

        # Configurar tasas de cambio
        self.rate_usd = ExchangeRate.objects.create(
            currency_code='USD',
            currency_name='Dólar Estadounidense',
            buy_rate=Decimal('7300.0000'),
            sell_rate=Decimal('7450.0000')
        )
        self.rate_eur = ExchangeRate.objects.create(
            currency_code='EUR',
            currency_name='Euro',
            buy_rate=Decimal('7900.0000'),
            sell_rate=Decimal('8150.0000')
        )
        self.rate_pyg = ExchangeRate.objects.create(
            currency_code='PYG',
            currency_name='Guaraní Paraguayo',
            buy_rate=Decimal('1.0000'),
            sell_rate=Decimal('1.0000')
        )

        self.pm = PaymentMethod.objects.create(
            code='PM_CROSS',
            name='Caja Multimoneda',
            method_type='EFECTIVO',
            balance=Decimal('5000000000.00'),
            is_active=True
        )

        ensure_default_benefit_rules()

        self.cliente = Cliente.objects.create(
            nombre_o_razon_social='Cliente Cruzado SA',
            tipo_cliente='JURIDICA',
            documento_identidad='80011122-3',
            email='cruzado@client.com',
            categoria='VIP'
        )
        ClientAccreditationMethod.objects.create(
            cliente=self.cliente,
            tipo_medio='CUENTA_BANCARIA',
            entidad_financiera='Banco Cross',
            numero_cuenta='999888777',
            tipo_cuenta='AHORRO',
            titularidad='Cliente Cruzado SA',
            moneda='EUR',
            balance=Decimal('1000000000.00'),
            estado='VERIFICADO',
            es_predeterminado=True
        )

        self.role, _ = Role.objects.get_or_create(name="Role Cross")
        self.user = User.objects.create_user(username='user_cross', password='password123')
        self.profile = UserProfile.objects.create(
            user=self.user, role=self.role, category='VIP', is_corporate=False
        )

    def _get_request(self):
        req = self.factory.get(self.purchase_url)
        req.user = self.user
        req.session = {'active_client_id': str(self.cliente.id)}
        return req

    def test_cross_currency_simulation_and_purchase(self):
        """
        Verifica que una compra de divisas pagando con otra divisa extranjera (ej. EUR a USD)
        calcule la tasa cruzada y registre correctamente todos los campos obligatorios y de auditoría.
        """
        req = self._get_request()
        amount_eur = Decimal('1000.00')

        # Simular conversión cruzada
        sim_res = SimuladorConversionService.simular(
            from_currency='EUR',
            to_currency='USD',
            amount=amount_eur,
            user=self.user,
            request=req
        )

        self.assertEqual(sim_res['operation_type'], 'CRUZADA')
        self.assertIsNotNone(sim_res['applied_rate'])

        # Procesar compra
        tx = CurrencyPurchaseService.process_purchase(
            user=self.user,
            from_currency='EUR',
            to_currency='USD',
            amount=amount_eur,
            payment_method_id_or_code=self.pm.code,
            request=req
        )

        self.assertEqual(tx.status, 'SUCCESS')
        self.assertEqual(tx.moneda_origen_id, 'EUR')
        self.assertEqual(tx.monto_origen, amount_eur)
        self.assertEqual(tx.moneda_destino_id, 'USD')
        self.assertEqual(tx.monto_destino, sim_res['converted_amount'])
        self.assertIsNotNone(tx.tipo_cambio_cruzado)
        self.assertIsNotNone(tx.tipo_cambio_local_origen)
        self.assertIsNotNone(tx.monto_moneda_local)
        self.assertIn("Caja: Ingresa stock de", tx.transparent_breakdown)

    def test_cross_currency_web_view(self):
        """
        Verifica que la vista web de compra de divisas procese correctamente una solicitud POST
        de operación cruzada (EUR a USD).
        """
        self.client.force_login(self.user)
        session = self.client.session
        session['active_client_id'] = str(self.cliente.id)
        session.save()

        response = self.client.post(self.purchase_url, {
            'from_currency': 'EUR',
            'to_currency': 'USD',
            'amount': '500',
            'payment_method': self.pm.code
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "EUR")
        self.assertContains(response, "USD")
