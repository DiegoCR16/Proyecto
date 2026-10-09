import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'globalexchange.settings')
django.setup()

from django.contrib.auth.models import User
from authentication.models import Role, UserProfile
from caja.models import Caja

def create_sample_data():
    print("Creando roles, usuarios y cajas de prueba...")

    admin_role, _ = Role.objects.get_or_create(name="Admin", defaults={'description': "Administrador del Sistema"})
    corporate_role, _ = Role.objects.get_or_create(name="Corporate", defaults={'description': "Cliente Corporativo"})
    individual_role, _ = Role.objects.get_or_create(name="Individual", defaults={'description': "Cliente Individual"})
    cajero_role, _ = Role.objects.get_or_create(name="Cajero", defaults={'description': "Cajero de Sucursal"})

    # 1. Admin User
    admin_user, created = User.objects.get_or_create(username="adminuser", defaults={'email': 'admin@globalexchange.com'})
    if created or not admin_user.check_password("password123"):
        admin_user.set_password("password123")
        admin_user.save()
    UserProfile.objects.update_or_create(
        user=admin_user,
        defaults={'role': admin_role, 'is_corporate': False, 'mfa_enabled': True}
    )

    # 2. Cajero User
    cajero_user, created = User.objects.get_or_create(username="cajero", defaults={'email': 'cajero@globalexchange.com'})
    if created or not cajero_user.check_password("password123"):
        cajero_user.set_password("password123")
        cajero_user.save()
    UserProfile.objects.update_or_create(
        user=cajero_user,
        defaults={'role': cajero_role, 'is_corporate': False, 'mfa_enabled': False}
    )

    # 3. Cajas físicas de prueba (PSE-20)
    Caja.objects.get_or_create(codigo="C01", defaults={'nombre': "Caja Principal 01", 'activa': True})
    Caja.objects.get_or_create(codigo="C02", defaults={'nombre': "Caja Secundaria 02", 'activa': True})

    print("¡Datos de prueba creados exitosamente!")
    print("------------------------------------------")
    print("Credenciales disponibles para prueba:")
    print("  - Admin:  adminuser / password123")
    print("  - Cajero: cajero / password123")
    print("------------------------------------------")

if __name__ == '__main__':
    create_sample_data()
