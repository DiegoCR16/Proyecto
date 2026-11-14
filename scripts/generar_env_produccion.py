#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Genera el archivo .env.production del servidor a partir de .env.production.example (PSE-40).

Completa automáticamente:
  - dominios (APP_DOMAIN, AUTH_DOMAIN, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, URLs de Keycloak),
  - SECRET_KEY, DB_PASSWORD y KEYCLOAK_ADMIN_PASSWORD aleatorios,
  - KEYCLOAK_CLIENT_SECRET leído del realm que importa Keycloak.

Las claves de SIFEN, Stripe y Gmail quedan vacías para cargarlas a mano en el servidor.
Solo usa la biblioteca estándar: se ejecuta en el servidor sin instalar nada.

Uso:
    python3 scripts/generar_env_produccion.py --dominio globalexchange-g05.duckdns.org
"""
import argparse
import json
import os
import re
import secrets
import sys

REGEX_DOMINIO = re.compile(r'^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$')


def validar_dominio(dominio):
    """Devuelve el dominio en minúsculas o lanza ValueError si no es un nombre de host válido."""
    dominio = (dominio or '').strip().lower()
    if not REGEX_DOMINIO.match(dominio):
        raise ValueError(f'Dominio inválido: {dominio!r}')
    return dominio


def leer_secreto_cliente(ruta_realm, client_id):
    """Obtiene el secreto del cliente OIDC desde el JSON del realm de Keycloak."""
    with open(ruta_realm, encoding='utf-8-sig') as archivo:
        realm = json.load(archivo)
    for cliente in realm.get('clients', []):
        if cliente.get('clientId') == client_id and cliente.get('secret'):
            return cliente['secret']
    raise ValueError(f'El realm no tiene secreto para el cliente {client_id!r}')


def valores_produccion(dominio, client_secret, generar_token=secrets.token_urlsafe):
    """
    Calcula los valores que se escriben en .env.production.

    Args:
        dominio (str): dominio público del sistema (ej. globalexchange-g05.duckdns.org).
        client_secret (str): secreto del cliente OIDC de Keycloak.
        generar_token (callable): generador de secretos (inyectable para pruebas).

    Returns:
        dict: variable -> valor.
    """
    dominio = validar_dominio(dominio)
    auth = f'auth.{dominio}'
    return {
        'APP_DOMAIN': dominio,
        'AUTH_DOMAIN': auth,
        'ALLOWED_HOSTS': f'{dominio},localhost,127.0.0.1,web',
        'CSRF_TRUSTED_ORIGINS': f'https://{dominio}',
        'KEYCLOAK_FRONTEND_URL': f'https://{auth}',
        'KEYCLOAK_REDIRECT_URI': f'https://{dominio}/auth/callback/',
        'KEYCLOAK_CLIENT_SECRET': client_secret,
        'SECRET_KEY': generar_token(50),
        'DB_PASSWORD': generar_token(24),
        'KEYCLOAK_ADMIN_PASSWORD': generar_token(18),
    }


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
    parser.add_argument('--dominio', required=True, help='Dominio público, ej. globalexchange-g05.duckdns.org')
    parser.add_argument('--plantilla', default='.env.production.example')
    parser.add_argument('--realm', default='docker/global-exchange-realm-realm.json')
    parser.add_argument('--client-id', default='global-exchange-client')
    parser.add_argument('--salida', default='.env.production')
    args = parser.parse_args(argv)

    if os.path.exists(args.salida):
        print(f'{args.salida} ya existe: no se sobrescribe.', file=sys.stderr)
        return 1
    try:
        valores = valores_produccion(args.dominio, leer_secreto_cliente(args.realm, args.client_id))
    except ValueError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    with open(args.plantilla, encoding='utf-8') as archivo:
        contenido = aplicar_valores(archivo.read(), valores)

    descriptor = os.open(args.salida, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as archivo:
        archivo.write(contenido)
    print(f'{args.salida} generado para {valores["APP_DOMAIN"]}.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
