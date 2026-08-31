from django.test import TestCase, Client
from django.contrib.auth.models import User
from unittest.mock import patch
from decimal import Decimal
from authentication.models import UserProfile, AuditLog, Role, CorporateGroup, GroupMembership

class AssociationKeycloakPSE7Tests(TestCase):
    """
    Suite de pruebas unitarias independiente y exclusiva para la Historia de Usuario PSE-7:
    Asociación de Cuentas Keycloak a Fichas de Clientes y Grupos Corporativos (Epic: Gestión de Clientes).
    Valida la creación directa de cuentas para personas físicas en el grupo de la empresa con rol (Operador/Analista),
    bloqueo operativo transaccional, y renderizado del módulo administrativo.
    """

    def setUp(self):
        """
        Configuración inicial de usuarios administradores, perfiles de clientes y URLs.
        """
        self.client = Client()
        self.admin_role, _ = Role.objects.get_or_create(name="Admin")
        self.client_role, _ = Role.objects.get_or_create(name="Cliente")
        self.corp_role, _ = Role.objects.get_or_create(name="Corporate")

        # Administrador del Sistema
        self.admin_user = User.objects.create_user(username="admin_pse7", email="admin7@globalexchange.com", password="Password123*")
        self.admin_profile = UserProfile.objects.create(
            user=self.admin_user,
            role=self.admin_role,
            is_corporate=True
        )

        # Persona Jurídica (Empresa)
        self.corp_user = User.objects.create_user(username="empresa_sa", email="empresa@globalexchange.com", password="Password123*")
        self.corp_profile = UserProfile.objects.create(
            user=self.corp_user,
            role=self.corp_role,
            is_corporate=True,
            ci_ruc='80012345-6'
        )

        self.detail_url = f'/auth/admin/clients/{self.corp_user.id}/'

    @patch('gestion_clientes.views.requests.get')
    @patch('gestion_clientes.views.requests.post')
    def test_direct_creation_of_physical_user_for_corporate_group(self, mock_post, mock_get):
        """
        Valida que desde la ficha de una persona jurídica se pueda crear directamente
        una cuenta para una persona física, asignándole un rol dentro de la empresa (Operador o Analista).
        """
        self.client.force_login(self.admin_user)

        # Mock token, post create user, and group link token
        mock_post.side_effect = [
            type('Resp', (object,), {'status_code': 200, 'text': 'ok', 'json': lambda self: {'access_token': 'mock_token'}})(),
            type('Resp', (object,), {'status_code': 201, 'text': 'ok', 'json': lambda self: {}})(),
            type('Resp', (object,), {'status_code': 200, 'text': 'ok', 'json': lambda self: {'access_token': 'mock_token'}})(),
            type('Resp', (object,), {'status_code': 204, 'text': 'ok', 'json': lambda self: {}})(),
        ]

        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = [
            {'id': 'kc-uuid-fisica-123', 'username': 'operador@empresa.com', 'email': 'operador@empresa.com', 'firstName': 'Juan Operador'}
        ]

        response = self.client.post(self.detail_url, {
            'action': 'create_direct',
            'new_username': 'Juan Operador',
            'new_email': 'operador@empresa.com',
            'new_ci_ruc': '1234567',
            'new_password': 'Password123*',
            'role_in_group': 'OPERADOR'
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "asociada exitosamente")

        # Verificar que la persona física fue creada y vinculada al grupo con rol OPERADOR
        fisica_profile = UserProfile.objects.filter(user__email='operador@empresa.com').first()
        self.assertIsNotNone(fisica_profile)
        self.assertFalse(fisica_profile.is_corporate)
        self.assertEqual(fisica_profile.keycloak_id, 'kc-uuid-fisica-123')
        self.assertEqual(fisica_profile.ci_ruc, '1234567')

        corp_group = CorporateGroup.objects.filter(juridica_profile=self.corp_profile).first()
        self.assertIsNotNone(corp_group)

        membership = GroupMembership.objects.filter(corporate_group=corp_group, fisica_profile=fisica_profile).first()
        self.assertIsNotNone(membership)
        self.assertEqual(membership.role_in_group, 'OPERADOR')

        audit = AuditLog.objects.filter(action="CREATE_PHYSICAL_MEMBER_FOR_CORPORATE", user=self.admin_user).first()
        self.assertIsNotNone(audit)
        self.assertIn("operador@empresa.com", audit.details)

    def test_operational_transaction_blocking(self):
        """
        Valida el bloqueo operativo transaccional: el sistema debe impedir cualquier intento
        de transacción si el usuario autenticado no está asociado a al menos un cliente activo (keycloak_id ausente).
        """
        unlinked_user = User.objects.create_user(username="sin_asociar", email="sin@globalexchange.com", password="Password123*")
        unlinked_profile = UserProfile.objects.create(
            user=unlinked_user,
            role=self.client_role,
            keycloak_id=None
        )

        self.assertFalse(unlinked_profile.has_active_client_association())

        with self.assertRaises(PermissionError):
            unlinked_profile.perform_transaction(Decimal('500000.00'))

        unlinked_profile.keycloak_id = 'kc-active-uuid-777'
        unlinked_profile.save()

        self.assertTrue(unlinked_profile.has_active_client_association())
        success = unlinked_profile.perform_transaction(Decimal('500000.00'))
        self.assertTrue(success)
        self.assertEqual(float(unlinked_profile.transaction_volume), 500000.00)

    def test_client_user_mapping_template_and_status_badges(self):
        """
        Valida que la vista de mapeo/ficha de cliente renderice correctamente con la plantilla
        `client_user_mapping.html`, mostrando los badges de estado operativo (vinculado vs no vinculado).
        """
        self.client.force_login(self.admin_user)

        response_corp = self.client.get(self.detail_url)
        self.assertEqual(response_corp.status_code, 200)
        self.assertTemplateUsed(response_corp, 'gestion_clientes/client_user_mapping.html')
        self.assertContains(response_corp, "Creación de Cuenta para Persona Física y Asignación a la Empresa")
