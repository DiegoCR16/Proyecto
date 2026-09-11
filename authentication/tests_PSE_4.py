from django.test import TestCase, Client
from django.contrib.auth.models import User
from authentication.models import Role, UserProfile, AuditLog
from unittest.mock import patch

class AuthenticationPSE4Tests(TestCase):
    """
    Suite de pruebas unitarias para la Historia de Usuario PSE-4:
    Autenticación SSO Keycloak, MFA obligatorio, redirección por roles y auditoría.
    """

    def setUp(self):
        self.client = Client()
        self.admin_role = Role.objects.create(name="Admin", description="Rol de Administrador")
        self.corporate_role = Role.objects.create(name="Corporate", description="Rol Corporativo")
        self.individual_role = Role.objects.create(name="Individual", description="Rol Individual")

        # Usuario Admin
        self.admin_user = User.objects.create_user(username="adminuser", password="password123")
        self.admin_profile = UserProfile.objects.create(user=self.admin_user, role=self.admin_role)

        # Usuario Corporativo
        self.corp_user = User.objects.create_user(username="corpuser", password="password123")
        self.corp_profile = UserProfile.objects.create(user=self.corp_user, role=self.corporate_role, is_corporate=True)

        # Usuario Individual (con rol Cliente para pruebas de cliente por defecto)
        self.client_role = Role.objects.create(name="Cliente", description="Rol de Cliente")
        self.ind_user = User.objects.create_user(username="induser", password="password123")
        self.ind_profile = UserProfile.objects.create(user=self.ind_user, role=self.client_role)

    def test_mfa_requirement(self):
        """Verifica que admin y corporativos requieran MFA obligatorio."""
        self.assertTrue(self.admin_profile.requires_mfa())
        self.assertTrue(self.corp_profile.requires_mfa())
        self.assertFalse(self.ind_profile.requires_mfa())

    def test_keycloak_login_redirect(self):
        """Verifica la redirección al flujo OIDC de Keycloak SSO desde el login."""
        response = self.client.get('/auth/sso/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('protocol/openid-connect/auth', response.url)

    def test_mfa_verification_success(self):
        """Verifica éxito al introducir el iToken correcto."""
        self.client.login(username='adminuser', password='password123')
        response = self.client.post('/auth/mfa/', {'itoken_code': '123456'})
        self.assertRedirects(response, '/auth/dashboard/', fetch_redirect_response=False)
        self.admin_profile.refresh_from_db()
        self.assertTrue(self.admin_profile.itoken_verified)

    def test_mfa_verification_failure(self):
        """Verifica error al introducir un iToken incorrecto."""
        self.client.login(username='adminuser', password='password123')
        response = self.client.post('/auth/mfa/', {'itoken_code': '999999'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "iToken inválido")

    @patch('authentication.views.requests.post')
    @patch('authentication.views.requests.get')
    def test_sso_keycloak_callback(self, mock_get, mock_post):
        """Verifica el callback de Keycloak SSO y el registro de auditoría."""
        mock_post.return_value.status_code = 200
        import base64
        import json
        header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').decode().rstrip('=')
        payload = base64.urlsafe_b64encode(json.dumps({
            'realm_access': {'roles': ['Admin']}
        }).encode()).decode().rstrip('=')
        dummy_jwt = f"{header}.{payload}.sig"
        
        mock_post.return_value.json.return_value = {
            'access_token': dummy_jwt
        }

        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            'preferred_username': 'adminuser',
            'email': 'admin@globalexchange.com'
        }

        response = self.client.get('/auth/callback/?code=mock_auth_code')
        self.assertRedirects(response, '/auth/mfa/', fetch_redirect_response=False)
        self.assertEqual(AuditLog.objects.filter(action="SSO_LOGIN_SUCCESS").count(), 1)

    @patch('authentication.views.requests.post')
    @patch('authentication.views.requests.get')
    def test_sso_keycloak_callback_cajero(self, mock_get, mock_post):
        """Verifica el callback de Keycloak SSO mapeando el rol 'cajero'."""
        mock_post.return_value.status_code = 200
        import base64
        import json
        header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').decode().rstrip('=')
        payload = base64.urlsafe_b64encode(json.dumps({
            'preferred_username': 'cajerosso',
            'email': 'cajero@globalexchange.com',
            'realm_access': {'roles': ['cajero']}
        }).encode()).decode().rstrip('=')
        dummy_jwt = f"{header}.{payload}.sig"
        
        mock_post.return_value.json.return_value = {
            'access_token': dummy_jwt
        }

        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            'preferred_username': 'cajerosso',
            'email': 'cajero@globalexchange.com'
        }

        response = self.client.get('/auth/callback/?code=mock_auth_code')
        self.assertRedirects(response, '/auth/dashboard/', fetch_redirect_response=False)
        user = User.objects.get(username='cajerosso')
        self.assertEqual(user.profile.role.name, "Cajero")

    def test_dashboard_redirect_by_role(self):
        """Verifica la redirección al panel según el rol del usuario."""
        self.client.login(username='induser', password='password123')
        response = self.client.get('/auth/dashboard/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authentication/client_dashboard.html')

    def test_logout(self):
        """Verifica el cierre de sesión local y redirección al endpoint de logout de Keycloak SSO."""
        self.client.login(username='induser', password='password123')
        response = self.client.get('/auth/logout/')
        expected_url = f"http://localhost:8080/realms/global-exchange-realm/protocol/openid-connect/logout?client_id=global-exchange-client&post_logout_redirect_uri=http://testserver/auth/login/"
        self.assertRedirects(response, expected_url, fetch_redirect_response=False, status_code=302)
        self.assertEqual(AuditLog.objects.filter(action="LOGOUT").count(), 1)

    def test_normal_user_no_group_no_role_badge(self):
        """Verifica que un usuario sin grupo ni rol no muestre insignia en el encabezado."""
        plain_user = User.objects.create_user(username="plainuser", password="password123")
        plain_profile = UserProfile.objects.create(user=plain_user, role=None)
        self.client.login(username='plainuser', password='password123')
        response = self.client.get('/auth/dashboard/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Administrador')
        self.assertNotContains(response, 'Cliente Corporativo')

    def test_dynamic_group_switching(self):
        """Verifica el cambio dinámico de grupo/cliente de Keycloak para un usuario con múltiples asociaciones."""
        from authentication.models import CorporateGroup, GroupMembership
        corp1 = CorporateGroup.objects.create(juridica_profile=self.corp_profile, group_name="Empresa A", keycloak_group_id="group-id-1")
        corp2 = CorporateGroup.objects.create(juridica_profile=self.corp_profile, group_name="Empresa B", keycloak_group_id="group-id-2")
        
        GroupMembership.objects.create(corporate_group=corp1, fisica_profile=self.ind_profile, role_in_group="OPERADOR")
        GroupMembership.objects.create(corporate_group=corp2, fisica_profile=self.ind_profile, role_in_group="ANALISTA")

        self.client.login(username='induser', password='password123')
        # Cambiar a Empresa B
        response = self.client.get('/auth/switch-group/group-id-2/')
        self.assertRedirects(response, '/auth/dashboard/', fetch_redirect_response=False)
        
        # Verificar que la interfaz muestre el grupo activo Empresa B
        dash_resp = self.client.get('/auth/dashboard/')
        self.assertContains(dash_resp, "Empresa B")
        self.assertContains(dash_resp, "ANALISTA")

    def test_normal_user_solicitar_ser_cliente_ui(self):
        """Verifica que un usuario normal sin rol ni grupo vea la opción 'Solicitar ser Cliente'."""
        plain_user = User.objects.create_user(username="solicitante", password="password123")
        UserProfile.objects.create(user=plain_user, role=None)
        self.client.login(username='solicitante', password='password123')
        response = self.client.get('/auth/dashboard/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Solicitar ser Cliente")
        self.assertContains(response, "Panel de Usuario Regular")

    def test_user_in_other_group_still_sees_solicitar_cliente(self):
        """Verifica que un usuario perteneciente al grupo de otro usuario pero sin grupo propio siga viendo 'Solicitar ser Cliente'."""
        from authentication.models import CorporateGroup, GroupMembership
        corp1 = CorporateGroup.objects.create(juridica_profile=self.corp_profile, group_name="Empresa X", keycloak_group_id="group-x")
        GroupMembership.objects.create(corporate_group=corp1, fisica_profile=self.ind_profile, role_in_group="OPERADOR")

        self.client.login(username='induser', password='password123')
        response = self.client.get('/auth/dashboard/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Solicitar ser Cliente")

    def test_request_registration_does_not_create_group_immediately(self):
        """Verifica que al solicitar registro de cliente no se cree el CorporateGroup hasta que el admin apruebe."""
        from authentication.models import ClientRegistrationRequest, CorporateGroup
        self.client.login(username='induser', password='password123')
        response = self.client.post('/auth/register/', {
            'client_name': 'Mi Empresa Nueva',
            'ci_ruc': '99988877-6',
            'client_type': 'JURIDICA',
            'email': 'miempresa@globalexchange.com'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Solicitud enviada exitosamente")

        req = ClientRegistrationRequest.objects.filter(ci_ruc='99988877-6').first()
        self.assertIsNotNone(req)
        self.assertEqual(req.status, 'PENDING')
        self.assertIsNone(req.corporate_group)
        self.assertFalse(CorporateGroup.objects.filter(juridica_profile=self.ind_profile).exists())

    def test_cajero_dashboard_render(self):
        """Verifica que un usuario con rol Cajero renderice correctamente la interfaz de cajero."""
        cajero_role = Role.objects.create(name="Cajero", description="Rol de Cajero")
        cajero_user = User.objects.create_user(username="cajero1", password="password123")
        UserProfile.objects.create(user=cajero_user, role=cajero_role)
        self.client.login(username='cajero1', password='password123')
        response = self.client.get('/auth/dashboard/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authentication/cajero_dashboard.html')
        self.assertContains(response, "Panel Operativo de Cajero")

    def test_analista_dashboard_render(self):
        """Verifica que un usuario con rol Analista renderice correctamente la interfaz de analista."""
        analista_role = Role.objects.create(name="Analista", description="Rol de Analista")
        analista_user = User.objects.create_user(username="analista1", password="password123")
        UserProfile.objects.create(user=analista_user, role=analista_role)
        self.client.login(username='analista1', password='password123')
        response = self.client.get('/auth/dashboard/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authentication/analista_dashboard.html')
        self.assertContains(response, "Panel de Analista de Operaciones")

    def test_user_without_client_interface_render(self):
        """Verifica la interfaz para el usuario sin cliente (user_dashboard.html) sin categoría de cliente."""
        self.client.login(username='induser', password='password123')
        resp_mode = self.client.get('/auth/mode/user/')
        self.assertRedirects(resp_mode, '/auth/dashboard/', fetch_redirect_response=False)
        
        response = self.client.get('/auth/dashboard/')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authentication/user_dashboard.html')
        self.assertContains(response, "Panel de Usuario Regular")
        self.assertNotContains(response, "Categoría Actual")
        self.assertNotContains(response, "Minorista")
        self.assertNotContains(response, "Corporativo")
        self.assertNotContains(response, "VIP")

    def test_switch_between_client_and_user_mode(self):
        """Verifica el cambio entre modo cliente y modo usuario sin cliente para solicitar ser cliente."""
        self.client.login(username='induser', password='password123')
        self.client.get('/auth/mode/user/')
        resp_user = self.client.get('/auth/dashboard/')
        self.assertTemplateUsed(resp_user, 'authentication/user_dashboard.html')
        self.assertContains(resp_user, "Solicitar ser Cliente")

        resp_client_mode = self.client.get('/auth/mode/client/')
        self.assertRedirects(resp_client_mode, '/auth/dashboard/', fetch_redirect_response=False)
        resp_client = self.client.get('/auth/dashboard/')
        self.assertTemplateUsed(resp_client, 'authentication/client_dashboard.html')
