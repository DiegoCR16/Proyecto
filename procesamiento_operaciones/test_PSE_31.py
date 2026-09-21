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


class CurrencyPurchasePSE31Tests(TestCase):
    """
    Suite de pruebas unitarias e integración independiente y exclusiva para la Historia de Usuario PSE-31:
    Cancelación de Transacción por Cambio de Cotización antes del Pago (Épica PSE-12: Procesamiento de Operaciones).

    Valida:
    1. Detección de variación de tasa en tiempo real mientras la operación se encuentra en estado Pendiente (previo a confirmación de pago).
    2. Cancelación e interrupción automática cambiando el estado de la transacción a Cancelada y notificando al usuario.
    3. Opción de cancelación manual por el cliente mediante botón de acción en estado Pendiente.
    4. Garantía de sin débito financiero (interrupción inmediata sin cargos ni comisiones, preservando el saldo del método de pago).
    5. Integración con vistas web (GET, iniciación pendiente, confirmación con detección de variación, cancelación manual).
    """

    def setUp(self):
        """
        Configura el entorno de prueba con tasas de cambio iniciales, métodos de pago con saldo,
        roles, perfiles y cliente activo.
        """
        self.client = Client()
        self.factory = RequestFactory()
        self.purchase_url = reverse('procesamiento_operaciones:currency_purchase')

        # Configurar tasa de cambio inicial USD
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

        # Configurar método de pago con saldo amplio
        self.pm = PaymentMethod.objects.create(
            code='PM_PSE31',
            name='Cuenta Principal PSE31',
            method_type='TRANSFERENCIA',
            balance=Decimal('5000000.00'),
            is_active=True
        )

        ensure_default_benefit_rules()

        # Cliente de prueba
        self.cliente_test = Cliente.objects.create(
            nombre_o_razon_social='Cliente PSE-31 S.A.',
            tipo_cliente='JURIDICA',
            documento_identidad='80031313-1',
            email='pse31@client.com',
            categoria='MINORISTA'
        )

        self.role_test, _ = Role.objects.get_or_create(name="Role PSE31")
        self.user_test = User.objects.create_user(username='user_pse31', password='password123')
        self.profile_test = UserProfile.objects.create(
            user=self.user_test, role=self.role_test, category='MINORISTA', is_corporate=False
        )

    def _get_request(self, user):
        """Genera una solicitud RequestFactory con sesión del cliente activo."""
        req = self.factory.get(self.purchase_url)
        req.user = user
        req.session = {'active_client_id': str(self.cliente_test.id)}
        return req

    def test_initiate_pending_purchase(self):
        """
        Valida que al iniciar la compra se cree exitosamente un registro
        en estado PENDIENTE capturando la tasa de cambio vigente sin descontar fondos aún.
        """
        req = self._get_request(self.user_test)
        initial_balance = self.pm.balance

        tx = CurrencyPurchaseService.initiate_purchase(
            user=self.user_test,
            from_currency='PYG',
            to_currency='USD',
            amount=500000,
            payment_method_id_or_code=self.pm.code,
            request=req
        )

        self.assertEqual(tx.status, 'PENDING')
        self.assertEqual(tx.applied_rate, Decimal('7450.0000'))
        
        # Verificar que el saldo del método de pago NO ha cambiado (sin débito en estado pendiente)
        self.pm.refresh_from_db()
        self.assertEqual(self.pm.balance, initial_balance)

    def test_rate_variation_detection_and_automatic_cancellation(self):
        """
        Valida que si la tasa de cambio del sistema se modifica mientras la transacción
        está pendiente, al intentar confirmar el pago el sistema detecta la variación,
        cambia automáticamente el estado a CANCELLED y lanza un error de validación
        sin realizar ningún débito financiero.
        """
        req = self._get_request(self.user_test)
        initial_balance = self.pm.balance

        # 1. Iniciar transacción en estado PENDIENTE (Tasa venta USD = 7450)
        tx = CurrencyPurchaseService.initiate_purchase(
            user=self.user_test,
            from_currency='PYG',
            to_currency='USD',
            amount=500000,
            payment_method_id_or_code=self.pm.code,
            request=req
        )
        self.assertEqual(tx.status, 'PENDING')

        # 2. Modificar la tasa de cambio en el sistema (variación de tasa antes del pago)
        self.rate_usd.sell_rate = Decimal('7800.0000')
        self.rate_usd.save()

        # 3. Intentar confirmar el pago -> Debe detectar variación, cancelar automáticamente y lanzar ValidationError
        with self.assertRaises(ValidationError) as ctx:
            CurrencyPurchaseService.confirm_purchase(
                transaction_id=tx.id,
                user=self.user_test,
                request=req
            )
        self.assertIn("Alerta de Variación de Tasa", str(ctx.exception))

        # 4. Verificar que la transacción cambió a CANCELLED
        tx.refresh_from_db()
        self.assertEqual(tx.status, 'CANCELLED')

        # 5. Garantía de Sin Débito Financiero (saldo intacto)
        self.pm.refresh_from_db()
        self.assertEqual(self.pm.balance, initial_balance)

    def test_manual_cancellation_by_user(self):
        """
        Valida que el cliente pueda cancelar manualmente una transacción pendiente
        antes de confirmar el pago, cambiando su estado a CANCELLED sin afectar el saldo.
        """
        req = self._get_request(self.user_test)
        initial_balance = self.pm.balance

        tx = CurrencyPurchaseService.initiate_purchase(
            user=self.user_test,
            from_currency='PYG',
            to_currency='USD',
            amount=500000,
            payment_method_id_or_code=self.pm.code,
            request=req
        )
        self.assertEqual(tx.status, 'PENDING')

        # Cancelar manualmente
        tx_cancelled = CurrencyPurchaseService.cancel_purchase_manual(
            transaction_id=tx.id,
            user=self.user_test,
            request=req
        )
        self.assertEqual(tx_cancelled.status, 'CANCELLED')

        # Verificar saldo intacto (sin débito financiero)
        self.pm.refresh_from_db()
        self.assertEqual(self.pm.balance, initial_balance)

    def test_successful_confirmation_when_rate_unchanged(self):
        """
        Valida que si la tasa permanece sin cambios, la confirmación del pago
        procese el débito financiero y marque la transacción como SUCCESS.
        """
        req = self._get_request(self.user_test)
        initial_balance = self.pm.balance

        tx = CurrencyPurchaseService.initiate_purchase(
            user=self.user_test,
            from_currency='PYG',
            to_currency='USD',
            amount=500000,
            payment_method_id_or_code=self.pm.code,
            request=req
        )

        tx_success = CurrencyPurchaseService.confirm_purchase(
            transaction_id=tx.id,
            user=self.user_test,
            request=req
        )
        self.assertEqual(tx_success.status, 'SUCCESS')

        # Verificar que se realizó el débito financiero
        self.pm.refresh_from_db()
        self.assertLess(self.pm.balance, initial_balance)

    def test_pse31_web_view_integration(self):
        """
        Valida la integración con las vistas web para el flujo PSE-31:
        1. GET inicial de la página de compra.
        2. POST para iniciar orden pendiente (action=initiate_pending).
        3. POST para cancelación manual (action=cancel_purchase).
        """
        self.client.force_login(self.user_test)
        session = self.client.session
        session['active_client_id'] = str(self.cliente_test.id)
        session.save()

        # 1. GET inicial
        res_get = self.client.get(self.purchase_url)
        self.assertEqual(res_get.status_code, 200)

        # 2. POST para iniciar compra pendiente
        res_init = self.client.post(self.purchase_url, {
            'action': 'initiate_pending',
            'from_currency': 'PYG',
            'to_currency': 'USD',
            'amount': '200000',
            'payment_method': self.pm.code
        })
        self.assertEqual(res_init.status_code, 200)
        self.assertContains(res_init, "Pantalla de Pago y Verificación")

        # Obtener la transacción pendiente creada
        pending_tx = CurrencyPurchaseTransaction.objects.filter(cliente=self.cliente_test, status='PENDING').first()
        self.assertIsNotNone(pending_tx)

        # 3. POST para cancelación manual
        res_cancel = self.client.post(self.purchase_url, {
            'action': 'cancel_purchase',
            'transaction_id': pending_tx.id
        })
        self.assertEqual(res_cancel.status_code, 200)
        self.assertContains(res_cancel, "Transacción Cancelada de Forma Segura")
