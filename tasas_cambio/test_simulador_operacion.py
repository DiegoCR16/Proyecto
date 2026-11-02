# -*- coding: utf-8 -*-
"""Pruebas del paso del simulador de divisas a la operación de compra o venta."""
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import resolve, reverse

from authentication.models import (
    ClientAccreditationMethod, Cliente, Role, UserProfile, UsuarioClienteRelacion
)
from procesamiento_operaciones.models import CurrencyPurchaseTransaction, CurrencySaleTransaction
from tasas_cambio.models import ExchangeRate, PaymentMethod
from tasas_cambio.views import resolver_tipo_operacion


class ResolverTipoOperacionTests(SimpleTestCase):
    """Valida la relación entre el tipo de operación y las monedas elegidas."""

    def test_deduce_la_operacion_desde_las_monedas(self):
        """Sin tipo explícito, las monedas determinan la operación."""
        self.assertEqual(resolver_tipo_operacion('', 'PYG', 'USD'), 'COMPRA')
        self.assertEqual(resolver_tipo_operacion('', 'USD', 'PYG'), 'VENTA')
        self.assertEqual(resolver_tipo_operacion('', 'EUR', 'USD'), 'COMPRA')

    def test_una_cruzada_respeta_el_tipo_elegido(self):
        """Una operación entre dos divisas extranjeras admite ambos tipos."""
        self.assertEqual(resolver_tipo_operacion('VENTA', 'EUR', 'USD'), 'VENTA')

    def test_rechaza_combinaciones_incoherentes(self):
        """Rechaza monedas iguales y tipos que contradicen las monedas."""
        for operacion, origen, destino in (
            ('COMPRA', 'USD', 'PYG'),
            ('VENTA', 'PYG', 'USD'),
            ('', 'USD', 'USD'),
        ):
            with self.subTest(operacion=operacion, origen=origen, destino=destino):
                with self.assertRaises(ValidationError):
                    resolver_tipo_operacion(operacion, origen, destino)


