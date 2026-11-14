# -*- coding: utf-8 -*-
"""Pruebas que verifican que el cliente no ve el saldo interno de los métodos de pago del negocio."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, Client, RequestFactory
from django.urls import reverse

from authentication.models import UserProfile, Role, Cliente, ClientAccreditationMethod
from procesamiento_operaciones.views import CurrencyPurchaseService
from tasas_cambio.models import ExchangeRate, PaymentMethod
from tasas_cambio.views import ensure_default_benefit_rules


class MetodosPagoSinSaldoTests(TestCase):
    """El saldo de cada método de pago es de la casa de cambios y no se muestra al cliente."""

    def setUp(self):
        """Crea cotización, un método de pago con saldo conocido y un cliente activo."""
        ExchangeRate.objects.create(
            currency_code="USD", currency_name="Dólar Estadounidense",
            buy_rate=Decimal("7300.0000"), sell_rate=Decimal("7450.0000"),
        )
        ensure_default_benefit_rules()
        self.pm = PaymentMethod.objects.create(
            code="PM_TRANSF", name="Transferencia Bancaria", method_type="TRANSFERENCIA",
            balance=Decimal("1234567.00"), is_active=True,
        )
        self.cliente = Cliente.objects.create(
            nombre_o_razon_social="Cliente Saldo", tipo_cliente="FISICA",
            documento_identidad="7654321", email="saldo@test.com", categoria="MINORISTA",
        )
        # La pantalla de compra exige que el cliente tenga un medio de acreditación
        ClientAccreditationMethod.objects.create(
            cliente=self.cliente, tipo_medio="CUENTA_BANCARIA", entidad_financiera="Banco Test",
            numero_cuenta="12345678", tipo_cuenta="AHORRO", titularidad="Cliente Saldo",
            estado="VERIFICADO", es_predeterminado=True,
        )
        self.user = User.objects.create_user(username="cliente_saldo", password="password123")
        role, _ = Role.objects.get_or_create(name="Role Saldo")
        UserProfile.objects.create(user=self.user, role=role, category="MINORISTA", is_corporate=False)

    def test_pantalla_de_compra_no_muestra_saldo_del_metodo(self):
        """La lista de métodos de pago muestra el nombre pero no el saldo del negocio."""
        self.client.force_login(self.user)
        session = self.client.session
        session["active_client_id"] = str(self.cliente.id)
        session.save()

        response = self.client.get(reverse("procesamiento_operaciones:currency_purchase"))

        self.assertContains(response, "Transferencia Bancaria")
        self.assertNotContains(response, "1,234,567")
        self.assertNotContains(response, "1.234.567")
        self.assertNotContains(response, "Saldo: ₲")

    def test_error_de_fondos_no_revela_saldo(self):
        """El error de fondos insuficientes no informa al cliente el saldo del método de pago."""
        request = RequestFactory().get("/")
        request.user = self.user
        request.session = {"active_client_id": str(self.cliente.id)}

        with self.assertRaises(ValidationError) as ctx:
            CurrencyPurchaseService.process_purchase(
                user=self.user, from_currency="PYG", to_currency="USD", amount=5000000,
                payment_method_id_or_code=self.pm.code, request=request,
            )

        mensaje = str(ctx.exception)
        self.assertIn("Fondos insuficientes", mensaje)
        self.assertNotIn("1,234,567", mensaje)
        self.assertNotIn("Saldo disponible", mensaje)
