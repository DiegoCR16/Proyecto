# -*- coding: utf-8 -*-
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from decimal import Decimal
from authentication.models import UserProfile, Role
from tasas_cambio.models import ExchangeRate, PaymentMethod
from tasas_cambio.views import ensure_default_payment_methods

class ParametrizacionDivisasMetodosPSE25Tests(TestCase):
    """
    Suite de pruebas unitarias y de integración independiente y exclusiva para la Historia de Usuario PSE-25:
    Parametrización y CRUD de Divisas y Métodos de Pago.
    Valida el CRUD completo de divisas, activación/desactivación y CRUD de métodos de pago,
    y la restricción estricta de permisos de acceso exclusivo para administradores.
    """

    def setUp(self):
        """
        Configura datos iniciales de roles, perfiles de usuario administrador y cliente, y métodos de pago base.
        """
        self.client = Client()
        self.config_url = reverse('tasas_cambio:currency_payment_config')
        self.rates_url = reverse('tasas_cambio:rates_board')

        ensure_default_payment_methods()

        # Rol y usuario Administrador
        self.role_admin, _ = Role.objects.get_or_create(name="Admin")
        self.admin_user = User.objects.create_superuser(username='admin_pse25', password='password123')
        self.admin_profile = UserProfile.objects.create(
            user=self.admin_user,
            role=self.role_admin,
            category='CORPORATIVO',
            is_corporate=True
        )

        # Rol y usuario Cliente / Cajero estándar (no administrador)
        self.role_client, _ = Role.objects.get_or_create(name="Cliente")
        self.client_user = User.objects.create_user(username='client_pse25', password='password123')
        self.client_profile = UserProfile.objects.create(
            user=self.client_user,
            role=self.role_client,
            category='MINORISTA',
            is_corporate=False
        )

    def test_currency_crud_operations(self):
        """
        Valida el CRUD completo de divisas (Crear, Leer, Actualizar y Eliminar) incluyendo atributos obligatorios.
        """
        # Create / Read
        ExchangeRate.objects.create(
            currency_code='BRL',
            currency_name='Real Brasileño',
            symbol='R$',
            buy_rate=Decimal('1350.0000'),
            sell_rate=Decimal('1450.0000')
        )
        brl = ExchangeRate.objects.get(currency_code='BRL')
        self.assertEqual(brl.currency_name, 'Real Brasileño')
        self.assertEqual(brl.symbol, 'R$')

        # Update
        brl.currency_name = 'Real Brasileño Actualizado'
        brl.save()
        self.assertEqual(ExchangeRate.objects.get(currency_code='BRL').currency_name, 'Real Brasileño Actualizado')

        # Delete
        brl.delete()
        self.assertFalse(ExchangeRate.objects.filter(currency_code='BRL').exists())

    def test_payment_method_crud_and_toggle(self):
        """
        Valida el CRUD completo, tipos de método de pago (tarjeta crédito/débito, número de cuenta) y toggles de estado.
        """
        # Create Credit Card payment method with account/card number
        pm = PaymentMethod.objects.create(
            code='TC_VISA',
            name='Tarjeta de Crédito Visa',
            method_type='TARJETA_CREDITO',
            account_number='4532-1111-2222-3334',
            bank_name='Banco Itaú',
            account_type='Crédito',
            holder_name='Global Exchange S.A.',
            description='Tarjeta corporativa de crédito.',
            is_active=True
        )
        self.assertTrue(PaymentMethod.objects.filter(code='TC_VISA').exists())
        self.assertEqual(pm.account_number, '4532-1111-2222-3334')
        self.assertEqual(pm.method_type, 'TARJETA_CREDITO')

        # Toggle state
        pm.is_active = False
        pm.save()
        self.assertFalse(PaymentMethod.objects.get(code='TC_VISA').is_active)

        # Delete
        pm.delete()
        self.assertFalse(PaymentMethod.objects.filter(code='TC_VISA').exists())

    def test_admin_permission_restriction(self):
        """
        Valida que únicamente los usuarios administradores puedan acceder al panel CRUD de parametrización.
        """
        self.client.force_login(self.client_user)
        response_client = self.client.get(self.config_url)
        self.assertRedirects(response_client, self.rates_url)

        self.client.force_login(self.admin_user)
        response_admin = self.client.get(self.config_url)
        self.assertEqual(response_admin.status_code, 200)
        self.assertContains(response_admin, "Catálogo de Métodos de Pago")

    def test_admin_post_crud_integration(self):
        """
        Valida las operaciones POST administrativas de creación y eliminación de divisas y métodos de pago (incluyendo tarjetas con número de cuenta).
        """
        self.client.force_login(self.admin_user)

        # POST Crear divisa CLP
        resp_curr = self.client.post(self.config_url, {
            'action': 'add_currency',
            'currency_code': 'CLP',
            'currency_name': 'Peso Chileno',
            'currency_symbol': '$'
        })
        self.assertEqual(resp_curr.status_code, 200)
        self.assertTrue(ExchangeRate.objects.filter(currency_code='CLP').exists())

        # POST Crear método de pago Tarjeta de Débito con número de cuenta
        resp_pm = self.client.post(self.config_url, {
            'action': 'add_payment_method',
            'pm_code': 'TD_MASTER',
            'pm_name': 'Tarjeta Débito Mastercard',
            'pm_method_type': 'TARJETA_DEBITO',
            'pm_account_number': '5412-9999-8888-7776',
            'pm_bank_name': 'Vision Banco',
            'pm_account_type': 'Débito',
            'pm_holder_name': 'Global Exchange S.A.',
            'pm_description': 'Cobro con tarjeta de débito.',
            'pm_is_active': 'on'
        })
        self.assertEqual(resp_pm.status_code, 200)
        pm_created = PaymentMethod.objects.get(code='TD_MASTER')
        self.assertTrue(pm_created)
        self.assertEqual(pm_created.method_type, 'TARJETA_DEBITO')
        self.assertEqual(pm_created.account_number, '5412-9999-8888-7776')

        # POST Eliminar divisa CLP
        resp_del_curr = self.client.post(self.config_url, {
            'action': 'delete_currency',
            'currency_code': 'CLP'
        })
        self.assertEqual(resp_del_curr.status_code, 200)
        self.assertFalse(ExchangeRate.objects.filter(currency_code='CLP').exists())
