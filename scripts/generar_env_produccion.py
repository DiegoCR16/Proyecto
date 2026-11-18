#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Genera el archivo .env.production del servidor a partir de .env.production.example (PSE-40).

Completa automáticamente:
  - dominios (APP_DOMAIN, AUTH_DOMAIN, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, URLs de Keycloak),
  - SECRET_KEY y DB_PASSWORD aleatorios,
  - KEYCLOAK_ADMIN_PASSWORD elegida por el usuario (archivo) o aleatoria,
  - KEYCLOAK_CLIENT_SECRET leído del realm que importa Keycloak,
  - SEED_TEST_USERS=false con --sin-usuarios-prueba (sin usuarios ni clientes de prueba),
  - correo Gmail (EMAIL_*, DEFAULT_FROM_EMAIL) con --correo-file (cuenta y contraseña de aplicación).

Con --solo-correo se actualiza solo el correo de un .env.production existente (no toca el resto).
Las claves de SIFEN y Stripe quedan vacías para cargarlas a mano en el servidor.
Solo usa la biblioteca estándar: se ejecuta en el servidor sin instalar nada.

Uso:
    python3 scripts/generar_env_produccion.py --dominio globalexchange-g05.duckdns.org [--correo-file archivo]
    python3 scripts/generar_env_produccion.py --solo-correo --correo-file archivo
