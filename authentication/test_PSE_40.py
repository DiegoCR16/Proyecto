# -*- coding: utf-8 -*-
"""
Pruebas unitarias PSE-40: automatización de despliegue y población inicial de datos.

Cubre:
  - Comando `seed_data` (divisas, denominaciones, métodos de pago, comisiones, roles,
    cajas y usuarios de prueba), su idempotencia y la opción --sin-usuarios.
  - Comando `keycloak_bootstrap` (URLs del cliente OIDC y usuarios de prueba) con la
    Admin API de Keycloak simulada.
  - Lectura de configuración desde variables de entorno en settings.
"""
import os
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from authentication.management.commands import keycloak_bootstrap
from authentication.management.commands.keycloak_bootstrap import KeycloakAdmin, smtp_realm_desde_settings
from authentication.management.commands.seed_data import (
    DIVISAS_DEFAULT, PASSWORD_PRUEBA, USUARIOS_PRUEBA, seed_all,
)
from authentication.models import Cliente, Role, UserProfile
from caja.models import Caja, DenominacionDivisa
from globalexchange.settings import env_bool, env_list
from tasas_cambio.models import ClientBenefitRule, ExchangeRate, PaymentMethod


class SeedDataTests(TestCase):
    """Pruebas del comando de población inicial seed_data."""

    def test_carga_todas_las_divisas(self):
        """Debe crear las 5 divisas base con sus tasas y símbolos."""
        seed_all()
        codigos = set(ExchangeRate.objects.values_list('currency_code', flat=True))
        self.assertEqual(codigos, {d[0] for d in DIVISAS_DEFAULT})
        usd = ExchangeRate.objects.get(currency_code='USD')
        self.assertEqual(usd.buy_rate, Decimal('7300.0000'))
        self.assertEqual(usd.sell_rate, Decimal('7450.0000'))

    def test_carga_metodos_de_pago(self):
        """Debe crear los métodos de pago predeterminados (PSE-25)."""
        seed_all()
        codigos = set(PaymentMethod.objects.values_list('code', flat=True))
        self.assertTrue({'TARJETA_CREDITO', 'TARJETA_DEBITO', 'TRANSFERENCIA', 'BILLETERA', 'EFECTIVO'} <= codigos)

    def test_carga_tabla_de_comisiones(self):
        """Debe crear las reglas de comisión/beneficio por categoría (PSE-29)."""
        seed_all()
        reglas = dict(ClientBenefitRule.objects.values_list('category_code', 'benefit_percentage'))
        self.assertEqual(reglas['MINORISTA'], Decimal('0.00'))
        self.assertEqual(reglas['VIP'], Decimal('2.00'))
        self.assertEqual(reglas['CORPORATIVO'], Decimal('4.00'))

    def test_carga_denominaciones_y_cajas(self):
        """Debe crear las denominaciones de billetes y las cajas físicas C01/C02."""
        seed_all()
        self.assertTrue(DenominacionDivisa.objects.filter(divisa='PYG', valor_facial=Decimal('100000')).exists())
        self.assertEqual(set(Caja.objects.values_list('codigo', flat=True)), {'C01', 'C02'})

    def test_crea_usuarios_de_prueba_con_rol(self):
        """Cada usuario de prueba debe existir con contraseña y el rol esperado."""
        seed_all()
        for data in USUARIOS_PRUEBA:
            user = User.objects.get(username=data['username'])
            self.assertTrue(user.check_password(PASSWORD_PRUEBA))
            self.assertEqual(UserProfile.objects.get(user=user).role.name, data['role'])
        self.assertTrue(Caja.objects.get(codigo='C01').cajeros_asignados.filter(username='cajero').exists())
        self.assertTrue(Cliente.objects.filter(documento_identidad='1234567').exists())

    def test_es_idempotente(self):
        """Ejecutar dos veces no debe duplicar registros."""
        primera = seed_all()
        segunda = seed_all()
        self.assertEqual(primera, segunda)

    def test_no_pisa_cambios_del_administrador(self):
        """Si el admin modificó una tasa o una contraseña, el seed no debe sobrescribirlas."""
        seed_all()
        ExchangeRate.objects.filter(currency_code='USD').update(buy_rate=Decimal('7000.0000'))
        user = User.objects.get(username='cajero')
        user.set_password('otra-clave')
        user.save()

        seed_all()

        self.assertEqual(ExchangeRate.objects.get(currency_code='USD').buy_rate, Decimal('7000.0000'))
        self.assertTrue(User.objects.get(username='cajero').check_password('otra-clave'))

    def test_no_reemplaza_rol_existente(self):
        """Un usuario que ya tiene rol asignado conserva su rol."""
        otro_rol = Role.objects.create(name='Supervisor')
        user = User.objects.create_user(username='cajero', password='x')
        UserProfile.objects.create(user=user, role=otro_rol)
        seed_all()
        self.assertEqual(UserProfile.objects.get(user=user).role.name, 'Supervisor')

    def test_sin_usuarios_solo_carga_catalogos(self):
        """Con --sin-usuarios no se crean usuarios ni cliente demo, pero sí los catálogos."""
        out = StringIO()
        call_command('seed_data', '--sin-usuarios', stdout=out)
        self.assertFalse(User.objects.exists())
        self.assertFalse(Cliente.objects.exists())
        self.assertEqual(ExchangeRate.objects.count(), len(DIVISAS_DEFAULT))
        self.assertIn('Población inicial completada', out.getvalue())

    def test_comando_imprime_resumen(self):
        """El comando debe informar los usuarios de prueba disponibles."""
        out = StringIO()
        call_command('seed_data', stdout=out)
        self.assertIn('adminuser', out.getvalue())


