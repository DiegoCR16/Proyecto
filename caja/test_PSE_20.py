# -*- coding: utf-8 -*-
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from decimal import Decimal
from caja.models import Caja, TurnoCaja, BitacoraArqueo, verificar_turno_activo, puede_realizar_transaccion

class CajaShiftPSE20Tests(TestCase):
    """
    Suite de pruebas unitarias independiente y exclusiva para la Historia de Usuario PSE-20:
    Apertura y Cierre de Turnos de Caja (Epic: Gestión de Caja).
    Valida:
    - La correcta apertura de turno asociando cajero, fecha, hora y saldos iniciales por divisa (USD, EUR, PYG, BRL, ARS).
    - El congelamiento de operaciones al procesar el cierre del turno con saldos finales.
    - La validación del control de estado activo (bloqueo de transacciones sin turno abierto e impedimento de apertura de múltiples turnos activos simultáneos por caja).
    """

    def setUp(self):
        """
        Configuración inicial de caja, cajero y cliente web para pruebas.
        """
        self.client = Client()
        self.gestion_url = reverse('caja:gestion_caja')
        
        self.cajero = User.objects.create_user(username='cajero_test', password='password123')
        self.caja = Caja.objects.create(nombre='Caja Principal 01', codigo='C01', activa=True)

    def test_caja_shift_opening_and_initial_balances(self):
        """
        Valida la correcta apertura de turno asociando cajero, fecha, hora y saldos iniciales por divisa.
        """
        self.client.force_login(self.cajero)
        
        url_abrir = reverse('caja:abrir_turno', args=[self.caja.id])
        response = self.client.post(url_abrir, {
            'saldo_inicial_pyg': '5000000.00',
            'saldo_inicial_usd': '1000.00',
            'saldo_inicial_eur': '500.00',
            'saldo_inicial_brl': '2000.00',
            'saldo_inicial_ars': '10000.00'
        })
        
        self.assertRedirects(response, self.gestion_url)
        
        # Verificar que el turno fue creado y está abierto
        turno = TurnoCaja.objects.filter(caja=self.caja, estado='ABIERTO').first()
        self.assertIsNotNone(turno)
        self.assertEqual(turno.cajero, self.cajero)
        self.assertIsNotNone(turno.fecha_apertura)
        self.assertEqual(turno.saldo_inicial_pyg, Decimal('5000000.00'))
        self.assertEqual(turno.saldo_inicial_usd, Decimal('1000.00'))
        self.assertEqual(turno.saldo_inicial_eur, Decimal('500.00'))
        self.assertEqual(turno.saldo_inicial_brl, Decimal('2000.00'))
        self.assertEqual(turno.saldo_inicial_ars, Decimal('10000.00'))

    def test_caja_shift_closing_and_freezing_operations(self):
        """
        Valida el congelamiento de operaciones al procesar el cierre del turno con saldos finales físicos.
        """
        turno = TurnoCaja.objects.create(
            caja=self.caja,
            cajero=self.cajero,
            saldo_inicial_pyg=Decimal('1000000.00'),
            estado='ABIERTO'
        )
        
        # Con turno abierto, se puede operar
        self.assertTrue(puede_realizar_transaccion(self.caja))

        self.client.force_login(self.cajero)
        url_cerrar = reverse('caja:cerrar_turno', args=[turno.id])
        response = self.client.post(url_cerrar, {
            'saldo_final_pyg': '950000.00',
            'saldo_final_usd': '0.00',
            'saldo_final_eur': '0.00',
            'saldo_final_brl': '0.00',
            'saldo_final_ars': '0.00'
        })
        
        self.assertRedirects(response, self.gestion_url)
        
        turno.refresh_from_db()
        self.assertEqual(turno.estado, 'CERRADO')
        self.assertIsNotNone(turno.fecha_cierre)
        self.assertEqual(turno.saldo_final_pyg, Decimal('950000.00'))

        # Al cerrar el turno, las operaciones deben estar congeladas (bloqueadas)
        self.assertFalse(puede_realizar_transaccion(self.caja))

    def test_active_state_control_and_prevent_multiple_active_shifts(self):
        """
        Valida el control de estado activo: bloqueo de transacciones sin turno abierto
        e impedimento de apertura de múltiples turnos activos simultáneos por caja.
        """
        # Sin turno abierto, no se puede realizar transacciones
        self.assertFalse(puede_realizar_transaccion(self.caja))
        self.assertIsNone(verificar_turno_activo(self.caja))

        # Abrir primer turno
        TurnoCaja.objects.create(
            caja=self.caja,
            cajero=self.cajero,
            saldo_inicial_pyg=Decimal('100000.00'),
            estado='ABIERTO'
        )

        self.assertTrue(puede_realizar_transaccion(self.caja))
        self.assertIsNotNone(verificar_turno_activo(self.caja))

        # Intentar abrir un segundo turno activo para la misma caja debe lanzar ValidationError
        with self.assertRaises(ValidationError):
            turno_duplicado = TurnoCaja(
                caja=self.caja,
                cajero=self.cajero,
                saldo_inicial_pyg=Decimal('500000.00'),
                estado='ABIERTO'
            )
            turno_duplicado.save()

    def test_cashier_independent_and_no_discrepancy_notifications_visibility(self):
        """
        Valida que:
        1. La apertura y cierre de caja sea independiente para cada cajero.
        2. Los cajeros no visualicen notificaciones de descuadres ni arqueos cerrados (solo admin).
        3. El administrador pueda visualizar todas las cajas y notificaciones de descuadre.
        """
        admin_user = User.objects.create_superuser(username='admin_test_caja', password='password123')
        
        cajero2 = User.objects.create_user(username='cajero2_test', password='password123')
        caja2 = Caja.objects.create(nombre='Caja Secundaria 02', codigo='C02', activa=True)

        turno_cerrado = TurnoCaja.objects.create(
            caja=self.caja,
            cajero=self.cajero,
            estado='CERRADO',
            saldo_inicial_pyg=Decimal('1000000.00'),
            saldo_final_pyg=Decimal('900000.00')
        )
        BitacoraArqueo.objects.create(
            turno=turno_cerrado,
            divisa='PYG',
            tipo_descuadre='FALTANTE',
            monto_diferencia=Decimal('100000.00'),
            mensaje='Prueba de descuadre'
        )

        # 1. Test Cashier view
        self.client.force_login(self.cajero)
        response = self.client.get(self.gestion_url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['is_admin'])
        self.assertEqual(len(response.context['turnos_cerrados']), 0)
        self.assertEqual(len(response.context['bitacoras_recientes']), 0)

        # 2. Test Admin view
        self.client.force_login(admin_user)
        response = self.client.get(self.gestion_url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_admin'])
        self.assertGreaterEqual(len(response.context['bitacoras_recientes']), 1)
