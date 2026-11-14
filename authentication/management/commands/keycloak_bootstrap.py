# -*- coding: utf-8 -*-
"""
Bootstrap idempotente de Keycloak (PSE-40).

Usa la cuenta de servicio del cliente OIDC (rol realm-admin) para:
  1. Registrar en el cliente la URL de callback y el origen de KEYCLOAK_REDIRECT_URI
     (necesario cuando se despliega en un dominio distinto de localhost).
  2. Crear en Keycloak los usuarios de prueba de seed_data con su rol de realm,
     ya que el inicio de sesión del sistema se hace exclusivamente vía Keycloak.
  3. Configurar el servidor SMTP del realm (correos de verificación y recuperación de
     contraseña) con el mismo correo que usa Django (EMAIL_HOST, Gmail), si está definido.

Uso:
    python manage.py keycloak_bootstrap                 # URLs + usuarios de prueba
    python manage.py keycloak_bootstrap --sin-usuarios  # solo URLs del cliente
    python manage.py keycloak_bootstrap --timeout 180   # espera hasta 180 s a Keycloak

Nunca resetea contraseñas de usuarios existentes. Si Keycloak no responde dentro del
timeout, termina con una advertencia sin interrumpir el arranque del contenedor.
"""
import time
from email.utils import parseaddr
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from authentication.management.commands.seed_data import PASSWORD_PRUEBA, USUARIOS_PRUEBA

REQUEST_TIMEOUT = 5


def smtp_realm_desde_settings(mailers, default_from_email):
    """
    Traduce la configuración SMTP de Django (MAILERS) al formato smtpServer de Keycloak.

    Args:
        mailers (dict): setting MAILERS de Django.
        default_from_email (str): remitente, ej. 'Global Exchange <grupo@gmail.com>'.

    Returns:
        dict | None: representación smtpServer, o None si Django no usa SMTP (consola).
    """
    default = mailers.get('default', {})
    if not default.get('BACKEND', '').endswith('smtp.EmailBackend'):
        return None
    opciones = default.get('OPTIONS', {})
    nombre, direccion = parseaddr(default_from_email or '')
    direccion = direccion or opciones.get('username', '')
    return {
        'host': opciones['host'],
        'port': str(opciones.get('port', 587)),
        'from': direccion,
        'fromDisplayName': nombre or 'Global Exchange',
        'auth': 'true' if opciones.get('username') else 'false',
        'user': opciones.get('username', ''),
        'password': opciones.get('password', ''),
        'starttls': 'true' if opciones.get('use_tls') else 'false',
        'ssl': 'false',
    }


