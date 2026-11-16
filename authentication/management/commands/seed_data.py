# -*- coding: utf-8 -*-
"""
Comando de población inicial de la base de datos (PSE-40).

Uso:
    python manage.py seed_data                # carga catálogos + usuarios de prueba
    python manage.py seed_data --sin-usuarios # solo catálogos (divisas, pagos, comisiones, cajas)

Es idempotente: puede ejecutarse en cada arranque del contenedor sin duplicar
registros ni pisar los valores que el administrador ya modificó desde la interfaz
(solo crea lo que falta).
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from authentication.models import Cliente, Role, UserProfile
from caja.models import Caja, DenominacionDivisa, inicializar_denominaciones_default
from tasas_cambio.models import ClientBenefitRule, ExchangeRate, PaymentMethod
from tasas_cambio.views import ensure_default_benefit_rules, ensure_default_payment_methods

# Divisas operadas por la casa de cambios (código, nombre, símbolo, compra, venta en Gs).
DIVISAS_DEFAULT = [
    ('USD', 'Dólar Estadounidense', '$', Decimal('7300.0000'), Decimal('7450.0000')),
    ('EUR', 'Euro', '€', Decimal('7900.0000'), Decimal('8150.0000')),
    ('BRL', 'Real Brasileño', 'R$', Decimal('1350.0000'), Decimal('1450.0000')),
    ('ARS', 'Peso Argentino', '$', Decimal('7.5000'), Decimal('9.0000')),
    ('PYG', 'Guaraní Paraguayo', '₲', Decimal('1.0000'), Decimal('1.0000')),
]

# Roles fijos del sistema (mismos nombres que usa authentication.views y Keycloak).
ROLES_DEFAULT = [
    ('Admin', 'Administrador General (Acceso Total - Protegido)'),
    ('Analista', 'Analista de Operaciones (Permisos Delegados)'),
    ('Cliente', 'Cliente de la Plataforma (Física y Jurídica General)'),
    ('Cajero', 'Cajero de Sucursal (Operaciones de Caja)'),
]

# Usuarios de prueba. Deben coincidir con docker/global-exchange-realm-users-0.json
# (el login real se hace contra Keycloak; aquí se pre-crea el perfil local).
USUARIOS_PRUEBA = [
    {'username': 'adminuser', 'email': 'admin@globalexchange.com', 'role': 'Admin', 'mfa': True},
    {'username': 'cajero', 'email': 'cajero@globalexchange.com', 'role': 'Cajero', 'mfa': False},
    {'username': 'analista', 'email': 'analista@globalexchange.com', 'role': 'Analista', 'mfa': False},
    {'username': 'cliente', 'email': 'cliente@globalexchange.com', 'role': 'Cliente', 'mfa': False},
]
PASSWORD_PRUEBA = 'password123'

CAJAS_DEFAULT = [
    ('C01', 'Caja Principal 01'),
    ('C02', 'Caja Secundaria 02'),
]

CLIENTE_DEMO = {
    'documento_identidad': '1234567',
    'nombre_o_razon_social': 'Cliente Demo',
    'tipo_cliente': 'FISICA',
    'email': 'cliente@globalexchange.com',
    'categoria': 'MINORISTA',
}


def seed_divisas():
    """Crea las divisas base con sus tasas iniciales si no existen. Devuelve cuántas se crearon."""
    creadas = 0
    for code, name, symbol, buy, sell in DIVISAS_DEFAULT:
        _, created = ExchangeRate.objects.get_or_create(
            currency_code=code,
            defaults={'currency_name': name, 'symbol': symbol, 'buy_rate': buy, 'sell_rate': sell},
        )
        creadas += int(created)
    return creadas


def seed_roles():
    """Crea los roles fijos del sistema si no existen. Devuelve un dict nombre -> Role."""
    roles = {}
    for name, description in ROLES_DEFAULT:
        roles[name], _ = Role.objects.get_or_create(name=name, defaults={'description': description})
    return roles


def seed_usuarios_prueba(roles):
    """
    Crea los usuarios de prueba con su perfil y rol.

    Solo asigna la contraseña al crear el usuario, para no pisar cambios hechos luego.
    Devuelve la cantidad de usuarios creados.
    """
    creados = 0
    for data in USUARIOS_PRUEBA:
        user, created = User.objects.get_or_create(username=data['username'], defaults={'email': data['email']})
        if created:
            user.set_password(PASSWORD_PRUEBA)
            user.save()
            creados += 1
        profile, _ = UserProfile.objects.get_or_create(user=user)
        if profile.role is None:
            profile.role = roles[data['role']]
            profile.mfa_enabled = data['mfa']
            profile.save()
    return creados


def seed_cajas(asignar_cajero=True):
    """Crea las cajas físicas de prueba y, si existe, asigna el usuario 'cajero' a la caja C01."""
    for codigo, nombre in CAJAS_DEFAULT:
        Caja.objects.get_or_create(codigo=codigo, defaults={'nombre': nombre, 'activa': True})
    if asignar_cajero:
        cajero = User.objects.filter(username='cajero').first()
        if cajero:
            Caja.objects.get(codigo='C01').cajeros_asignados.add(cajero)


def seed_cliente_demo():
    """Crea un cliente de demostración (persona física minorista) si no existe."""
    Cliente.objects.get_or_create(
        documento_identidad=CLIENTE_DEMO['documento_identidad'],
        defaults={k: v for k, v in CLIENTE_DEMO.items() if k != 'documento_identidad'},
    )


@transaction.atomic
def seed_all(con_usuarios=True):
    """
    Ejecuta toda la población inicial en una única transacción.

    Args:
        con_usuarios (bool): si es False no crea usuarios ni el cliente de prueba.

    Returns:
        dict: totales por catálogo luego de la carga.
    """
    seed_divisas()
    inicializar_denominaciones_default()
    ensure_default_payment_methods()
    ensure_default_benefit_rules()
    roles = seed_roles()
    if con_usuarios:
        seed_usuarios_prueba(roles)
        seed_cliente_demo()
    seed_cajas(asignar_cajero=con_usuarios)
    return {
        'divisas': ExchangeRate.objects.count(),
        'denominaciones': DenominacionDivisa.objects.count(),
        'metodos_pago': PaymentMethod.objects.count(),
        'reglas_comision': ClientBenefitRule.objects.count(),
        'roles': Role.objects.count(),
        'usuarios': User.objects.count(),
        'cajas': Caja.objects.count(),
    }


class Command(BaseCommand):
    """Comando `manage.py seed_data`: carga divisas, métodos de pago, comisiones, roles, cajas y usuarios de prueba."""

    help = 'Población inicial idempotente: divisas, métodos de pago, comisiones, roles, cajas y usuarios de prueba (PSE-40).'

    def add_arguments(self, parser):
        """Registra la opción --sin-usuarios."""
        parser.add_argument(
            '--sin-usuarios',
            action='store_true',
            help='No crear usuarios ni cliente de prueba (solo catálogos).',
        )

    def handle(self, *args, **options):
        """Ejecuta la carga e imprime un resumen."""
        con_usuarios = not options['sin_usuarios']
        totales = seed_all(con_usuarios=con_usuarios)
        self.stdout.write(self.style.SUCCESS('Población inicial completada:'))
        for nombre, total in totales.items():
            self.stdout.write(f'  - {nombre}: {total}')
        if con_usuarios:
            self.stdout.write(
                'Usuarios de prueba (login vía Keycloak): '
                + ', '.join(u['username'] for u in USUARIOS_PRUEBA)
                + f' / {PASSWORD_PRUEBA}'
            )
