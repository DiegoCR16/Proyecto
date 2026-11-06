# -*- coding: utf-8 -*-
"""
Pruebas unitarias de la eliminación completa de un Cliente por parte del Administrador
(base de datos local + Keycloak + reseteo del perfil de los usuarios asociados).
"""
from urllib.parse import unquote
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client
from django.contrib.auth.models import User
from authentication.models import (
    UserProfile, Role, CorporateGroup, GroupMembership, Cliente,
    UsuarioClienteRelacion, ClientRegistrationRequest, AuditLog,
)


def _resp(status, json_data=None):
    """Crea una respuesta HTTP simulada."""
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data if json_data is not None else {}
    return r


def _fake_get(groups=None):
    """GET simulado: devuelve el rol Cliente para role-mappings y ``groups`` para la búsqueda de grupos."""
    def _get(url, *args, **kwargs):
        if url.endswith('/role-mappings/realm'):
            return _resp(200, [{'id': 'r-cli', 'name': 'Cliente'}, {'id': 'r-off', 'name': 'offline_access'}])
        return _resp(200, groups or [])
    return _get


class EliminarClienteTests(TestCase):
    """Valida que eliminar un cliente lo borre también en Keycloak y libere a sus usuarios."""

    def setUp(self):
        self.http = Client()
        admin_role, _ = Role.objects.get_or_create(name="Admin")
        cliente_role, _ = Role.objects.get_or_create(name="Cliente")

        self.admin = User.objects.create_user(username="admin_del", password="Password123*")
        UserProfile.objects.create(user=self.admin, role=admin_role)

        self.owner = User.objects.create_user(username="duenio", password="Password123*")
        self.owner_profile = UserProfile.objects.create(
            user=self.owner, role=cliente_role, keycloak_id="kc-owner", is_corporate=True
        )

        self.cliente_role = cliente_role
        self.cliente = Cliente.objects.create(
            nombre_o_razon_social="Empresa Borrar SA", tipo_cliente="JURIDICO",
            documento_identidad="80099999-1", email="borrar@ge.com",
        )
        self.group = CorporateGroup.objects.create(
            juridica_profile=self.owner_profile, group_name="Empresa Borrar SA", keycloak_group_id="kc-group-1"
        )
        GroupMembership.objects.create(corporate_group=self.group, fisica_profile=self.owner_profile, role_in_group='CLIENTE')
        UsuarioClienteRelacion.objects.create(keycloak_user_id="kc-owner", cliente=self.cliente, rol_en_cliente='ADMIN')
        ClientRegistrationRequest.objects.create(
            user=self.owner, client_name="Empresa Borrar SA", ci_ruc="80099999-1",
            client_type="JURIDICO", email="borrar@ge.com", corporate_group=self.group, status='APPROVED'
        )

        self.http.login(username="admin_del", password="Password123*")
        self.url = f'/auth/admin/clients/{self.cliente.id}/'

    def _post_delete(self):
        return self.http.post(self.url, {'action': 'delete_client'})

    @patch('gestion_clientes.views.requests.delete')
    @patch('gestion_clientes.views.requests.get')
    @patch('gestion_clientes.views.requests.post')
    def test_elimina_en_keycloak_y_localmente(self, mock_post, mock_get, mock_delete):
        """El grupo y los roles del cliente se eliminan en Keycloak y los datos locales desaparecen."""
        mock_post.return_value = _resp(200, {'access_token': 'tok'})
        mock_get.side_effect = _fake_get([{'id': 'kc-group-1', 'name': 'Empresa Borrar SA'}])
        mock_delete.return_value = _resp(204)

        response = self._post_delete()

        self.assertEqual(response.status_code, 302)
        deleted_urls = [c.args[0] for c in mock_delete.call_args_list]
        self.assertTrue(any(u.endswith('/groups/kc-group-1') for u in deleted_urls))
        self.assertTrue(any('Empresa%20Borrar%20SA_CLIENTE' in u for u in deleted_urls))
        self.assertFalse(Cliente.objects.filter(id=self.cliente.id).exists())
        self.assertFalse(CorporateGroup.objects.filter(id=self.group.id).exists())
        self.assertFalse(UsuarioClienteRelacion.objects.filter(keycloak_user_id="kc-owner").exists())
        self.assertFalse(ClientRegistrationRequest.objects.filter(ci_ruc="80099999-1").exists())
        self.assertTrue(AuditLog.objects.filter(action="DELETE_CLIENT").exists())

    @patch('gestion_clientes.views.requests.delete')
    @patch('gestion_clientes.views.requests.get')
    @patch('gestion_clientes.views.requests.post')
    def test_usuario_sin_otro_cliente_vuelve_a_usuario_regular(self, mock_post, mock_get, mock_delete):
        """Si el usuario no tiene otro cliente, deja de ser corporativo/cliente."""
        mock_post.return_value = _resp(200, {'access_token': 'tok'})
        mock_get.side_effect = _fake_get()
        mock_delete.return_value = _resp(204)

        self._post_delete()

        self.owner_profile.refresh_from_db()
        self.assertFalse(self.owner_profile.is_corporate)
        self.assertIsNone(self.owner_profile.role)
        self.assertFalse(GroupMembership.objects.filter(fisica_profile=self.owner_profile).exists())
        role_removals = [c for c in mock_delete.call_args_list if c.args[0].endswith('/users/kc-owner/role-mappings/realm')]
        self.assertEqual(len(role_removals), 1)
        self.assertEqual([r['name'] for r in role_removals[0].kwargs['json']], ['Cliente'])

    @patch('gestion_clientes.views.requests.delete')
    @patch('gestion_clientes.views.requests.get')
    @patch('gestion_clientes.views.requests.post')
    def test_usuario_con_otro_cliente_lo_conserva(self, mock_post, mock_get, mock_delete):
        """Si el usuario tiene otro cliente asociado, mantiene ese cliente."""
        otro = Cliente.objects.create(
            nombre_o_razon_social="Otra Empresa", tipo_cliente="JURIDICO",
            documento_identidad="80011111-2", email="otra@ge.com",
        )
        UsuarioClienteRelacion.objects.create(keycloak_user_id="kc-owner", cliente=otro, rol_en_cliente='ADMIN')
        mock_post.return_value = _resp(200, {'access_token': 'tok'})
        mock_get.side_effect = _fake_get()
        mock_delete.return_value = _resp(204)

        self._post_delete()

        self.owner_profile.refresh_from_db()
        self.assertTrue(self.owner_profile.is_corporate)
        self.assertEqual(self.owner_profile.role, self.cliente_role)
        self.assertFalse(any(c.args[0].endswith('/role-mappings/realm') for c in mock_delete.call_args_list))
        self.assertTrue(UsuarioClienteRelacion.objects.filter(keycloak_user_id="kc-owner", cliente=otro).exists())

    @patch('gestion_clientes.views.requests.post')
    def test_falla_keycloak_no_borra_nada(self, mock_post):
        """Si Keycloak no responde, no se elimina nada localmente para evitar inconsistencias."""
        mock_post.return_value = _resp(401)

        response = self._post_delete()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Cliente.objects.filter(id=self.cliente.id).exists())
        self.assertTrue(CorporateGroup.objects.filter(id=self.group.id).exists())


