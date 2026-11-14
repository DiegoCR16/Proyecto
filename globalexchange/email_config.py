# -*- coding: utf-8 -*-
"""
Configuración del envío de correos a partir de variables de entorno.

- Con EMAIL_HOST definido (ej. smtp.gmail.com) se usa SMTP real.
- Sin EMAIL_HOST (entorno de desarrollo) los correos se muestran por consola.

Se usa desde settings.py: ``MAILERS = configurar_mailers(os.environ)``.
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