def _resp(status=200, payload=None):
    """Construye una respuesta HTTP simulada de requests."""
    response = mock.Mock(status_code=status)
    response.json.return_value = payload if payload is not None else {}
    response.raise_for_status.return_value = None
    return response


class KeycloakAdminTests(SimpleTestCase):
    """Pruebas del cliente de Admin API usado por keycloak_bootstrap."""

    def setUp(self):
        """Crea un cliente KeycloakAdmin ya autenticado."""
        self.kc = KeycloakAdmin('http://keycloak:8080/', 'realm', 'client', 'secret')
        self.kc.headers = {'Authorization': 'Bearer t'}

    @mock.patch.object(keycloak_bootstrap.requests, 'post')
    def test_authenticate_guarda_token(self, post):
        """authenticate() debe usar client_credentials y guardar el bearer token."""
        post.return_value = _resp(payload={'access_token': 'abc'})
        self.kc.authenticate()
        self.assertEqual(self.kc.headers['Authorization'], 'Bearer abc')
        self.assertEqual(post.call_args.kwargs['data']['grant_type'], 'client_credentials')
        self.assertTrue(post.call_args.args[0].startswith('http://keycloak:8080/realms/realm/'))

    @mock.patch.object(keycloak_bootstrap.requests, 'put')
    @mock.patch.object(keycloak_bootstrap.requests, 'get')
    def test_agrega_urls_de_produccion_al_cliente(self, get, put):
        """Debe agregar callback, origen y logout del dominio público si faltan."""
        get.return_value = _resp(payload=[{
            'id': 'uuid-1',
            'redirectUris': ['http://localhost:8000/auth/callback/'],
            'webOrigins': ['http://localhost:8000'],
            'attributes': {'post.logout.redirect.uris': 'http://localhost:8000/auth/login/'},
        }])
        put.return_value = _resp()

        changed = self.kc.ensure_client_urls('https://ge.duckdns.org/auth/callback/')

        self.assertTrue(changed)
        enviado = put.call_args.kwargs['json']
        self.assertIn('https://ge.duckdns.org/auth/callback/', enviado['redirectUris'])
        self.assertIn('http://localhost:8000/auth/callback/', enviado['redirectUris'])
        self.assertIn('https://ge.duckdns.org', enviado['webOrigins'])
        self.assertEqual(
            enviado['attributes']['post.logout.redirect.uris'],
            'http://localhost:8000/auth/login/##https://ge.duckdns.org/auth/login/',
        )

    @mock.patch.object(keycloak_bootstrap.requests, 'put')
    @mock.patch.object(keycloak_bootstrap.requests, 'get')
    def test_no_modifica_cliente_si_ya_esta_configurado(self, get, put):
        """Si las URLs ya están registradas no debe hacer PUT."""
        get.return_value = _resp(payload=[{
            'id': 'uuid-1',
            'redirectUris': ['http://localhost:8000/auth/callback/'],
            'webOrigins': ['http://localhost:8000'],
            'attributes': {'post.logout.redirect.uris': 'http://localhost:8000/auth/login/'},
        }])
        self.assertFalse(self.kc.ensure_client_urls('http://localhost:8000/auth/callback/'))
        put.assert_not_called()

    @mock.patch.object(keycloak_bootstrap.requests, 'get')
    def test_cliente_inexistente_lanza_error(self, get):
        """Si el cliente OIDC no existe en el realm debe lanzar RuntimeError."""
        get.return_value = _resp(payload=[])
        with self.assertRaises(RuntimeError):
            self.kc.ensure_client_urls('https://ge.duckdns.org/auth/callback/')

    @mock.patch.object(keycloak_bootstrap.requests, 'post')
    @mock.patch.object(keycloak_bootstrap.requests, 'get')
    def test_usuario_existente_no_se_recrea(self, get, post):
        """Un usuario que ya existe en Keycloak no se vuelve a crear ni cambia su contraseña."""
        get.return_value = _resp(payload=[{'id': 'u1', 'username': 'cajero'}])
        self.assertFalse(self.kc.ensure_user('cajero', 'c@x.com', 'pw', 'Cajero'))
        post.assert_not_called()

    @mock.patch.object(keycloak_bootstrap.requests, 'post')
    @mock.patch.object(keycloak_bootstrap.requests, 'get')
    def test_crea_usuario_y_asigna_rol(self, get, post):
        """Debe crear el usuario con contraseña no temporal y asignarle el rol de realm."""
        rol = {'id': 'r1', 'name': 'Cajero'}
        get.side_effect = [_resp(payload=[]), _resp(payload=[{'id': 'u1'}]), _resp(payload=rol)]
        post.return_value = _resp(status=201)

        self.assertTrue(self.kc.ensure_user('cajero', 'c@x.com', 'pw', 'Cajero'))

        crear, asignar = post.call_args_list
        credencial = crear.kwargs['json']['credentials'][0]
        self.assertEqual(credencial, {'type': 'password', 'value': 'pw', 'temporary': False})
        self.assertTrue(asignar.args[0].endswith('/users/u1/role-mappings/realm'))
        self.assertEqual(asignar.kwargs['json'], [rol])


