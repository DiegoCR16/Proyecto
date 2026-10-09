# -*- coding: utf-8 -*-
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from decimal import Decimal
from django.contrib.auth.models import User
from authentication.models import UserProfile, Role
from tasas_cambio.models import ExchangeRate, ExchangeRateHistory, CurrencyAlert, NotificationLog


class RateAlertsPSE35Tests(TestCase):
    """
    Suite de pruebas unitarias independiente y exclusiva para la Historia de Usuario PSE-35:
    Notificaciones por cambios de tasa (Alertas de tasa objetivo, disparo automático y variación abrupta).
    """

    def setUp(self):
        """
        Configura el cliente de prueba, usuario cliente, rol y divisa inicial USD.
        """
        self.client = Client()
        self.alerts_url = reverse('tasas_cambio:currency_alerts')
        self.manager_url = reverse('tasas_cambio:rates_manager')

        self.admin_user = User.objects.create_superuser(username='admin_pse35', password='password123')
        self.client_user = User.objects.create_user(username='client_pse35', password='password123')
        self.client_profile, _ = UserProfile.objects.get_or_create(user=self.client_user)

        self.usd_rate, _ = ExchangeRate.objects.update_or_create(
            currency_code='USD',
            defaults={
                'currency_name': 'Dólar Estadounidense',
                'symbol': '$',
                'buy_rate': Decimal('7300.0000'),
                'sell_rate': Decimal('7450.0000')
            }
        )

    def test_alert_crud_and_management(self):
        """
        Valida el ciclo completo de gestión de alertas de tasa (Criterio a y d):
        1. Creación de alerta (divisa USD, compra, tasa objetivo 7400, canal PUSH).
        2. Consulta de alertas vigentes.
        3. Edición de la alerta (cambio de tasa objetivo).
        4. Desactivación (toggle) y eliminación de la alerta.
        """
        self.client.force_login(self.client_user)

        # 1. Crear alerta
        response_create = self.client.post(self.alerts_url, {
            'action': 'create_alert',
            'currency_code': 'USD',
            'condition_type': 'COMPRA',
            'target_rate': '7400.0000',
            'notification_channel': 'PUSH'
        })
        self.assertEqual(response_create.status_code, 200)
        
        alert = CurrencyAlert.objects.filter(user=self.client_user, currency_code='USD').first()
        self.assertIsNotNone(alert)
        self.assertEqual(alert.target_rate, Decimal('7400.0000'))
        self.assertTrue(alert.is_active)

        # 2. Editar alerta
        response_edit = self.client.post(self.alerts_url, {
            'action': 'edit_alert',
            'alert_id': alert.id,
            'target_rate': '7420.0000',
            'notification_channel': 'EMAIL'
        })
        self.assertEqual(response_edit.status_code, 200)
        alert.refresh_from_db()
        self.assertEqual(alert.target_rate, Decimal('7420.0000'))
        self.assertEqual(alert.notification_channel, 'EMAIL')

        # 3. Desactivar (Toggle) alerta
        response_toggle = self.client.post(self.alerts_url, {
            'action': 'toggle_alert',
            'alert_id': alert.id
        })
        self.assertEqual(response_toggle.status_code, 200)
        alert.refresh_from_db()
        self.assertFalse(alert.is_active)

        # 4. Eliminar alerta
        response_delete = self.client.post(self.alerts_url, {
            'action': 'delete_alert',
            'alert_id': alert.id
        })
        self.assertEqual(response_delete.status_code, 200)
        self.assertFalse(CurrencyAlert.objects.filter(id=alert.id).exists())

    def test_automatic_alert_triggering_on_rate_reach(self):
        """
        Valida que cuando la cotización de mercado alcance o supere el valor configurado
        por el cliente, el sistema dispare automáticamente la notificación en tiempo real (Criterio b).
        """
        # Crear alerta activa con objetivo 7350 en compra para USD
        alert = CurrencyAlert.objects.create(
            user=self.client_user,
            currency_code='USD',
            condition_type='COMPRA',
            target_rate=Decimal('7350.0000'),
            notification_channel='PUSH',
            is_active=True
        )

        # Actualizar cotización de mercado a 7360 (supera los 7350 de la alerta)
        self.client.force_login(self.admin_user)
        response = self.client.post(self.manager_url, {
            'action': 'update_live_rate',
            'currency_code': 'USD',
            'buy_rate': '7360.0000',
            'sell_rate': '7500.0000'
        })
        self.assertEqual(response.status_code, 200)

        # Verificar que la alerta se marcó como disparada y se creó el NotificationLog
        alert.refresh_from_db()
        self.assertTrue(alert.triggered)

        notif = NotificationLog.objects.filter(user=self.client_user, notification_type='ALERTA_TASA').first()
        self.assertIsNotNone(notif)
        self.assertIn("alcanzó", notif.message)

    def test_abrupt_variation_broadcast_notification(self):
        """
        Valida que cuando ocurra un cambio porcentual significativo (>= 1.5%) en la tasa
        de cambio en intervalo corto, el sistema notifique a todos los usuarios registrados (Criterio c).
        """
        # Crear historial previo (USD compra = 7300)
        ExchangeRateHistory.objects.create(
            currency_code='USD',
            buy_rate=Decimal('7300.0000'),
            sell_rate=Decimal('7450.0000'),
            timestamp=timezone.now() - timezone.timedelta(hours=1)
        )

        # Actualizar cotización a 7500 (variación de ~2.74%, superando el umbral de 1.5%)
        self.client.force_login(self.admin_user)
        response = self.client.post(self.manager_url, {
            'action': 'update_live_rate',
            'currency_code': 'USD',
            'buy_rate': '7500.0000',
            'sell_rate': '7650.0000'
        })
        self.assertEqual(response.status_code, 200)

        # Verificar que se generó una notificación de variación abrupta para el cliente registrado
        abrupt_notif = NotificationLog.objects.filter(user=self.client_user, notification_type='VARIACION_ABRUPTA').first()
        self.assertIsNotNone(abrupt_notif)
        self.assertIn("Variación abrupta", abrupt_notif.message)

    def test_client_dashboard_rate_alerts_integration(self):
        """
        Valida la integración de las alertas de tasas en el dashboard del cliente,
        verificando que renderice correctamente la vista y contenga enlaces/contadores de alertas.
        """
        self.client.force_login(self.client_user)
        dashboard_url = reverse('dashboard_redirect')
        response = self.client.get(dashboard_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alertas de Tasas")
        self.assertContains(response, reverse('tasas_cambio:currency_alerts'))

    def test_email_sending_on_rate_alert(self):
        """
        Valida que al dispararse una alerta con canal EMAIL o AMBOS,
        el sistema envíe un correo electrónico utilizando Django mail.outbox.
        """
        from django.core import mail
        self.client_user.email = 'client_pse35@test.com'
        self.client_user.save()

        CurrencyAlert.objects.create(
            user=self.client_user,
            currency_code='USD',
            condition_type='COMPRA',
            target_rate=Decimal('7350.0000'),
            notification_channel='EMAIL',
            is_active=True
        )

        self.client.force_login(self.admin_user)
        self.client.post(self.manager_url, {
            'action': 'update_live_rate',
            'currency_code': 'USD',
            'buy_rate': '7360.0000',
            'sell_rate': '7500.0000'
        })

        self.assertGreaterEqual(len(mail.outbox), 1)
        self.assertIn('client_pse35@test.com', mail.outbox[0].to)
