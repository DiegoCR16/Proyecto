from django.test import TestCase, Client
from django.contrib.auth.models import User

class RegistrationPSE3Tests(TestCase):
    """
    Suite de pruebas unitarias para la solicitud de registro de cliente y Keycloak IdP.
    """

    def setUp(self):
        """Configuración inicial para las pruebas de registro."""
        self.client = Client()
        self.register_url = '/auth/register/'
        self.keycloak_reg_url = '/auth/register/sso/'

    def test_register_page_renders_form(self):
        """
        Verifica que la página de solicitud de registro renderice la ficha de cliente.
        """
        user = User.objects.create_user(username="testclientreq2", password="password123")
        self.client.login(username='testclientreq2', password='password123')
        response = self.client.get(self.register_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'gestion_clientes/register.html')
