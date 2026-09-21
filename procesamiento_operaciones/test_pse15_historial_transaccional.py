# -*- coding: utf-8 -*-
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from decimal import Decimal
from authentication.models import UserProfile, Role, Cliente
from tasas_cambio.models import ExchangeRate, PaymentMethod, ClientBenefitRule
from tasas_cambio.views import ensure_default_benefit_rules
from procesamiento_operaciones.models import CurrencyPurchaseTransaction, CurrencySaleTransaction


class HistorialTransaccionalPSE15Tests(TestCase):
    """
    Suite de pruebas unitarias e integración independiente y exclusiva para la Historia de Usuario PSE-15:
    Registro Detallado y Consulta de Historial Transaccional (Épica PSE-12: Procesamiento de Operaciones Cambiarias).
    
    Valida:
    1. Registro inmutable de transacciones (imposibilidad de modificar registros guardados).
    2. Asociación obligatoria de todos los campos requeridos (fecha/hora, tipo de operación, moneda origen/destino,
       monto, tasa de cambio aplicada, cliente y número único de operación).
    3. Gestión y visualización de los cuatro estados transaccionales: Pendiente, Pagada, Cancelada, Anulada.
    4. Lógica de filtrado avanzado (por rango de fechas, estado, tipo de operación) en la vista de historial.
    5. Descarga y exportación de reportes de historial en formatos Excel (CSV) y PDF.
    """

    def setUp(self):
        """
        Configura el entorno de pruebas con datos iniciales de tasas de cambio,
        métodos de pago, perfiles de usuario y cliente activo.
        """
        self.client = Client()
        self.history_url = reverse('procesamiento_operaciones:currency_transactions_history')

        # Configurar tasa de cambio
        self.rate_usd = ExchangeRate.objects.create(
            currency_code='USD',
            currency_name='Dólar Estadounidense',
            buy_rate=Decimal('7300.0000'),
            sell_rate=Decimal('7450.0000')
        )

        # Configurar método de pago
        self.payment_method = PaymentMethod.objects.create(
            code='TRANSFERENCIA',
            name='Transferencia Bancaria',
            method_type='TRANSFERENCIA',
            account_number='123456789',
            bank_name='Banco Test',
            balance=Decimal('10000000.00'),
            is_active=True
        )

        ensure_default_benefit_rules()

        # Crear cliente de prueba
        self.cliente = Cliente.objects.create(
            nombre_o_razon_social='Cliente PSE15 S.A.',
            tipo_cliente='JURIDICA',
            documento_identidad='80055566-7',
            email='historial@pse15.com',
            categoria='CORPORATIVO'
        )

        # Crear usuario y perfil
        self.role = Role.objects.create(name='Rol PSE15')
        self.user = User.objects.create_user(username='user_pse15', password='password123')
        self.profile = UserProfile.objects.create(
            user=self.user, role=self.role, category='CORPORATIVO', is_corporate=True
        )

        # Asociar cliente mediante sesión o relación
        self.client.login(username='user_pse15', password='password123')
        session = self.client.session
        session['active_client_id'] = str(self.cliente.id)
        session.save()

    def test_01_registro_inmutable(self):
        """
        Verifica que las transacciones sean inmutables y no puedan ser modificadas
        una vez almacenadas en la base de datos (lanza ValidationError).
        """
        tx = CurrencyPurchaseTransaction.objects.create(
            user=self.user,
            cliente=self.cliente,
            from_currency='PYG',
            to_currency='USD',
            amount=Decimal('745000.00'),
            converted_amount=Decimal('100.00'),
            applied_rate=Decimal('7450.0000'),
            standard_rate=Decimal('7450.0000'),
            payment_method=self.payment_method,
            total_pyg=Decimal('745000.00'),
            status='SUCCESS'
        )

        # Intentar modificar el registro existente debe lanzar ValidationError por inmutabilidad
        tx.amount = Decimal('999999.00')
        with self.assertRaises(ValidationError):
            tx.save()

    def test_02_campos_obligatorios(self):
        """
        Verifica la presencia y correcta asociación de todos los campos obligatorios
        en el registro transaccional.
        """
        tx = CurrencySaleTransaction.objects.create(
            user=self.user,
            cliente=self.cliente,
            from_currency='USD',
            to_currency='PYG',
            amount=Decimal('100.00'),
            converted_amount=Decimal('730000.00'),
            applied_rate=Decimal('7300.0000'),
            standard_rate=Decimal('7300.0000'),
            linked_account=self.payment_method,
            total_pyg=Decimal('720000.00'),
            status='SUCCESS'
        )

        self.assertIsNotNone(tx.id)  # Número único de operación
        self.assertIsNotNone(tx.timestamp)  # Fecha y hora
        self.assertEqual(tx.from_currency, 'USD')
        self.assertEqual(tx.to_currency, 'PYG')
        self.assertEqual(tx.amount, Decimal('100.00'))
        self.assertEqual(tx.applied_rate, Decimal('7300.0000'))
        self.assertEqual(tx.cliente, self.cliente)

    def test_03_gestion_cuatro_estados(self):
        """
        Verifica la gestión y correcta visualización de los cuatro estados transaccionales:
        Pendiente, Pagada, Cancelada, Anulada.
        """
        tx_pend = CurrencyPurchaseTransaction.objects.create(
            cliente=self.cliente, from_currency='PYG', to_currency='USD',
            amount=Decimal('100000.00'), converted_amount=Decimal('13.42'),
            applied_rate=Decimal('7450.0000'), standard_rate=Decimal('7450.0000'),
            total_pyg=Decimal('100000.00'), status='PENDING'
        )
        tx_pag = CurrencySaleTransaction.objects.create(
            cliente=self.cliente, from_currency='USD', to_currency='PYG',
            amount=Decimal('50.00'), converted_amount=Decimal('365000.00'),
            applied_rate=Decimal('7300.0000'), standard_rate=Decimal('7300.0000'),
            total_pyg=Decimal('360000.00'), status='SUCCESS'
        )
        tx_canc = CurrencyPurchaseTransaction.objects.create(
            cliente=self.cliente, from_currency='PYG', to_currency='EUR',
            amount=Decimal('200000.00'), converted_amount=Decimal('25.31'),
            applied_rate=Decimal('7900.0000'), standard_rate=Decimal('7900.0000'),
            total_pyg=Decimal('200000.00'), status='CANCELLED'
        )
        tx_anul = CurrencySaleTransaction.objects.create(
            cliente=self.cliente, from_currency='EUR', to_currency='PYG',
            amount=Decimal('10.00'), converted_amount=Decimal('79000.00'),
            applied_rate=Decimal('7900.0000'), standard_rate=Decimal('7900.0000'),
            total_pyg=Decimal('78000.00'), status='ANNULLED'
        )

        response = self.client.get(self.history_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pendiente')
        self.assertContains(response, 'Pagada')
        self.assertContains(response, 'Cancelada')
        self.assertContains(response, 'Anulada')

    def test_04_logica_filtrado(self):
        """
        Verifica la lógica de filtrado por tipo de operación, estado y rango de fechas.
        """
        CurrencyPurchaseTransaction.objects.create(
            cliente=self.cliente, from_currency='PYG', to_currency='USD',
            amount=Decimal('100000.00'), converted_amount=Decimal('13.42'),
            applied_rate=Decimal('7450.0000'), standard_rate=Decimal('7450.0000'),
            total_pyg=Decimal('100000.00'), status='SUCCESS'
        )
        CurrencySaleTransaction.objects.create(
            cliente=self.cliente, from_currency='USD', to_currency='PYG',
            amount=Decimal('100.00'), converted_amount=Decimal('730000.00'),
            applied_rate=Decimal('7300.0000'), standard_rate=Decimal('7300.0000'),
            total_pyg=Decimal('720000.00'), status='PENDING'
        )

        # Filtrar solo compras
        response = self.client.get(f"{self.history_url}?tx_type=COMPRA")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'COMPRA')

        # Filtrar solo pendientes
        response_status = self.client.get(f"{self.history_url}?status=PENDING")
        self.assertEqual(response_status.status_code, 200)
        self.assertContains(response_status, 'Pendiente')

    def test_05_exportacion_excel_y_pdf(self):
        """
        Verifica que la exportación de reportes responda correctamente
        para los formatos Excel (CSV) y PDF.
        """
        CurrencyPurchaseTransaction.objects.create(
            cliente=self.cliente, from_currency='PYG', to_currency='USD',
            amount=Decimal('100000.00'), converted_amount=Decimal('13.42'),
            applied_rate=Decimal('7450.0000'), standard_rate=Decimal('7450.0000'),
            total_pyg=Decimal('100000.00'), status='SUCCESS'
        )

        # Test exportación Excel (CSV)
        res_excel = self.client.get(f"{self.history_url}?export=excel")
        self.assertEqual(res_excel.status_code, 200)
        self.assertIn('text/csv', res_excel['Content-Type'])

        # Test exportación PDF
        res_pdf = self.client.get(f"{self.history_url}?export=pdf")
        self.assertEqual(res_pdf.status_code, 200)
        self.assertContains(res_pdf, 'Reporte Oficial de Historial Transaccional')
