# -*- coding: utf-8 -*-
"""Pruebas unitarias de la solicitud de anulación de facturas desde la ventanilla del cajero (PSE-40)."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from authentication.models import Cliente, Role, UserProfile
from authentication.permisos import PERMISOS_POR_DEFECTO, ROL_CAJERO, asegurar_catalogo_permisos
from caja.models import Caja, TurnoCaja, inicializar_denominaciones_default
from procesamiento_operaciones import facturacion
from procesamiento_operaciones.facturacion import FacturacionError
from procesamiento_operaciones.models import (
    AnulacionFactura, CurrencyPurchaseTransaction, SolicitudAnulacionFactura,
)

CDC_FACTURA = "1" * 44
FACTURA_APROBADA = f"SIFEN CDC: {CDC_FACTURA} | SIFEN Estado: Emitida | SIFEN Estado: Aprobada"


class AnulacionDesdeCajaTests(TestCase):
    """El cajero pide la anulación de facturas de ventanilla; el Administrador la resuelve sin devolución."""

    def setUp(self):
        """Crea cajero con turno abierto, administrador, cliente y una compra de ventanilla facturada."""
        inicializar_denominaciones_default()
        asegurar_catalogo_permisos()
        rol_cajero, _ = Role.objects.get_or_create(name=ROL_CAJERO)
        from authentication.models import Permission as PermisoRol
        rol_cajero.permissions.set(PermisoRol.objects.filter(codename__in=PERMISOS_POR_DEFECTO[ROL_CAJERO]))
        self.cajero = User.objects.create_user(username="cajero_anula", password="password123")
        UserProfile.objects.create(user=self.cajero, role=rol_cajero)
        self.admin = User.objects.create_user(username="admin_anula", password="password123")
        UserProfile.objects.create(user=self.admin, role=Role.objects.get_or_create(name="Admin")[0])
        caja = Caja.objects.create(nombre="Caja Anulación", codigo="CA01", activa=True)
        caja.cajeros_asignados.add(self.cajero)
        TurnoCaja.objects.create(
            caja=caja, cajero=self.cajero, saldo_inicial_pyg=Decimal("1000000"),
            saldo_inicial_usd=Decimal("100"), estado="ABIERTO",
        )
        self.cliente = Cliente.objects.create(
            nombre_o_razon_social="Cliente Ventanilla", documento_identidad="7654321",
            email="ventanilla@test.com", tipo_cliente="FISICA",
        )
        self.tx = self._compra()
        self.client.login(username="cajero_anula", password="password123")

    def _compra(self, metodo="EFECTIVO_VENTANILLA", marcas=FACTURA_APROBADA):
        """Crea una compra del cliente con el medio de pago y el desglose indicados."""
        return CurrencyPurchaseTransaction.objects.create(
            user=self.cajero, cliente=self.cliente, from_currency="USD", to_currency="PYG",
            amount=Decimal("100"), converted_amount=Decimal("730000"), applied_rate=Decimal("7300"),
            standard_rate=Decimal("7300"), total_pyg=Decimal("730000"), status="SUCCESS",
            payment_method_type=metodo, transparent_breakdown=f"Ventanilla Comprobante: V-1 | {marcas}",
        )

    def _solicitar(self, tx=None, motivo="El cliente se equivocó de monto"):
        """Envía la solicitud de anulación desde la ventanilla."""
        tx = tx or self.tx
        return self.client.post(
            reverse("caja:solicitar_anulacion_factura"),
            {"tipo": "COMPRA", "id": tx.pk, "motivo": motivo, "documento_busqueda": "7654321"},
            follow=True,
        )

    def test_cajero_registra_solicitud_sin_devolucion(self):
        """La solicitud queda pendiente, sin devolución de fondos y a nombre del cajero."""
        respuesta = self._solicitar()
        solicitud = SolicitudAnulacionFactura.objects.get()
        self.assertEqual(solicitud.estado, SolicitudAnulacionFactura.PENDIENTE)
        self.assertFalse(solicitud.con_devolucion)
        self.assertEqual(solicitud.solicitante, self.cajero)
        self.assertEqual(solicitud.cliente, self.cliente)
        self.assertIn("enviada al Administrador", respuesta.content.decode())

    def test_no_permite_operaciones_en_linea(self):
        """Desde la caja no se piden anulaciones de operaciones hechas en la web."""
        web = self._compra(metodo="TRANSFERENCIA")
        respuesta = self._solicitar(web)
        self.assertFalse(SolicitudAnulacionFactura.objects.exists())
        self.assertIn("solo se anulan facturas de operaciones de ventanilla", respuesta.content.decode())

    def test_no_duplica_solicitud_pendiente(self):
        """Una segunda solicitud para la misma factura se rechaza."""
        self._solicitar()
        self._solicitar()
        self.assertEqual(SolicitudAnulacionFactura.objects.count(), 1)

    def test_motivo_obligatorio(self):
        """Sin motivo no se registra la solicitud."""
        self._solicitar(motivo="")
        self.assertFalse(SolicitudAnulacionFactura.objects.exists())

    def test_factura_no_aprobada(self):
        """Solo se pide la anulación de facturas aprobadas por SIFEN."""
        with self.assertRaises(FacturacionError):
            facturacion.solicitar_anulacion_en_caja(
                self._compra(marcas=f"SIFEN CDC: {CDC_FACTURA} | SIFEN Estado: Emitida"), self.cajero, "Motivo válido",
            )

    def test_usuario_sin_permiso_de_caja(self):
        """Un cliente no puede usar la ruta del cajero."""
        cliente_web = User.objects.create_user(username="cliente_web", password="password123")
        UserProfile.objects.create(user=cliente_web, role=Role.objects.get_or_create(name="Cliente")[0])
        self.client.login(username="cliente_web", password="password123")
        self._solicitar()
        self.assertFalse(SolicitudAnulacionFactura.objects.exists())

    def test_ventanilla_muestra_facturas_del_cliente(self):
        """Al buscar al cliente, el POS lista sus facturas de ventanilla con la opción de anular."""
        respuesta = self.client.get(reverse("caja:ventanilla_pos"), {"documento_busqueda": "7654321"})
        facturas = respuesta.context["facturas_cliente"]
        self.assertEqual([tx.pk for tx in facturas], [self.tx.pk])
        self.assertTrue(facturas[0].anulable)
        self.assertContains(respuesta, "Solicitar anulación")

    @patch("procesamiento_operaciones.facturacion.cancelar_factura")
    def test_aprobacion_anula_sin_devolver_fondos(self, cancelar):
        """Al aprobar la solicitud del cajero, la factura se anula sin devolución de fondos."""
        cancelar.return_value = AnulacionFactura(tipo=AnulacionFactura.CANCELACION)
        solicitud = facturacion.solicitar_anulacion_en_caja(self.tx, self.cajero, "Error en el monto")
        facturacion.aprobar_solicitud(solicitud, self.admin, "auto")
        self.assertFalse(cancelar.call_args.args[4])  # devolucion
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, SolicitudAnulacionFactura.APROBADA)
        self.assertNotIn("fondos", solicitud.respuesta)
