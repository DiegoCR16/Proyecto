# -*- coding: utf-8 -*-
"""
Pruebas unitarias de la configuración de correo por variables de entorno
(envío real por SMTP / Gmail y reemplazo de Mailpit).
"""
from django.core import mail
from django.test import SimpleTestCase

from globalexchange.email_config import (
    CONSOLE_BACKEND, SMTP_BACKEND, SMTP_TIMEOUT_SEGUNDOS, configurar_mailers,
)


class ConfigurarMailersTests(SimpleTestCase):
    """Pruebas de configurar_mailers()."""

    def test_sin_email_host_usa_consola(self):
        """Sin EMAIL_HOST (desarrollo) los correos deben ir a la consola."""
        self.assertEqual(configurar_mailers({}), {'default': {'BACKEND': CONSOLE_BACKEND}})

    def test_email_host_vacio_usa_consola(self):
        """EMAIL_HOST vacío o con espacios equivale a no configurado."""
        self.assertEqual(configurar_mailers({'EMAIL_HOST': '  '})['default']['BACKEND'], CONSOLE_BACKEND)

    def test_con_email_host_usa_smtp(self):
        """Con EMAIL_HOST definido debe usar SMTP con las credenciales del entorno."""
        config = configurar_mailers({
            'EMAIL_HOST': 'smtp.gmail.com',
            'EMAIL_PORT': '587',
            'EMAIL_HOST_USER': 'grupo@gmail.com',
            'EMAIL_HOST_PASSWORD': 'clave-app',
            'EMAIL_USE_TLS': 'true',
        })['default']
        self.assertEqual(config['BACKEND'], SMTP_BACKEND)
        self.assertEqual(config['OPTIONS'], {
            'host': 'smtp.gmail.com',
            'port': 587,
            'username': 'grupo@gmail.com',
            'password': 'clave-app',
            'use_tls': True,
            'timeout': SMTP_TIMEOUT_SEGUNDOS,
        })

    def test_valores_por_defecto(self):
        """Sin EMAIL_PORT ni EMAIL_USE_TLS usa el puerto 587 con TLS activado."""
        opciones = configurar_mailers({'EMAIL_HOST': 'smtp.gmail.com'})['default']['OPTIONS']
        self.assertEqual(opciones['port'], 587)
        self.assertTrue(opciones['use_tls'])
        self.assertEqual(opciones['username'], '')

    def test_tls_desactivado(self):
        """EMAIL_USE_TLS=false desactiva TLS."""
        opciones = configurar_mailers({'EMAIL_HOST': 'mail.local', 'EMAIL_USE_TLS': 'False'})['default']['OPTIONS']
        self.assertFalse(opciones['use_tls'])

    def test_puerto_personalizado(self):
        """EMAIL_PORT se convierte a entero."""
        opciones = configurar_mailers({'EMAIL_HOST': 'smtp.x.com', 'EMAIL_PORT': '2525'})['default']['OPTIONS']
        self.assertEqual(opciones['port'], 2525)


class EnvioCorreoEnPruebasTests(SimpleTestCase):
    """Verifica que durante las pruebas no se envían correos reales."""

    def test_correo_queda_en_bandeja_de_pruebas(self):
        """El test runner de Django captura los correos en mail.outbox."""
        mail.send_mail('Asunto', 'Cuerpo', 'origen@x.com', ['destino@x.com'])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, 'Asunto')
