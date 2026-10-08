# -*- coding: utf-8 -*-
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from decimal import Decimal
import json
from authentication.models import UserProfile, Role, Cliente, ClientAccreditationMethod
from integracion.models import PaymentGatewayLog, WithdrawalRequest
from integracion.gateways import PaymentGatewayFactory, StripeGateway, SIPAPGateway, BancardGateway, TigoMoneyGateway
from integracion.services import PaymentGatewaySimulator, AutomaticCreditorService


class PSE18BackendIntegrationUnitTests(TestCase):
    """
    Suite de pruebas unitarias independiente y dedicada exclusivamente para la Historia de Usuario PSE-18
    con arquitectura backend refactorizada (Strategy Pattern, Stripe Webhooks, SIPAP SPI, Bancard y Tigo Money).
    """

    def setUp(self):
        """Prepara el entorno de prueba con cliente, métodos de acreditación y credenciales."""
        self.client = Client()
        self.webhook_url = reverse('integracion:stripe_webhook')

        self.cliente = Cliente.objects.create(
            nombre_o_razon_social='Corporación SIPAP & Stripe SA',
            tipo_cliente='JURIDICA',
            documento_identidad='80022233-4',
            email='backend@corporation.com',
            categoria='CORPORATIVO'
        )

        self.role, _ = Role.objects.get_or_create(name="Role Backend Integracion")
        self.user = User.objects.create_user(username='user_backend_pse18', password='password123')
        self.profile = UserProfile.objects.create(
            user=self.user, role=self.role, category='CORPORATIVO', is_corporate=True, ci_ruc='80022233-4'
        )

        self.acc_bank = ClientAccreditationMethod.objects.create(
            cliente=self.cliente,
            tipo_medio='CUENTA_BANCARIA',
            entidad_financiera='Banco Central Simulado',
            numero_cuenta='123456789',
            tipo_cuenta='CORRIENTE',
            moneda='PYG',
            balance=Decimal('5000000.00'),
            titularidad='Corporación SIPAP & Stripe SA',
            estado='VERIFICADO',
            es_predeterminado=True
        )

    def test_strategy_pattern_factory_gateways(self):
        """
        Valida el Patrón Strategy y Factory para la instanciación y ejecución
        de las pasarelas (Stripe, SIPAP, Bancard, Tigo Money).
        """
        stripe_gw = PaymentGatewayFactory.get_gateway('STRIPE')
        self.assertIsInstance(stripe_gw, StripeGateway)
        res_stripe = stripe_gw.process_payment(Decimal('100.00'), 'USD')
        self.assertEqual(res_stripe['status'], 'SUCCESS')
        self.assertIn('pi_', res_stripe['reference_code'])

        sipap_gw = PaymentGatewayFactory.get_gateway('SIPAP')
        self.assertIsInstance(sipap_gw, SIPAPGateway)
        res_sipap = sipap_gw.process_payment(Decimal('1000000.00'), 'PYG', destination_account='CBU-001')
        self.assertEqual(res_sipap['status'], 'APPROVED')
        self.assertIn('SIPAP-', res_sipap['reference_code'])

        bancard_gw = PaymentGatewayFactory.get_gateway('BANCARD')
        self.assertIsInstance(bancard_gw, BancardGateway)
        res_bancard = bancard_gw.process_payment(Decimal('200000.00'), 'PYG', card_number='4152310000001234')
        self.assertEqual(res_bancard['status'], 'SUCCESS')

        tigo_gw = PaymentGatewayFactory.get_gateway('TIGO_MONEY')
        self.assertIsInstance(tigo_gw, TigoMoneyGateway)
        res_tigo = tigo_gw.process_payment(Decimal('50000.00'), 'PYG', phone_number='0981123456')
        self.assertEqual(res_tigo['status'], 'SUCCESS')

    def test_automatic_creditor_service_with_strategy(self):
        """
        Valida que el servicio de acreditación automática 24/7 procesa los pagos Server-to-Server
        y actualiza los saldos de los clientes en la base de datos.
        """
        initial_balance = self.acc_bank.balance
        amount_to_credit = Decimal('2000000.00')

        withdrawal_req = AutomaticCreditorService.credit_client_account(
            client_acc_id=self.acc_bank.id,
            amount=amount_to_credit,
            currency='PYG',
            channel='SIPAP',
            details='Acreditación automática backend SIPAP'
        )

        self.assertEqual(withdrawal_req.status, 'COMPLETED')
        self.acc_bank.refresh_from_db()
        self.assertEqual(self.acc_bank.balance, initial_balance + amount_to_credit)

    def test_stripe_webhook_async_confirmation(self):
        """
        Valida el procesamiento asíncrono de confirmaciones mediante Webhooks de Stripe (`payment_intent.succeeded`).
        """
        ref_pi = "pi_test_123456789abc"
        log_obj = PaymentGatewayLog.objects.create(
            gateway_name='STRIPE',
            transaction_type='PAYMENT',
            reference_code=ref_pi,
            amount=Decimal('150.00'),
            currency='USD',
            status='PENDING'
        )

        payload = {
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": ref_pi,
                    "status": "succeeded",
                    "amount": 15000
                }
            }
        }

        response = self.client.post(
            self.webhook_url,
            data=json.dumps(payload),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        log_obj.refresh_from_db()
        self.assertEqual(log_obj.status, 'SUCCESS')
