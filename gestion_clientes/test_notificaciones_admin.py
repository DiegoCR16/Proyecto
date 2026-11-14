# -*- coding: utf-8 -*-
"""Pruebas unitarias de las notificaciones de error de la interfaz de administración."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse

from authentication.middleware import describir_error, es_administrador
from authentication.models import Role, UserProfile, Cliente, ClientRegistrationRequest

PASSWORD_VALIDA = "Clave123!"


class BaseAdminTest(TestCase):
    """Crea un administrador con sesión iniciada."""

    def setUp(self):
        """Prepara un administrador y un usuario común."""
        admin_role = Role.objects.create(name="Admin")
        self.admin = User.objects.create_user(username="admin_notif", password="password123")
        UserProfile.objects.create(user=self.admin, role=admin_role)
        self.client = Client()
        self.client.force_login(self.admin)

    def mensajes(self, response):
        """Devuelve los textos de las notificaciones de la respuesta."""
        return [str(m) for m in response.context["messages"]]


class MiddlewareErroresAdminTests(BaseAdminTest):
    """Un error inesperado en la administración se muestra como notificación, no como página 500."""

    @patch("gestion_clientes.views.validate_password_complexity", side_effect=RuntimeError("fallo de prueba"))
    def test_error_inesperado_vuelve_a_la_pantalla_con_notificacion(self, _validar):
        """El administrador vuelve a la pantalla anterior y ve qué falló."""
        anterior = reverse("admin_user_list")
        response = self.client.post(
            reverse("admin_user_create"),
            {"username": "nuevo", "email": "n@test.com", "password": PASSWORD_VALIDA},
            HTTP_REFERER=f"http://testserver{anterior}",
        )
        self.assertRedirects(response, f"http://testserver{anterior}", fetch_redirect_response=False)

        pantalla = self.client.get(anterior)
        texto = " ".join(self.mensajes(pantalla))
        self.assertIn("No se pudo completar la operación", texto)
        self.assertIn("RuntimeError: fallo de prueba", texto)
        self.assertContains(pantalla, "Ocurrió un error")

    def test_solo_intercepta_administradores(self):
        """El middleware actúa solo con administradores; para el resto el error sigue su curso normal."""
        comun = User.objects.create_user(username="comun", password="password123")
        UserProfile.objects.create(user=comun, role=Role.objects.create(name="Cliente"))
        self.assertTrue(es_administrador(self.admin))
        self.assertFalse(es_administrador(comun))

    def test_describir_error_validation_error(self):
        """Los ValidationError muestran sus mensajes, no la representación interna."""
        from django.core.exceptions import ValidationError
        self.assertEqual(describir_error(ValidationError("Monto inválido")), "ValidationError: Monto inválido")


class UsuariosKeycloakNotificacionesTests(BaseAdminTest):
    """Los problemas con Keycloak al guardar usuarios se notifican al administrador."""

    @patch("gestion_clientes.views.requests.post")
    def test_alta_avisa_si_keycloak_no_responde(self, mock_post):
        """Si Keycloak rechaza la autenticación, el administrador ve el motivo."""
        mock_post.return_value.status_code = 401
        mock_post.return_value.text = "unauthorized_client"

        response = self.client.post(
            reverse("admin_user_create"),
            {"username": "pedro", "email": "pedro@test.com", "password": PASSWORD_VALIDA, "is_active": "on"},
            follow=True,
        )

        texto = " ".join(self.mensajes(response))
        self.assertIn("Usuario pedro creado exitosamente.", texto)
        self.assertIn("hubo un problema con Keycloak", texto)
        self.assertIn("HTTP 401", texto)

    @patch("gestion_clientes.views.sync_user_roles_to_keycloak", return_value="kc-ok")
    def test_alta_exitosa_confirma(self, _sync):
        """Sin problemas, solo se muestra la confirmación."""
        response = self.client.post(
            reverse("admin_user_create"),
            {"username": "ana", "email": "ana@test.com", "password": PASSWORD_VALIDA, "is_active": "on"},
            follow=True,
        )
        self.assertEqual(self.mensajes(response), ["Usuario ana creado exitosamente."])

    def test_no_puede_desactivarse_a_si_mismo(self):
        """Intentar desactivar la propia cuenta muestra un error en lugar de no hacer nada."""
        response = self.client.get(reverse("admin_user_delete", args=[self.admin.id]), follow=True)
        self.assertIn("No puedes desactivar tu propio usuario", " ".join(self.mensajes(response)))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_desactivar_otro_usuario_confirma(self):
        """Desactivar a otro usuario muestra la confirmación."""
        otro = User.objects.create_user(username="otro", password="password123")
        response = self.client.get(reverse("admin_user_delete", args=[otro.id]), follow=True)
        self.assertIn("Usuario otro desactivado.", self.mensajes(response))


class SolicitudesNotificacionesTests(BaseAdminTest):
    """Aprobar solicitudes informa el resultado y los problemas con Keycloak."""

    def _solicitud(self):
        """Crea una solicitud de cliente pendiente."""
        solicitante = User.objects.create_user(username="solicitante", email="s@test.com", password="password123")
        return ClientRegistrationRequest.objects.create(
            user=solicitante, client_name="Cliente Nuevo", ci_ruc="4443332", client_type="FISICA",
            email="contacto@test.com",
        )

    @patch("gestion_clientes.views.requests.post", side_effect=ConnectionError("Keycloak caído"))
    def test_aprobacion_avisa_error_de_keycloak(self, _post):
        """El cliente se aprueba, pero el administrador ve que Keycloak falló y por qué."""
        solicitud = self._solicitud()
        response = self.client.get(reverse("admin_approve_client", args=[solicitud.id]), follow=True)

        texto = " ".join(self.mensajes(response))
        self.assertIn("Solicitud de Cliente Nuevo aprobada", texto)
        self.assertIn("problemas con Keycloak", texto)
        self.assertIn("Keycloak caído", texto)

    @patch("gestion_clientes.views.requests.post", side_effect=ConnectionError("Keycloak caído"))
    def test_solicitud_ya_procesada_informa(self, _post):
        """Aprobar dos veces la misma solicitud avisa que ya fue procesada."""
        solicitud = self._solicitud()
        self.client.get(reverse("admin_approve_client", args=[solicitud.id]))
        response = self.client.get(reverse("admin_approve_client", args=[solicitud.id]), follow=True)
        self.assertIn("ya fue procesada anteriormente", " ".join(self.mensajes(response)))


class VolumenClienteTests(BaseAdminTest):
    """El volumen de transacciones inválido se informa y el válido se guarda."""

    def test_volumen_invalido_al_crear_cliente(self):
        """Un volumen que no es número muestra el error y no crea el cliente."""
        response = self.client.post(reverse("admin_client_list"), {
            "action": "create_client", "nombre_o_razon_social": "Cliente X", "documento_identidad": "9998887",
            "email": "x@test.com", "transaction_volume": "abc",
        })
        self.assertContains(response, "no es un número válido")
        self.assertFalse(Cliente.objects.filter(documento_identidad="9998887").exists())

    def test_volumen_valido_se_guarda(self):
        """El volumen ingresado se guarda (antes se perdía porque faltaba importar Decimal)."""
        self.client.post(reverse("admin_client_list"), {
            "action": "create_client", "nombre_o_razon_social": "Cliente Y", "documento_identidad": "9998886",
            "email": "y@test.com", "transaction_volume": "1500000.50",
        })
        self.assertEqual(Cliente.objects.get(documento_identidad="9998886").transaction_volume, Decimal("1500000.50"))

    def test_volumen_invalido_al_actualizar_categoria(self):
        """Actualizar con un volumen inválido avisa y no cambia el cliente."""
        cliente = Cliente.objects.create(
            nombre_o_razon_social="Cliente Z", documento_identidad="9998885", email="z@test.com",
            tipo_cliente="FISICA", categoria="MINORISTA",
        )
        response = self.client.post(reverse("admin_client_detail", args=[cliente.id]), {
            "action": "update_category", "category": "VIP", "transaction_volume": "-5",
        })
        self.assertContains(response, "no es un número válido")
        cliente.refresh_from_db()
        self.assertEqual(cliente.categoria, "MINORISTA")
