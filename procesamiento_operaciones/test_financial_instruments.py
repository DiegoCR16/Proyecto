# -*- coding: utf-8 -*-
from django.test import TestCase, RequestFactory
from django.contrib.auth.models import User
from decimal import Decimal
from authentication.models import UserProfile, Role, Cliente, ClientAccreditationMethod
from tasas_cambio.models import ExchangeRate, PaymentMethod, ClientBenefitRule
from tasas_cambio.views import ensure_default_benefit_rules
from procesamiento_operaciones.models import CurrencyPurchaseTransaction, CurrencySaleTransaction
from procesamiento_operaciones.views import CurrencyPurchaseService, CurrencySaleService


class FinancialInstrumentsRoutingAndValidationTests(TestCase):
    """
    Suite de pruebas unitarias para validar la detección, catalogación, validación y enrutamiento
    de los 5 tipos de instrumentos/cuentas financieras:
    1. TARJETA_CREDITO (Stripe/Bancard)
    2. TARJETA_DEBITO (Débito Directo)
    3. CUENTA_BANCARIA_LOCAL (SIPAP)
    4. BILLETERA_ELECTRONICA (Tigo Money)
    5. CUENTA_BANCARIA_EXTRANJERA (SWIFT/IBAN - PENDING liquidation)
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.rate_usd = ExchangeRate.objects.create(
            currency_code='USD',
            currency_name='Dólar Estadounidense',
            buy_rate=Decimal('7300.0000'),
            sell_rate=Decimal('7450.0000')
        )
        self.rate_pyg = ExchangeRate.objects.create(
            currency_code='PYG',
            currency_name='Guaraní Paraguayo',
            buy_rate=Decimal('1.0000'),
            sell_rate=Decimal('1.0000')
        )
        ensure_default_benefit_rules()

        self.cliente = Cliente.objects.create(
            nombre_o_razon_social='Global Trading S.A.',
            tipo_cliente='JURIDICA',
            documento_identidad='80011223-4',
            email='trading@global.com',
            categoria='CORPORATIVO'
        )
        self.role, _ = Role.objects.get_or_create(name="Cliente")
        self.user = User.objects.create_user(username='user_fin', password='password123')
        self.profile = UserProfile.objects.create(
            user=self.user, role=self.role, category='CORPORATIVO', ci_ruc='80011223-4'
        )

    def _get_request(self):
        req = self.factory.get('/')
        req.user = self.user
        req.session = {'active_client_id': str(self.cliente.id)}
        return req

    def test_01_tarjeta_credito_routing_and_validation(self):
        """Valida que TARJETA_CREDITO requiera número de tarjeta y expiración, y enrute a Stripe/Bancard."""
        pm_card = PaymentMethod.objects.create(
            code='CC_VISA',
            name='Visa Crédito Global',
            method_type='TARJETA_CREDITO',
            account_number='4532111122223333',
            balance=Decimal('50000000.00'),
            is_active=True
        )
        acc = ClientAccreditationMethod.objects.create(
            cliente=self.cliente,
            tipo_medio='TARJETA_CREDITO',
            entidad_financiera='Stripe / Bancard',
            numero_tarjeta='4532111122223333',
            fecha_expiracion='12/28',
            titularidad='Global Trading S.A.'
        )
        req = self._get_request()
        tx = CurrencyPurchaseService.process_purchase(
            user=self.user,
            from_currency='USD',
            to_currency='PYG',
            amount=100,
            payment_method_id_or_code='CC_VISA',
            origin_method_id=acc.id,
            destination_method_id=acc.id,
            request=req
        )
        self.assertEqual(tx.payment_method_type, 'TARJETA_CREDITO')
        self.assertTrue(tx.gateway_reference)
        self.assertEqual(tx.status, 'SUCCESS')

    def test_02_billetera_electronica_routing(self):
        """Valida que BILLETERA_ELECTRONICA requiera teléfono y enrute a Tigo Money."""
        pm_wallet = PaymentMethod.objects.create(
            code='TIGO_W',
            name='Tigo Money Móvil',
            method_type='BILLETERA_ELECTRONICA',
            account_number='0981123456',
            balance=Decimal('20000000.00'),
            is_active=True
        )
        acc = ClientAccreditationMethod.objects.create(
            cliente=self.cliente,
            tipo_medio='BILLETERA_ELECTRONICA',
            entidad_financiera='Tigo Money',
            numero_telefono='0981123456',
            titularidad='Global Trading S.A.'
        )
        req = self._get_request()
        tx = CurrencySaleService.process_sale(
            user=self.user,
            from_currency='USD',
            to_currency='PYG',
            amount=50,
            payment_method_id_or_code='TIGO_W',
            origin_method_id=acc.id,
            destination_method_id=acc.id,
            request=req
        )
        self.assertEqual(tx.payment_method_type, 'BILLETERA_ELECTRONICA')
        self.assertIn('TIGO-', tx.gateway_reference)
        self.assertEqual(tx.status, 'SUCCESS')

    def test_03_cuenta_bancaria_local_sipap(self):
        """Valida que CUENTA_BANCARIA_LOCAL enrute a SIPAP."""
        pm_local = PaymentMethod.objects.create(
            code='SIPAP_ACC',
            name='Caja de Ahorro Local',
            method_type='CUENTA_BANCARIA_LOCAL',
            account_number='99887766',
            bank_name='Banco Itaú PY',
            balance=Decimal('100000000.00'),
            is_active=True
        )
        acc = ClientAccreditationMethod.objects.create(
            cliente=self.cliente,
            tipo_medio='CUENTA_BANCARIA_LOCAL',
            entidad_financiera='Banco Itaú PY',
            numero_cuenta='99887766',
            tipo_cuenta='AHORRO',
            titularidad='Global Trading S.A.'
        )
        req = self._get_request()
        tx = CurrencySaleService.process_sale(
            user=self.user,
            from_currency='USD',
            to_currency='PYG',
            amount=100,
            payment_method_id_or_code='SIPAP_ACC',
            origin_method_id=acc.id,
            destination_method_id=acc.id,
            request=req
        )
        self.assertEqual(tx.payment_method_type, 'CUENTA_BANCARIA_LOCAL')
        self.assertIn('SIPAP-', tx.gateway_reference)
        self.assertEqual(tx.status, 'SUCCESS')

    def test_04_cuenta_bancaria_extranjera_pending(self):
        """Valida que CUENTA_BANCARIA_EXTRANJERA requiera SWIFT/IBAN y asigne estado PENDING para liquidación internacional."""
        pm_ext = PaymentMethod.objects.create(
            code='SWIFT_ACC',
            name='Chase Bank NY SWIFT',
            method_type='CUENTA_BANCARIA_EXTRANJERA',
            account_number='US33CHASE001',
            swift_iban='CHASUS33XXX',
            balance=Decimal('500000000.00'),
            is_active=True
        )
        acc = ClientAccreditationMethod.objects.create(
            cliente=self.cliente,
            tipo_medio='CUENTA_BANCARIA_EXTRANJERA',
            entidad_financiera='Chase Bank New York',
            swift_iban='CHASUS33XXX',
            numero_cuenta='US33CHASE001',
            titularidad='Global Trading S.A.'
        )
        req = self._get_request()
        tx = CurrencySaleService.process_sale(
            user=self.user,
            from_currency='USD',
            to_currency='PYG',
            amount=200,
            payment_method_id_or_code='SWIFT_ACC',
            origin_method_id=acc.id,
            destination_method_id=acc.id,
            request=req
        )
        self.assertEqual(tx.payment_method_type, 'CUENTA_BANCARIA_EXTRANJERA')
        self.assertIn('SWIFT-', tx.gateway_reference)
        self.assertEqual(tx.status, 'PENDING')
        self.assertEqual(tx.gateway_status, 'PENDING')