class SimuladorAOperacionTests(TestCase):
    """Recorre el flujo desde la simulación hasta la operación confirmada."""

    def setUp(self):
        """Crea tasas, un cliente con cuentas en PYG y USD, y un usuario asociado."""
        ExchangeRate.objects.create(
            currency_code='USD', currency_name='Dólar', symbol='$',
            buy_rate=Decimal('7300.0000'), sell_rate=Decimal('7450.0000')
        )
        ExchangeRate.objects.create(
            currency_code='PYG', currency_name='Guaraní', symbol='₲',
            buy_rate=Decimal('1.0000'), sell_rate=Decimal('1.0000')
        )
        self.simulator_url = reverse('tasas_cambio:simulator')
        self.user = User.objects.create_user(username='sim_op', password='clave-segura')
        self.role, _ = Role.objects.get_or_create(name='Cliente Sim Op')
        UserProfile.objects.create(user=self.user, role=self.role, category='MINORISTA')
        self.cliente = Cliente.objects.create(
            nombre_o_razon_social='Cliente Simulador',
            tipo_cliente='FISICA',
            documento_identidad='7654321',
            email='cliente.simulador@example.com',
            categoria='MINORISTA',
        )
        UsuarioClienteRelacion.objects.create(
            keycloak_user_id=str(self.user.id),
            cliente=self.cliente,
            rol_en_cliente='TITULAR',
        )
        self.cuenta_pyg = self._crear_cuenta('PYG', '111', Decimal('10000000.00'))
        self.cuenta_usd = self._crear_cuenta('USD', '222', Decimal('10000.00'))
        PaymentMethod.objects.update_or_create(
            code='SIM_TEST',
            defaults={'name': 'Pago de prueba', 'balance': Decimal('100000000.00'), 'is_active': True},
        )

    def _crear_cuenta(self, moneda, numero, saldo):
        """Crea una cuenta verificada del cliente en la moneda indicada."""
        return ClientAccreditationMethod.objects.create(
            cliente=self.cliente,
            user=self.user,
            tipo_medio='CUENTA_BANCARIA',
            entidad_financiera=f'Banco {moneda}',
            numero_cuenta=numero,
            tipo_cuenta='AHORRO',
            moneda=moneda,
            titularidad=self.cliente.nombre_o_razon_social,
            estado='VERIFICADO',
            balance=saldo,
        )

    def _simular(self, operation, from_currency, to_currency, amount):
        """Envía una simulación y devuelve la respuesta del simulador."""
        return self.client.post(self.simulator_url, {
            'operation': operation,
            'from_currency': from_currency,
            'to_currency': to_currency,
            'amount': amount,
        })

    def test_compra_simulada_ofrece_ir_a_la_compra_con_los_mismos_datos(self):
        """La compra simulada enlaza a la pantalla de compra con monedas y monto."""
        self.client.force_login(self.user)
        response = self._simular('COMPRA', 'PYG', 'USD', '745000')

        url = urlparse(response.context['operation_proposal']['url'])
        self.assertEqual(url.path, reverse('procesamiento_operaciones:currency_purchase'))
        params = parse_qs(url.query)
        self.assertEqual(params['from_currency'], ['PYG'])
        self.assertEqual(params['to_currency'], ['USD'])
        self.assertEqual(params['amount'], ['745000'])
        self.assertContains(response, 'Continuar con esta compra')

    def test_venta_simulada_ofrece_ir_a_la_venta(self):
        """La venta simulada enlaza a la pantalla de venta."""
        self.client.force_login(self.user)
        response = self._simular('VENTA', 'USD', 'PYG', '100')

        url = response.context['operation_proposal']['url']
        self.assertEqual(resolve(urlparse(url).path).url_name, 'currency_sale')
        self.assertContains(response, 'Continuar con esta venta')

    def test_la_pantalla_de_compra_llega_precargada(self):
        """La compra precarga los datos, muestra el resumen y elige las cuentas por moneda."""
        self.client.force_login(self.user)
        url = self._simular('COMPRA', 'PYG', 'USD', '745000').context['operation_proposal']['url']
        response = self.client.get(url)

        self.assertEqual(response.context['amount'], '745000')
        self.assertEqual(response.context['simulacion_origen']['converted_amount'], Decimal('100.00'))
        self.assertContains(response, 'Compra precargada desde su simulación')
        self.assertContains(response, f'<option value="{self.cuenta_pyg.id}" selected>')
        self.assertContains(response, f'<option value="{self.cuenta_usd.id}" selected>')

    def test_compra_simulada_se_procesa_con_el_resultado_mostrado(self):
        """La compra iniciada y confirmada coincide con el resultado de la simulación."""
        self.client.force_login(self.user)
        simulated = self._simular('COMPRA', 'PYG', 'USD', '745000').context['simulation_result']

        response = self.client.post(reverse('procesamiento_operaciones:currency_purchase'), {
            'from_currency': 'PYG',
            'to_currency': 'USD',
            'amount': '745000',
            'origin_account': self.cuenta_pyg.id,
            'destination_account': self.cuenta_usd.id,
            'payment_method': 'SIM_TEST',
        })
        transaction = response.context['pending_transaction']
        self.assertIsNotNone(transaction, response.context['error_message'])
        self.assertEqual(transaction.converted_amount, simulated['converted_amount'])
        self.assertContains(response, 'Monto a Recibir')

        self.client.post(reverse('procesamiento_operaciones:currency_purchase'), {
            'action': 'confirm_purchase',
            'transaction_id': transaction.id,
        })
        confirmed = CurrencyPurchaseTransaction.objects.get(id=transaction.id)
        self.assertEqual(confirmed.status, 'SUCCESS')
        self.assertEqual(confirmed.converted_amount, simulated['converted_amount'])

    def test_venta_simulada_se_inicia_con_el_resultado_mostrado(self):
        """La venta iniciada coincide con el resultado de la simulación."""
        self.client.force_login(self.user)
        simulated = self._simular('VENTA', 'USD', 'PYG', '100').context['simulation_result']

        response = self.client.post(reverse('procesamiento_operaciones:currency_sale'), {
            'from_currency': 'USD',
            'to_currency': 'PYG',
            'amount': '100',
            'origin_account': self.cuenta_usd.id,
            'destination_account': self.cuenta_pyg.id,
            'payment_method': 'SIM_TEST',
        })
        transaction = response.context['pending_transaction']
        self.assertIsNotNone(transaction, response.context['error_message'])
        self.assertEqual(transaction.converted_amount, simulated['converted_amount'])
        self.assertEqual(CurrencySaleTransaction.objects.get(id=transaction.id).status, 'PENDING')

    def test_monto_fuera_de_limites_no_ofrece_operar(self):
        """Un monto bajo el mínimo operativo informa el motivo y no genera enlace."""
        self.client.force_login(self.user)
        response = self._simular('COMPRA', 'PYG', 'USD', '1000')

        proposal = response.context['operation_proposal']
        self.assertIsNone(proposal['url'])
        self.assertIn('mínimo', proposal['bloqueo'])

    def test_invitado_debe_iniciar_sesion(self):
        """Un visitante puede simular, pero se le pide iniciar sesión para operar."""
        response = self._simular('COMPRA', 'PYG', 'USD', '745000')

        proposal = response.context['operation_proposal']
        self.assertIsNone(proposal['url'])
        self.assertEqual(proposal['enlace_url'], reverse('login'))

    def test_analista_no_puede_operar(self):
        """El rol Analista puede simular, pero no recibe el enlace para operar."""
        analyst_role, _ = Role.objects.get_or_create(name='Analista Sim Op')
        self.user.profile.role = analyst_role
        self.user.profile.save()
        self.client.force_login(self.user)

        proposal = self._simular('COMPRA', 'PYG', 'USD', '745000').context['operation_proposal']
        self.assertIsNone(proposal['url'])
        self.assertIn('Analista', proposal['bloqueo'])

    def test_modo_usuario_exige_cliente_activo(self):
        """En modo usuario se simula sin beneficios y se pide elegir un cliente para operar."""
        self.client.force_login(self.user)
        session = self.client.session
        session['user_mode'] = True
        session.save()

        proposal = self._simular('COMPRA', 'PYG', 'USD', '745000').context['operation_proposal']
        self.assertIsNone(proposal['url'])
        self.assertIn('cliente activo', proposal['bloqueo'])

    def test_cliente_sin_cuentas_debe_registrar_un_medio(self):
        """Sin medios de acreditación, se indica dónde registrarlos."""
        ClientAccreditationMethod.objects.filter(cliente=self.cliente).delete()
        self.client.force_login(self.user)

        proposal = self._simular('COMPRA', 'PYG', 'USD', '745000').context['operation_proposal']
        self.assertIsNone(proposal['url'])
        self.assertEqual(proposal['enlace_url'], reverse('client_acreditation_methods'))

    def test_tipo_incoherente_muestra_error(self):
        """Una compra con destino en guaraníes se rechaza con un mensaje claro."""
        response = self._simular('COMPRA', 'USD', 'PYG', '100')

        self.assertIsNone(response.context['simulation_result'])
        self.assertContains(response, 'Error en la Simulación')

    def test_pantallas_de_operacion_sin_simulacion_no_muestran_resumen(self):
        """Quien entra directo a la operación no ve el resumen de simulación."""
        self.client.force_login(self.user)
        response = self.client.get(reverse('procesamiento_operaciones:currency_sale'))

        self.assertIsNone(response.context['simulacion_origen'])
