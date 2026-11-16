# -*- coding: utf-8 -*-
"""Pruebas unitarias PSE-40: los clientes corporativos usan el panel de cliente (no hay panel corporativo)."""
import base64
import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.test import TestCase

from authentication.models import Role, UserProfile


class SinPanelCorporativoTests(TestCase):
    """"Corporativo" es una categoría de beneficio del cliente, como VIP, y no tiene interfaz propia."""

    def _entrar(self, username, rol, is_corporate=False):
        """Crea el usuario con el rol indicado, inicia sesión y abre el panel."""
        user = User.objects.create_user(username=username, password="password123")
        UserProfile.objects.create(
            user=user, role=Role.objects.get_or_create(name=rol)[0] if rol else None, is_corporate=is_corporate,
            itoken_verified=True,
        )
        self.client.login(username=username, password="password123")
        return self.client.get("/auth/dashboard/")

    def test_plantilla_corporativa_eliminada(self):
        """La plantilla del panel corporativo ya no existe."""
        with self.assertRaises(TemplateDoesNotExist):
            get_template("authentication/corporate_dashboard.html")

    def test_cliente_corporativo_usa_panel_de_cliente(self):
        """Un perfil marcado como corporativo entra al panel de cliente."""
        respuesta = self._entrar("cliente_corp", "Cliente", is_corporate=True)
        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "authentication/client_dashboard.html")

    def test_rol_corporativo_sin_cliente_usa_panel_de_usuario(self):
        """Un rol "(Corporativo)" sin cliente asociado ve el panel de usuario, no un panel corporativo."""
        respuesta = self._entrar("jefe_corp", "Jefe (Corporativo)")
        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, "authentication/user_dashboard.html")


class SinRolCorporateTests(TestCase):
    """El rol "Corporate" del modelo anterior ya no existe ni se asigna al iniciar sesión."""

    def test_migracion_elimina_el_rol(self):
        """Después de las migraciones no queda el rol "Corporate"."""
        self.assertFalse(Role.objects.filter(name="Corporate").exists())

    @patch("authentication.views.requests.post")
    @patch("authentication.views.requests.get")
    def test_login_keycloak_no_asigna_rol_corporativo(self, mock_get, mock_post):
        """Un usuario con rol "corporate" en Keycloak ya no recibe el rol "Corporate" ni queda marcado corporativo."""
        header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').decode().rstrip("=")
        payload = base64.urlsafe_b64encode(json.dumps({
            "preferred_username": "empresa_sso", "email": "empresa@test.com",
            "realm_access": {"roles": ["corporate", "empresa", "jefe"]},
        }).encode()).decode().rstrip("=")
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"access_token": f"{header}.{payload}.sig"}
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"preferred_username": "empresa_sso", "email": "empresa@test.com"}

        self.client.get("/auth/callback/?code=codigo")

        perfil = User.objects.get(username="empresa_sso").profile
        self.assertIsNone(perfil.role)
        self.assertFalse(perfil.is_corporate)
        self.assertFalse(Role.objects.filter(name="Corporate").exists())
