# -*- coding: utf-8 -*-
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from decimal import Decimal
from authentication.models import UserProfile, Role
from tasas_cambio.models import ExchangeRate, ClientBenefitRule
from tasas_cambio.views import SimuladorConversionService, ensure_default_benefit_rules

class ConfiguracionBeneficiosPSE29Tests(TestCase):
    """
    Suite de pruebas unitarias y de integración independiente y exclusiva para la Historia de Usuario PSE-29:
    Configuración de Porcentajes de Comisión y Beneficios Arancelarios por Tipo de Cliente.
    Valida la parametrización dinámica de reglas sin modificar código fuente, la lógica del motor de precios
    según categoría y umbrales transaccionales, y el desglose transparente en la cotización.
    """

    def setUp(self):
        """
        Configura datos iniciales de tasas de cambio, roles, perfiles de usuario y reglas de beneficio PSE-29.
        """
        self.client = Client()
        self.simulator_url = reverse('tasas_cambio:simulator')
        self.config_url = reverse('tasas_cambio:client_benefit_config')

        # Asegurar reglas por defecto
        ensure_default_benefit_rules()

        # Crear rol administrativo y usuario admin
        self.role_admin, _ = Role.objects.get_or_create(name="Admin")
        self.admin_user = User.objects.create_superuser(username='admin_pse29', password='password123')
        self.admin_profile = UserProfile.objects.create(
            user=self.admin_user,
            role=self.role_admin,
            category='MINORISTA',
            is_corporate=False
        )

        # Crear usuarios clientes de prueba
        self.role_client, _ = Role.objects.get_or_create(name="Cliente")

        self.user_minorista = User.objects.create_user(username='minorista_pse29', password='password123')
        UserProfile.objects.create(
            user=self.user_minorista,
            role=self.role_client,
            category='MINORISTA',
            is_corporate=False
        )

        self.user_vip = User.objects.create_user(username='vip_pse29', password='password123')
        UserProfile.objects.create(
            user=self.user_vip,
            role=self.role_client,
            category='VIP',
            is_corporate=False
        )

        self.user_corp = User.objects.create_user(username='corp_pse29', password='password123')
        UserProfile.objects.create(
            user=self.user_corp,
            role=self.role_client,
            category='CORPORATIVO',
            is_corporate=True
        )

        # Tasas de cambio
        ExchangeRate.objects.create(
            currency_code='USD',
            currency_name='Dólar Estadounidense',
            buy_rate=Decimal('7300.0000'),
            sell_rate=Decimal('7450.0000')
        )
        ExchangeRate.objects.create(
            currency_code='PYG',
            currency_name='Guaraní Paraguayo',
            buy_rate=Decimal('1.0000'),
            sell_rate=Decimal('1.0000')
        )

    def test_dynamic_rule_parametrization_without_code_changes(self):
        """
        Valida que el administrador pueda parametrizar y actualizar los porcentajes de beneficio
        y umbrales por categoría dinámicamente desde la base de datos sin modificar el código fuente.
        """
        rule_vip = ClientBenefitRule.objects.get(category_code='VIP')
        self.assertEqual(rule_vip.benefit_percentage, Decimal('2.00'))
        self.assertEqual(rule_vip.min_operation_amount, Decimal('0.00'))

        # Simular actualización dinámica vía BD o formulario admin
        rule_vip.benefit_percentage = Decimal('3.50')
        rule_vip.min_operation_amount = Decimal('50000000.00')
        rule_vip.save()

        rule_vip_updated = ClientBenefitRule.objects.get(category_code='VIP')
        self.assertEqual(rule_vip_updated.benefit_percentage, Decimal('3.50'))
        self.assertEqual(rule_vip_updated.min_operation_amount, Decimal('50000000.00'))

    def test_price_engine_logic_and_thresholds(self):
        """
        Valida la lógica del motor de precios según la categoría y el umbral transaccional parametrizado:
        - Minorista: 0% beneficio.
        - VIP: 2% beneficio cuando se alcanza el umbral configurado (> 50.000.000 Gs).
        """
        # Configurar umbral VIP a 50.000.000 Gs
        rule_vip = ClientBenefitRule.objects.get(category_code='VIP')
        rule_vip.min_operation_amount = Decimal('50000000.00')
        rule_vip.save()

        # 1. VIP con monto superior al umbral (60.000.000 PYG >= 50M) -> Aplica 2%
        res_vip_ok = SimuladorConversionService.simular('PYG', 'USD', Decimal('60000000.00'), self.user_vip)
        self.assertTrue(res_vip_ok['threshold_met'])
        self.assertEqual(res_vip_ok['benefit_percentage'], Decimal('2.00'))

        # 2. VIP con monto inferior al umbral (30.000.000 PYG < 50M) -> No aplica beneficio (0%)
        res_vip_bajo = SimuladorConversionService.simular('PYG', 'USD', Decimal('30000000.00'), self.user_vip)
        self.assertFalse(res_vip_bajo['threshold_met'])
        self.assertEqual(res_vip_bajo['benefit_percentage'], Decimal('0.00'))

        # 3. Corporativo con monto superior al umbral configurado (120.000.000 PYG >= 100M)
        rule_corp = ClientBenefitRule.objects.get(category_code='CORPORATIVO')
        rule_corp.min_operation_amount = Decimal('100000000.00')
        rule_corp.save()

        res_corp_ok = SimuladorConversionService.simular('PYG', 'USD', Decimal('120000000.00'), self.user_corp)
        self.assertTrue(res_corp_ok['threshold_met'])
        self.assertEqual(res_corp_ok['benefit_percentage'], Decimal('4.00'))

    def test_transparent_breakdown_in_quotation(self):
        """
        Valida que el resultado de la simulación incluya el desglose transparente del beneficio aplicado
        (categoría, umbral, porcentaje de beneficio y estado) visible para el usuario previo a la transacción.
        """
        res = SimuladorConversionService.simular('PYG', 'USD', Decimal('75000000.00'), self.user_vip)
        self.assertIn('transparent_breakdown', res)
        self.assertIn('VIP', res['transparent_breakdown'])
        self.assertIn('2.00%', res['transparent_breakdown'])
        self.assertTrue(res['threshold_met'])
        self.assertEqual(res['category_name'], 'VIP')

    def test_admin_config_view_integration(self):
        """
        Valida la integración web del panel de configuración de beneficios para el Administrador (GET y POST).
        """
        self.client.force_login(self.admin_user)

        # GET configuración
        resp_get = self.client.get(self.config_url)
        self.assertEqual(resp_get.status_code, 200)
        self.assertContains(resp_get, "Configuración de Porcentajes de Comisión")

        # POST actualización de reglas
        resp_post = self.client.post(self.config_url, {
            'min_amount_MINORISTA': '0.00',
            'benefit_MINORISTA': '0.00',
            'min_amount_VIP': '45000000.00',
            'benefit_VIP': '2.50',
            'min_amount_CORPORATIVO': '90000000.00',
            'benefit_CORPORATIVO': '4.50',
        })
        self.assertEqual(resp_post.status_code, 200)
        self.assertContains(resp_post, "Reglas de beneficio y umbrales actualizados exitosamente")

        # Verificar cambio persistido
        rule_vip = ClientBenefitRule.objects.get(category_code='VIP')
        self.assertEqual(rule_vip.benefit_percentage, Decimal('2.50'))
        self.assertEqual(rule_vip.min_operation_amount, Decimal('45000000.00'))
