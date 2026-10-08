# -*- coding: utf-8 -*-
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from decimal import Decimal
from caja.models import (
    Caja, TurnoCaja, DesgloseEfectivoCaja, ArqueoCaja, 
    BitacoraArqueo, procesar_arqueo_cierre, inicializar_denominaciones_default
)

class ArqueoCajaPSE22Tests(TestCase):
    """
    Suite de pruebas unitarias independiente y exclusiva para la Historia de Usuario PSE-22:
    Arqueo de Caja Cambiario (Epic: Gestión de Caja).

    Valida:
    - La precisión del cálculo comparativo matemático automático entre saldo teórico (esperado) y saldo físico declarado por divisa.
    - La asignación correcta de las etiquetas de estado de arqueo ('Cuadrado', 'Faltante', 'Sobrante').
    - La persistencia del registro en la bitácora histórica de arqueos y la notificación inmediata al Administrador ante descuadres.
    """

    def setUp(self):
        """Configuración inicial para pruebas de arqueo de caja PSE-22."""
        inicializar_denominaciones_default()
        self.client = Client()
        self.cajero = User.objects.create_user(username='cajero_pse22', password='password123')
        self.caja = Caja.objects.create(nombre='Caja Arqueo 01', codigo='CA01', activa=True)
        self.turno = TurnoCaja.objects.create(
            caja=self.caja,
            cajero=self.cajero,
            saldo_inicial_pyg=Decimal('5000000.00'),
            saldo_inicial_usd=Decimal('1000.00'),
            estado='ABIERTO'
        )

    def test_arqueo_matematico_precision_y_estado_cuadrado(self):
        """Valida el cálculo matemático correcto y la etiqueta 'Cuadrado' cuando el saldo físico coincide con el teórico."""
        # Registrar un ingreso físico de PYG 1.000.000
        DesgloseEfectivoCaja.objects.create(
            turno=self.turno,
            tipo_operacion='INGRESO',
            divisa='PYG',
            monto_total=Decimal('1000000.00'),
            observacion='Depósito inicial'
        )

        # Saldo teórico esperado PYG = 5.000.000 + 1.000.000 = 6.000.000
        # Declarar saldo físico de cierre exacto
        self.turno.saldo_final_pyg = Decimal('6000000.00')
        self.turno.saldo_final_usd = Decimal('1000.00')
        self.turno.estado = 'CERRADO'
        self.turno.save()

        procesar_arqueo_cierre(self.turno)

        arqueo_pyg = ArqueoCaja.objects.get(turno=self.turno, divisa='PYG')
        self.assertEqual(arqueo_pyg.saldo_inicial, Decimal('5000000.00'))
        self.assertEqual(arqueo_pyg.movimientos_sistema, Decimal('1000000.00'))
        self.assertEqual(arqueo_pyg.saldo_teorico, Decimal('6000000.00'))
        self.assertEqual(arqueo_pyg.saldo_fisico_cierre, Decimal('6000000.00'))
        self.assertEqual(arqueo_pyg.diferencia, Decimal('0.00'))
        self.assertEqual(arqueo_pyg.estado_arqueo, 'CUADRADO')
        self.assertEqual(BitacoraArqueo.objects.filter(turno=self.turno, divisa='PYG').count(), 0)

    def test_arqueo_clasificacion_faltante_y_bitacora_notificacion(self):
        """Valida la detección de 'Faltante', su registro en bitácora histórica y la emisión de notificación al Administrador."""
        self.turno.saldo_final_pyg = Decimal('4500000.00') # Teórico es 5.000.000 -> Faltante de 500.000
        self.turno.saldo_final_usd = Decimal('1000.00')
        self.turno.estado = 'CERRADO'
        self.turno.save()

        procesar_arqueo_cierre(self.turno)

        arqueo_pyg = ArqueoCaja.objects.get(turno=self.turno, divisa='PYG')
        self.assertEqual(arqueo_pyg.estado_arqueo, 'FALTANTE')
        self.assertEqual(arqueo_pyg.diferencia, Decimal('-500000.00'))

        bitacora = BitacoraArqueo.objects.filter(turno=self.turno, divisa='PYG').first()
        self.assertIsNotNone(bitacora)
        self.assertEqual(bitacora.tipo_descuadre, 'FALTANTE')
        self.assertEqual(bitacora.monto_diferencia, Decimal('500000.00'))
        self.assertTrue(bitacora.administrador_notificado)
        self.assertIn("FALTANTE", bitacora.mensaje)

    def test_arqueo_clasificacion_sobrante_y_bitacora(self):
        """Valida la detección de 'Sobrante' y el registro persistente en la bitácora de auditoría."""
        self.turno.saldo_final_usd = Decimal('1050.00') # Teórico es 1000.00 -> Sobrante de 50.00
        self.turno.saldo_final_pyg = Decimal('5000000.00')
        self.turno.estado = 'CERRADO'
        self.turno.save()

        procesar_arqueo_cierre(self.turno)

        arqueo_usd = ArqueoCaja.objects.get(turno=self.turno, divisa='USD')
        self.assertEqual(arqueo_usd.estado_arqueo, 'SOBRANTE')
        self.assertEqual(arqueo_usd.diferencia, Decimal('50.00'))

        bitacora = BitacoraArqueo.objects.filter(turno=self.turno, divisa='USD').first()
        self.assertIsNotNone(bitacora)
        self.assertEqual(bitacora.tipo_descuadre, 'SOBRANTE')
        self.assertEqual(bitacora.monto_diferencia, Decimal('50.00'))
        self.assertTrue(bitacora.administrador_notificado)
