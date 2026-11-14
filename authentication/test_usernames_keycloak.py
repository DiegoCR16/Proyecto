# -*- coding: utf-8 -*-
"""
Pruebas unitarias que evitan usuarios duplicados por mayúsculas entre Django y Keycloak
(ej. "Diego" creado por el administrador y "diego" devuelto por Keycloak al iniciar sesión).
"""
import base64
import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse

from authentication.models import Role, UserProfile
from authentication.usuarios import normalizar_username, existe_username, obtener_o_crear_usuario_keycloak

SYNC_KEYCLOAK = "gestion_clientes.views.sync_user_roles_to_keycloak"


def _jwt(payload):
    """Arma un JWT sin firma con el payload indicado, como el que devuelve Keycloak."""
    def b64(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    return f"{b64({'alg': 'HS256'})}.{b64(payload)}.sig"


class UtilidadesUsernameTests(TestCase):
    """Verifica la normalización y búsqueda de usernames sin distinguir mayúsculas."""

    def test_normalizar_username_igual_que_keycloak(self):
        """El username queda en minúsculas y sin espacios alrededor."""
        self.assertEqual(normalizar_username("  Diego "), "diego")
        self.assertEqual(normalizar_username(None), "")

    def test_existe_username_no_distingue_mayusculas(self):
        """'DIEGO' se considera el mismo nombre que 'diego'."""
        user = User.objects.create(username="diego")
        self.assertTrue(existe_username("DIEGO"))
        self.assertFalse(existe_username("Diego", excluir_id=user.id))

    def test_obtener_usuario_por_id_de_keycloak(self):
        """Si el perfil ya tiene el ID de Keycloak, se usa ese usuario aunque cambie el nombre."""
        user = User.objects.create(username="diego")
        UserProfile.objects.create(user=user, keycloak_id="kc-123")
        encontrado, creado = obtener_o_crear_usuario_keycloak("otro_nombre", kc_id="kc-123")
        self.assertEqual(encontrado, user)
        self.assertFalse(creado)

    def test_crea_usuario_nuevo_en_minusculas(self):
        """Un usuario que no existe se crea con el username en minúsculas."""
        user, creado = obtener_o_crear_usuario_keycloak("Maria", defaults={"email": "maria@test.com"})
        self.assertTrue(creado)
        self.assertEqual(user.username, "maria")
        self.assertEqual(user.email, "maria@test.com")


class LoginKeycloakSinDuplicadosTests(TestCase):
    """El inicio de sesión con Keycloak reutiliza el usuario creado desde la administración."""

    @patch("authentication.views.requests.get")
    @patch("authentication.views.requests.post")
    def test_login_no_duplica_usuario_con_mayusculas(self, mock_post, mock_get):
        """Un usuario 'Diego' existente no se duplica cuando Keycloak devuelve 'diego'."""
        existente = User.objects.create(username="Diego", email="diego@test.com")
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "access_token": _jwt({"preferred_username": "diego", "email": "diego@test.com"})
        }
        mock_get.return_value.status_code = 500

        response = self.client.get("/auth/callback/?code=codigo")

        self.assertRedirects(response, reverse("dashboard_redirect"), fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), existente.id)
        self.assertEqual(User.objects.filter(username__iexact="diego").count(), 1)
        self.assertEqual(User.objects.get(username__iexact="diego"), existente)


class AdminUsuariosMinusculasTests(TestCase):
    """La administración guarda los usernames en minúsculas y no permite repetirlos."""

    def setUp(self):
        """Crea un administrador con sesión iniciada."""
        admin_role = Role.objects.create(name="Admin")
        self.admin = User.objects.create_user(username="admin_test", password="password123")
        UserProfile.objects.create(user=self.admin, role=admin_role)
        self.client = Client()
        self.client.force_login(self.admin)
        self.datos = {
            "username": "Diego", "email": "diego@test.com",
            "password": "Clave123!", "is_active": "on",
        }

    @patch(SYNC_KEYCLOAK, return_value="kc-diego")
    def test_alta_guarda_username_en_minusculas(self, _sync):
        """Crear 'Diego' desde la administración lo guarda como 'diego', igual que Keycloak."""
        self.client.post(reverse("admin_user_create"), self.datos)
        self.assertTrue(User.objects.filter(username="diego").exists())
        self.assertFalse(User.objects.filter(username="Diego").exists())

    @patch(SYNC_KEYCLOAK, return_value="kc-diego")
    def test_alta_rechaza_mismo_nombre_con_otras_mayusculas(self, sync):
        """No se puede crear 'DIEGO' si ya existe 'diego'."""
        User.objects.create(username="diego")
        response = self.client.post(reverse("admin_user_create"), {**self.datos, "username": "DIEGO"})
        self.assertContains(response, "El nombre de usuario ya existe.")
        self.assertEqual(User.objects.filter(username__iexact="diego").count(), 1)
        sync.assert_not_called()

    @patch(SYNC_KEYCLOAK, return_value="kc-otro")
    def test_edicion_rechaza_nombre_de_otro_usuario(self, sync):
        """Al editar no se puede renombrar a un usuario con el nombre de otro, con otras mayúsculas."""
        User.objects.create(username="diego")
        otro = User.objects.create(username="otro")
        response = self.client.post(
            reverse("admin_user_edit", args=[otro.id]), {**self.datos, "username": "Diego", "password": ""}
        )
        self.assertContains(response, "El nombre de usuario ya existe.")
        otro.refresh_from_db()
        self.assertEqual(otro.username, "otro")
        sync.assert_not_called()
