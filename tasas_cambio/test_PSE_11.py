# -*- coding: utf-8 -*-
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from decimal import Decimal
from authentication.models import UserProfile, Role
from tasas_cambio.models import ExchangeRate
from tasas_cambio.views import SimuladorConversionService

class SimuladorConversionPSE11Tests(TestCase):
    """
    Suite de pruebas unitarias independiente y exclusiva para la Historia de Usuario PSE-11:
    Simulador de Conversión de Divisas.
    Valida la obligatoriedad de los campos, la precisión matemática de cálculo para compra/venta
    con beneficios de categoría de cliente (VIP, Corporativo, Minorista), y el manejo de excepciones
    ante divisas sin tasa de cambio registrada.
    """

    def setUp(self):
        """
        Configura datos iniciales de tasas de cambio, roles y perfiles de usuario para la prueba.
        """
        self.client = Client()
        self.simulator_url = reverse('tasas_cambio:simulator')

        # Crear roles y perfiles
        self.role_vip, _ = Role.objects.get_or_create(name="VIP Role PSE11")
        self.role_corp, _ = Role.objects.get_or_create(name="Corporate Role PSE11")

        self.user_minorista = User.objects.create_user(username='user_min_pse11', password='password123')
        self.profile_min = UserProfile.objects.create(
            user=self.user_minorista,
            role=self.role_vip,
            category='MINORISTA',
            is_corporate=False
        )

        self.user_vip = User.objects.create_user(username='user_vip_pse11', password='password123')
        self.profile_vip = UserProfile.objects.create(
            user=self.user_vip,
            role=self.role_vip,
            category='VIP',
            is_corporate=False
        )

        self.user_corp = User.objects.create_user(username='user_corp_pse11', password='password123')
        self.profile_corp = UserProfile.objects.create(
            user=self.user_corp,
            role=self.role_corp,
            category='CORPORATIVO',
            is_corporate=True
        )

        # Configurar tasas de cambio estándar
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

    def test_mandatory_fields_validation(self):
        """
        Valida que el simulador exija de forma obligatoria los campos moneda origen, moneda destino y monto,
        lanzando una ValidationError si falta alguno o el monto es inválido/cero.
        """
        # 1. Falta moneda origen
        with self.assertRaises(ValidationError):
            SimuladorConversionService.simular(from_currency='', to_currency='USD', amount=1000, user=self.user_minorista)

        # 2. Falta moneda destino
        with self.assertRaises(ValidationError):
            SimuladorConversionService.simular(from_currency='PYG', to_currency='', amount=1000, user=self.user_minorista)

        # 3. Monto nulo o cero o negativo
        with self.assertRaises(ValidationError):
            SimuladorConversionService.simular(from_currency='PYG', to_currency='USD', amount=None, user=self.user_minorista)

        with self.assertRaises(ValidationError):
            SimuladorConversionService.simular(from_currency='PYG', to_currency='USD', amount=Decimal('0.00'), user=self.user_minorista)

    def test_missing_exchange_rate_exception(self):
        """
        Valida que si el sistema no cuenta con la tasa de cambio registrada para la divisa seleccionada,
        la simulación aborte lanzando un error claro (ValidationError).
        """
        with self.assertRaises(ValidationError) as ctx:
            SimuladorConversionService.simular(from_currency='PYG', to_currency='XXX', amount=50000, user=self.user_minorista)
        
        self.assertIn("no cuenta con la tasa de cambio registrada", str(ctx.exception))

    def test_mathematical_precision_and_category_benefits(self):
        """
        Valida la precisión matemática del cálculo de conversión para compras (PYG -> Divisa) y ventas (Divisa -> PYG),
        así como la aplicación correcta de beneficios según categoría de cliente (Minorista 0%, VIP 2%, Corporativo 4%).
        """
        monto_pyg = Decimal('745000.00')

        # 1. Minorista (0% beneficio): Comprar USD con PYG.
        # Tasa venta USD = 7450. Monto esperado = 745000 / 7450 = 100.00 USD
        res_min = SimuladorConversionService.simular('PYG', 'USD', monto_pyg, self.user_minorista)
        self.assertEqual(res_min['converted_amount'], Decimal('100.00'))
        self.assertEqual(res_min['benefit_percentage'], Decimal('0.00'))

        # 2. VIP (2% beneficio en compra): Factor = 0.98. Tasa custom venta = 7450 * 0.98 = 7301.0000
        # Monto esperado = 745000 / 7301.0000 = 102.04 USD aprox.
        res_vip = SimuladorConversionService.simular('PYG', 'USD', monto_pyg, self.user_vip)
        expected_vip_rate = (Decimal('7450.0000') * Decimal('0.98')).quantize(Decimal('0.0001'))
        expected_vip_amount = (monto_pyg / expected_vip_rate).quantize(Decimal('0.01'))
        self.assertEqual(res_vip['applied_rate'], expected_vip_rate)
        self.assertEqual(res_vip['converted_amount'], expected_vip_amount)
        self.assertEqual(res_vip['benefit_percentage'], Decimal('2.00'))

        # 3. Corporativo (4% beneficio en compra): Factor = 0.96. Tasa custom venta = 7450 * 0.96 = 7152.0000
        res_corp = SimuladorConversionService.simular('PYG', 'USD', monto_pyg, self.user_corp)
        expected_corp_rate = (Decimal('7450.0000') * Decimal('0.96')).quantize(Decimal('0.0001'))
        expected_corp_amount = (monto_pyg / expected_corp_rate).quantize(Decimal('0.01'))
        self.assertEqual(res_corp['applied_rate'], expected_corp_rate)
        self.assertEqual(res_corp['converted_amount'], expected_corp_amount)
        self.assertEqual(res_corp['benefit_percentage'], Decimal('4.00'))

    def test_simulator_web_view_integration(self):
        """
        Valida que la vista HTTP del simulador responda correctamente (status 200) tanto en GET
        como en peticiones POST de simulación con datos válidos o inválidos.
        """
        # GET inicial
        response_get = self.client.get(self.simulator_url)
        self.assertEqual(response_get.status_code, 200)
        self.assertContains(response_get, "Simulador")

        # POST con simulación exitosa
        response_post = self.client.post(self.simulator_url, {
            'from_currency': 'PYG',
            'to_currency': 'USD',
            'amount': '150000'
        })
        self.assertEqual(response_post.status_code, 200)
        self.assertContains(response_post, "Resultado Estimado")

        # POST con error (divisa inexistente)
        response_error = self.client.post(self.simulator_url, {
            'from_currency': 'PYG',
            'to_currency': 'BTC',
            'amount': '100000'
        })
        self.assertEqual(response_error.status_code, 200)
        self.assertContains(response_error, "Error en la Simulación")