"""
import argparse
import json
import os
import re
import secrets
import sys

REGEX_DOMINIO = re.compile(r'^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$')
# Sin espacios, comillas, $, #, ni barra invertida: romperían el archivo .env de Docker Compose.
REGEX_PASSWORD = re.compile(r'^[A-Za-z0-9!%&*+,\-./:;<=>?@^_~]{12,128}$')
REGEX_CORREO = re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')
# Contraseña de aplicación de Google: 16 letras (Gmail la muestra en grupos de 4 separados por espacios).
REGEX_PASSWORD_APP = re.compile(r'^[A-Za-z]{16}$')


def validar_dominio(dominio):
    """Devuelve el dominio en minúsculas o lanza ValueError si no es un nombre de host válido."""
    dominio = (dominio or '').strip().lower()
    if not REGEX_DOMINIO.match(dominio):
        raise ValueError(f'Dominio inválido: {dominio!r}')
    return dominio


def validar_password_admin(password):
    """
    Valida la contraseña del administrador de Keycloak elegida por el usuario.

    Debe tener entre 12 y 128 caracteres: letras, números y !%&*+,-./:;<=>?@^_~
    (sin espacios, comillas, $ ni #, que romperían el archivo .env).
    """
    password = (password or '').strip()
    if not REGEX_PASSWORD.match(password):
        raise ValueError(
            'La contraseña de Keycloak debe tener al menos 12 caracteres: letras, números y '
            '!%&*+,-./:;<=>?@^_~ (sin espacios, comillas, $ ni #).'
        )
    return password


def valores_correo(cuenta, password_app):
    """
    Valida la cuenta de Gmail y su contraseña de aplicación y devuelve las variables de correo.

    Args:
        cuenta (str): dirección que envía los correos, ej. globalexchange.g05@gmail.com.
        password_app (str): contraseña de aplicación de Google (16 letras; se ignoran los espacios).

    Returns:
        dict: EMAIL_HOST, EMAIL_PORT, EMAIL_USE_TLS, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD y DEFAULT_FROM_EMAIL.
    """
    cuenta = (cuenta or '').strip()
    password_app = (password_app or '').replace(' ', '').strip()
    if not REGEX_CORREO.match(cuenta):
        raise ValueError(f'Correo inválido: {cuenta!r}')
    if not REGEX_PASSWORD_APP.match(password_app):
        raise ValueError('La contraseña de aplicación de Gmail debe tener 16 letras (los espacios se ignoran).')
    return {
        'EMAIL_HOST': 'smtp.gmail.com',
        'EMAIL_PORT': '587',
        'EMAIL_USE_TLS': 'true',
        'EMAIL_HOST_USER': cuenta,
        'EMAIL_HOST_PASSWORD': password_app,
        'DEFAULT_FROM_EMAIL': f'Global Exchange <{cuenta}>',
    }


def leer_correo(ruta):
    """Lee un archivo con la cuenta de Gmail (1.ª línea) y la contraseña de aplicación (2.ª) y las valida."""
    with open(ruta, encoding='utf-8') as archivo:
        lineas = archivo.read().splitlines() + ['', '']
    return valores_correo(lineas[0], lineas[1])


def leer_secreto_cliente(ruta_realm, client_id):
    """Obtiene el secreto del cliente OIDC desde el JSON del realm de Keycloak."""
    with open(ruta_realm, encoding='utf-8-sig') as archivo:
        realm = json.load(archivo)
    for cliente in realm.get('clients', []):
        if cliente.get('clientId') == client_id and cliente.get('secret'):
            return cliente['secret']
    raise ValueError(f'El realm no tiene secreto para el cliente {client_id!r}')


def valores_produccion(dominio, client_secret, generar_token=secrets.token_urlsafe,
                       keycloak_admin_password=None, sin_usuarios_prueba=False, correo=None):
    """
    Calcula los valores que se escriben en .env.production.

    Args:
        dominio (str): dominio público del sistema (ej. globalexchange-g05.duckdns.org).
        client_secret (str): secreto del cliente OIDC de Keycloak.
        generar_token (callable): generador de secretos (inyectable para pruebas).
        keycloak_admin_password (str | None): contraseña elegida para el admin de Keycloak;
            si es None se genera una aleatoria.
        sin_usuarios_prueba (bool): si es True, SEED_TEST_USERS=false (sin usuarios ni clientes de prueba).
        correo (dict | None): variables de correo de valores_correo(); None las deja vacías.

    Returns:
        dict: variable -> valor.
    """
    dominio = validar_dominio(dominio)
    auth = f'auth.{dominio}'
    if keycloak_admin_password is not None:
        keycloak_admin_password = validar_password_admin(keycloak_admin_password)
    valores = {
        'APP_DOMAIN': dominio,
        'AUTH_DOMAIN': auth,
        'ALLOWED_HOSTS': f'{dominio},localhost,127.0.0.1,web',
        'CSRF_TRUSTED_ORIGINS': f'https://{dominio}',
        'KEYCLOAK_FRONTEND_URL': f'https://{auth}',
        'KEYCLOAK_REDIRECT_URI': f'https://{dominio}/auth/callback/',
        'KEYCLOAK_CLIENT_SECRET': client_secret,
        'SECRET_KEY': generar_token(50),
        'DB_PASSWORD': generar_token(24),
        'KEYCLOAK_ADMIN_PASSWORD': keycloak_admin_password or generar_token(18),
    }
    if sin_usuarios_prueba:
        valores['SEED_TEST_USERS'] = 'false'
    if correo:
        valores.update(correo)
    return valores


def aplicar_valores(plantilla, valores):
    """
    Reemplaza el valor de cada línea VARIABLE=... de la plantilla presente en `valores`.

    Conserva comentarios, orden y variables no mencionadas.
    """
    lineas = []
    for linea in plantilla.splitlines():
        clave = linea.split('=', 1)[0].strip() if '=' in linea and not linea.lstrip().startswith('#') else None
        lineas.append(f'{clave}={valores[clave]}' if clave in valores else linea)
    return '\n'.join(lineas) + '\n'


def main(argv=None):
    """Punto de entrada de línea de comandos."""
    parser = argparse.ArgumentParser(description='Genera .env.production para el servidor.')
    parser.add_argument('--dominio', help='Dominio público, ej. globalexchange-g05.duckdns.org')
    parser.add_argument('--plantilla', default='.env.production.example')
    parser.add_argument('--realm', default='docker/global-exchange-realm-realm.json')
    parser.add_argument('--client-id', default='global-exchange-client')
    parser.add_argument('--salida', default='.env.production')
    parser.add_argument('--sin-usuarios-prueba', action='store_true',
                        help='No cargar usuarios ni clientes de prueba (SEED_TEST_USERS=false).')
    parser.add_argument('--keycloak-admin-password-file',
                        help='Archivo con la contraseña elegida para el admin de Keycloak (se lee y no se modifica).')
    parser.add_argument('--correo-file',
                        help='Archivo con la cuenta de Gmail (1.ª línea) y su contraseña de aplicación (2.ª línea).')
    parser.add_argument('--solo-correo', action='store_true',
                        help='Actualiza solo el correo de un .env.production existente (requiere --correo-file).')
    args = parser.parse_args(argv)

    if args.solo_correo:
        return actualizar_correo(args.salida, args.correo_file)
    if not args.dominio:
        print('ERROR: falta --dominio.', file=sys.stderr)
        return 1
    if os.path.exists(args.salida):
        print(f'{args.salida} ya existe: no se sobrescribe.', file=sys.stderr)
        return 1
    password_admin = None
    if args.keycloak_admin_password_file:
        with open(args.keycloak_admin_password_file, encoding='utf-8') as archivo:
            password_admin = archivo.read().strip()
    try:
        correo = leer_correo(args.correo_file) if args.correo_file else None
        valores = valores_produccion(
            args.dominio, leer_secreto_cliente(args.realm, args.client_id),
            keycloak_admin_password=password_admin, sin_usuarios_prueba=args.sin_usuarios_prueba,
            correo=correo,
        )
    except ValueError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    with open(args.plantilla, encoding='utf-8') as archivo:
        contenido = aplicar_valores(archivo.read(), valores)

    descriptor = os.open(args.salida, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as archivo:
        archivo.write(contenido)
    print(f'{args.salida} generado para {valores["APP_DOMAIN"]}.')
    if correo:
        print(f'Correo configurado: {correo["EMAIL_HOST_USER"]}.')
    return 0


def actualizar_correo(salida, correo_file):
    """
    Escribe las variables de correo en un .env.production existente, conservando el resto del archivo.

    Returns:
        int: 0 si se actualizó, 1 si hubo un error (el archivo no se modifica).
    """
    if not correo_file:
        print('ERROR: --solo-correo requiere --correo-file.', file=sys.stderr)
        return 1
    if not os.path.exists(salida):
        print(f'ERROR: no existe {salida}.', file=sys.stderr)
        return 1
    try:
        correo = leer_correo(correo_file)
    except ValueError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    with open(salida, encoding='utf-8') as archivo:
        contenido = archivo.read()
    faltantes = [clave for clave in correo if not re.search(rf'^{clave}=', contenido, re.M)]
    contenido = aplicar_valores(contenido, correo)
    if faltantes:
        contenido += ''.join(f'{clave}={correo[clave]}\n' for clave in faltantes)
    with open(salida, 'w', encoding='utf-8') as archivo:
        archivo.write(contenido)
    print(f'Correo configurado en {salida}: {correo["EMAIL_HOST_USER"]}.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
