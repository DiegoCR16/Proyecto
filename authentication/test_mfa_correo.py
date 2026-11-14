# -*- coding: utf-8 -*-
"""
Pruebas unitarias del iToken por correo (verificación en dos pasos con código aleatorio).

Cubre la generación y envío del código en cada inicio de sesión SSO, vencimiento,
uso único, bloqueo por intentos, reenvío limitado y el middleware que impide saltear la verificación.
"""
import base64
import json
import re
import time
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, TestCase

from authentication import mfa
from authentication.models import AuditLog, Role, UserProfile


def _jwt(payload):
    """Construye un JWT de prueba (sin firma válida) con el payload indicado."""
    cabecera = base64.urlsafe_b64encode(b'{"alg":"HS256"}').decode().rstrip('=')
    cuerpo = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
    return f'{cabecera}.{cuerpo}.firma'


class ItokenPorCorreoTests(TestCase):
    """Flujo completo del iToken enviado por correo."""

    def setUp(self):
        """Crea un administrador (requiere MFA) con correo y un cliente de pruebas."""
        self.client = Client()
        self.admin_role = Role.objects.create(name='Admin')
        self.admin = User.objects.create_user('admin.mfa', email='admin.mfa@gmail.com', password='x')
        self.profile = UserProfile.objects.create(user=self.admin, role=self.admin_role, itoken_verified=True)

    def _login_sso(self):
        """Simula el callback de Keycloak para el administrador y devuelve la respuesta."""
        token = _jwt({
            'preferred_username': 'admin.mfa', 'email': 'admin.mfa@gmail.com',
            'sub': 'kc-admin-mfa', 'realm_access': {'roles': ['Admin']},
        })
        with patch('authentication.views.requests.post') as post, patch('authentication.views.requests.get') as get:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {'access_token': token}
            get.return_value.status_code = 404
            return self.client.get('/auth/callback/?code=abc')

    def _codigo_del_correo(self, indice=-1):
        """Extrae el código de 6 dígitos del correo enviado."""
        return re.search(r'\b(\d{6})\b', mail.outbox[indice].body).group(1)

    def test_login_envia_codigo_por_correo(self):
        """Al iniciar sesión se envía un correo con un código de 6 dígitos y se pide verificarlo."""
        response = self._login_sso()
        self.assertRedirects(response, '/auth/mfa/', fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['admin.mfa@gmail.com'])
        self.assertIn('Código de verificación', mail.outbox[0].subject)
        self.assertRegex(self._codigo_del_correo(), r'^\d{6}$')

    def test_cada_login_exige_verificar_de_nuevo(self):
        """Aunque el perfil estuviera verificado antes, cada login vuelve a pedir el iToken."""
        self._login_sso()
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.itoken_verified)

    def test_codigo_correcto_permite_ingresar(self):
        """El código recibido por correo completa la verificación."""
        self._login_sso()
        response = self.client.post('/auth/mfa/', {'itoken_code': self._codigo_del_correo()})
        self.assertRedirects(response, '/auth/dashboard/', fetch_redirect_response=False)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.itoken_verified)
        self.assertTrue(AuditLog.objects.filter(user=self.admin, action='MFA_SUCCESS').exists())

    def test_codigo_es_de_un_solo_uso(self):
        """Tras usar el código, la pantalla de verificación ya no está pendiente."""
        self._login_sso()
        self.client.post('/auth/mfa/', {'itoken_code': self._codigo_del_correo()})
        self.assertNotIn(mfa.SESION_HASH, self.client.session)
        response = self.client.get('/auth/mfa/')
        self.assertRedirects(response, '/auth/dashboard/', fetch_redirect_response=False)

    def test_codigo_no_se_guarda_en_claro(self):
        """La sesión guarda solo el hash del código, nunca el código."""
        self._login_sso()
        codigo = self._codigo_del_correo()
        self.assertNotIn(codigo, json.dumps(dict(self.client.session.items())))

    def test_codigo_incorrecto_muestra_intentos_restantes(self):
        """Un código erróneo informa cuántos intentos quedan y queda auditado."""
        self._login_sso()
        codigo = self._codigo_del_correo()
        erroneo = '000000' if codigo != '000000' else '111111'
        response = self.client.post('/auth/mfa/', {'itoken_code': erroneo})
        self.assertContains(response, 'Le quedan 4 intentos')
        self.assertTrue(AuditLog.objects.filter(user=self.admin, action='MFA_FAILED').exists())

    def test_bloqueo_tras_cinco_intentos(self):
        """Tras 5 intentos fallidos el código se invalida aunque luego se ingrese el correcto."""
        self._login_sso()
        codigo = self._codigo_del_correo()
        erroneo = '000000' if codigo != '000000' else '111111'
        for _ in range(mfa.MAX_INTENTOS):
            response = self.client.post('/auth/mfa/', {'itoken_code': erroneo})
        self.assertContains(response, 'Demasiados intentos')
        response = self.client.post('/auth/mfa/', {'itoken_code': codigo})
        self.assertContains(response, 'Solicite uno nuevo')

    def test_codigo_vencido(self):
        """Pasados 5 minutos el código ya no sirve."""
        self._login_sso()
        session = self.client.session
        session[mfa.SESION_EXPIRA] = time.time() - 1
        session.save()
        response = self.client.post('/auth/mfa/', {'itoken_code': self._codigo_del_correo()})
        self.assertContains(response, 'El código venció')

    def test_reenvio_genera_un_codigo_nuevo(self):
        """Reenviar (pasado el minuto de espera) envía otro código e invalida el anterior."""
        self._login_sso()
        primero = self._codigo_del_correo()
        session = self.client.session
        session[mfa.SESION_ENVIADO] = time.time() - mfa.REENVIO_ESPERA_SEGUNDOS - 1
        session.save()
        with patch('authentication.mfa.generar_codigo', return_value='000000' if primero != '000000' else '111111'):
            response = self.client.post('/auth/mfa/', {'action': 'reenviar'})
        self.assertContains(response, 'Enviamos un nuevo código')
        self.assertEqual(len(mail.outbox), 2)
        segundo = self._codigo_del_correo()
        self.assertNotEqual(primero, segundo)
        response = self.client.post('/auth/mfa/', {'itoken_code': primero})
        self.assertEqual(response.status_code, 200)
        response = self.client.post('/auth/mfa/', {'itoken_code': segundo})
        self.assertRedirects(response, '/auth/dashboard/', fetch_redirect_response=False)

    def test_reenvio_limitado_a_uno_por_minuto(self):
        """No se puede pedir otro código antes de que pase un minuto."""
        self._login_sso()
        response = self.client.post('/auth/mfa/', {'action': 'reenviar'})
        self.assertContains(response, 'Espere')
        self.assertEqual(len(mail.outbox), 1)

    def test_no_se_puede_saltear_la_verificacion(self):
        """Con el iToken pendiente, cualquier otra página redirige a la verificación."""
        self._login_sso()
        for url in ('/auth/dashboard/', '/auth/roles/', '/auth/audit/'):
            response = self.client.get(url)
            self.assertRedirects(response, '/auth/mfa/', fetch_redirect_response=False, msg_prefix=url)

    def test_logout_permitido_con_verificacion_pendiente(self):
        """Se puede cancelar y cerrar sesión sin verificar."""
        self._login_sso()
        response = self.client.get('/auth/logout/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('openid-connect/logout', response.url)

    def test_usuario_sin_correo(self):
        """Si el usuario no tiene correo no se envía nada y se avisa en pantalla."""
        self.admin.email = ''
        self.admin.save()
        token = _jwt({'preferred_username': 'admin.mfa', 'sub': 'kc-admin-mfa', 'realm_access': {'roles': ['Admin']}})
        with patch('authentication.views.requests.post') as post, patch('authentication.views.requests.get') as get:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {'access_token': token}
            get.return_value.status_code = 404
            self.client.get('/auth/callback/?code=abc')
        self.assertEqual(len(mail.outbox), 0)
        response = self.client.get('/auth/mfa/')
        self.assertContains(response, 'no tiene correo')

    def test_usuario_sin_mfa_entra_directo(self):
        """Un usuario que no requiere MFA no recibe código y va directo a su panel."""
        self.profile.role = Role.objects.create(name='Cliente')
        self.profile.save()
        token = _jwt({'preferred_username': 'admin.mfa', 'email': 'admin.mfa@gmail.com',
                      'sub': 'kc-admin-mfa', 'realm_access': {'roles': ['Cliente']}})
        with patch('authentication.views.requests.post') as post, patch('authentication.views.requests.get') as get:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {'access_token': token}
            get.return_value.status_code = 404
            response = self.client.get('/auth/callback/?code=abc')
        self.assertRedirects(response, '/auth/dashboard/', fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 0)


class FuncionesMfaTests(TestCase):
    """Pruebas de las funciones auxiliares de authentication.mfa."""

    def test_generar_codigo_formato_y_aleatoriedad(self):
        """Siempre 6 dígitos (con ceros a la izquierda) y no se repite el mismo valor."""
        codigos = {mfa.generar_codigo() for _ in range(50)}
        self.assertTrue(all(re.fullmatch(r'\d{6}', c) for c in codigos))
        self.assertGreater(len(codigos), 40)
        with patch('authentication.mfa.secrets.randbelow', return_value=42):
            self.assertEqual(mfa.generar_codigo(), '000042')

    def test_enmascarar_email(self):
        """Muestra solo la primera y última letra del usuario del correo."""
        self.assertEqual(mfa.enmascarar_email('diego@gmail.com'), 'd***o@gmail.com')
        self.assertEqual(mfa.enmascarar_email('ab@x.com'), 'a***@x.com')
        self.assertEqual(mfa.enmascarar_email(''), '')

    def test_fallo_smtp_no_rompe_el_login(self):
        """Si el envío falla, enviar_codigo devuelve False en lugar de lanzar la excepción."""
        user = User.objects.create_user('u1', email='u1@x.com')

        class SesionFalsa:
            session = {}

        with patch('authentication.mfa.send_mail', side_effect=OSError('SMTP caído')):
            self.assertFalse(mfa.enviar_codigo(SesionFalsa(), user))
