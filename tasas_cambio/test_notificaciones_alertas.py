# -*- coding: utf-8 -*-
"""
Pruebas unitarias de las reglas de visibilidad y envío de notificaciones de tasas (PSE-35):

* Alerta por objetivo: pertenece al cliente; en pantalla solo para ese cliente (canal PUSH/AMBOS)
  y por correo al cliente y a todos sus usuarios asociados (canal EMAIL/AMBOS). Se dispara una sola vez.
* Variación abrupta: llega a todos los usuarios y clientes.
"""
from decimal import Decimal
from django.core import mail
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.models import User
from authentication.models import UserProfile, Cliente, UsuarioClienteRelacion
from tasas_cambio.models import ExchangeRate, ExchangeRateHistory, CurrencyAlert, NotificationLog
from tasas_cambio.views import check_and_trigger_rate_alerts


class NotificacionesAlertasTests(TestCase):
    """Valida que las notificaciones lleguen solo a quien corresponde."""

    def setUp(self):
        self.http = Client()
        self.alerts_url = reverse('tasas_cambio:currency_alerts')

        # Usuario con dos clientes (A y B) y un usuario asociado solo a A
        self.user = User.objects.create_user(username='multi', email='multi@test.com', password='Password123*')
        UserProfile.objects.create(user=self.user, keycloak_id='kc-multi')
        self.socio = User.objects.create_user(username='socio', email='socio@test.com', password='Password123*')
        UserProfile.objects.create(user=self.socio)
        self.otro = User.objects.create_user(username='otro', email='otro@test.com', password='Password123*')
        UserProfile.objects.create(user=self.otro)

        self.cliente_a = Cliente.objects.create(
            nombre_o_razon_social='Cliente A', documento_identidad='111', email='a@cliente.com', tipo_cliente='FISICA'
        )
        self.cliente_b = Cliente.objects.create(
            nombre_o_razon_social='Cliente B', documento_identidad='222', email='b@cliente.com', tipo_cliente='FISICA'
        )
        UsuarioClienteRelacion.objects.create(keycloak_user_id='kc-multi', cliente=self.cliente_a, rol_en_cliente='ADMIN')
        UsuarioClienteRelacion.objects.create(keycloak_user_id='kc-multi', cliente=self.cliente_b, rol_en_cliente='ADMIN')
        # Relación guardada con el ID numérico del usuario (usuario sin keycloak_id)
        UsuarioClienteRelacion.objects.create(keycloak_user_id=str(self.socio.id), cliente=self.cliente_a, rol_en_cliente='OPERADOR')

        ExchangeRate.objects.update_or_create(
            currency_code='USD',
            defaults={'currency_name': 'Dólar', 'symbol': '$', 'buy_rate': Decimal('7300'), 'sell_rate': Decimal('7450')}
        )

    def _alerta(self, cliente, canal, objetivo='7350'):
        return CurrencyAlert.objects.create(
            cliente=cliente, user=self.user, currency_code='USD', condition_type='COMPRA',
            target_rate=Decimal(objetivo), notification_channel=canal, is_active=True
        )

    def _ver_alertas_como(self, user, cliente=None, user_mode=False):
        self.http.force_login(user)
        session = self.http.session
        if cliente:
            session['active_client_id'] = str(cliente.id)
        session['user_mode'] = user_mode
        session.save()
        return self.http.get(self.alerts_url)

    def test_alerta_objetivo_solo_en_pantalla_de_su_cliente(self):
        """La alerta del cliente A no aparece cuando el mismo usuario opera con el cliente B."""
        self._alerta(self.cliente_a, 'PUSH')
        check_and_trigger_rate_alerts('USD', Decimal('7360'), Decimal('7500'))

        resp_a = self._ver_alertas_como(self.user, self.cliente_a)
        self.assertEqual(len(resp_a.context['notifications']), 1)

        resp_b = self._ver_alertas_como(self.user, self.cliente_b)
        self.assertEqual(len(resp_b.context['notifications']), 0)

        resp_user = self._ver_alertas_como(self.user, user_mode=True)
        self.assertEqual(len(resp_user.context['notifications']), 0)

    def test_alerta_objetivo_canal_email_no_aparece_en_pantalla_y_llega_a_todos_los_correos(self):
        """Canal EMAIL: correo al cliente y a todos sus usuarios asociados, pero no en pantalla."""
        self._alerta(self.cliente_a, 'EMAIL')
        check_and_trigger_rate_alerts('USD', Decimal('7360'), Decimal('7500'))

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(set(mail.outbox[0].to), {'a@cliente.com', 'multi@test.com', 'socio@test.com'})

        resp = self._ver_alertas_como(self.user, self.cliente_a)
        self.assertEqual(len(resp.context['notifications']), 0)

    def test_alerta_objetivo_canal_push_no_envia_correo(self):
        """Canal PUSH: solo pantalla, sin correo."""
        self._alerta(self.cliente_a, 'PUSH')
        check_and_trigger_rate_alerts('USD', Decimal('7360'), Decimal('7500'))
        self.assertEqual(len(mail.outbox), 0)

    def test_alerta_objetivo_se_dispara_una_sola_vez_y_se_rearma_al_editar(self):
        """Actualizaciones sucesivas por encima del objetivo no repiten la notificación; editarla la rearma."""
        alerta = self._alerta(self.cliente_a, 'AMBOS')
        check_and_trigger_rate_alerts('USD', Decimal('7360'), Decimal('7500'))
        check_and_trigger_rate_alerts('USD', Decimal('7370'), Decimal('7500'))
        self.assertEqual(NotificationLog.objects.filter(notification_type='ALERTA_TASA').count(), 1)
        self.assertEqual(len(mail.outbox), 1)

        self._ver_alertas_como(self.user, self.cliente_a)
        self.http.post(self.alerts_url, {'action': 'edit_alert', 'alert_id': alerta.id, 'target_rate': '7380'})
        alerta.refresh_from_db()
        self.assertFalse(alerta.triggered)

        check_and_trigger_rate_alerts('USD', Decimal('7390'), Decimal('7500'))
        self.assertEqual(NotificationLog.objects.filter(notification_type='ALERTA_TASA').count(), 2)

    def test_variacion_abrupta_llega_a_todos_y_no_se_filtra_entre_usuarios(self):
        """La variación abrupta llega a todos los usuarios y clientes, y cada usuario ve solo la suya."""
        ExchangeRateHistory.objects.create(
            currency_code='USD', buy_rate=Decimal('7300'), sell_rate=Decimal('7450'),
            timestamp=timezone.now() - timezone.timedelta(hours=1)
        )
        ExchangeRateHistory.objects.create(
            currency_code='USD', buy_rate=Decimal('7500'), sell_rate=Decimal('7650'), timestamp=timezone.now()
        )
        check_and_trigger_rate_alerts('USD', Decimal('7500'), Decimal('7650'))

        destinatarios = set(mail.outbox[0].to)
        self.assertTrue({'multi@test.com', 'socio@test.com', 'otro@test.com', 'a@cliente.com', 'b@cliente.com'} <= destinatarios)

        # Visible en cualquier cliente del usuario y en modo usuario, pero solo su propia copia
        for kwargs in ({'cliente': self.cliente_a}, {'cliente': self.cliente_b}, {'user_mode': True}):
            resp = self._ver_alertas_como(self.user, **kwargs)
            notifs = list(resp.context['notifications'])
            self.assertEqual(len(notifs), 1)
            self.assertEqual(notifs[0].user, self.user)

    def test_modo_usuario_no_crea_cliente_ni_permite_alertas(self):
        """Sin cliente activo no se crea un cliente ficticio ni se permite crear alertas."""
        total_clientes = Cliente.objects.count()
        self._ver_alertas_como(self.otro, user_mode=True)
        resp = self.http.post(self.alerts_url, {
            'action': 'create_alert', 'currency_code': 'USD', 'condition_type': 'COMPRA',
            'target_rate': '7350', 'notification_channel': 'PUSH'
        })
        self.assertEqual(Cliente.objects.count(), total_clientes)
        self.assertEqual(CurrencyAlert.objects.count(), 0)
        self.assertIn('Debe operar como cliente', resp.context['error_message'])
