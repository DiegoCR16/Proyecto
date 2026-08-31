# -*- coding: utf-8 -*-
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from decimal import Decimal
import time
from authentication.models import UserProfile, Role
from tasas_cambio.models import ExchangeRate

class PublicRatesPSE9Tests(TestCase):
    """
    Suite de pruebas unitarias independiente y exclusiva para la Historia de Usuario PSE-9:
    Consulta Pública de Tasas de Cambio en Tiempo Real (Epic: Consultas de Tasas de Cambio e Historicos).
    Valida la obtención de divisas predeterminadas (USD, EUR, BRL, ARS, PYG), la aplicación
    automática de beneficios/descuentos (2% VIP, 4% Corporativo) para usuarios autenticados versus invitados,
    y el desempeño en la entrega de las tasas tanto en la vista dedicada como en la interfaz de login.
    """

    def setUp(self):
        """
        Configuración inicial de clientes, roles y perfiles de prueba.
        """
        self.client = Client()
        self.rates_url = reverse('tasas_cambio:rates_board')
        self.login_url = reverse('login')

        # Crear roles y usuarios de prueba
        self.role_vip, _ = Role.objects.get_or_create(name="VIP Role")
        self.role_corp, _ = Role.objects.get_or_create(name="Corporate Role")

        self.user_vip = User.objects.create_user(username='user_vip', password='password123')
        self.profile_vip = UserProfile.objects.create(
            user=self.user_vip,
            role=self.role_vip,
            category='VIP',
            is_corporate=False
        )

        self.user_corp = User.objects.create_user(username='user_corp', password='password123')
        self.profile_corp = UserProfile.objects.create(
            user=self.user_corp,
            role=self.role_corp,
            category='CORPORATIVO',
            is_corporate=True
        )

    def test_default_currencies_creation_and_display(self):
        """
        Valida que tanto la pizarra pública como la interfaz de login obtengan y desplieguen
        correctamente las 5 divisas predeterminadas: USD, EUR, BRL, ARS y PYG.
        """
        for url in [self.rates_url, self.login_url]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)

            # Verificar existencia en Base de Datos de las 5 divisas
            currencies = ['USD', 'EUR', 'BRL', 'ARS', 'PYG']
            for code in currencies:
                self.assertTrue(ExchangeRate.objects.filter(currency_code=code).exists())
                self.assertContains(response, code)

    def test_guest_vs_authenticated_benefits_logic(self):
        """
        Valida que un invitado en login vea tasas estándar (0% beneficio), un usuario VIP
        vea aplicado el 2% de beneficio, y un usuario Corporativo vea aplicado el 4%.
        """
        # 1. Invitado (Guest en Login)
        response_guest = self.client.get(self.login_url)
        self.assertEqual(response_guest.status_code, 200)
        self.assertEqual(response_guest.context['benefit_percentage'], Decimal('0.00'))
        self.assertEqual(response_guest.context['category_display'], 'Invitado (Acceso Público)')

        # 2. Usuario VIP (2% beneficio)
        self.client.force_login(self.user_vip)
        response_vip = self.client.get(self.login_url)
        self.assertEqual(response_vip.status_code, 200)
        self.assertEqual(response_vip.context['benefit_percentage'], Decimal('2.00'))
        self.assertEqual(response_vip.context['category_display'], 'VIP')

        # 3. Usuario Corporativo (4% beneficio)
        self.client.force_login(self.user_corp)
        response_corp = self.client.get(self.login_url)
        self.assertEqual(response_corp.status_code, 200)
        self.assertEqual(response_corp.context['benefit_percentage'], Decimal('4.00'))
        self.assertEqual(response_corp.context['category_display'], 'Corporativo')

    def test_exchange_rates_performance_and_realtime(self):
        """
        Valida el correcto desempeño y tiempo de respuesta en la entrega de las tasas de cambio
        en tiempo real (menor a 0.5 segundos por solicitud en la interfaz de login).
        """
        start_time = time.time()
        response = self.client.get(self.login_url)
        end_time = time.time()

        duration = end_time - start_time
        self.assertEqual(response.status_code, 200)
        self.assertLess(duration, 0.5, f"La respuesta tardó {duration:.4f}s, superando el límite de desempeño.")
