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

    def test_admin_approve_member_request_no_double_assignment(self):
        """
        Valida que al aprobar una solicitud de miembro, se cree una única relación UsuarioClienteRelacion (sin doble asignación).
        """
        from authentication.models import MemberRequest, UsuarioClienteRelacion
        corp_group = CorporateGroup.objects.create(
            juridica_profile=self.admin_profile,
            group_name=self.cliente.nombre_o_razon_social
        )
        target_user = User.objects.create_user(username="jorge", email="jorge@gmail.com", password="Password123*")
        target_profile = UserProfile.objects.create(
            user=target_user,
            role=self.client_role,
            keycloak_id="kc-jorge-uuid-123"
        )
        mem_req = MemberRequest.objects.create(
            corporate_group=corp_group,
            requester=self.admin_user,
            target_email="jorge@gmail.com",
            role_requested="ANALISTA",
            status="PENDING"
        )

        self.client.force_login(self.admin_user)
        approve_url = f'/auth/admin/approve-member/{mem_req.id}/'
        response = self.client.get(approve_url)

        relaciones = UsuarioClienteRelacion.objects.filter(cliente=self.cliente)
        self.assertEqual(relaciones.count(), 1)
        rel = relaciones.first()
        self.assertEqual(rel.keycloak_user_id, "kc-jorge-uuid-123")
        self.assertEqual(rel.rol_en_cliente, "ANALISTA")
