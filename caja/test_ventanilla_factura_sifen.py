# -*- coding: utf-8 -*-
"""Pruebas unitarias de la emisión de facturas electrónicas SIFEN desde la ventanilla del cajero."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse

from authentication.models import Cliente
from caja.models import Caja, TurnoCaja, OperacionVentanilla, inicializar_denominaciones_default
from procesamiento_operaciones import views as po_views
from procesamiento_operaciones.models import CurrencyPurchaseTransaction, CurrencySaleTransaction
from tasas_cambio.models import ExchangeRate

CDC_VALIDO = "1" * 44
SERVICIO = "procesamiento_operaciones.views.FacturaSeguraService"


@patch.object(po_views.sys, "argv", ["manage.py"])
class VentanillaFacturaSifenTests(TestCase):
    """Verifica que las operaciones del cajero en ventanilla también generen su factura electrónica."""

    def setUp(self):
        """Crea cajero con turno abierto, cotizaciones y un cliente identificado."""
        inicializar_denominaciones_default()
        self.client = Client()
        self.cajero = User.objects.create_user(username="cajero_sifen", password="password123")
        caja = Caja.objects.create(nombre="Caja SIFEN", codigo="CS01", activa=True)
        caja.cajeros_asignados.add(self.cajero)
        self.turno = TurnoCaja.objects.create(
            caja=caja,
            cajero=self.cajero,
            saldo_inicial_pyg=Decimal("10000000.00"),
            saldo_inicial_usd=Decimal("500.00"),
            estado="ABIERTO",
        )
        ExchangeRate.objects.get_or_create(
            currency_code="USD",
            defaults={
                "currency_name": "Dólar Estadounidense",
                "symbol": "$",
                "buy_rate": Decimal("7300.0000"),
                "sell_rate": Decimal("7400.0000"),
            },
        )
        self.cliente = Cliente.objects.create(
            nombre_o_razon_social="Ana Gómez",
            documento_identidad="4567890",
            email="ana.gomez@test.com",
            tipo_cliente="FISICA",
            categoria="MINORISTA",
        )
        self.client.login(username="cajero_sifen", password="password123")

    def _procesar(self, tipo="COMPRA", origen="USD", destino="PYG", monto="100.00", **extra):
        """Envía una operación de ventanilla y sigue la redirección al POS."""
        datos = {
            "tipo_operacion": tipo,
            "moneda_origen": origen,
            "moneda_destino": destino,
            "monto_origen": monto,
            "efectivo_recibido": monto,
        }
        datos.update(extra)
        return self.client.post(reverse("caja:procesar_ventanilla"), datos, follow=True)

    @patch(SERVICIO)
    def test_compra_en_ventanilla_emite_factura(self, servicio):
        """Una compra del cajero envía el DE a SIFEN y guarda el CDC en la transacción."""
        servicio.obtener_token.return_value = "token"
        servicio.generar_de.return_value = {"code": 0, "results": [{"CDC": CDC_VALIDO}]}

        response = self._procesar(cliente_id=self.cliente.id)

        self.assertEqual(response.status_code, 200)
        servicio.generar_de.assert_called_once()
        de = servicio.generar_de.call_args[0][1]["DE"]
        self.assertEqual(de["iTipTra"], "6")
        self.assertEqual(de["dNumIDRec"], "4567890")
        self.assertEqual(de["dNomRec"], "Ana Gomez")

        tx = CurrencyPurchaseTransaction.objects.get()
        self.assertEqual(tx.sifen_cdc, CDC_VALIDO)
        self.assertEqual(tx.turno, self.turno)
        self.assertContains(response, f"CDC {CDC_VALIDO}")

    @patch(SERVICIO)
    def test_venta_en_ventanilla_emite_factura_de_venta(self, servicio):
        """Una venta del cajero se factura con tipo de transacción 5 (venta de divisas)."""
        servicio.obtener_token.return_value = "token"
        servicio.generar_de.return_value = {"code": 0, "results": [{"CDC": CDC_VALIDO}]}

        self._procesar(tipo="VENTA", origen="PYG", destino="USD", monto="740000.00", cliente_id=self.cliente.id)

        de = servicio.generar_de.call_args[0][1]["DE"]
        self.assertEqual(de["iTipTra"], "5")
        self.assertEqual(CurrencySaleTransaction.objects.get().sifen_cdc, CDC_VALIDO)

    @patch(SERVICIO)
    def test_error_de_sifen_no_revierte_la_operacion(self, servicio):
        """Si SIFEN rechaza el DE, la operación de caja queda registrada y el cajero ve el error."""
        servicio.obtener_token.return_value = "token"
        servicio.generar_de.return_value = {"code": 1, "description": "RUC invalido"}

        response = self._procesar(cliente_id=self.cliente.id)

        self.assertEqual(OperacionVentanilla.objects.count(), 1)
        tx = CurrencyPurchaseTransaction.objects.get()
        self.assertEqual(tx.sifen_estado, "Error")
        self.assertContains(response, "No se pudo emitir la factura electrónica")

    @patch(SERVICIO)
    def test_cliente_ocasional_se_factura_como_innominado(self, servicio):
        """El código interno OCASIONAL-... no se envía a SIFEN como si fuera una cédula."""
        servicio.obtener_token.return_value = "token"
        servicio.generar_de.return_value = {"code": 0, "results": [{"CDC": CDC_VALIDO}]}

        self._procesar(nombre_cliente="Visitante", documento_cliente="OCASIONAL-001")

        de = servicio.generar_de.call_args[0][1]["DE"]
        self.assertEqual(de["iTipIDRec"], "5")
        self.assertEqual(de["dNumIDRec"], "0")

    @patch(SERVICIO)
    def test_pantallas_del_cajero_muestran_descarga_de_factura_aprobada(self, servicio):
        """El POS y el detalle del turno muestran el estado de la factura y la descarga del KuDE."""
        servicio.obtener_token.return_value = "token"
        servicio.generar_de.return_value = {"code": 0, "results": [{"CDC": CDC_VALIDO}]}
        servicio.get_estado_sifen.return_value = {"code": 0, "results": [{"estado_sifen": "Aprobado"}]}
        self._procesar(cliente_id=self.cliente.id)

        pos = self.client.get(reverse("caja:ventanilla_pos"))
        self.assertEqual(pos.context["operaciones_recientes"][0].factura.sifen_estado, "Aprobada")
        self.assertContains(pos, f"/auth/sifen/kude/{CDC_VALIDO}/")

        detalle = self.client.get(reverse("caja:detalle_turno_operaciones", args=[self.turno.id]))
        self.assertContains(detalle, f"/auth/sifen/kude/{CDC_VALIDO}/")


class DatosReceptorOcasionalTests(TestCase):
    """Verifica el receptor de la factura para clientes ocasionales de ventanilla."""

    def test_documento_ocasional_es_innominado(self):
        """Un documento OCASIONAL-XXXX se trata como receptor innominado."""
        cliente = Cliente(nombre_o_razon_social="Visitante", documento_identidad="OCASIONAL-1A2B3C4D")
        receptor = po_views._datos_receptor(cliente, "x@test.com")
        self.assertEqual(receptor["iTipIDRec"], "5")
        self.assertEqual(receptor["dNumIDRec"], "0")

    def test_cedula_real_sigue_igual(self):
        """Una cédula real se sigue enviando como documento del receptor."""
        cliente = Cliente(nombre_o_razon_social="Ana", documento_identidad="4567890")
        receptor = po_views._datos_receptor(cliente, "x@test.com")
        self.assertEqual(receptor["iTipIDRec"], "1")
        self.assertEqual(receptor["dNumIDRec"], "4567890")
