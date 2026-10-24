from django.test import TestCase
from django.contrib.auth.models import User
from rest_framework.test import APIClient
from rest_framework import status
from unittest.mock import patch, MagicMock
from .facturacion_service import FacturaSeguraService


class SifenServiceTestCase(TestCase):
    """
    Pruebas unitarias para el servicio de integración SIFEN (Factura Segura) y sus métodos.
    """

    def setUp(self):
        self.user = User.objects.create_user(username='admin_sifen', password='password123', is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch('authentication.facturacion_service.requests.post')
    def test_obtener_token_success(self, mock_post):
        """
        Prueba la obtención exitosa del token de autenticación de Factura Segura.
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"authentication_token": "mock-token-12345"}
        mock_post.return_value = mock_response

        token = FacturaSeguraService.obtener_token("test@facturasegura.com.py", "password")
        self.assertEqual(token, "mock-token-12345")
        mock_post.assert_called_once()

    @patch('authentication.facturacion_service.requests.post')
    def test_generar_de_success(self, mock_post):
        """
        Prueba la generación exitosa de un Documento Electrónico (DE) en SIFEN.
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"code": 0, "message": "Generado con éxito", "cdc": "01800000123456789012345678901234567890123456"}
        mock_post.return_value = mock_response

        resultado = FacturaSeguraService.generar_de("mock-token-12345", {"tasa_cambio": 7300})
        self.assertEqual(resultado["code"], 0)
        self.assertIn("cdc", resultado)

    @patch('authentication.facturacion_service.requests.post')
    def test_get_estado_sifen(self, mock_post):
        """
        Prueba la consulta de estado SIFEN de un documento electrónico.
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"code": 0, "estado": "APROBADO"}
        mock_post.return_value = mock_response

        resultado = FacturaSeguraService.get_estado_sifen("mock-token-12345", "01800000123456789012345678901234567890123456", "80000000-1")
        self.assertEqual(resultado["estado"], "APROBADO")

    @patch('authentication.facturacion_service.requests.get')
    def test_descargar_kude(self, mock_get):
        """
        Prueba la descarga del archivo KuDE (PDF).
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"%PDF-1.4 mock pdf content"
        mock_get.return_value = mock_response

        content = FacturaSeguraService.descargar_kude("mock-token-12345", "01800000123456789012345678901234567890123456")
        self.assertEqual(content, b"%PDF-1.4 mock pdf content")

    @patch('authentication.facturacion_service.requests.get')
    def test_descargar_xml(self, mock_get):
        """
        Prueba la descarga del archivo XML firmado.
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<rDE>mock xml</rDE>"
        mock_get.return_value = mock_response

        xml_text = FacturaSeguraService.descargar_xml("mock-token-12345", "01800000123456789012345678901234567890123456")
        self.assertEqual(xml_text, "<rDE>mock xml</rDE>")

    @patch('authentication.facturacion_service.FacturaSeguraService.obtener_token')
    def test_api_view_sifen_login(self, mock_obtener_token):
        """
        Prueba el endpoint API de login SIFEN.
        """
        mock_obtener_token.return_value = "token-api-123"
        response = self.client.post('/api/sifen/login/', {
            'email': 'test@facturasegura.com.py',
            'password': 'password123'
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["authentication_token"], "token-api-123")

    @patch('authentication.facturacion_service.FacturaSeguraService.generar_de')
    def test_api_view_generar_de(self, mock_generar_de):
        """
        Prueba el endpoint API de generación de DE.
        """
        mock_generar_de.return_value = {"code": 0, "cdc": "12345"}
        response = self.client.post('/api/sifen/generar-de/', {
            'authentication_token': 'token-api-123',
            'transaccion_data': {'tasa_cambio': 7300}
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["code"], 0)
