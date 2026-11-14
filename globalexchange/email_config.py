# -*- coding: utf-8 -*-
"""
Configuración del envío de correos a partir de variables de entorno.

- Con EMAIL_HOST definido (ej. smtp.gmail.com) se usa SMTP real.
- Sin EMAIL_HOST (entorno de desarrollo) los correos se muestran por consola.

Se usa desde settings.py: ``MAILERS = configurar_mailers(os.environ)``.

Django 6.1 lee MAILERS; las versiones anteriores (5.x, la que se instala en la imagen
Docker con Python 3.11) leen EMAIL_BACKEND, EMAIL_HOST, etc. Por eso settings.py también
aplica ``settings_email_clasicos(MAILERS)`` cuando Django es anterior a 6.1.
"""

SMTP_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
CONSOLE_BACKEND = 'django.core.mail.backends.console.EmailBackend'
SMTP_TIMEOUT_SEGUNDOS = 10


def _es_verdadero(valor):
    """Interpreta 'true', '1', 'yes', 'on' (sin importar mayúsculas) como True."""
    return str(valor).strip().lower() in ('true', '1', 'yes', 'on')


def configurar_mailers(env):
    """
    Construye el setting MAILERS de Django 6.1 a partir de un diccionario de entorno.

    Variables leídas: EMAIL_HOST, EMAIL_PORT (default 587), EMAIL_HOST_USER,
    EMAIL_HOST_PASSWORD y EMAIL_USE_TLS (default true).

    Args:
        env (Mapping): normalmente ``os.environ``.

    Returns:
        dict: configuración con el mailer 'default' (SMTP o consola).
    """
    host = (env.get('EMAIL_HOST') or '').strip()
    if not host:
        return {'default': {'BACKEND': CONSOLE_BACKEND}}

    return {
        'default': {
            'BACKEND': SMTP_BACKEND,
            'OPTIONS': {
                'host': host,
                'port': int(env.get('EMAIL_PORT') or 587),
                'username': env.get('EMAIL_HOST_USER', ''),
                'password': env.get('EMAIL_HOST_PASSWORD', ''),
                'use_tls': _es_verdadero(env.get('EMAIL_USE_TLS', 'true')),
                'timeout': SMTP_TIMEOUT_SEGUNDOS,
            },
        },
    }


def settings_email_clasicos(mailers):
    """
    Traduce MAILERS a los settings de correo de Django anteriores a 6.1.

    Django 5.x ignora MAILERS y, sin estos valores, intenta enviar a localhost:25.

    Args:
        mailers (dict): resultado de ``configurar_mailers``.

    Returns:
        dict: EMAIL_BACKEND y, para SMTP, EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER,
        EMAIL_HOST_PASSWORD, EMAIL_USE_TLS y EMAIL_TIMEOUT.
    """
    default = mailers['default']
    valores = {'EMAIL_BACKEND': default['BACKEND']}
    if default['BACKEND'] == SMTP_BACKEND:
        opciones = default['OPTIONS']
        valores.update({
            'EMAIL_HOST': opciones['host'],
            'EMAIL_PORT': opciones['port'],
            'EMAIL_HOST_USER': opciones['username'],
            'EMAIL_HOST_PASSWORD': opciones['password'],
            'EMAIL_USE_TLS': opciones['use_tls'],
            'EMAIL_TIMEOUT': opciones['timeout'],
        })
    return valores
