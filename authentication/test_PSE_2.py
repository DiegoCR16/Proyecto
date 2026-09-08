from django.test import TestCase, Client
from django.contrib.auth.models import User

class RegistrationPSE2Tests(TestCase):
    """
    Suite de pruebas unitarias para el registro exclusivo a través de Keycloak IdP.
    """

    def setUp(self):
        """Configuración inicial para las pruebas de registro."""
        self.client = Client()
        self.register_url = '/auth/register/'
        self.keycloak_reg_url = '/auth/register/sso/'

    def test_register_renders_form(self):
        """
        Verifica que el endpoint /auth/register/ requiera autenticación o renderice la ficha de registro de cliente.
        """
        user = User.objects.create_user(username="testclientreq", password="password123")
        self.client.login(username='testclientreq', password='password123')
        response = self.client.get(self.register_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'gestion_clientes/register.html')

    def test_keycloak_registration_redirect(self):
        """
        Verifica la correcta redirección al flujo de registro de Keycloak IdP.
        """
        response = self.client.get(self.keycloak_reg_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('protocol/openid-connect/registrations', response.url)