class AprobacionYLoginRolTests(TestCase):
    """Valida que el rol Cliente solo se asigna al aprobar una solicitud y que el login no asigna rol por defecto."""

    def setUp(self):
        self.http = Client()
        admin_role, _ = Role.objects.get_or_create(name="Admin")
        self.admin = User.objects.create_user(username="admin_apr", password="Password123*")
        UserProfile.objects.create(user=self.admin, role=admin_role)
        self.solicitante = User.objects.create_user(username="solicitante", password="Password123*")
        self.sol_profile = UserProfile.objects.create(user=self.solicitante, keycloak_id="kc-sol")
        self.req = ClientRegistrationRequest.objects.create(
            user=self.solicitante, client_name="Nuevo Cliente SA", ci_ruc="80077777-3",
            client_type="JURIDICO", email="nuevo@ge.com", status='PENDING'
        )

    @patch('gestion_clientes.views.requests.put')
    @patch('gestion_clientes.views.requests.get')
    @patch('gestion_clientes.views.requests.post')
    def test_aprobacion_asigna_rol_cliente(self, mock_post, mock_get, mock_put):
        """Al aprobar, el usuario recibe el rol Cliente local y en Keycloak."""
        mock_post.return_value = _resp(200, {'access_token': 'tok'})
        mock_put.return_value = _resp(204)

        def _get(url, *args, **kwargs):
            if url.endswith('/groups'):
                return _resp(200, [{'id': 'kc-new', 'name': 'Nuevo Cliente SA'}])
            if '/roles/' in url:
                name = unquote(url.rsplit('/roles/', 1)[1])
                return _resp(200, {'id': f'id-{name}', 'name': name})
            return _resp(200, [])
        mock_get.side_effect = _get

        self.http.login(username="admin_apr", password="Password123*")
        self.http.get(f'/auth/admin/approve-client/{self.req.id}/')

        self.sol_profile.refresh_from_db()
        self.assertIsNotNone(self.sol_profile.role)
        self.assertEqual(self.sol_profile.role.name, "Cliente")
        role_assign = [c for c in mock_post.call_args_list if c.args[0].endswith('/users/kc-sol/role-mappings/realm')]
        self.assertEqual(len(role_assign), 1)
        names = [r['name'] for r in role_assign[0].kwargs['json']]
        self.assertIn('Cliente', names)
        self.assertIn('Nuevo Cliente SA_CLIENTE', names)
