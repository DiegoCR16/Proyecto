# -*- coding: utf-8 -*-
from django.test import TestCase, Client, RequestFactory
from django.urls import reverse
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from decimal import Decimal
import time
from authentication.models import UserProfile, Role, Cliente
from tasas_cambio.models import ExchangeRate, PaymentMethod, ClientBenefitRule
from tasas_cambio.views import ensure_default_benefit_rules
from procesamiento_operaciones.models import CurrencyPurchaseTransaction
from procesamiento_operaciones.views import CurrencyPurchaseService


class CurrencyPurchasePSE13Tests(TestCase):
    """
    Suite de pruebas unitarias e integración independiente y exclusiva para la Historia de Usuario PSE-13:
    Operación de Compra de Divisas (Épica PSE-12: Procesamiento de Operaciones).
    
    Valida:
    1. Validación estricta de límites (mínimo 50.000 PYG, máximo 1.000.000.000 PYG).
    2. Comprobación estricta de fondos suficientes en el método de pago seleccionado antes de procesar.
    3. Cálculo transparente de tasa de cambio, comisiones, impuestos y descuentos por perfil de cliente (VIP 2%, Corporativo 4%).
    4. Rendimiento y tiempo de respuesta optimizado inferior a 5 segundos.
    5. Aislamiento estricto de operaciones e historial por cliente activo.
    """

    def setUp(self):
        """
        Configura el entorno de prueba con tasas de cambio, métodos de pago,
        roles, perfiles de usuario y cliente activo.
        """
        self.client = Client()
        self.factory = RequestFactory()
        self.purchase_url = reverse('procesamiento_operaciones:currency_purchase')
        self.history_url = reverse('procesamiento_operaciones:currency_purchase_history')

        # Configurar tasas de cambio
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

        # Configurar método de pago con saldo suficiente y saldo bajo
        self.pm_sufficient = PaymentMethod.objects.create(
            code='PM_SUFFICIENT',
            name='Transferencia Saldo Alto',
            method_type='TRANSFERENCIA',
            balance=Decimal('2000000000.00'),
            is_active=True
        )
        self.pm_insufficient = PaymentMethod.objects.create(
            code='PM_INSUFFICIENT',
            name='Tarjeta Saldo Bajo',
            method_type='TARJETA_DEBITO',
            balance=Decimal('10000.00'),
            is_active=True
        )

        # Configurar reglas de beneficio iniciales
        ensure_default_benefit_rules()

        # Crear cliente de prueba
        self.cliente_test = Cliente.objects.create(
            nombre_o_razon_social='Cliente Test SA',
            tipo_cliente='JURIDICA',
            documento_identidad='80099999-1',
            email='test@client.com',
            categoria='MINORISTA'
        )

        # Crear perfiles de usuario
        self.role_test, _ = Role.objects.get_or_create(name="Role PSE13")

        self.user_min = User.objects.create_user(username='user_min_pse13', password='password123')
        self.profile_min = UserProfile.objects.create(
            user=self.user_min, role=self.role_test, category='MINORISTA', is_corporate=False
        )

        self.user_vip = User.objects.create_user(username='user_vip_pse13', password='password123')
        self.profile_vip = UserProfile.objects.create(
            user=self.user_vip, role=self.role_test, category='VIP', is_corporate=False
        )

        self.user_corp = User.objects.create_user(username='user_corp_pse13', password='password123')
        self.profile_corp = UserProfile.objects.create(
            user=self.user_corp, role=self.role_test, category='CORPORATIVO', is_corporate=True
        )

    def _get_request(self, user):
        """Helper para crear una solicitud HTTP con sesión del cliente activo."""
        req = self.factory.get(self.purchase_url)
        req.user = user
        req.session = {'active_client_id': str(self.cliente_test.id)}
        return req

    def test_amount_limits_validation(self):
        """
        Valida que el sistema verifique estrictamente que el monto de la transacción
        en guaraníes no exceda 1.000.000.000 PYG ni sea inferior a 50.000 PYG.
        """
        req = self._get_request(self.user_min)
        # 1. Monto inferior al mínimo (ej. 10.000 PYG)
        with self.assertRaises(ValidationError) as ctx_min:
            CurrencyPurchaseService.process_purchase(
                user=self.user_min,
                from_currency='PYG',
                to_currency='USD',
                amount=10000,
                payment_method_id_or_code=self.pm_sufficient.code,
                request=req
            )
        self.assertIn("inferior al límite mínimo", str(ctx_min.exception))

        # 2. Monto superior al máximo (ej. 1.500.000.000 PYG)
        with self.assertRaises(ValidationError) as ctx_max:
            CurrencyPurchaseService.process_purchase(
                user=self.user_min,
                from_currency='PYG',
                to_currency='USD',
                amount=1500000000,
                payment_method_id_or_code=self.pm_sufficient.code,
                request=req
            )
        self.assertIn("excede el límite máximo", str(ctx_max.exception))

    def test_sufficient_funds_validation(self):
        """
        Valida que se compruebe de manera estricta que el método de pago seleccionado
        posea saldo suficiente para cubrir la transacción (incluyendo comisiones e impuestos).
        """
        req = self._get_request(self.user_min)
        with self.assertRaises(ValidationError) as ctx_funds:
            CurrencyPurchaseService.process_purchase(
                user=self.user_min,
                from_currency='PYG',
                to_currency='USD',
                amount=500000,
                payment_method_id_or_code=self.pm_insufficient.code,
                request=req
            )
        self.assertIn("Fondos insuficientes", str(ctx_funds.exception))

    def test_transparent_rate_and_profile_discounts(self):
        """
        Valida el cálculo transparente de tasa, comisiones, impuestos y descuento
        según el perfil del cliente (Minorista 0%, VIP 2%, Corporativo 4%).
        """
        ClientBenefitRule.objects.filter(category_code__in=['VIP', 'CORPORATIVO']).update(min_operation_amount=Decimal('0.00'))
        monto_compra = 500000  # 500.000 PYG

        req_min = self._get_request(self.user_min)
        tx_min = CurrencyPurchaseService.process_purchase(
            user=self.user_min,
            from_currency='PYG',
            to_currency='USD',
            amount=monto_compra,
            payment_method_id_or_code=self.pm_sufficient.code,
            request=req_min
        )
        self.assertEqual(tx_min.benefit_percentage, Decimal('0.00'))
        self.assertEqual(tx_min.status, 'SUCCESS')

        # Transacción VIP (2% descuento)
        req_vip = self._get_request(self.user_vip)
        self.cliente_test.categoria = 'VIP'
        self.cliente_test.save()
        tx_vip = CurrencyPurchaseService.process_purchase(
            user=self.user_vip,
            from_currency='PYG',
            to_currency='USD',
            amount=monto_compra,
            payment_method_id_or_code=self.pm_sufficient.code,
            request=req_vip
        )
        self.assertEqual(tx_vip.benefit_percentage, Decimal('2.00'))
        self.assertEqual(tx_vip.status, 'SUCCESS')

        # Transacción Corporativa (4% descuento)
        req_corp = self._get_request(self.user_corp)
        self.cliente_test.categoria = 'CORPORATIVO'
        self.cliente_test.save()
        tx_corp = CurrencyPurchaseService.process_purchase(
            user=self.user_corp,
            from_currency='PYG',
            to_currency='USD',
            amount=monto_compra,
            payment_method_id_or_code=self.pm_sufficient.code,
            request=req_corp
        )
        self.assertEqual(tx_corp.benefit_percentage, Decimal('4.00'))
        self.assertEqual(tx_corp.status, 'SUCCESS')

    def test_processing_time_performance(self):
        """
        Valida que el tiempo de respuesta para procesar la transacción de compra
        no exceda los 5 segundos (estándar menor a 5000 ms).
        """
        req = self._get_request(self.user_min)
        start = time.time()
        tx = CurrencyPurchaseService.process_purchase(
            user=self.user_min,
            from_currency='PYG',
            to_currency='USD',
            amount=100000,
            payment_method_id_or_code=self.pm_sufficient.code,
            request=req
        )
        duration_ms = int((time.time() - start) * 1000)

        self.assertLess(duration_ms, 5000)
        self.assertLess(tx.processing_time_ms, 5000)

    def test_currency_purchase_web_view_integration(self):
        """
        Valida que la vista HTTP web de compra de divisas responda correctamente (status 200)
        tanto en solicitudes GET como en el flujo de iniciación pendiente y confirmación de pago (PSE-31 / PSE-13).
        """
        self.client.force_login(self.user_vip)
        session = self.client.session
        session['active_client_id'] = str(self.cliente_test.id)
        session.save()

        # GET inicial
        res_get = self.client.get(self.purchase_url)
        self.assertEqual(res_get.status_code, 200)
        self.assertContains(res_get, "Operación Digital de Compra de Divisas")

        # POST para iniciar orden pendiente
        res_post = self.client.post(self.purchase_url, {
            'from_currency': 'PYG',
            'to_currency': 'USD',
            'amount': '200000',
            'payment_method': self.pm_sufficient.code
        })
        self.assertEqual(res_post.status_code, 200)
        self.assertContains(res_post, "Pantalla de Pago y Verificación")

        pending_tx = CurrencyPurchaseTransaction.objects.filter(cliente=self.cliente_test, status='PENDING').first()
        self.assertIsNotNone(pending_tx)

        # POST para confirmar pago
        res_confirm = self.client.post(self.purchase_url, {
            'action': 'confirm_purchase',
            'transaction_id': pending_tx.id
        })
        self.assertEqual(res_confirm.status_code, 200)
        self.assertContains(res_confirm, "¡Transacción de Compra Procesada con Éxito!")

    def test_multi_client_volume_and_category(self):
        """
        Valida que la compra de divisas se asocie al cliente activo en sesión,
        actualizando de manera independiente su volumen transaccional y respetando su categoría.
        """
        cliente_a = self.cliente_test
        cliente_b = Cliente.objects.create(
            nombre_o_razon_social='Cliente Beta S.A.',
            tipo_cliente='JURIDICA',
            documento_identidad='80054321-9',
            email='beta@test.com',
            categoria='CORPORATIVO',
            transaction_volume=Decimal('0.00')
        )

        session = self.client.session
        session['active_client_id'] = str(cliente_a.id)
        session.save()

        self.client.force_login(self.user_vip)
        response = self.client.post(self.purchase_url, {
            'from_currency': 'PYG',
            'to_currency': 'USD',
            'amount': '200000',
            'payment_method': self.pm_sufficient.code
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Cliente Test SA')

        pending_tx = CurrencyPurchaseTransaction.objects.filter(cliente=cliente_a, status='PENDING').first()
        self.assertIsNotNone(pending_tx)
        
        # Confirmar pago para aplicar volumen transaccional
        self.client.post(self.purchase_url, {
            'action': 'confirm_purchase',
            'transaction_id': pending_tx.id
        })

        cliente_a.refresh_from_db()
        self.assertGreater(cliente_a.transaction_volume, Decimal('0.00'))

        cliente_b.refresh_from_db()
        self.assertEqual(cliente_b.transaction_volume, Decimal('0.00'))

    def test_analista_cannot_purchase_currencies(self):
        """
        Valida que un usuario con rol o rol en cliente Analista tenga prohibido
        realizar operaciones de compra de divisas, lanzando un error de validación.
        """
        role_analista, _ = Role.objects.get_or_create(name="Analista")
        user_analista = User.objects.create_user(username='user_analista_pse13', password='password123')
        UserProfile.objects.create(user=user_analista, role=role_analista, category='MINORISTA')

        req = self._get_request(user_analista)
        with self.assertRaises(ValidationError) as ctx:
            CurrencyPurchaseService.process_purchase(
                user=user_analista,
                from_currency='PYG',
                to_currency='USD',
                amount=100000,
                payment_method_id_or_code=self.pm_sufficient.code,
                request=req
            )
        self.assertIn("El rol Analista no tiene permisos", str(ctx.exception))
