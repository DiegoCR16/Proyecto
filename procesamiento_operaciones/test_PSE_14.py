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
from procesamiento_operaciones.models import CurrencySaleTransaction
from procesamiento_operaciones.views import CurrencySaleService


class CurrencySalePSE14Tests(TestCase):
    """
    Suite de pruebas unitarias e integración independiente y exclusiva para la Historia de Usuario PSE-14:
    Operación de Venta de Divisas (Épica PSE-12: Procesamiento de Operaciones Cambiarias).
    
    Valida:
    1. Validación estricta de límites (mínimo 50.000 PYG, máximo 1.000.000.000 PYG) en base a la moneda cotizada.
    2. Aplicación de la tasa de compra vigente provista por la casa de cambio.
    3. Validación obligatoria de cuenta bancaria o billetera digital vinculada en el sistema.
    4. Rendimiento y tiempo de respuesta optimizado inferior a 5 segundos (< 5000 ms).
    5. Aislamiento de operaciones y restricciones por rol Analista.
    """

    def setUp(self):
        """
        Configura el entorno de prueba con tasas de cambio, métodos de pago vinculados,
        roles, perfiles de usuario y cliente activo.
        """
        self.client = Client()
        self.factory = RequestFactory()
        self.sale_url = reverse('procesamiento_operaciones:currency_sale')
        self.history_url = reverse('procesamiento_operaciones:currency_sale_history')

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

        # Configurar cuenta bancaria o billetera digital vinculada
        self.linked_account = PaymentMethod.objects.create(
            code='CTA_VINCULADA_1',
            name='Cuenta Bancaria Vinculada Principal',
            method_type='TRANSFERENCIA',
            account_number='1029384756',
            bank_name='Banco Central PSE14',
            balance=Decimal('1000000.00'),
            is_active=True
        )
        self.inactive_account = PaymentMethod.objects.create(
            code='CTA_INACTIVA',
            name='Cuenta Inactiva',
            method_type='TRANSFERENCIA',
            account_number='9999999999',
            bank_name='Banco Inactivo',
            balance=Decimal('0.00'),
            is_active=False
        )

        # Configurar reglas de beneficio iniciales
        ensure_default_benefit_rules()

        # Crear cliente de prueba
        self.cliente_test = Cliente.objects.create(
            nombre_o_razon_social='Cliente Venta SA',
            tipo_cliente='JURIDICA',
            documento_identidad='80011122-3',
            email='ventas@client.com',
            categoria='MINORISTA'
        )

        # Crear perfiles de usuario
        self.role_test, _ = Role.objects.get_or_create(name="Role PSE14")

        self.user_min = User.objects.create_user(username='user_min_pse14', password='password123')
        self.profile_min = UserProfile.objects.create(
            user=self.user_min, role=self.role_test, category='MINORISTA', is_corporate=False
        )

        self.user_vip = User.objects.create_user(username='user_vip_pse14', password='password123')
        self.profile_vip = UserProfile.objects.create(
            user=self.user_vip, role=self.role_test, category='VIP', is_corporate=False
        )

    def _get_request(self, user):
        """
        Helper para crear una solicitud HTTP con sesión del cliente activo.

        Args:
            user (User): Usuario solicitante.

        Returns:
            HttpRequest: Solicitud HTTP configurada.
        """
        req = self.factory.get(self.sale_url)
        req.user = user
        req.session = {'active_client_id': str(self.cliente_test.id)}
        return req

    def test_amount_limits_validation(self):
        """
        Valida que el sistema verifique estrictamente que el monto de la venta
        equivalente en guaraníes no exceda 1.000.000.000 PYG ni sea inferior a 50.000 PYG.
        """
        req = self._get_request(self.user_min)
        # 1. Monto inferior al mínimo (ej. 1 USD -> 7.300 PYG < 50.000 PYG)
        with self.assertRaises(ValidationError) as ctx_min:
            CurrencySaleService.process_sale(
                user=self.user_min,
                from_currency='USD',
                to_currency='PYG',
                amount=1,
                payment_method_id_or_code=self.linked_account.code,
                request=req
            )
        self.assertIn("inferior al límite mínimo", str(ctx_min.exception))

        # 2. Monto superior al máximo (ej. 200.000 USD -> > 1.000.000.000 PYG)
        with self.assertRaises(ValidationError) as ctx_max:
            CurrencySaleService.process_sale(
                user=self.user_min,
                from_currency='USD',
                to_currency='PYG',
                amount=200000,
                payment_method_id_or_code=self.linked_account.code,
                request=req
            )
        self.assertIn("excede el límite máximo", str(ctx_max.exception))

    def test_mandatory_linked_account_validation(self):
        """
        Valida que el cliente deba contar obligatoriamente con una cuenta bancaria
        o billetera digital vinculada y activa para proceder con la venta.
        """
        req = self._get_request(self.user_min)
        # 1. Sin cuenta vinculada
        with self.assertRaises(ValidationError) as ctx_no_acc:
            CurrencySaleService.process_sale(
                user=self.user_min,
                from_currency='USD',
                to_currency='PYG',
                amount=100,
                payment_method_id_or_code='',
                request=req
            )
        self.assertIn("debe contar obligatoriamente", str(ctx_no_acc.exception))

        # 2. Con cuenta inactiva
        with self.assertRaises(ValidationError) as ctx_inactive:
            CurrencySaleService.process_sale(
                user=self.user_min,
                from_currency='USD',
                to_currency='PYG',
                amount=100,
                payment_method_id_or_code=self.inactive_account.code,
                request=req
            )
        self.assertIn("debe contar obligatoriamente", str(ctx_inactive.exception))

    def test_buy_rate_application_and_transparent_breakdown(self):
        """
        Valida que la conversión se realice aplicando estrictamente la tasa de compra vigente (buy_rate)
        provista por la casa de cambio y generando un desglose transparente.
        """
        req = self._get_request(self.user_min)
        monto_venta = 100  # 100 USD -> 100 * 7300 = 730.000 PYG

        tx = CurrencySaleService.process_sale(
            user=self.user_min,
            from_currency='USD',
            to_currency='PYG',
            amount=monto_venta,
            payment_method_id_or_code=self.linked_account.code,
            request=req
        )

        self.assertEqual(tx.status, 'SUCCESS')
        self.assertEqual(tx.applied_rate, Decimal('7300.0000'))
        self.assertEqual(tx.amount, Decimal('100'))
        self.assertGreater(tx.converted_amount, Decimal('0'))
        self.assertIn("Comisión", tx.transparent_breakdown)

    def test_processing_time_performance(self):
        """
        Valida que el tiempo de respuesta para procesar la transacción de venta
        no exceda los 5 segundos (estándar menor a 5000 ms).
        """
        req = self._get_request(self.user_min)
        start = time.time()
        tx = CurrencySaleService.process_sale(
            user=self.user_min,
            from_currency='USD',
            to_currency='PYG',
            amount=50,
            payment_method_id_or_code=self.linked_account.code,
            request=req
        )
        duration_ms = int((time.time() - start) * 1000)

        self.assertLess(duration_ms, 5000)
        self.assertLess(tx.processing_time_ms, 5000)

    def test_currency_sale_web_view_integration(self):
        """
        Valida que la vista HTTP web de venta de divisas responda correctamente (status 200)
        tanto en solicitudes GET como en el flujo de iniciación pendiente y confirmación exitosa (PSE-31 / PSE-14).
        """
        self.client.force_login(self.user_vip)
        session = self.client.session
        session['active_client_id'] = str(self.cliente_test.id)
        session.save()

        # GET inicial
        res_get = self.client.get(self.sale_url)
        self.assertEqual(res_get.status_code, 200)
        self.assertContains(res_get, "Operación Digital de Venta de Divisas")

        # POST para iniciar orden pendiente
        res_post = self.client.post(self.sale_url, {
            'from_currency': 'USD',
            'to_currency': 'PYG',
            'amount': '50',
            'payment_method': self.linked_account.code
        })
        self.assertEqual(res_post.status_code, 200)
        self.assertContains(res_post, "Pantalla de Acreditación y Verificación")

        pending_tx = CurrencySaleTransaction.objects.filter(cliente=self.cliente_test, status='PENDING').first()
        self.assertIsNotNone(pending_tx)

        # POST para confirmar la venta
        res_confirm = self.client.post(self.sale_url, {
            'action': 'confirm_sale',
            'transaction_id': pending_tx.id
        })
        self.assertEqual(res_confirm.status_code, 200)
        self.assertContains(res_confirm, "¡Transacción de Venta Procesada con Éxito!")

    def test_analista_cannot_sell_currencies(self):
        """
        Valida que un usuario con rol Analista tenga prohibido realizar operaciones
        de venta de divisas, lanzando un error de validación.
        """
        role_analista, _ = Role.objects.get_or_create(name="Analista")
        user_analista = User.objects.create_user(username='user_analista_pse14', password='password123')
        UserProfile.objects.create(user=user_analista, role=role_analista, category='MINORISTA')

        req = self._get_request(user_analista)
        with self.assertRaises(ValidationError) as ctx:
            CurrencySaleService.process_sale(
                user=user_analista,
                from_currency='USD',
                to_currency='PYG',
                amount=50,
                payment_method_id_or_code=self.linked_account.code,
                request=req
            )
        self.assertIn("El rol Analista no tiene permisos", str(ctx.exception))
