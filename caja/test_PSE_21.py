# -*- coding: utf-8 -*-
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from decimal import Decimal
from caja.models import (
    Caja, TurnoCaja, DenominacionDivisa, DesgloseEfectivoCaja, 
    DetalleDesgloseBillete, inicializar_denominaciones_default
)

class CurrencyBreakdownPSE21Tests(TestCase):
    """
    Suite de pruebas unitarias independiente y exclusiva para la Historia de Usuario PSE-21:
    Registro Detallado y Desglose de Papel Moneda (Epic: Gestión de Caja).
    Valida:
    - La correcta estructura de almacenamiento y asociación de denominaciones por cada divisa autorizada (PYG, USD, EUR, BRL, ARS).
    - La vinculación exacta entre el desglose de billetes y los movimientos físicos de entrada/salida de efectivo por caja.
    - La precisión matemática de la sumatoria automática de los subtotales y total general en base a las cantidades de billetes ingresadas.
    """

    def setUp(self):
        """Configuración inicial de caja, cajero, denominaciones y cliente web para pruebas."""
        inicializar_denominaciones_default()
        self.client = Client()
        self.cajero = User.objects.create_user(username='cajero_pse21', password='password123')
        self.caja = Caja.objects.create(nombre='Caja Desglose 01', codigo='CD01', activa=True)
        self.turno = TurnoCaja.objects.create(
            caja=self.caja,
            cajero=self.cajero,
            saldo_inicial_pyg=Decimal('1000000.00'),
            estado='ABIERTO'
        )

    def test_denominations_storage_and_currency_association(self):
        """Valida la correcta estructura de almacenamiento y asociación de denominaciones por cada divisa autorizada."""
        divisas = ['PYG', 'USD', 'EUR', 'BRL', 'ARS']
        for div in divisas:
            denoms = DenominacionDivisa.objects.filter(divisa=div, activa=True)
            self.assertGreater(denoms.count(), 0, f"Debe haber denominaciones activas para {div}")

        pyg_100k = DenominacionDivisa.objects.get(divisa='PYG', valor_facial=Decimal('100000'))
        self.assertEqual(pyg_100k.nombre, 'Billete de 100.000 Guaraníes')

        usd_100 = DenominacionDivisa.objects.get(divisa='USD', valor_facial=Decimal('100'))
        self.assertEqual(usd_100.nombre, 'Billete de 100 USD')

    def test_automatic_subtotal_and_total_mathematical_precision(self):
        """Valida la precisión matemática de la sumatoria automática de subtotales en base a cantidades ingresadas."""
        pyg_100k = DenominacionDivisa.objects.get(divisa='PYG', valor_facial=Decimal('100000'))
        pyg_50k = DenominacionDivisa.objects.get(divisa='PYG', valor_facial=Decimal('50000'))

        desglose = DesgloseEfectivoCaja.objects.create(
            turno=self.turno,
            tipo_operacion='INGRESO',
            divisa='PYG',
            observacion='Prueba de cálculo automático'
        )

        detalle1 = DetalleDesgloseBillete.objects.create(
            desglose=desglose,
            denominacion=pyg_100k,
            cantidad=5
        )
        self.assertEqual(detalle1.subtotal, Decimal('500000.00'))

        detalle2 = DetalleDesgloseBillete.objects.create(
            desglose=desglose,
            denominacion=pyg_50k,
            cantidad=4
        )
        self.assertEqual(detalle2.subtotal, Decimal('200000.00'))

        monto_total = sum(d.subtotal for d in desglose.detalles.all())
        desglose.monto_total = monto_total
        desglose.save()

        self.assertEqual(desglose.monto_total, Decimal('700000.00'))

    def test_cash_in_out_movement_linkage_with_bill_breakdown(self):
        """Valida la vinculación exacta entre el desglose de billetes y los movimientos de entrada/salida de efectivo por caja vía HTTP POST."""
        self.client.force_login(self.cajero)
        url = reverse('caja:movimiento_efectivo', args=[self.turno.id])

        pyg_100k = DenominacionDivisa.objects.get(divisa='PYG', valor_facial=Decimal('100000'))

        response = self.client.post(url, {
            'tipo_operacion': 'INGRESO',
            'divisa': 'PYG',
            'observacion': 'Ingreso físico de prueba con billetes',
            f'denom_{pyg_100k.id}': '10'
        })

        self.assertRedirects(response, reverse('caja:gestion_caja'))

        desglose = DesgloseEfectivoCaja.objects.filter(turno=self.turno, tipo_operacion='INGRESO').first()
        self.assertIsNotNone(desglose)
        self.assertEqual(desglose.divisa, 'PYG')
        self.assertEqual(desglose.monto_total, Decimal('1000000.00'))

        detalle = desglose.detalles.first()
        self.assertIsNotNone(detalle)
        self.assertEqual(detalle.denominacion, pyg_100k)
        self.assertEqual(detalle.cantidad, 10)
        self.assertEqual(detalle.subtotal, Decimal('1000000.00'))
