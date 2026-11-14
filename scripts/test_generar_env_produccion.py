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
    aplicar_valores, leer_secreto_cliente, main, validar_dominio, validar_password_admin, valores_produccion,
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


class OpcionesPrimerDespliegueTests(TestCase):
    """Contraseña elegida para Keycloak y opción sin usuarios de prueba (C0)."""

    def test_usa_la_password_elegida(self):
        """Si se indica contraseña de Keycloak, se usa en lugar de una aleatoria."""
        valores = valores_produccion(DOMINIO, 's', generar_token=_token_fijo, keycloak_admin_password='GlobalExch2026!')
        self.assertEqual(valores['KEYCLOAK_ADMIN_PASSWORD'], 'GlobalExch2026!')

    def test_sin_password_genera_una_aleatoria(self):
        """Sin contraseña elegida se genera una aleatoria."""
        self.assertEqual(valores_produccion(DOMINIO, 's', generar_token=_token_fijo)['KEYCLOAK_ADMIN_PASSWORD'], 'tok18')

    def test_password_invalida(self):
        """Rechaza contraseñas cortas o con caracteres que rompen el archivo .env."""
        for invalida in ('corta1!', 'con espacio 1234', 'tiene$dolar1234', 'tiene#numeral12', 'comilla"12345678'):
            with self.subTest(invalida=invalida), self.assertRaises(ValueError):
                validar_password_admin(invalida)
        self.assertEqual(validar_password_admin('  Valida-con.puntos_9 '), 'Valida-con.puntos_9')

    def test_sin_usuarios_prueba(self):
        """--sin-usuarios-prueba deja SEED_TEST_USERS=false; por defecto no se modifica."""
        self.assertEqual(valores_produccion(DOMINIO, 's', sin_usuarios_prueba=True)['SEED_TEST_USERS'], 'false')
        self.assertNotIn('SEED_TEST_USERS', valores_produccion(DOMINIO, 's'))

    def test_plantilla_real_con_opciones(self):
        """Aplicado a .env.production.example, SEED_TEST_USERS queda en false."""
        with open('.env.production.example', encoding='utf-8') as f:
            contenido = aplicar_valores(f.read(), valores_produccion(DOMINIO, 's', sin_usuarios_prueba=True))
        self.assertIn('\nSEED_TEST_USERS=false\n', contenido)
        self.assertIn('\nSEED_DATA=true\n', contenido)


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

    def test_password_desde_archivo_y_sin_usuarios(self):
        """Lee la contraseña de Keycloak desde archivo y aplica --sin-usuarios-prueba."""
        with open(self.plantilla, 'a', encoding='utf-8') as f:
            f.write('KEYCLOAK_ADMIN_PASSWORD=x\nSEED_TEST_USERS=true\n')
        archivo_password = os.path.join(self.tmp.name, 'pw')
        with open(archivo_password, 'w', encoding='utf-8') as f:
            f.write('GlobalExch2026!\n')
        self.assertEqual(self._main('--keycloak-admin-password-file', archivo_password, '--sin-usuarios-prueba'), 0)
        with open(self.salida, encoding='utf-8') as f:
            contenido = f.read()
        self.assertIn('KEYCLOAK_ADMIN_PASSWORD=GlobalExch2026!\n', contenido)
        self.assertIn('SEED_TEST_USERS=false\n', contenido)

    def test_password_invalida_no_genera_archivo(self):
        """Una contraseña inválida en el archivo termina con error sin crear .env.production."""
        archivo_password = os.path.join(self.tmp.name, 'pw')
        with open(archivo_password, 'w', encoding='utf-8') as f:
            f.write('corta')
        self.assertEqual(self._main('--keycloak-admin-password-file', archivo_password), 1)
        self.assertFalse(os.path.exists(self.salida))

    def test_dominio_invalido_no_genera_archivo(self):
        """Con un dominio inválido termina con error y no crea el archivo."""
        args = ['--dominio', 'no valido', '--plantilla', self.plantilla, '--realm', self.realm, '--salida', self.salida]
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(main(args), 1)
        self.assertFalse(os.path.exists(self.salida))