@override_settings(
    KEYCLOAK_SERVER_URL='http://keycloak:8080', KEYCLOAK_REALM='realm',
    KEYCLOAK_CLIENT_ID='client', KEYCLOAK_CLIENT_SECRET='secret',
    KEYCLOAK_REDIRECT_URI='https://ge.duckdns.org/auth/callback/',
    # Sin SMTP por defecto: las pruebas no dependen del .env del entorno (Gmail) ni llaman a Keycloak real.
    MAILERS={'default': {'BACKEND': 'django.core.mail.backends.console.EmailBackend'}},
)
class KeycloakBootstrapCommandTests(SimpleTestCase):
    """Pruebas del comando keycloak_bootstrap."""

    @mock.patch.object(keycloak_bootstrap.requests, 'post', side_effect=keycloak_bootstrap.requests.ConnectionError('down'))
    def test_keycloak_caido_no_rompe_el_arranque(self, _post):
        """Si Keycloak no responde, el comando advierte y termina sin excepción."""
        err = StringIO()
        call_command('keycloak_bootstrap', '--timeout', '0', stdout=StringIO(), stderr=err)
        self.assertIn('Keycloak no disponible', err.getvalue())

    @mock.patch.object(KeycloakAdmin, 'ensure_user', return_value=True)
    @mock.patch.object(KeycloakAdmin, 'ensure_client_urls', return_value=True)
    @mock.patch.object(KeycloakAdmin, 'authenticate')
    def test_configura_urls_y_usuarios(self, _auth, urls, user):
        """Debe registrar la URL de callback y crear todos los usuarios de prueba."""
        out = StringIO()
        call_command('keycloak_bootstrap', stdout=out)
        urls.assert_called_once_with('https://ge.duckdns.org/auth/callback/')
        self.assertEqual(user.call_count, len(USUARIOS_PRUEBA))
        self.assertIn('Bootstrap de Keycloak completado', out.getvalue())

    @mock.patch.object(KeycloakAdmin, 'ensure_user')
    @mock.patch.object(KeycloakAdmin, 'ensure_client_urls', return_value=False)
    @mock.patch.object(KeycloakAdmin, 'authenticate')
    def test_sin_usuarios(self, _auth, _urls, user):
        """Con --sin-usuarios no crea usuarios en Keycloak."""
        call_command('keycloak_bootstrap', '--sin-usuarios', stdout=StringIO())
        user.assert_not_called()


