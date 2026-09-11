# -*- coding: utf-8 -*-
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from decimal import Decimal
from authentication.models import UserProfile, Role
from tasas_cambio.models import ExchangeRate, ExchangeRateHistory


class RatesManagerPSE30Tests(TestCase):
    """
    Suite de pruebas unitarias independiente y exclusiva para la Historia de Usuario PSE-30:
    Gestor y CRUD de Cotizaciones de Divisas.
    Valida la actualización de tasas en tiempo real, el registro de auditoría e historial,
    la creación, edición y eliminación de cotizaciones históricas, y las restricciones de acceso.
    """

    def setUp(self):
        """
        Configura el cliente de prueba, usuarios de prueba (admin y operador)
        y datos iniciales de tasas de cambio para USD.
        """
        self.client = Client()
        self.manager_url = reverse('tasas_cambio:rates_manager')

        self.admin_role, _ = Role.objects.get_or_create(name='Administrador')

        from django.contrib.auth.models import User
        self.admin_user = User.objects.create_user(username='admin_pse30', password='password123', is_staff=True)
        self.admin_profile, _ = UserProfile.objects.get_or_create(user=self.admin_user)
        self.admin_profile.role = self.admin_role
        self.admin_profile.save()

        self.client_user = User.objects.create_user(username='client_pse30', password='password123')

        self.usd_rate, _ = ExchangeRate.objects.update_or_create(
            currency_code='USD',
            defaults={
                'currency_name': 'Dólar Estadounidense',
                'symbol': '$',
                'buy_rate': Decimal('7300.0000'),
                'sell_rate': Decimal('7450.0000')
            }
        )

    def test_live_rate_update_and_audit(self):
        """
        Valida que la actualización en tiempo real de una tasa de cambio actualice
        el modelo ExchangeRate y genere automáticamente un registro en ExchangeRateHistory (auditoría).
        """
        self.client.force_login(self.admin_user)
        initial_history_count = ExchangeRateHistory.objects.filter(currency_code='USD').count()

        response = self.client.post(self.manager_url, {
            'action': 'update_live_rate',
            'currency_code': 'USD',
            'buy_rate': '7350.0000',
            'sell_rate': '7500.0000'
        })
        self.assertEqual(response.status_code, 200)

        self.usd_rate.refresh_from_db()
        self.assertEqual(self.usd_rate.buy_rate, Decimal('7350.0000'))
        self.assertEqual(self.usd_rate.sell_rate, Decimal('7500.0000'))

        new_history_count = ExchangeRateHistory.objects.filter(currency_code='USD').count()
        self.assertEqual(new_history_count, initial_history_count + 1)
        latest_hist = ExchangeRateHistory.objects.filter(currency_code='USD').order_by('-timestamp').first()
        self.assertEqual(latest_hist.buy_rate, Decimal('7350.0000'))
        self.assertEqual(latest_hist.sell_rate, Decimal('7500.0000'))

    def test_historical_rate_crud_operations(self):
        """
        Valida el ciclo CRUD completo para cotizaciones históricas:
        1. Creación de cotización histórica con fecha/hora específica.
        2. Edición del registro histórico para corregir errores.
        3. Eliminación del registro histórico.
        """
        self.client.force_login(self.admin_user)
        past_time = timezone.now() - timezone.timedelta(days=5)
        past_time_str = past_time.strftime('%Y-%m-%dT%H:%M')

        # 1. Crear cotización histórica
        resp_create = self.client.post(self.manager_url, {
            'action': 'add_historical_rate',
            'history_currency_code': 'USD',
            'history_buy_rate': '7100.0000',
            'history_sell_rate': '7250.0000',
            'history_timestamp': past_time_str
        })
        self.assertEqual(resp_create.status_code, 200)
        hist_record = ExchangeRateHistory.objects.filter(currency_code='USD', buy_rate=Decimal('7100.0000')).first()
        self.assertIsNotNone(hist_record)

        # 2. Editar cotización histórica
        resp_edit = self.client.post(self.manager_url, {
            'action': 'edit_historical_rate',
            'history_id': hist_record.id,
            'history_buy_rate': '7150.0000',
            'history_sell_rate': '7300.0000',
            'history_timestamp': past_time_str
        })
        self.assertEqual(resp_edit.status_code, 200)
        hist_record.refresh_from_db()
        self.assertEqual(hist_record.buy_rate, Decimal('7150.0000'))
        self.assertEqual(hist_record.sell_rate, Decimal('7300.0000'))

        # 3. Eliminar cotización histórica
        resp_delete = self.client.post(self.manager_url, {
            'action': 'delete_historical_rate',
            'history_id': hist_record.id
        })
        self.assertEqual(resp_delete.status_code, 200)
        self.assertFalse(ExchangeRateHistory.objects.filter(id=hist_record.id).exists())

    def test_rates_manager_access_control(self):
        """
        Valida que usuarios sin privilegios de administrador/operador sean redirigidos,
        mientras que administradores accedan correctamente al panel Gestor (status 200).
        """
        self.client.force_login(self.client_user)
        resp_client = self.client.get(self.manager_url)
        self.assertRedirects(resp_client, reverse('tasas_cambio:rates_board'))

        self.client.force_login(self.admin_user)
        resp_admin = self.client.get(self.manager_url)
        self.assertEqual(resp_admin.status_code, 200)
        self.assertContains(resp_admin, "Gestor y CRUD de Cotizaciones")