class KeycloakAdmin:
    """Cliente mínimo de la Admin REST API de Keycloak autenticado con client_credentials."""

    def __init__(self, server_url, realm, client_id, client_secret):
        """Guarda los datos de conexión; el token se obtiene con authenticate()."""
        self.server_url = server_url.rstrip('/')
        self.realm = realm
        self.client_id = client_id
        self.client_secret = client_secret
        self.headers = {}

    @property
    def admin_url(self):
        """URL base de la Admin API para el realm configurado."""
        return f'{self.server_url}/admin/realms/{self.realm}'

    def authenticate(self):
        """Obtiene un access token de la cuenta de servicio. Lanza requests.HTTPError si falla."""
        resp = requests.post(
            f'{self.server_url}/realms/{self.realm}/protocol/openid-connect/token',
            data={
                'grant_type': 'client_credentials',
                'client_id': self.client_id,
                'client_secret': self.client_secret,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        self.headers = {'Authorization': f"Bearer {resp.json()['access_token']}"}

    def _get(self, path, **params):
        """GET autenticado contra la Admin API; devuelve el JSON."""
        resp = requests.get(f'{self.admin_url}{path}', headers=self.headers, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def ensure_client_urls(self, redirect_uri):
        """
        Agrega redirect_uri, su origen y la URL de logout al cliente OIDC si faltan.

        Returns:
            bool: True si el cliente fue modificado.
        """
        parts = urlsplit(redirect_uri)
        origin = f'{parts.scheme}://{parts.netloc}'
        logout_uri = f'{origin}/auth/login/'

        clients = self._get('/clients', clientId=self.client_id)
        if not clients:
            raise RuntimeError(f'No existe el cliente {self.client_id} en el realm {self.realm}')
        client = clients[0]

        changed = False
        redirect_uris = client.get('redirectUris') or []
        if redirect_uri not in redirect_uris:
            client['redirectUris'] = redirect_uris + [redirect_uri]
            changed = True
        web_origins = client.get('webOrigins') or []
        if origin not in web_origins:
            client['webOrigins'] = web_origins + [origin]
            changed = True
        attributes = client.setdefault('attributes', {})
        logout_uris = [u for u in attributes.get('post.logout.redirect.uris', '').split('##') if u]
        if logout_uri not in logout_uris:
            attributes['post.logout.redirect.uris'] = '##'.join(logout_uris + [logout_uri])
            changed = True

        if changed:
            resp = requests.put(
                f"{self.admin_url}/clients/{client['id']}",
                headers=self.headers, json=client, timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
        return changed

    def ensure_user(self, username, email, password, role_name):
        """
        Crea el usuario con contraseña y rol de realm si no existe.

        Returns:
            bool: True si el usuario fue creado.
        """
        if self._get('/users', username=username, exact='true'):
            return False

        resp = requests.post(
            f'{self.admin_url}/users',
            headers=self.headers,
            json={
                'username': username,
                'email': email,
                'firstName': username.capitalize(),
                'lastName': 'Prueba',
                'enabled': True,
                'emailVerified': True,
                'credentials': [{'type': 'password', 'value': password, 'temporary': False}],
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()

        user_id = self._get('/users', username=username, exact='true')[0]['id']
        role = self._get(f'/roles/{role_name}')
        resp = requests.post(
            f'{self.admin_url}/users/{user_id}/role-mappings/realm',
            headers=self.headers, json=[role], timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return True


    def ensure_smtp(self, smtp_server):
        """
        Aplica la configuración SMTP al realm (PUT parcial de la representación del realm).

        Se aplica siempre que haya configuración, porque Keycloak oculta la contraseña
        guardada y no se puede comparar para saber si cambió.
        """
        resp = requests.put(
            self.admin_url, headers=self.headers,
            json={'smtpServer': smtp_server}, timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()


class Command(BaseCommand):
    """Comando `manage.py keycloak_bootstrap`: configura URLs del cliente y usuarios de prueba en Keycloak."""

    help = 'Configura en Keycloak las URLs del cliente OIDC y los usuarios de prueba (PSE-40).'

    def add_arguments(self, parser):
        """Registra las opciones --sin-usuarios y --timeout."""
        parser.add_argument('--sin-usuarios', action='store_true', help='No crear usuarios de prueba en Keycloak.')
        parser.add_argument('--timeout', type=int, default=120, help='Segundos máximos de espera a que Keycloak responda.')

    def wait_for_token(self, kc, timeout):
        """Reintenta la autenticación cada 5 s hasta que Keycloak responda o venza el timeout."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                kc.authenticate()
                return True
            except (requests.RequestException, KeyError, ValueError) as exc:
                if time.monotonic() >= deadline:
                    self.stderr.write(self.style.WARNING(f'Keycloak no disponible tras {timeout}s: {exc}'))
                    return False
                time.sleep(5)

    def handle(self, *args, **options):
        """Ejecuta el bootstrap y reporta los cambios aplicados."""
        kc = KeycloakAdmin(
            settings.KEYCLOAK_SERVER_URL, settings.KEYCLOAK_REALM,
            settings.KEYCLOAK_CLIENT_ID, settings.KEYCLOAK_CLIENT_SECRET,
        )
        if not self.wait_for_token(kc, options['timeout']):
            return

        try:
            if kc.ensure_client_urls(settings.KEYCLOAK_REDIRECT_URI):
                self.stdout.write(f'Cliente OIDC actualizado con {settings.KEYCLOAK_REDIRECT_URI}')
            smtp_server = smtp_realm_desde_settings(settings.MAILERS, settings.DEFAULT_FROM_EMAIL)
            if smtp_server:
                kc.ensure_smtp(smtp_server)
                self.stdout.write(f"Correo de Keycloak configurado: {smtp_server['host']} ({smtp_server['from']})")
            if not options['sin_usuarios']:
                for data in USUARIOS_PRUEBA:
                    if kc.ensure_user(data['username'], data['email'], PASSWORD_PRUEBA, data['role']):
                        self.stdout.write(f"Usuario Keycloak creado: {data['username']} ({data['role']})")
        except (requests.RequestException, RuntimeError) as exc:
            self.stderr.write(self.style.WARNING(f'Bootstrap de Keycloak incompleto: {exc}'))
            return
        self.stdout.write(self.style.SUCCESS('Bootstrap de Keycloak completado.'))
