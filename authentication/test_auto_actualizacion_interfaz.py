# -*- coding: utf-8 -*-
"""
Suite independiente de pruebas para la actualización automática de los dashboards
(Administrador, Cliente y Analista) cada 30 segundos.

Valida:
* La API ``estado_interfaz_api``: requiere sesión, su firma es estable si no hay cambios
  y cambia cuando llega una notificación, una solicitud de anulación o se actualiza una cotización.
* Que cada usuario vea solo lo suyo (notificaciones de su cliente; solicitudes solo con permiso).
* Que los dashboards incluyan la firma inicial y el script de polling, y que el del Cajero no.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from authentication.estado_interfaz import INTERVALO_SEGUNDOS
from authentication.models import Cliente, Role, UserProfile, UsuarioClienteRelacion
from procesamiento_operaciones.models import CurrencySaleTransaction, SolicitudAnulacionFactura
from tasas_cambio.models import ExchangeRate, NotificationLog


class AutoActualizacionInterfazTests(TestCase):
    """Pruebas de la API de estado de la interfaz y de su integración en los dashboards."""

    def setUp(self):
        """Crea Administrador, Analista, Cajero y un cliente con su usuario, más una cotización."""
        self.http = Client()
        self.api = reverse('estado_interfaz_api')
        self.dashboard = reverse('dashboard_redirect')

        self.admin = User.objects.create_superuser(username='admin_auto', password='x')
        UserProfile.objects.create(user=self.admin, role=Role.objects.get_or_create(name='Admin')[0])
        self.analista = User.objects.create_user(username='analista_auto', password='x')
        UserProfile.objects.create(user=self.analista, role=Role.objects.get_or_create(name='Analista')[0])
        self.cajero = User.objects.create_user(username='cajero_auto', password='x')
        UserProfile.objects.create(user=self.cajero, role=Role.objects.get_or_create(name='Cajero')[0])

        self.usuario_cliente = User.objects.create_user(username='cliente_auto', password='x', email='c@x.com')
        UserProfile.objects.create(user=self.usuario_cliente, role=Role.objects.get_or_create(name='Cliente')[0])
        self.cliente = Cliente.objects.create(nombre_o_razon_social='Cliente Auto', documento_identidad='3030001', email='c@x.com')
        self.otro_cliente = Cliente.objects.create(nombre_o_razon_social='Otro Cliente', documento_identidad='3030002', email='o@x.com')
        UsuarioClienteRelacion.objects.create(keycloak_user_id=str(self.usuario_cliente.id), cliente=self.cliente)

        self.usd = ExchangeRate.objects.create(currency_code='USD', currency_name='Dólar', buy_rate=Decimal('7300'), sell_rate=Decimal('7400'))

    def _estado(self, usuario):
        """Inicia sesión con el usuario y devuelve la respuesta JSON de la API."""
        self.http.force_login(usuario)
        return self.http.get(self.api).json()

    def _solicitud_pendiente(self):
        """Crea una solicitud de anulación pendiente sobre una venta."""
        venta = CurrencySaleTransaction.objects.create(
            user=self.usuario_cliente, cliente=self.cliente, from_currency='USD', to_currency='PYG',
            amount=Decimal('10'), converted_amount=Decimal('74000'), applied_rate=Decimal('7400'),
            standard_rate=Decimal('7400'), total_pyg=Decimal('74000'),
        )
        return SolicitudAnulacionFactura.objects.create(
            venta=venta, cdc='0' * 44, solicitante=self.usuario_cliente, cliente=self.cliente, motivo='Prueba',
        )

    # --- API -------------------------------------------------------------------------------

    def test_api_requiere_sesion(self):
        """Sin sesión, la API redirige al login."""
        self.assertEqual(self.http.get(self.api).status_code, 302)

    def test_firma_estable_sin_cambios(self):
        """Si no cambia nada, dos consultas seguidas devuelven la misma firma (no hay recargas en bucle)."""
        self.assertEqual(self._estado(self.usuario_cliente)['firma'], self._estado(self.usuario_cliente)['firma'])

    def test_notificacion_del_cliente_cambia_la_firma(self):
        """Una notificación nueva para el cliente activo cambia la firma y el contador de no leídas."""
        antes = self._estado(self.usuario_cliente)
        NotificationLog.objects.create(cliente=self.cliente, title='Aviso', message='Mensaje manual', channel='PUSH')
        despues = self._estado(self.usuario_cliente)
        self.assertNotEqual(antes['firma'], despues['firma'])
        self.assertEqual(despues['notificaciones_no_leidas'], antes['notificaciones_no_leidas'] + 1)

    def test_notificacion_personal_y_general_cambian_la_firma(self):
        """Las notificaciones al usuario y las generales (sin destinatario) también se detectan."""
        antes = self._estado(self.admin)['firma']
        NotificationLog.objects.create(user=self.admin, title='Personal', message='Para el admin')
        intermedia = self._estado(self.admin)['firma']
        NotificationLog.objects.create(title='General', message='Para todos')
        self.assertNotEqual(antes, intermedia)
        self.assertNotEqual(intermedia, self._estado(self.admin)['firma'])

    def test_notificacion_de_otro_cliente_no_afecta(self):
        """Una notificación de otro cliente no cambia la firma de este usuario."""
        antes = self._estado(self.usuario_cliente)['firma']
        NotificationLog.objects.create(cliente=self.otro_cliente, title='Ajena', message='No es suya', channel='PUSH')
        self.assertEqual(antes, self._estado(self.usuario_cliente)['firma'])

    def test_marcar_leida_cambia_la_firma(self):
        """Al marcar una notificación como leída (desde otra pestaña) la firma se actualiza."""
        notificacion = NotificationLog.objects.create(cliente=self.cliente, title='Aviso', message='x', channel='PUSH')
        antes = self._estado(self.usuario_cliente)
        notificacion.is_read = True
        notificacion.save()
        despues = self._estado(self.usuario_cliente)
        self.assertEqual(despues['notificaciones_no_leidas'], antes['notificaciones_no_leidas'] - 1)
        self.assertNotEqual(antes['firma'], despues['firma'])

    def test_solicitud_de_anulacion_cambia_la_firma_del_admin(self):
        """El Administrador detecta una solicitud de anulación nueva."""
        antes = self._estado(self.admin)
        self.assertEqual(antes['solicitudes_anulacion_pendientes'], 0)
        self._solicitud_pendiente()
        despues = self._estado(self.admin)
        self.assertEqual(despues['solicitudes_anulacion_pendientes'], 1)
        self.assertNotEqual(antes['firma'], despues['firma'])

    def test_cliente_no_ve_solicitudes_de_anulacion(self):
        """Sin el permiso de facturación, las solicitudes no forman parte del estado."""
        self._solicitud_pendiente()
        estado = self._estado(self.usuario_cliente)
        self.assertIsNone(estado['solicitudes_anulacion_pendientes'])
        self.assertIsNone(estado['ultima_solicitud_anulacion_id'])

    def test_cotizacion_actualizada_cambia_la_firma(self):
        """Una actualización de cotización (que el cliente ve en su dashboard) cambia la firma."""
        antes = self._estado(self.usuario_cliente)['firma']
        self.usd.buy_rate = Decimal('7350')
        self.usd.save()
        self.assertNotEqual(antes, self._estado(self.usuario_cliente)['firma'])

    # --- Dashboards ------------------------------------------------------------------------

    def _dashboard(self, usuario, modo_usuario=False):
        """Abre el dashboard del usuario (opcionalmente en modo usuario) y devuelve la respuesta."""
        self.http.force_login(usuario)
        if modo_usuario:
            sesion = self.http.session
            sesion['user_mode'] = True
            sesion.save()
        return self.http.get(self.dashboard)

    def _assert_con_auto_actualizacion(self, respuesta):
        """Verifica que la página trae la firma inicial y el script de polling."""
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'id="gx-estado-interfaz"')
        self.assertContains(respuesta, self.api)
        self.assertContains(respuesta, 'window.location.reload()')
        self.assertEqual(respuesta.context['intervalo_auto_actualizacion'], INTERVALO_SEGUNDOS)

    def test_dashboard_admin_se_actualiza(self):
        """El dashboard del Administrador incluye la actualización automática."""
        respuesta = self._dashboard(self.admin)
        self.assertTemplateUsed(respuesta, 'authentication/admin_dashboard.html')
        self._assert_con_auto_actualizacion(respuesta)

    def test_dashboard_cliente_se_actualiza(self):
        """El dashboard del Cliente incluye la actualización automática."""
        respuesta = self._dashboard(self.usuario_cliente)
        self.assertTemplateUsed(respuesta, 'authentication/client_dashboard.html')
        self._assert_con_auto_actualizacion(respuesta)

    def test_dashboard_modo_usuario_se_actualiza(self):
        """El dashboard del cliente en modo usuario también se actualiza."""
        respuesta = self._dashboard(self.usuario_cliente, modo_usuario=True)
        self.assertTemplateUsed(respuesta, 'authentication/user_dashboard.html')
        self._assert_con_auto_actualizacion(respuesta)

    def test_dashboard_analista_se_actualiza(self):
        """El dashboard del Analista incluye la actualización automática."""
        respuesta = self._dashboard(self.analista)
        self.assertTemplateUsed(respuesta, 'authentication/analista_dashboard.html')
        self._assert_con_auto_actualizacion(respuesta)

    def test_dashboard_cajero_sin_actualizacion(self):
        """El dashboard del Cajero no se actualiza solo (no se pidió)."""
        respuesta = self._dashboard(self.cajero)
        self.assertTemplateUsed(respuesta, 'authentication/cajero_dashboard.html')
        self.assertNotContains(respuesta, 'id="gx-estado-interfaz"')

    def test_firma_inicial_coincide_con_la_api(self):
        """La firma con la que nace el dashboard es la misma que devuelve la API sin cambios."""
        respuesta = self._dashboard(self.usuario_cliente)
        self.assertEqual(respuesta.context['estado_interfaz']['firma'], self.http.get(self.api).json()['firma'])