class SettingsEnvTests(SimpleTestCase):
    """Pruebas de los helpers que leen la configuración desde variables de entorno."""

    def test_env_bool(self):
        """Interpreta true/1/yes/on como verdadero y usa el default si falta."""
        with mock.patch.dict(os.environ, {'X_FLAG': 'False'}):
            self.assertFalse(env_bool('X_FLAG', default=True))
        with mock.patch.dict(os.environ, {'X_FLAG': ' TRUE '}):
            self.assertTrue(env_bool('X_FLAG'))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(env_bool('X_FLAG', default=True))

    def test_env_list(self):
        """Separa por comas e ignora espacios y elementos vacíos."""
        with mock.patch.dict(os.environ, {'X_LIST': 'a.com, b.com,,'}):
            self.assertEqual(env_list('X_LIST'), ['a.com', 'b.com'])
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(env_list('X_LIST'), [])


SMTP_GMAIL = {
    'default': {
        'BACKEND': 'django.core.mail.backends.smtp.EmailBackend',
        'OPTIONS': {
            'host': 'smtp.gmail.com', 'port': 587, 'username': 'grupo@gmail.com',
            'password': 'clave-app', 'use_tls': True, 'timeout': 10,
        },
    },
}


class SmtpKeycloakTests(SimpleTestCase):
    """Pruebas de la configuración del correo de Keycloak a partir del correo de Django."""

    def test_traduce_smtp_de_django_a_keycloak(self):
        """Los datos de MAILERS y DEFAULT_FROM_EMAIL se pasan al formato smtpServer del realm."""
        smtp = smtp_realm_desde_settings(SMTP_GMAIL, 'Global Exchange <grupo@gmail.com>')
        self.assertEqual(smtp, {
            'host': 'smtp.gmail.com', 'port': '587', 'from': 'grupo@gmail.com',
            'fromDisplayName': 'Global Exchange', 'auth': 'true', 'user': 'grupo@gmail.com',
            'password': 'clave-app', 'starttls': 'true', 'ssl': 'false',
        })

    def test_sin_remitente_usa_la_cuenta_smtp(self):
        """Si DEFAULT_FROM_EMAIL está vacío, el remitente es la cuenta SMTP."""
        smtp = smtp_realm_desde_settings(SMTP_GMAIL, '')
        self.assertEqual(smtp['from'], 'grupo@gmail.com')
        self.assertEqual(smtp['fromDisplayName'], 'Global Exchange')

    def test_correo_por_consola_no_configura_keycloak(self):
        """Con el backend de consola (desarrollo) no se toca el SMTP de Keycloak."""
        consola = {'default': {'BACKEND': 'django.core.mail.backends.console.EmailBackend'}}
        self.assertIsNone(smtp_realm_desde_settings(consola, 'x@x.com'))

    @mock.patch.object(keycloak_bootstrap.requests, 'put')
    def test_ensure_smtp_actualiza_el_realm(self, put):
        """ensure_smtp hace un PUT parcial del realm solo con smtpServer."""
        put.return_value = _resp()
        kc = KeycloakAdmin('http://keycloak:8080', 'realm', 'client', 'secret')
        kc.headers = {'Authorization': 'Bearer t'}
        kc.ensure_smtp({'host': 'smtp.gmail.com'})
        self.assertEqual(put.call_args.args[0], 'http://keycloak:8080/admin/realms/realm')
        self.assertEqual(put.call_args.kwargs['json'], {'smtpServer': {'host': 'smtp.gmail.com'}})

    @override_settings(
        KEYCLOAK_REDIRECT_URI='https://ge.duckdns.org/auth/callback/',
        MAILERS=SMTP_GMAIL, DEFAULT_FROM_EMAIL='Global Exchange <grupo@gmail.com>',
    )
    @mock.patch.object(KeycloakAdmin, 'ensure_smtp')
    @mock.patch.object(KeycloakAdmin, 'ensure_client_urls', return_value=False)
    @mock.patch.object(KeycloakAdmin, 'authenticate')
    def test_comando_configura_smtp_si_hay_correo_real(self, _auth, _urls, smtp):
        """El comando keycloak_bootstrap aplica el SMTP cuando Django usa SMTP."""
        out = StringIO()
        call_command('keycloak_bootstrap', '--sin-usuarios', stdout=out)
        smtp.assert_called_once()
        self.assertEqual(smtp.call_args.args[0]['host'], 'smtp.gmail.com')
        self.assertIn('Correo de Keycloak configurado', out.getvalue())


class ComposeSinMailpitTests(SimpleTestCase):
    """Los archivos de Docker Compose ya no deben incluir Mailpit."""

    def test_compose_no_define_mailpit(self):
        """Ni desarrollo ni producción levantan el servicio mailpit."""
        for archivo in ('docker-compose.yml', 'docker-compose.prod.yml'):
            with open(archivo, encoding='utf-8') as f:
                self.assertNotIn('mailpit', f.read().lower(), archivo)
