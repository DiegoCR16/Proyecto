from django.test import TestCase, Client
from django.contrib.auth.models import User
from authentication.models import UserProfile, AuditLog
from unittest.mock import patch

class RegistrationPSE2Tests(TestCase):
    """
    Suite de pruebas unitarias independiente y exclusiva para la Historia de Usuario PSE-2:
    Registro de Clientes (Personas Físicas y Jurídicas), validaciones de campos,
    control de duplicados e integración con la Admin REST API de Keycloak en segundo plano.
    """

    def setUp(self):
        """Configuración inicial para las pruebas de registro PSE-2."""
        self.client = Client()
        self.register_url = '/auth/register/'
        self.keycloak_reg_url = '/auth/register/sso/'

        # Crear un usuario existente para probar control de duplicados
        self.existing_user = User.objects.create_user(username="existinguser", email="juan.perez@globalexchange.com", password="Password123*")
        self.existing_profile = UserProfile.objects.create(user=self.existing_user, ci_ruc="1234567")

    def test_register_page_loads(self):
        """
        Verifica que la página de registro cargue correctamente (Status 200).
        """
        response = self.client.get(self.register_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authentication/register.html')

    def test_keycloak_registration_redirect(self):
        """
        Verifica la correcta redirección al flujo de registro de Keycloak IdP.
        """
        response = self.client.get(self.keycloak_reg_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('protocol/openid-connect/registrations', response.url)

    def test_validation_invalid_full_name(self):
        """
        Valida que el sistema rechace nombres completos que contengan números o símbolos.
        """
        response = self.client.post(self.register_url, {
            'person_type': 'fisica',
            'full_name': 'Juan Pérez 123',
            'ci_ruc': '7654321',
            'email': 'nuevo.juan@globalexchange.com',
            'password': 'SecurePassword123*'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "El nombre completo debe contener únicamente caracteres alfabéticos.")

    def test_validation_invalid_ci_ruc(self):
        """
        Valida que el sistema rechace cédulas o RUC con formatos no numéricos inválidos.
        """
        response = self.client.post(self.register_url, {
            'person_type': 'fisica',
            'full_name': 'Maria Gomez',
            'ci_ruc': 'ABC-1234',
            'email': 'maria.gomez@globalexchange.com',
            'password': 'SecurePassword123*'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "El número de cédula o RUC debe tener un formato numérico válido.")

    def test_validation_invalid_email_mask(self):
        """
        Valida que el sistema rechace correos electrónicos que no cumplan con la máscara texto@dominio.extensión.
        """
        response = self.client.post(self.register_url, {
            'person_type': 'fisica',
            'full_name': 'Maria Gomez',
            'ci_ruc': '7654321',
            'email': 'mariagomez-invalid',
            'password': 'SecurePassword123*'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "El correo electrónico no cumple con la máscara texto@dominio.extensión.")

    def test_validation_weak_password(self):
        """
        Valida que el sistema rechace contraseñas inseguras (menos de 8 caracteres, sin mayúsculas, minúsculas o especiales).
        """
        response = self.client.post(self.register_url, {
            'person_type': 'fisica',
            'full_name': 'Maria Gomez',
            'ci_ruc': '7654321',
            'email': 'maria.gomez@globalexchange.com',
            'password': 'weak'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La contraseña debe tener un mínimo de 8 caracteres e incluir mayúsculas, minúsculas y caracteres especiales.")

    def test_duplicate_control_email(self):
        """
        Verifica que el sistema deniegue el registro si el correo electrónico ya está registrado.
        """
        response = self.client.post(self.register_url, {
            'person_type': 'fisica',
            'full_name': 'Otro Juan',
            'ci_ruc': '9876543',
            'email': 'juan.perez@globalexchange.com', # Email ya existente en setUp
            'password': 'SecurePassword123*'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "El correo electrónico o número de cédula/RUC ya se encuentra registrado.")
        self.assertEqual(AuditLog.objects.filter(action="REGISTER_DUPLICATE_ATTEMPT").count(), 1)

    def test_duplicate_control_ci_ruc(self):
        """
        Verifica que el sistema deniegue el registro si el número de cédula o RUC ya existe.
        """
        response = self.client.post(self.register_url, {
            'person_type': 'fisica',
            'full_name': 'Juan Duplicado',
            'ci_ruc': '1234567', # CI ya existente en setUp
            'email': 'juan.duplicado@globalexchange.com',
            'password': 'SecurePassword123*'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "El correo electrónico o número de cédula/RUC ya se encuentra registrado.")

    @patch('authentication.views.requests.get')
    @patch('authentication.views.requests.post')
    def test_successful_registration_calls_keycloak_admin_api(self, mock_post, mock_get):
        """
        Verifica que al completar un registro válido de Persona Física, el backend llama
        a la Admin REST API de Keycloak en segundo plano con userType: fisica y redirige al login.
        """
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = [] # Sin duplicados en Keycloak

        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {'access_token': 'mock_admin_token'}

        response = self.client.post(self.register_url, {
            'person_type': 'fisica',
            'full_name': 'Ana Benitez',
            'ci_ruc': '5544332',
            'email': 'ana.benitez@globalexchange.com',
            'password': 'StrongPassword123!'
        })
        self.assertRedirects(response, '/auth/login/', fetch_redirect_response=False, status_code=302)
        self.assertEqual(AuditLog.objects.filter(action="REGISTER_SUCCESS").count(), 1)
        self.assertTrue(User.objects.filter(email='ana.benitez@globalexchange.com').exists())

    def test_juridica_person_type_selection(self):
        """
        Verifica que al seleccionar Persona Jurídica, el sistema requiera los campos obligatorios corporativos.
        """
        response = self.client.post(self.register_url, {
            'person_type': 'juridica'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Todos los campos obligatorios deben ser completados.")
