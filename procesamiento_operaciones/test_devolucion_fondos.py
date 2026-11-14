# -*- coding: utf-8 -*-
"""Pruebas unitarias de los saldos: caja del negocio en la venta y devolución de fondos al anular."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse

from authentication.models import ClientAccreditationMethod, Cliente, Role, UserProfile, UsuarioClienteRelacion
from procesamiento_operaciones import facturacion
from procesamiento_operaciones.facturacion import FacturacionError
from procesamiento_operaciones.models import (
    AnulacionFactura, CurrencyPurchaseTransaction, MovimientoSaldoOperacion, SolicitudAnulacionFactura,
)
from procesamiento_operaciones.views import CurrencyPurchaseService, CurrencySaleService
from tasas_cambio.models import ExchangeRate, PaymentMethod
from tasas_cambio.views import ensure_default_benefit_rules

CDC = "7" * 44
CDC_NC = "8" * 44
SERVICIO = "procesamiento_operaciones.facturacion.FacturaSeguraService"
APROBADA = f" | SIFEN CDC: {CDC} | SIFEN Estado: Emitida | SIFEN Estado: Aprobada"


class BaseSaldosTest(TestCase):
    """Cliente con una cuenta en guaraníes y otra en dólares, y la caja del negocio."""

    def setUp(self):
        """Prepara tasas, caja del negocio, cliente, cuentas y usuarios."""
        ExchangeRate.objects.create(currency_code="USD", currency_name="Dólar", buy_rate=Decimal("7300"), sell_rate=Decimal("7450"))
        ExchangeRate.objects.create(currency_code="PYG", currency_name="Guaraní", buy_rate=Decimal("1"), sell_rate=Decimal("1"))
        ensure_default_benefit_rules()
        self.caja = PaymentMethod.objects.create(code="TRF_GX", name="Transferencia GX", method_type="TRANSFERENCIA",
                                                 balance=Decimal("100000000.00"), is_active=True)
        self.cliente = Cliente.objects.create(nombre_o_razon_social="Cliente Saldos", tipo_cliente="FISICA",
                                              documento_identidad="3334445", email="saldos@test.com", categoria="MINORISTA")
        self.cuenta_pyg = ClientAccreditationMethod.objects.create(
            cliente=self.cliente, tipo_medio="CUENTA_BANCARIA", entidad_financiera="Banco PYG", numero_cuenta="1",
            tipo_cuenta="AHORRO", moneda="PYG", balance=Decimal("10000000.00"), titularidad="Cliente Saldos", estado="VERIFICADO")
        self.cuenta_usd = ClientAccreditationMethod.objects.create(
            cliente=self.cliente, tipo_medio="CUENTA_BANCARIA", entidad_financiera="Banco USD", numero_cuenta="2",
            tipo_cuenta="AHORRO", moneda="USD", balance=Decimal("500.00"), titularidad="Cliente Saldos", estado="VERIFICADO")
        self.usuario = User.objects.create_user(username="cli_saldos", password="password123", email="cli@test.com")
        UserProfile.objects.create(user=self.usuario, role=Role.objects.get_or_create(name="Cliente")[0])
        UsuarioClienteRelacion.objects.create(keycloak_user_id=str(self.usuario.id), cliente=self.cliente, rol_en_cliente="CLIENTE")
        self.admin = User.objects.create_user(username="adm_saldos", password="password123")
        UserProfile.objects.create(user=self.admin, role=Role.objects.get_or_create(name="Admin")[0])

    def request(self):
        """Solicitud con el cliente activo en la sesión."""
        req = RequestFactory().get("/")
        req.user = self.usuario
        req.session = {"active_client_id": str(self.cliente.id)}
        return req

    def comprar_usd(self, monto_pyg="730000"):
        """Compra USD pagando desde la cuenta en guaraníes, y deja la factura aprobada."""
        tx = CurrencyPurchaseService.process_purchase(
            user=self.usuario, from_currency="PYG", to_currency="USD", amount=Decimal(monto_pyg),
            payment_method_id_or_code=self.caja.code, origin_method_id=self.cuenta_pyg.id,
            destination_method_id=self.cuenta_usd.id, request=self.request(),
        )
        CurrencyPurchaseTransaction.objects.filter(pk=tx.pk).update(transparent_breakdown=f"{tx.transparent_breakdown}{APROBADA}")
        tx.refresh_from_db()
        return tx

    def saldos(self):
        """Saldos actuales: cuenta PYG, cuenta USD, caja del negocio y volumen del cliente."""
        for obj in (self.cuenta_pyg, self.cuenta_usd, self.caja, self.cliente):
            obj.refresh_from_db()
        return self.cuenta_pyg.balance, self.cuenta_usd.balance, self.caja.balance, self.cliente.transaction_volume

    def aprobar_nota(self, servicio):
        """Simula que SIFEN aprueba la nota de crédito emitida y sincroniza."""
        servicio.get_estado_sifen.return_value = {"code": 0, "results": [{"estado_sifen": "Aprobado"}]}
        facturacion.sincronizar_anulaciones()

    def configurar(self, servicio):
        """Respuestas OK de Factura Segura."""
        servicio.obtener_token.return_value = "token"
        servicio.generar_de.return_value = {"code": 0, "results": [{"CDC": CDC_NC}]}
        servicio.descargar_kude.return_value = None


class CajaNegocioEnVentaTests(BaseSaldosTest):
    """En la venta el negocio paga guaraníes: la caja del negocio baja."""

    def vender(self, monto="100"):
        """Vende USD desde la cuenta en dólares y recibe guaraníes en la cuenta en guaraníes."""
        return CurrencySaleService.process_sale(
            user=self.usuario, from_currency="USD", to_currency="PYG", amount=Decimal(monto),
            payment_method_id_or_code=self.caja.code, origin_method_id=self.cuenta_usd.id,
            destination_method_id=self.cuenta_pyg.id, request=self.request(),
        )

    def test_venta_descuenta_la_caja_del_negocio(self):
        """La caja del negocio baja en los guaraníes pagados al cliente (antes subía)."""
        caja_antes = self.caja.balance
        tx = self.vender()
        self.caja.refresh_from_db()
        self.assertEqual(self.caja.balance, caja_antes - tx.total_pyg)

    def test_venta_sin_fondos_en_la_caja_del_negocio(self):
        """Si la caja no alcanza para pagar, la venta se rechaza sin mover las cuentas del cliente."""
        PaymentMethod.objects.filter(pk=self.caja.pk).update(balance=Decimal("1000"))
        usd_antes = self.cuenta_usd.balance
        with self.assertRaisesMessage(ValidationError, "caja del negocio no tiene fondos suficientes"):
            self.vender()
        self.cuenta_usd.refresh_from_db()
        self.assertEqual(self.cuenta_usd.balance, usd_antes)

    def test_venta_registra_sus_movimientos(self):
        """Cada movimiento de saldo de la venta queda registrado con su monto exacto."""
        tx = self.vender()
        tipos = {m.tipo: m.monto for m in MovimientoSaldoOperacion.objects.filter(venta=tx)}
        self.assertEqual(tipos[MovimientoSaldoOperacion.CAJA_NEGOCIO], -tx.total_pyg)
        self.assertIn(MovimientoSaldoOperacion.VOLUMEN_CLIENTE, tipos)


class DevolucionFondosTests(BaseSaldosTest):
    """Anular con devolución revierte exactamente los saldos; anular para corregir no los toca."""

    @patch(SERVICIO)
    def test_nota_de_credito_con_devolucion_revierte_los_saldos(self, servicio):
        """Cuando SIFEN aprueba la nota, los saldos vuelven a los valores previos y la operación queda ANULADA."""
        self.configurar(servicio)
        antes = self.saldos()
        tx = self.comprar_usd()
        self.assertNotEqual(self.saldos(), antes)

        facturacion.emitir_nota_credito(tx, "Devolución pedida", self.admin, devolucion=True)
        self.assertNotEqual(self.saldos(), antes)  # todavía no: espera la aprobación de SIFEN
        self.aprobar_nota(servicio)

        self.assertEqual(self.saldos(), antes)
        tx.refresh_from_db()
        self.assertEqual(tx.status, "ANULADA")
        self.assertEqual(tx.sifen_estado, "Anulada")
        self.assertIsNotNone(AnulacionFactura.objects.get().devuelta_en)
        self.assertEqual(MovimientoSaldoOperacion.objects.filter(compra=tx, es_devolucion=True).count(), 4)

    @patch(SERVICIO)
    def test_cancelacion_con_devolucion_espera_la_confirmacion_de_sifen(self, servicio):
        """La cancelación devuelve los fondos recién cuando SIFEN aprueba el evento."""
        self.configurar(servicio)
        servicio.sol_cancelacion.return_value = {"code": 0, "results": []}
        antes = self.saldos()
        tx = self.comprar_usd()
        facturacion.cancelar_factura(tx, "Devolución pedida", self.admin, devolucion=True)
        servicio.get_estado_sifen.return_value = {"code": 0, "results": [{"estado_sifen": "Aprobado", "estado_can": "Aprobado"}]}
        facturacion.sincronizar_anulaciones()
        self.assertEqual(self.saldos(), antes)

    @patch(SERVICIO)
    def test_solo_corregir_no_toca_los_saldos_y_permite_refacturar(self, servicio):
        """Anular para corregir deja los saldos y la operación vigente, y habilita volver a facturar."""
        self.configurar(servicio)
        tx = self.comprar_usd()
        despues_de_comprar = self.saldos()

        facturacion.emitir_nota_credito(tx, "RUC del cliente equivocado", self.admin, devolucion=False)
        de = servicio.generar_de.call_args[0][1]["DE"]
        self.assertEqual(de["iMotEmi"], "1")
        self.aprobar_nota(servicio)

        self.assertEqual(self.saldos(), despues_de_comprar)
        tx.refresh_from_db()
        self.assertEqual(tx.status, "SUCCESS")
        opciones = facturacion.opciones_factura(tx)
        self.assertTrue(opciones["refacturar"])
        self.assertFalse(opciones["nota_credito"])

    @patch(SERVICIO)
    def test_no_se_devuelve_si_el_cliente_ya_uso_los_fondos(self, servicio):
        """Si la cuenta en dólares ya no tiene lo recibido, no se puede anular con devolución."""
        self.configurar(servicio)
        tx = self.comprar_usd()
        ClientAccreditationMethod.objects.filter(pk=self.cuenta_usd.pk).update(balance=Decimal("1.00"))
        with self.assertRaisesMessage(FacturacionError, "No se pueden devolver los fondos"):
            facturacion.emitir_nota_credito(tx, "Devolución pedida", self.admin, devolucion=True)
        servicio.generar_de.assert_not_called()

    @patch(SERVICIO)
    def test_devolucion_queda_pendiente_si_el_saldo_cambia_antes_de_la_aprobacion(self, servicio):
        """Si al aprobarse la nota el saldo ya no alcanza, no se mueve nada y se puede reintentar."""
        self.configurar(servicio)
        tx = self.comprar_usd()
        facturacion.emitir_nota_credito(tx, "Devolución pedida", self.admin, devolucion=True)
        usd = self.cuenta_usd.balance
        ClientAccreditationMethod.objects.filter(pk=self.cuenta_usd.pk).update(balance=Decimal("1.00"))
        self.aprobar_nota(servicio)

        tx.refresh_from_db()
        self.assertEqual(tx.status, "SUCCESS")
        self.assertTrue(facturacion.opciones_factura(tx)["reintentar_devolucion"])
        self.assertIn("pendiente", AnulacionFactura.objects.get().detalle_devolucion)

        ClientAccreditationMethod.objects.filter(pk=self.cuenta_usd.pk).update(balance=usd)
        facturacion.reintentar_devolucion(tx)
        tx.refresh_from_db()
        self.assertEqual(tx.status, "ANULADA")

    def test_ventanilla_no_admite_devolucion_desde_el_panel(self):
        """Las operaciones de ventanilla se devuelven en efectivo en la caja."""
        tx = CurrencyPurchaseTransaction.objects.create(
            cliente=self.cliente, from_currency="PYG", to_currency="USD", amount=Decimal("730000"),
            converted_amount=Decimal("100"), applied_rate=Decimal("7300"), standard_rate=Decimal("7300"),
            total_pyg=Decimal("730000"), status="SUCCESS", payment_method_type="EFECTIVO_VENTANILLA",
            transparent_breakdown=f"Ventanilla{APROBADA}",
        )
        self.assertFalse(facturacion.opciones_factura(tx)["devolver"])
        with self.assertRaisesMessage(FacturacionError, "ventanilla"):
            facturacion.verificar_devolucion_posible(tx)


class SolicitudClienteEsDevolucionTests(BaseSaldosTest):
    """La solicitud del cliente siempre implica devolver los fondos."""

    @patch(SERVICIO)
    def test_solicitud_aprobada_devuelve_los_fondos(self, servicio):
        """El cliente pide la anulación, el admin la aprueba y, al aprobarse en SIFEN, recupera su dinero."""
        self.configurar(servicio)
        servicio.sol_cancelacion.return_value = {"code": 0, "results": []}
        antes = self.saldos()
        tx = self.comprar_usd()

        self.client.force_login(self.usuario)
        self.client.post(reverse("procesamiento_operaciones:solicitar_anulacion_factura"),
                         {"tipo": "COMPRA", "id": tx.pk, "motivo": "Ya no necesito los dólares"})
        solicitud = SolicitudAnulacionFactura.objects.get()
        self.client.force_login(self.admin)
        self.client.post(reverse("procesamiento_operaciones:admin_facturacion"),
                         {"accion": "aprobar_solicitud", "solicitud_id": solicitud.pk, "metodo": "auto"})
        self.assertTrue(AnulacionFactura.objects.get().con_devolucion)

        servicio.get_estado_sifen.return_value = {"code": 0, "results": [{"estado_sifen": "Aprobado", "estado_can": "Aprobado"}]}
        facturacion.sincronizar_anulaciones()
        self.assertEqual(self.saldos(), antes)

    def test_no_puede_pedir_si_ya_uso_los_fondos(self):
        """Si el cliente ya gastó lo recibido, se le informa que no puede pedir la devolución."""
        tx = self.comprar_usd()
        ClientAccreditationMethod.objects.filter(pk=self.cuenta_usd.pk).update(balance=Decimal("0"))
        with self.assertRaisesMessage(FacturacionError, "No se pueden devolver los fondos"):
            facturacion.solicitar_anulacion(tx, self.usuario, "Ya no necesito los dólares")
