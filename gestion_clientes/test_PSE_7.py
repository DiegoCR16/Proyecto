# -*- coding: utf-8 -*-
from django.test import TestCase, Client
from django.contrib.auth.models import User
from unittest.mock import patch
from decimal import Decimal
from authentication.models import UserProfile, AuditLog, Role, CorporateGroup, GroupMembership, Cliente

class AssociationKeycloakPSE7Tests(TestCase):
    """
    Suite de pruebas unitarias para la ficha de cliente basada en el modelo Cliente.
    """

    def setUp(self):
        self.client = Client()
        self.admin_role, _ = Role.objects.get_or_create(name="Admin")
        self.client_role, _ = Role.objects.get_or_create(name="Cliente")
        self.corp_role, _ = Role.objects.get_or_create(name="Corporate")

        # Administrador del Sistema
        self.admin_user = User.objects.create_user(username="admin_pse7", email="admin7@globalexchange.com", password="Password123*")
        self.admin_profile = UserProfile.objects.create(
            user=self.admin_user,
            role=self.admin_role,
            is_corporate=True
        )

        # Cliente (Entidad de Base de Datos)
        self.cliente = Cliente.objects.create(
            nombre_o_razon_social="Empresa SA",
            tipo_cliente="JURIDICO",
            documento_identidad="80012345-6",
            email="empresa@globalexchange.com",
            categoria="CORPORATIVO"
        )

        self.detail_url = f'/auth/admin/clients/{self.cliente.id}/'

    def test_operational_transaction_blocking(self):
        """
        Valida el bloqueo operativo transaccional.
        """
        unlinked_user = User.objects.create_user(username="sin_asociar", email="sin@globalexchange.com", password="Password123*")
        unlinked_profile = UserProfile.objects.create(
            user=unlinked_user,
            role=self.client_role,
            keycloak_id=None
        )

        self.assertFalse(unlinked_profile.has_active_client_association())

        with self.assertRaises(PermissionError):
            unlinked_profile.perform_transaction(Decimal('500000.00'))

        unlinked_profile.keycloak_id = 'kc-active-uuid-777'
        unlinked_profile.save()

        self.assertTrue(unlinked_profile.has_active_client_association())
        success = unlinked_profile.perform_transaction(Decimal('500000.00'))
        self.assertTrue(success)
        self.assertEqual(float(unlinked_profile.transaction_volume), 500000.00)

    def test_admin_client_detail_template_render(self):
        """
        Valida que la ficha de detalle del cliente renderice correctamente con la plantilla `admin_client_detail.html`.
        """
        self.client.force_login(self.admin_user)

        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'gestion_clientes/admin_client_detail.html')
        self.assertContains(response, "Empresa SA")
        self.assertContains(response, "80012345-6")
