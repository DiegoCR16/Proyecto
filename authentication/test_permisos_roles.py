# -*- coding: utf-8 -*-
"""Pruebas unitarias del control de acceso por permisos de cada rol (RBAC)."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from authentication.models import AuditLog, Cliente, Permission, Role, UserProfile, UsuarioClienteRelacion
from authentication.permisos import (
    ROL_ANALISTA_CORP, ROL_CAJERO, ROL_CLIENTE, ROL_JEFE, ROL_OPERADOR,
    asegurar_catalogo_permisos, permisos_de, rol_efectivo,
)


class BasePermisosTest(TestCase):
    """Crea un administrador y utilidades para armar usuarios de cada rol."""

    def setUp(self):
        """Prepara el catálogo de permisos y un administrador."""
        asegurar_catalogo_permisos()
        self.admin = self.usuario("admin_rbac", "Admin")

    def usuario(self, username, rol=None):
        """Crea un usuario con perfil y, opcionalmente, un rol fijo."""
        user = User.objects.create_user(username=username, password="password123")
        role = Role.objects.get_or_create(name=rol)[0] if rol else None
        UserProfile.objects.create(user=user, role=role)
        return user

    def miembro_de_cliente(self, username, rol_en_cliente, tipo_cliente="JURIDICA"):
        """Crea un usuario asociado a un cliente con el rol indicado (OPERADOR, ANALISTA, CLIENTE)."""
        user = self.usuario(username, "Cliente")
        cliente = Cliente.objects.create(
            nombre_o_razon_social=f"Empresa {username}", documento_identidad=f"80{user.id:05d}",
            email=f"{username}@test.com", tipo_cliente=tipo_cliente,
        )
        UsuarioClienteRelacion.objects.create(keycloak_user_id=str(user.id), cliente=cliente, rol_en_cliente=rol_en_cliente)
        return user

    def configurar_rol(self, nombre_rol, codenames):
        """Guarda los permisos de un rol desde la pantalla de administración."""
        rol = Role.objects.get_or_create(name=nombre_rol)[0]
        ids = [str(i) for i in Permission.objects.filter(codename__in=codenames).values_list("id", flat=True)]
        self.client.force_login(self.admin)
        return self.client.post(reverse("admin_roles"), {
            "action": "edit", "role_id": rol.id, "description": rol.description or "", "permissions": ids,
        }, follow=True)

    def mensajes(self, response):
        """Textos de las notificaciones de la respuesta."""
        return " ".join(str(m) for m in response.context["messages"])


class RolEfectivoTests(BasePermisosTest):
    """Se identifica correctamente con qué rol opera cada usuario."""

    def test_roles_de_cliente_segun_relacion(self):
        """Operador, Analista y dueño (Jefe/Cliente) se resuelven por su rol en el cliente."""
        self.assertEqual(rol_efectivo(self.miembro_de_cliente("op", "OPERADOR")), ROL_OPERADOR)
        self.assertEqual(rol_efectivo(self.miembro_de_cliente("an", "ANALISTA")), ROL_ANALISTA_CORP)
        self.assertEqual(rol_efectivo(self.miembro_de_cliente("jefe", "ADMIN")), ROL_JEFE)
        self.assertEqual(rol_efectivo(self.miembro_de_cliente("fis", "CLIENTE", "FISICA")), ROL_CLIENTE)

    def test_cajero_por_rol_o_por_asignacion_de_caja(self):
        """Es cajero quien tiene el rol Cajero o fue asignado a una caja."""
        from caja.models import Caja
        self.assertEqual(rol_efectivo(self.usuario("caj", "Cajero")), ROL_CAJERO)
        asignado = self.usuario("asignado")
        Caja.objects.create(codigo="CX1", nombre="Caja X", activa=True).cajeros_asignados.add(asignado)
        self.assertEqual(rol_efectivo(asignado), ROL_CAJERO)


class PermisosPorDefectoTests(BasePermisosTest):
    """Permisos por defecto de cada rol mientras el administrador no los cambie."""

    def test_cliente_opera_y_gestiona_cuentas(self):
        """El cliente compra/vende, ve su historial y gestiona sus medios de acreditación."""
        permisos = permisos_de(self.miembro_de_cliente("fis2", "CLIENTE", "FISICA"))
        self.assertTrue({"can_perform_exchange", "can_view_history", "can_manage_accreditation"} <= permisos)
        self.assertFalse(permisos & {"can_manage_users", "can_operate_cashier"})

    def test_operador_opera_pero_no_gestiona_cuentas(self):
        """El operador del cliente compra/vende pero no administra los medios de acreditación."""
        permisos = permisos_de(self.miembro_de_cliente("op2", "OPERADOR"))
        self.assertIn("can_perform_exchange", permisos)
        self.assertNotIn("can_manage_accreditation", permisos)

    def test_analista_de_cliente_no_opera(self):
        """El analista del cliente consulta historial y alertas, pero no compra ni vende."""
        permisos = permisos_de(self.miembro_de_cliente("an2", "ANALISTA"))
        self.assertTrue({"can_view_history", "can_manage_alerts"} <= permisos)
        self.assertNotIn("can_perform_exchange", permisos)

    def test_cajero_y_analista_del_sistema(self):
        """El cajero opera la caja (no la interfaz de cliente) y el analista ve la auditoría."""
        cajero = permisos_de(self.usuario("caj2", "Cajero"))
        self.assertIn("can_operate_cashier", cajero)
        self.assertNotIn("can_perform_exchange", cajero)
        self.assertIn("can_view_audit", permisos_de(self.usuario("ana_sis", "Analista")))


class AplicacionDePermisosTests(BasePermisosTest):
    """Las vistas obedecen los permisos y la configuración del administrador."""

    def test_cajero_no_entra_a_compra_de_cliente(self):
        """Sin el permiso, la vista devuelve al panel con una notificación y queda en auditoría."""
        cajero = self.usuario("caj3", "Cajero")
        self.client.force_login(cajero)
        response = self.client.get(reverse("procesamiento_operaciones:currency_purchase"), follow=True)
        self.assertRedirects(response, reverse("dashboard_redirect"))
        self.assertIn("Realizar Transacciones", self.mensajes(response))
        self.assertTrue(AuditLog.objects.filter(user=cajero, action="RBAC_ACCESS_DENIED").exists())

    def test_cliente_no_entra_a_ventanilla(self):
        """Un cliente no puede abrir el POS de ventanilla del cajero."""
        self.client.force_login(self.miembro_de_cliente("fis3", "CLIENTE", "FISICA"))
        response = self.client.get(reverse("caja:ventanilla_pos"), follow=True)
        self.assertIn("Operar Caja y Ventanilla", self.mensajes(response))

    def test_operador_no_gestiona_medios_de_acreditacion(self):
        """El operador es rechazado en los medios de acreditación del cliente."""
        self.client.force_login(self.miembro_de_cliente("op3", "OPERADOR"))
        response = self.client.get(reverse("client_acreditation_methods"), follow=True)
        self.assertIn("Gestionar Medios de Acreditación", self.mensajes(response))

    def test_quitar_permiso_al_cliente_bloquea_la_compra(self):
        """Si el administrador quita «Realizar Transacciones» al rol Cliente, el cliente ya no compra."""
        restantes = ["can_view_history", "can_manage_alerts", "can_simulate", "can_view_rates", "can_manage_accreditation"]
        self.configurar_rol("Cliente", restantes)
        cliente = self.miembro_de_cliente("fis4", "CLIENTE", "FISICA")
        self.client.force_login(cliente)
        antes = self.client.get(reverse("dashboard_redirect"))
        self.assertNotContains(antes, "Iniciar Compra")
        response = self.client.get(reverse("procesamiento_operaciones:currency_purchase"), follow=True)
        self.assertIn("Acceso denegado", self.mensajes(response))
        self.assertIn("Realizar Transacciones", self.mensajes(response))

    def test_delegar_permiso_al_analista_habilita_el_modulo(self):
        """Si el administrador delega «Gestionar Tasas» al Analista, este accede y ve la tarjeta."""
        analista = self.usuario("ana2", "Analista")
        self.client.force_login(analista)
        self.assertEqual(self.client.get(reverse("tasas_cambio:rates_manager")).status_code, 302)
        self.assertNotContains(self.client.get(reverse("dashboard_redirect")), "Gestor de Cotizaciones")

        self.configurar_rol("Analista", ["can_view_audit", "can_view_rates", "can_manage_rates"])
        self.client.force_login(analista)
        self.assertEqual(self.client.get(reverse("tasas_cambio:rates_manager")).status_code, 200)
        self.assertContains(self.client.get(reverse("dashboard_redirect")), "Gestor de Cotizaciones")

    def test_analista_consulta_auditoria(self):
        """El analista tiene «Ver Auditoría» y puede abrir el registro (antes solo el admin)."""
        self.client.force_login(self.usuario("ana3", "Analista"))
        self.assertEqual(self.client.get(reverse("admin_audit_logs")).status_code, 200)


class PantallaDeRolesTests(BasePermisosTest):
    """Reglas de la pantalla de administración de roles."""

    def test_roles_sin_configurar_muestran_sus_permisos_por_defecto(self):
        """Al abrir la pantalla, cada rol muestra sus permisos vigentes."""
        self.client.force_login(self.admin)
        self.client.get(reverse("admin_roles"))
        operador = Role.objects.get(name="Operador (Corporativo)")
        self.assertTrue(operador.permisos_configurados)
        self.assertIn("can_perform_exchange", operador.permissions.values_list("codename", flat=True))

    def test_rol_de_cliente_no_recibe_permisos_administrativos(self):
        """Un rol de cliente no puede recibir permisos de administración ni de caja."""
        response = self.configurar_rol("Operador (Corporativo)", ["can_perform_exchange", "can_operate_cashier"])
        self.assertContains(response, "no puede tener asignados permisos administrativos")

    def test_no_puede_modificar_su_propio_rol(self):
        """Un analista con «Gestionar Roles» no puede ampliar los permisos de su propio rol."""
        self.configurar_rol("Analista", ["can_manage_roles", "can_view_audit"])
        analista = self.usuario("ana4", "Analista")
        self.client.force_login(analista)
        rol = Role.objects.get(name="Analista")
        response = self.client.post(reverse("admin_roles"), {
            "action": "edit", "role_id": rol.id, "description": "", "permissions": [
                str(p.id) for p in Permission.objects.all()
            ],
        })
        self.assertContains(response, "No puedes modificar los permisos de tu propio rol")

    def test_admin_no_ve_la_tarjeta_de_simulacion(self):
        """El panel de administrador ya no muestra la tarjeta del simulador de divisas."""
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard_redirect"))
        self.assertNotContains(response, "Simulación de Tasas")
        self.assertNotContains(response, reverse("tasas_cambio:simulator"))
