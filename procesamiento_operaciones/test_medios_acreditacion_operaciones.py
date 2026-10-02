# -*- coding: utf-8 -*-
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from decimal import Decimal
from authentication.models import UserProfile, Role, Cliente, ClientAccreditationMethod
from tasas_cambio.models import ExchangeRate, PaymentMethod, ClientBenefitRule
from tasas_cambio.views import ensure_default_benefit_rules
from procesamiento_operaciones.models import CurrencyPurchaseTransaction, CurrencySaleTransaction
from procesamiento_operaciones.views import CurrencyPurchaseService, CurrencySaleService

class MediosAcreditacionOperacionesTests(TestCase):
    """
    Pruebas unitarias para validar la integración de los medios de acreditación del cliente
    en las operaciones de compra y venta de divisas (Cuenta origen, Cuenta destino, redirección si no hay cuentas, y Método de pago).
    """

    def setUp(self):
        self.client = Client()
        self.purchase_url = reverse('procesamiento_operaciones:currency_purchase')
        self.sale_url = reverse('procesamiento_operaciones:currency_sale')

        self.rate_usd = ExchangeRate.objects.create(
            currency_code='USD',
            currency_name='Dólar Estadounidense',
            buy_rate=Decimal('7300.0000'),
            sell_rate=Decimal('7450.0000')
        )
        self.rate_pyg = ExchangeRate.objects.create(
            currency_code='PYG',
            currency_name='Guaraní Paraguayo',
            buy_rate=Decimal('1.0000'),
            sell_rate=Decimal('1.0000')
        )

        self.pm = PaymentMethod.objects.create(
            code='TRANSFERENCIA',
            name='Transferencia Bancaria GX',
            method_type='TRANSFERENCIA',
            balance=Decimal('1000000000.00'),
            is_active=True
        )

        ensure_default_benefit_rules()

        self.cliente = Cliente.objects.create(
            nombre_o_razon_social='Cliente Bancario SA',
            tipo_cliente='JURIDICA',
            documento_identidad='80099988-1',
            email='bancario@client.com',
            categoria='VIP'
        )

        self.role, _ = Role.objects.get_or_create(name="Role Bank")
        self.user = User.objects.create_user(username='user_bank', password='password123')
        self.profile = UserProfile.objects.create(
            user=self.user, role=self.role, category='VIP', is_corporate=True, ci_ruc='80099988-1'
        )

    def test_redirect_when_no_acreditation_methods(self):
        """
        Verifica que si el cliente no tiene ningún medio de acreditación registrado,
        la vista de operación lo redirige a la gestión de medios de acreditación.
        """
        self.client.force_login(self.user)
        session = self.client.session
        session['active_client_id'] = str(self.cliente.id)
        session.save()

        # Sin medios de acreditación creados
        response = self.client.get(self.purchase_url)
        self.assertRedirects(response, reverse('client_acreditation_methods'))

    def test_operation_with_acreditation_methods(self):
        """
        Verifica que al tener medios de acreditación registrados, las operaciones de compra y venta
        utilizan correctamente la cuenta origen y cuenta destino asociadas.
        """
        acc_origin = ClientAccreditationMethod.objects.create(
            cliente=self.cliente,
            tipo_medio='CUENTA_BANCARIA',
            entidad_financiera='Banco Itaú',
            numero_cuenta='111222333',
            tipo_cuenta='AHORRO',
            titularidad='Cliente Bancario SA',
            estado='VERIFICADO',
            es_predeterminado=True
        )
        acc_dest = ClientAccreditationMethod.objects.create(
            cliente=self.cliente,
            tipo_medio='CUENTA_BANCARIA',
            entidad_financiera='Vision Banco',
            numero_cuenta='444555666',
            tipo_cuenta='CORRIENTE',
            titularidad='Cliente Bancario SA',
            estado='VERIFICADO',
            es_predeterminado=False
        )

        req = self.client.get(self.purchase_url).wsgi_request
        req.user = self.user
        req.session = {'active_client_id': str(self.cliente.id)}

        # Probar compra usando cuentas de origen y destino específicas
        tx_purchase = CurrencyPurchaseService.process_purchase(
            user=self.user,
            from_currency='PYG',
            to_currency='USD',
            amount=Decimal('745000.00'),
            payment_method_id_or_code=self.pm.code,
            origin_method_id=acc_origin.id,
            destination_method_id=acc_dest.id,
            request=req
        )

        self.assertEqual(tx_purchase.status, 'SUCCESS')
        self.assertEqual(tx_purchase.origin_acreditation_method, acc_origin)
        self.assertEqual(tx_purchase.destination_acreditation_method, acc_dest)

        # Probar venta usando cuentas de origen y destino específicas
        tx_sale = CurrencySaleService.process_sale(
            user=self.user,
            from_currency='USD',
            to_currency='PYG',
            amount=Decimal('100.00'),
            payment_method_id_or_code=self.pm.code,
            origin_method_id=acc_dest.id,
            destination_method_id=acc_origin.id,
            request=req
        )

        self.assertEqual(tx_sale.status, 'SUCCESS')
        self.assertEqual(tx_sale.origin_acreditation_method, acc_dest)
        self.assertEqual(tx_sale.destination_acreditation_method, acc_origin)
