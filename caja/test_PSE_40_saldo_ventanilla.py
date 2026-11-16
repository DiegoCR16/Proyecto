# -*- coding: utf-8 -*-
"""Pruebas unitarias de la validación de saldo en caja en la ventanilla (PSE-40)."""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from caja.models import (
    Caja, DenominacionDivisa, TurnoCaja, calcular_saldos_actuales_turno, inicializar_denominaciones_default,
)
from procesamiento_operaciones.models import CurrencyPurchaseTransaction, CurrencySaleTransaction
from tasas_cambio.models import ExchangeRate


@patch("caja.views._generar_factura_sifen_async")
class SaldoVentanillaTests(TestCase):
    """La ventanilla muestra el saldo por divisa y no entrega más efectivo del que hay en caja."""

    def setUp(self):
        """Cajero con turno abierto en cero, cotización del dólar y denominaciones de billetes."""
        inicializar_denominaciones_default()
        ExchangeRate.objects.update_or_create(
            currency_code="USD",
            defaults={"currency_name": "Dólar", "symbol": "$", "buy_rate": Decimal("7300"), "sell_rate": Decimal("7400")},
        )
        self.cajero = User.objects.create_user(username="cajero_saldo", password="password123")
        caja = Caja.objects.create(nombre="Caja Saldo", codigo="CS40", activa=True)
        caja.cajeros_asignados.add(self.cajero)
        self.turno = TurnoCaja.objects.create(caja=caja, cajero=self.cajero, estado="ABIERTO")
        self.client.force_login(self.cajero)

    def _ingresar_billetes(self, divisa, valor, cantidad):
        """Registra un ingreso de efectivo con desglose de billetes."""
        billete = DenominacionDivisa.objects.get(divisa=divisa, valor_facial=Decimal(valor))
        self.client.post(
            reverse("caja:movimiento_efectivo", args=[self.turno.id]),
            {"tipo_operacion": "INGRESO", "divisa": divisa, f"denom_{billete.id}": str(cantidad)},
        )

    def _vender_usd(self, monto, efectivo):
        """El cliente compra `monto` USD pagando `efectivo` guaraníes."""
        return self.client.post(reverse("caja:procesar_ventanilla"), {
            "tipo_operacion": "VENTA", "moneda_origen": "PYG", "moneda_destino": "USD",
            "monto_origen": monto, "efectivo_recibido": efectivo,
            "nombre_cliente": "Cliente Saldo", "documento_cliente": "DOC-SALDO-1",
        }, follow=True)

    def test_saldo_incluye_ingresos_por_desglose(self, _factura):
        """Con turno abierto en cero, el saldo de la divisa es lo ingresado con desglose de billetes."""
        self._ingresar_billetes("USD", "100", 20)
        self.assertEqual(calcular_saldos_actuales_turno(self.turno)["USD"], Decimal("2000"))

    def test_bloquea_venta_sin_saldo_suficiente(self, _factura):
        """El cliente quiere 2000 USD y en caja hay 500: la operación no se realiza."""
        self._ingresar_billetes("USD", "100", 5)
        respuesta = self._vender_usd("2000", "15000000")
        self.assertContains(respuesta, "Saldo insuficiente de USD en caja")
        self.assertFalse(CurrencySaleTransaction.objects.exists())

    def test_permite_venta_con_saldo_y_descuenta_de_caja(self, _factura):
        """Con 2000 USD en caja, la venta de 2000 USD se realiza y el saldo de USD queda en cero."""
        self._ingresar_billetes("USD", "100", 20)
        self._vender_usd("2000", "15000000")
        self.assertEqual(CurrencySaleTransaction.objects.count(), 1)
        self.assertEqual(calcular_saldos_actuales_turno(self.turno)["USD"], Decimal("0"))

    def test_bloquea_compra_sin_guaranies_para_entregar(self, _factura):
        """Si el cliente vende dólares y no hay guaraníes en caja, la operación no se realiza."""
        respuesta = self.client.post(reverse("caja:procesar_ventanilla"), {
            "tipo_operacion": "COMPRA", "moneda_origen": "USD", "moneda_destino": "PYG", "monto_origen": "100",
            "nombre_cliente": "Cliente Saldo", "documento_cliente": "DOC-SALDO-1",
        }, follow=True)
        self.assertContains(respuesta, "Saldo insuficiente de PYG en caja")
        self.assertFalse(CurrencyPurchaseTransaction.objects.exists())

    def test_pos_muestra_saldos_por_divisa(self, _factura):
        """El POS muestra el saldo actual de cada divisa del turno."""
        self._ingresar_billetes("USD", "100", 3)
        respuesta = self.client.get(reverse("caja:ventanilla_pos"))
        self.assertEqual(respuesta.context["saldos_actuales"]["USD"], Decimal("300"))
        self.assertContains(respuesta, "Cálculo Total de Divisas en el Sistema")
