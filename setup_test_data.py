import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'globalexchange.settings')
django.setup()

from django.contrib.auth.models import User
from authentication.models import Role, UserProfile

def create_sample_data():
    print("Creando roles y usuarios de prueba...")

    admin_role, _ = Role.objects.get_or_create(name="Admin", defaults={'description': "Administrador del Sistema"})
    corporate_role, _ = Role.objects.get_or_create(name="Corporate", defaults={'description': "Cliente Corporativo"})
    individual_role, _ = Role.objects.get_or_create(name="Individual", defaults={'description': "Cliente Individual"})

    # 1. Admin User
    admin_user, created = User.objects.get_or_create(username="adminuser", defaults={'email': 'admin@globalexchange.com'})
    if created or not admin_user.check_password("password123"):
        admin_user.set_password("password123")
        admin_user.save()
    UserProfile.objects.update_or_create(
        user=admin_user,
        defaults={'role': admin_role, 'is_corporate': False, 'mfa_enabled': True}
    )

    # 2. Corporate User
    corp_user, created = User.objects.get_or_create(username="corpuser", defaults={'email': 'corp@globalexchange.com'})
    if created or not corp_user.check_password("password123"):
        corp_user.set_password("password123")
        corp_user.save()
    UserProfile.objects.update_or_create(
        user=corp_user,
        defaults={'role': corporate_role, 'is_corporate': True, 'mfa_enabled': True}
    )

    # 3. Individual User
    ind_user, created = User.objects.get_or_create(username="induser", defaults={'email': 'ind@globalexchange.com'})
    if created or not ind_user.check_password("password123"):
        ind_user.set_password("password123")
        ind_user.save()
    UserProfile.objects.update_or_create(
        user=ind_user,
        defaults={'role': individual_role, 'is_corporate': False, 'mfa_enabled': False}
    )

    print("¡Usuarios de prueba creados exitosamente!")
    print("------------------------------------------")
    print("Credenciales disponibles para prueba:")
    print("  - Admin:       adminuser / password123 (Requiere iToken: 123456)")
    print("  - Corporativo: corpuser / password123  (Requiere iToken: 123456)")
    print("  - Individual:  induser / password123   (Acceso directo sin iToken)")
    print("------------------------------------------")

if __name__ == '__main__':
    create_sample_data()
