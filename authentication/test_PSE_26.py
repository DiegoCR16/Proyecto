"""
Suite de Pruebas Unitarias Independiente para PSE-26: Gestión de Permisos de Roles Fijos.
Valida la gestión de roles fijos (Admin, Analista, Cliente, Cajero), asignación granular de permisos,
reglas de validación cruzada (Cliente vs Admin/Analista), control de acceso RBAC y auditoría.
"""

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from authentication.models import Role, Permission, UserProfile, AuditLog

class FixedRolesPermissionsPSE26TestCase(TestCase):
    """
    Casos de prueba unitarios para validar la historia de usuario PSE-26 con roles fijos y reglas de negocio.
    """

    def setUp(self):
        """
        Configuración inicial de roles fijos (Admin, Analista, Cliente, Cajero) y usuarios para las pruebas.
        """
        self.client = Client()
        
        # Crear permisos granulares
        self.perm_clients = Permission.objects.create(name="Gestionar Clientes", codename="can_manage_clients", description="Permiso de clientes")
        self.perm_exchange = Permission.objects.create(name="Realizar Transacciones", codename="can_perform_exchange", description="Permiso transaccional")

        # Roles fijos
        self.admin_role = Role.objects.create(name="Admin", description="Administrador General")
        self.analista_role = Role.objects.create(name="Analista", description="Analista de Operaciones")
        self.cliente_role = Role.objects.create(name="Cliente", description="Cliente Final")
        self.cajero_role = Role.objects.create(name="Cajero", description="Cajero de Sucursal")
        self.cliente_role.permissions.add(self.perm_exchange)

        # Usuarios
        self.admin_user = User.objects.create_user(username="admin_test", password="Password123*")
        self.admin_profile = UserProfile.objects.create(user=self.admin_user, role=self.admin_role)

        self.analista_user = User.objects.create_user(username="analista_test", password="Password123*")
        self.analista_profile = UserProfile.objects.create(user=self.analista_user, role=self.analista_role)

        self.client_user = User.objects.create_user(username="client_test", password="Password123*")
        self.client_profile = UserProfile.objects.create(user=self.client_user, role=self.cliente_role)

        self.roles_url = reverse('admin_roles')

    def test_analista_role_permission_update(self):
        """
        Valida que un Administrador pueda modificar la descripción y los permisos granulares
        del rol fijo Analista para delegar tareas (excluyendo transacciones de cliente).
        """
        self.client.login(username="admin_test", password="Password123*")
        
        perm_rate = Permission.objects.create(name="Gestionar Tasas", codename="can_manage_rates", description="Tasas")
        
        response = self.client.post(self.roles_url, {
            'action': 'edit',
            'role_id': str(self.analista_role.id),
            'description': 'Analista con permisos avanzados delegados',
            'permissions': [str(self.perm_clients.id), str(perm_rate.id)]
        })
        
        self.assertEqual(response.status_code, 302)
        
        self.analista_role.refresh_from_db()
        self.assertEqual(self.analista_role.description, 'Analista con permisos avanzados delegados')
        self.assertEqual(self.analista_role.permissions.count(), 2)

        audit = AuditLog.objects.filter(action="ROLE_UPDATE", user=self.admin_user).first()
        self.assertIsNotNone(audit)
        self.assertIn("Analista", audit.details)

    def test_analista_cannot_have_exchange_permission(self):
        """
        Valida que el rol Analista rechace la asignación del permiso de realizar transacciones.
        """
        self.client.login(username="admin_test", password="Password123*")
        
        response = self.client.post(self.roles_url, {
            'action': 'edit',
            'role_id': str(self.analista_role.id),
            'description': 'Analista test',
            'permissions': [str(self.perm_exchange.id)]
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Error de validación")

    def test_cliente_cannot_have_admin_permissions(self):
        """
        Valida que el rol Cliente rechace la asignación de permisos administrativos.
        """
        self.client.login(username="admin_test", password="Password123*")
        
        response = self.client.post(self.roles_url, {
            'action': 'edit',
            'role_id': str(self.cliente_role.id),
            'description': 'Cliente test',
            'permissions': [str(self.perm_clients.id)]
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Error de validación")

    def test_admin_role_protection(self):
        """
        Valida que el rol Admin esté protegido y no se puedan alterar sus permisos ni descripción.
        """
        self.client.login(username="admin_test", password="Password123*")

        response = self.client.post(self.roles_url, {
            'action': 'edit',
            'role_id': str(self.admin_role.id),
            'description': 'Intento de modificar admin',
            'permissions': []
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "El rol Administrador está protegido")

    def test_rbac_access_control_denial_for_client(self):
        """
        Valida que los usuarios con rol Cliente tengan denegado el acceso al módulo de gestión de roles.
        """
        self.client.login(username="client_test", password="Password123*")
        
        response = self.client.get(self.roles_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Acceso denegado")

        audit = AuditLog.objects.filter(action="RBAC_ACCESS_DENIED", user=self.client_user).first()
        self.assertIsNotNone(audit)
