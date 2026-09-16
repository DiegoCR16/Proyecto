# -*- coding: utf-8 -*-
from django.test import TestCase, Client
from django.contrib.auth.models import User
from authentication.models import UserProfile, AuditLog, Role, Cliente

class SegmentationPSE6Tests(TestCase):
    """
    Suite de pruebas unitarias para la segmentación y gestión de clientes basada en el modelo Cliente.
    """

    def setUp(self):
        self.client = Client()
        self.admin_role, _ = Role.objects.get_or_create(name="Admin")
        self.admin_user = User.objects.create_user(username="admin_pse6", email="admin@globalexchange.com", password="Password123*")
        self.admin_profile = UserProfile.objects.create(
            user=self.admin_user,
            role=self.admin_role
        )

        self.cliente_fisico = Cliente.objects.create(
            nombre_o_razon_social="Juan Pérez",
            tipo_cliente="FISICO",
            documento_identidad="1234567",
            email="juan@globalexchange.com",
            categoria="MINORISTA"
        )
        self.cliente_juridico = Cliente.objects.create(
            nombre_o_razon_social="Empresa SA",
            tipo_cliente="JURIDICO",
            documento_identidad="80011122-3",
            email="empresa@globalexchange.com",
            categoria="CORPORATIVO"
        )

        self.list_url = '/auth/admin/clients/'
        self.detail_url = f'/auth/admin/clients/{self.cliente_fisico.id}/'

    def test_default_category_is_minorista(self):
        """Verifica que por defecto la categoría inicial de un cliente sea Minorista."""
        nuevo_cliente = Cliente.objects.create(
            nombre_o_razon_social="Nuevo Cliente",
            tipo_cliente="FISICO",
            documento_identidad="999888",
            email="nuevo@globalexchange.com"
        )
        self.assertEqual(nuevo_cliente.categoria, 'MINORISTA')

    def test_admin_client_list_view_access_and_filtering(self):
        """Verifica que el administrador pueda acceder al listado de clientes y filtrar por categoría."""
        self.client.force_login(self.admin_user)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Juan Pérez")
        self.assertContains(response, "Empresa SA")

        response_corp = self.client.get(self.list_url, {'category': 'CORPORATIVO'})
        self.assertEqual(response_corp.status_code, 200)
        self.assertContains(response_corp, "Empresa SA")
        self.assertNotContains(response_corp, "Juan Pérez")

    def test_admin_client_update_category_and_audit_log(self):
        """Verifica que el administrador pueda actualizar la categoría de un cliente y registrar auditoría."""
        self.client.force_login(self.admin_user)
        response = self.client.post(self.detail_url, {
            'action': 'update_category',
            'category': 'VIP'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Categoría del cliente actualizada exitosamente")

        self.cliente_fisico.refresh_from_db()
        self.assertEqual(self.cliente_fisico.categoria, 'VIP')

        audit_entry = AuditLog.objects.filter(action="UPDATE_CLIENT_CATEGORY", user=self.admin_user).first()
        self.assertIsNotNone(audit_entry)
        self.assertIn("VIP", audit_entry.details)
