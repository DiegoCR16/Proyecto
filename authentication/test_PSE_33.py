# -*- coding: utf-8 -*-
from django.test import TestCase, Client, RequestFactory
from django.urls import reverse
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from decimal import Decimal

from authentication.models import UserProfile, Role, Cliente, ClientAccreditationMethod
from procesamiento_operaciones.models import CurrencySaleTransaction
from tasas_cambio.models import ExchangeRate, PaymentMethod


class ClientAccreditationMethodPSE33Tests(TestCase):
    """
    Suite de pruebas unitarias e integración independiente y exclusiva para la Historia de Usuario PSE-33:
    Gestión y CRUD de Medios de Acreditación de Fondos del Cliente (Épica PSE-5: Gestión de Clientes, Roles y Permisos).

    Valida:
    1. Alta de Medio: Registro de cuentas bancarias, billeteras electrónicas y alias de transferencia.
    2. Validación de Datos: Validación de formato de cuenta/teléfono/alias y titularidad.
    3. Listado y Consulta: Consulta de medios vinculados al cliente con estados (Verificado/Pendiente).
    4. Eliminación / Desvinculación: Regla que bloquea la desvinculación si el medio está asociado a una transacción en proceso (PENDING) y permite desvincular si no hay transacciones activas.
    5. Selección Predeterminada: Marcado como predeterminado con exclusividad entre medios del cliente.
    6. Integración Web: Vistas y formularios web para el CRUD completo de medios de acreditación.
    """

    def setUp(self):
        """Configura el entorno de prueba con cliente, usuario, roles y datos iniciales."""
        self.client = Client()
        self.factory = RequestFactory()
        self.url = reverse('client_acreditation_methods')

        self.role_client, _ = Role.objects.get_or_create(name="Cliente")
        self.user = User.objects.create_user(username='user_pse33', email='pse33@client.com', password='Password123!')
        self.profile = UserProfile.objects.create(
            user=self.user, role=self.role_client, category='MINORISTA', ci_ruc='4000123-4'
        )

        self.cliente = Cliente.objects.create(
            nombre_o_razon_social='Cliente PSE-33 S.A.',
            tipo_cliente='JURIDICA',
            documento_identidad='80033333-3',
            email='cliente33@pse.com',
            categoria='MINORISTA'
        )
        # Asociar sesión o perfil
        self.profile.keycloak_id = 'kc-user-33'
        self.profile.save()

    def test_create_valid_acreditation_methods(self):
        """Valida el registro exitoso de medios de acreditación válidos (Cuenta, Billetera, Alias)."""
        # Cuenta Bancaria
        acc = ClientAccreditationMethod.objects.create(
            cliente=self.cliente,
            user=self.user,
            tipo_medio='CUENTA_BANCARIA',
            entidad_financiera='Banco Itaú',
            numero_cuenta='123456789',
            tipo_cuenta='CORRIENTE',
            titularidad='Cliente PSE-33 S.A.',
            estado='VERIFICADO'
        )
        self.assertEqual(acc.tipo_medio, 'CUENTA_BANCARIA')
        self.assertFalse(acc.has_pending_transactions())

        # Billetera
        wallet = ClientAccreditationMethod.objects.create(
            cliente=self.cliente,
            user=self.user,
            tipo_medio='BILLETERA',
            entidad_financiera='Tigo Money',
            numero_telefono='+595981123456',
            titularidad='Cliente PSE-33 S.A.',
            estado='VERIFICADO'
        )
        self.assertEqual(wallet.tipo_medio, 'BILLETERA')

        # Alias
        alias = ClientAccreditationMethod.objects.create(
            cliente=self.cliente,
            user=self.user,
            tipo_medio='ALIAS',
            entidad_financiera='Bancard',
            alias_transferencia='cliente.pse33.alias',
            titularidad='Cliente PSE-33 S.A.',
            estado='VERIFICADO'
        )
        self.assertEqual(alias.tipo_medio, 'ALIAS')

    def test_validation_rules_invalid_formats(self):
        """Valida que se lancen errores de validación ante formatos incorrectos."""
        # Cuenta sin dígitos
        with self.assertRaises(ValidationError):
            bad_acc = ClientAccreditationMethod(
                cliente=self.cliente,
                user=self.user,
                tipo_medio='CUENTA_BANCARIA',
                entidad_financiera='Banco',
                numero_cuenta='ABC-XYZ', # Sin dígitos
                titularidad='Titular'
            )
            bad_acc.full_clean()

        # Alias muy corto
        with self.assertRaises(ValidationError):
            bad_alias = ClientAccreditationMethod(
                cliente=self.cliente,
                user=self.user,
                tipo_medio='ALIAS',
                entidad_financiera='Banco',
                alias_transferencia='ab', # Menos de 3 caracteres
                titularidad='Titular'
            )
            bad_alias.full_clean()

    def test_default_selection_exclusivity(self):
        """Valida que al marcar un medio como predeterminado, los demás dejen de serlo."""
        m1 = ClientAccreditationMethod.objects.create(
            cliente=self.cliente,
            user=self.user,
            tipo_medio='CUENTA_BANCARIA',
            entidad_financiera='Banco 1',
            numero_cuenta='111111',
            titularidad='Titular',
            es_predeterminado=True
        )
        self.assertTrue(m1.es_predeterminado)

        m2 = ClientAccreditationMethod.objects.create(
            cliente=self.cliente,
            user=self.user,
            tipo_medio='CUENTA_BANCARIA',
            entidad_financiera='Banco 2',
            numero_cuenta='222222',
            titularidad='Titular',
            es_predeterminado=True
        )
        self.assertTrue(m2.es_predeterminado)

        m1.refresh_from_db()
        self.assertFalse(m1.es_predeterminado)

    def test_unlinking_blocked_by_pending_transaction(self):
        """
        Valida la regla de desvinculación: bloquea la eliminación de un medio de acreditación
        si está asociado a una transacción de venta en proceso (PENDING).
        Permite la desvinculación si no hay transacciones en proceso.
        """
        method = ClientAccreditationMethod.objects.create(
            cliente=self.cliente,
            user=self.user,
            tipo_medio='CUENTA_BANCARIA',
            entidad_financiera='Banco Vision',
            numero_cuenta='987654321',
            titularidad='Titular'
        )

        # Crear transacción pendiente asociada a este medio
        rate_usd = ExchangeRate.objects.create(currency_code='USD', currency_name='Dólar', buy_rate=Decimal('7300'), sell_rate=Decimal('7450'))
        pm = PaymentMethod.objects.create(code='PM_33', name='PM 33', balance=Decimal('10000000'))

        tx_pending = CurrencySaleTransaction.objects.create(
            user=self.user,
            cliente=self.cliente,
            from_currency='USD',
            to_currency='PYG',
            amount=Decimal('100.00'),
            converted_amount=Decimal('730000.00'),
            applied_rate=Decimal('7300'),
            standard_rate=Decimal('7300'),
            linked_account=pm,
            acreditation_method=method,
            total_pyg=Decimal('730000.00'),
            status='PENDING'
        )

        self.assertTrue(method.has_pending_transactions())

        # Intentar eliminar/desvincular simulando la vista o lógica
        self.client.force_login(self.user)
        session = self.client.session
        session['active_client_id'] = str(self.cliente.id)
        session.save()

        response = self.client.post(self.url, {
            'action': 'delete_method',
            'method_id': method.id
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No se puede desvincular o eliminar el medio de acreditación porque está asociado a una transacción de venta en proceso")

        # Cambiar transacción a SUCCESS o CANCELLED
        tx_pending.status = 'SUCCESS'
        tx_pending.save()

        self.assertFalse(method.has_pending_transactions())

        # Ahora sí debe permitir desvincular
        response_success = self.client.post(self.url, {
            'action': 'delete_method',
            'method_id': method.id
        })
        self.assertEqual(response_success.status_code, 200)
        self.assertFalse(ClientAccreditationMethod.objects.filter(id=method.id).exists())

    def test_web_integration_crud_flows(self):
        """Valida la integración con las vistas web para el CRUD completo de medios de acreditación (Alta, Consulta, Edición, Predeterminado)."""
        self.client.force_login(self.user)
        session = self.client.session
        session['active_client_id'] = str(self.cliente.id)
        session.save()

        # 1. GET inicial de la lista
        res_get = self.client.get(self.url)
        self.assertEqual(res_get.status_code, 200)
        self.assertContains(res_get, "Gestión de Medios de Acreditación")

        # 2. POST para crear medio (Alta)
        res_create = self.client.post(self.url, {
            'action': 'create_method',
            'tipo_medio': 'BILLETERA',
            'entidad_financiera': 'Zimple',
            'numero_telefono': '0981999888',
            'titularidad': 'Cliente PSE-33 S.A.',
            'es_predeterminado': 'on'
        })
        self.assertEqual(res_create.status_code, 200)
        self.assertContains(res_create, "Medio de acreditación registrado y verificado exitosamente")

        created_method = ClientAccreditationMethod.objects.filter(cliente=self.cliente, entidad_financiera='Zimple').first()
        self.assertIsNotNone(created_method)
        self.assertTrue(created_method.es_predeterminado)

        # 3. POST para editar medio
        res_edit = self.client.post(self.url, {
            'action': 'update_method',
            'method_id': created_method.id,
            'tipo_medio': 'BILLETERA',
            'entidad_financiera': 'Zimple Plus',
            'numero_telefono': '0981999888',
            'titularidad': 'Cliente PSE-33 S.A. Editado'
        })
        self.assertEqual(res_edit.status_code, 200)
        created_method.refresh_from_db()
        self.assertEqual(created_method.entidad_financiera, 'Zimple Plus')
        self.assertEqual(created_method.titularidad, 'Cliente PSE-33 S.A. Editado')
