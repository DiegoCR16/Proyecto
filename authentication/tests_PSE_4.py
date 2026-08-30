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

        # Usuario Individual
        self.ind_user = User.objects.create_user(username="induser", password="password123")
        self.ind_profile = UserProfile.objects.create(user=self.ind_user, role=self.individual_role)

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
