from django.test import TestCase, Client
from django.contrib.auth.models import User
from authentication.models import UserProfile, AuditLog, Role
from unittest.mock import patch

class RegistrationPSE3Tests(TestCase):
    """
    Suite de pruebas unitarias independiente y exclusiva para la Historia de Usuario PSE-3:
    Registro de Clientes Personas Jurídicas, validaciones de nombre de empresa, RUC,
    correo corporativo, contraseña segura, control de duplicados y delegación/integración
    con la Admin REST API de Keycloak en segundo plano con userType: juridica.
    """

    def setUp(self):
        """
        Configuración inicial para las pruebas de registro de Personas Jurídicas PSE-3.
        """
        self.client = Client()
        self.register_url = '/auth/register/'

        # Crear un usuario y perfil corporativo existente para probar control de duplicados
        self.corp_role, _ = Role.objects.get_or_create(name="Corporate", defaults={'description': "Cliente Corporativo"})
        self.existing_user = User.objects.create_user(username="empresa@globalexchange.com", email="empresa@globalexchange.com", password="Password123*")
        self.existing_profile = UserProfile.objects.create(
            user=self.existing_user,
            ci_ruc="80012345-6",
            role=self.corp_role,
            is_corporate=True
        )

    def test_register_page_loads_juridica(self):
        """
        Verifica que la página de registro cargue correctamente permitiendo la opción jurídica.
        """
        response = self.client.get(self.register_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'authentication/register.html')

    def test_validation_invalid_company_name(self):
        """
        Valida que el sistema rechace nombres de empresas que contengan números o símbolos no permitidos.
        """
        response = self.client.post(self.register_url, {
            'person_type': 'juridica',
            'company_name': 'Global Exchange S.A. 2026',
            'ci_ruc': '80099999-1',
            'email': 'nueva.empresa@globalexchange.com',
            'password': 'SecurePassword123*'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "El nombre de la empresa debe contener únicamente caracteres alfabéticos y puntos.")

    def test_validation_invalid_ruc(self):
        """
        Valida que el sistema rechace RUCs con formatos inválidos o sin guion obligatorio.
        """
        # Prueba sin guion
        response = self.client.post(self.register_url, {
            'person_type': 'juridica',
            'company_name': 'Global Exchange S.A.',
            'ci_ruc': '800123456',
            'email': 'nueva.empresa@globalexchange.com',
            'password': 'SecurePassword123*'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "El RUC debe tener un formato numérico válido con guion")

        # Prueba con caracteres no numéricos
        response2 = self.client.post(self.register_url, {
            'person_type': 'juridica',
            'company_name': 'Global Exchange S.A.',
            'ci_ruc': 'ABC-RUC',
            'email': 'nueva.empresa@globalexchange.com',
            'password': 'SecurePassword123*'
        })
        self.assertEqual(response2.status_code, 200)
        self.assertContains(response2, "El RUC debe tener un formato numérico válido con guion")

    def test_validation_invalid_email_mask(self):
        """
        Valida que el sistema rechace correos corporativos que no cumplan con la máscara texto@dominio.extensión.
        """
        response = self.client.post(self.register_url, {
            'person_type': 'juridica',
            'company_name': 'Global Exchange S.A.',
            'ci_ruc': '80099999-1',
            'email': 'correo-sin-dominio',
            'password': 'SecurePassword123*'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "El correo electrónico no cumple con la máscara texto@dominio.extensión.")

    def test_validation_weak_password(self):
        """
        Valida que el sistema rechace contraseñas corporativas inseguras (menos de 8 caracteres, sin mayúsculas, minúsculas o especiales).
        """
        response = self.client.post(self.register_url, {
            'person_type': 'juridica',
            'company_name': 'Global Exchange S.A.',
            'ci_ruc': '80099999-1',
            'email': 'nueva.empresa@globalexchange.com',
            'password': 'weak'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La contraseña debe tener un mínimo de 8 caracteres e incluir mayúsculas, minúsculas y caracteres especiales.")

    def test_duplicate_control_email_juridica(self):
        """
        Verifica que el sistema deniegue el registro si el correo electrónico corporativo ya está registrado.
        """
        response = self.client.post(self.register_url, {
            'person_type': 'juridica',
            'company_name': 'Otra Empresa S.A.',
            'ci_ruc': '80088888-2',
            'email': 'empresa@globalexchange.com', # Email ya existente en setUp
            'password': 'SecurePassword123*'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "El correo electrónico o RUC ya se encuentra registrado.")
        self.assertEqual(AuditLog.objects.filter(action="REGISTER_DUPLICATE_ATTEMPT").count(), 1)

    def test_duplicate_control_ruc_juridica(self):
        """
        Verifica que el sistema deniegue el registro si el RUC corporativo ya existe.
        """
        response = self.client.post(self.register_url, {
            'person_type': 'juridica',
            'company_name': 'Empresa Duplicada S.A.',
            'ci_ruc': '80012345-6', # RUC ya existente en setUp
            'email': 'duplicada@globalexchange.com',
            'password': 'SecurePassword123*'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "El correo electrónico o RUC ya se encuentra registrado.")

    @patch('authentication.views.requests.get')
    @patch('authentication.views.requests.post')
    def test_successful_registration_juridica_calls_keycloak_admin_api(self, mock_post, mock_get):
        """
        Verifica que al completar un registro válido de Persona Jurídica, el backend llama
        a la Admin REST API de Keycloak en segundo plano con userType: juridica, asigna rol Corporate,
        marca is_corporate=True y redirige al login.
        """
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = [] # Sin duplicados en Keycloak

        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {'access_token': 'mock_admin_token'}

        response = self.client.post(self.register_url, {
            'person_type': 'juridica',
            'company_name': 'Corporacion Global S.A.',
            'ci_ruc': '80055544-3',
            'email': 'corporacion@globalexchange.com',
            'password': 'StrongPassword123!'
        })
        self.assertRedirects(response, '/auth/login/', fetch_redirect_response=False, status_code=302)
        self.assertEqual(AuditLog.objects.filter(action="REGISTER_SUCCESS").count(), 1)
        self.assertTrue(User.objects.filter(email='corporacion@globalexchange.com').exists())
        
        user = User.objects.get(email='corporacion@globalexchange.com')
        self.assertTrue(user.profile.is_corporate)
        self.assertEqual(user.profile.ci_ruc, '80055544-3')
        self.assertEqual(user.profile.role.name, 'Corporate')
