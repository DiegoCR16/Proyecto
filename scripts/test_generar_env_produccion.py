# -*- coding: utf-8 -*-
"""
Pruebas unitarias del generador de .env.production usado por deploy-remoto.sh (PSE-40).
"""
import json
import os
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest import TestCase

from scripts.generar_env_produccion import (
    aplicar_valores, leer_secreto_cliente, main, validar_dominio, valores_produccion,
)

DOMINIO = 'globalexchange-g05.duckdns.org'


def _token_fijo(n):
    """Generador de secretos determinista para las pruebas."""
    return f'tok{n}'


class ValidarDominioTests(TestCase):
    """Pruebas de validar_dominio()."""

    def test_acepta_dominio_duckdns(self):
        """Un subdominio de DuckDNS es válido y se normaliza a minúsculas."""
        self.assertEqual(validar_dominio('  GlobalExchange-G05.DuckDNS.org '), DOMINIO)

    def test_rechaza_valores_invalidos(self):
        """Rechaza vacíos, URLs completas, espacios e IPs sin dominio."""
        for invalido in ('', 'https://x.duckdns.org', 'mi dominio.org', 'localhost', '-x.duckdns.org'):
            with self.subTest(invalido=invalido), self.assertRaises(ValueError):
                validar_dominio(invalido)


class ValoresProduccionTests(TestCase):
    """Pruebas de valores_produccion() y aplicar_valores()."""

    def test_calcula_dominios_y_secretos(self):
        """Arma todas las URLs a partir del dominio y genera secretos."""
        valores = valores_produccion(DOMINIO, 'secreto-cliente', generar_token=_token_fijo)
        self.assertEqual(valores['APP_DOMAIN'], DOMINIO)
        self.assertEqual(valores['AUTH_DOMAIN'], f'auth.{DOMINIO}')
        self.assertEqual(valores['ALLOWED_HOSTS'], f'{DOMINIO},localhost,127.0.0.1,web')
        self.assertEqual(valores['CSRF_TRUSTED_ORIGINS'], f'https://{DOMINIO}')
        self.assertEqual(valores['KEYCLOAK_FRONTEND_URL'], f'https://auth.{DOMINIO}')
        self.assertEqual(valores['KEYCLOAK_REDIRECT_URI'], f'https://{DOMINIO}/auth/callback/')
        self.assertEqual(valores['KEYCLOAK_CLIENT_SECRET'], 'secreto-cliente')
        self.assertEqual(valores['SECRET_KEY'], 'tok50')
        self.assertEqual(valores['DB_PASSWORD'], 'tok24')

    def test_secretos_reales_son_distintos(self):
        """Con el generador real, cada despliegue obtiene secretos distintos y largos."""
        a = valores_produccion(DOMINIO, 's')
        b = valores_produccion(DOMINIO, 's')
        self.assertNotEqual(a['SECRET_KEY'], b['SECRET_KEY'])
        self.assertGreaterEqual(len(a['SECRET_KEY']), 50)

    def test_aplicar_valores_conserva_comentarios_y_otras_variables(self):
        """Solo reemplaza las variables indicadas; comentarios y el resto quedan igual."""
        plantilla = '# comentario\nSECRET_KEY=cambiar\n# SECRET_KEY=no-tocar\nSIFEN_API_EMAIL=\nDEBUG=False\n'
        resultado = aplicar_valores(plantilla, {'SECRET_KEY': 'nuevo'})
        self.assertEqual(resultado, '# comentario\nSECRET_KEY=nuevo\n# SECRET_KEY=no-tocar\nSIFEN_API_EMAIL=\nDEBUG=False\n')


class LeerSecretoClienteTests(TestCase):
    """Pruebas de leer_secreto_cliente()."""

    def test_lee_secreto_del_realm_del_proyecto(self):
        """El realm versionado contiene el secreto del cliente global-exchange-client."""
        secreto = leer_secreto_cliente('docker/global-exchange-realm-realm.json', 'global-exchange-client')
        self.assertTrue(secreto)

    def test_cliente_inexistente(self):
        """Si el cliente no existe lanza ValueError."""
        with self.assertRaises(ValueError):
            leer_secreto_cliente('docker/global-exchange-realm-realm.json', 'no-existe')


class MainTests(TestCase):
    """Pruebas de la línea de comandos del generador."""

    def setUp(self):
        """Crea una carpeta temporal con plantilla y realm de prueba."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plantilla = os.path.join(self.tmp.name, 'plantilla')
        self.realm = os.path.join(self.tmp.name, 'realm.json')
        self.salida = os.path.join(self.tmp.name, '.env.production')
        with open(self.plantilla, 'w', encoding='utf-8') as f:
            f.write('APP_DOMAIN=x\nSECRET_KEY=cambiar\nSIFEN_API_PASSWORD=\n')
        with open(self.realm, 'w', encoding='utf-8') as f:
            json.dump({'clients': [{'clientId': 'global-exchange-client', 'secret': 'abc'}]}, f)

    def _main(self, *extra):
        """Ejecuta main() con las rutas temporales y silencia la salida."""
        args = ['--dominio', DOMINIO, '--plantilla', self.plantilla, '--realm', self.realm, '--salida', self.salida, *extra]
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return main(args)

    def test_genera_archivo_con_permisos_restringidos(self):
        """Genera el archivo con los valores calculados y permisos 600."""
        self.assertEqual(self._main(), 0)
        with open(self.salida, encoding='utf-8') as f:
            contenido = f.read()
        self.assertIn(f'APP_DOMAIN={DOMINIO}\n', contenido)
        self.assertNotIn('SECRET_KEY=cambiar', contenido)
        self.assertIn('SIFEN_API_PASSWORD=\n', contenido)
        if os.name == 'posix':
            self.assertEqual(os.stat(self.salida).st_mode & 0o777, 0o600)

    def test_no_sobrescribe_un_archivo_existente(self):
        """Si .env.production ya existe no lo modifica (conserva las claves cargadas a mano)."""
        with open(self.salida, 'w', encoding='utf-8') as f:
            f.write('ORIGINAL=1\n')
        self.assertEqual(self._main(), 1)
        with open(self.salida, encoding='utf-8') as f:
            self.assertEqual(f.read(), 'ORIGINAL=1\n')

    def test_dominio_invalido_no_genera_archivo(self):
        """Con un dominio inválido termina con error y no crea el archivo."""
        args = ['--dominio', 'no valido', '--plantilla', self.plantilla, '--realm', self.realm, '--salida', self.salida]
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(main(args), 1)
        self.assertFalse(os.path.exists(self.salida))
