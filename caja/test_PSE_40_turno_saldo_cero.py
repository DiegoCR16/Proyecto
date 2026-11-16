# -*- coding: utf-8 -*-
"""Pruebas unitarias de la apertura de turno con saldo inicial cero (PSE-40)."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from caja.models import ArqueoCaja, Caja, DenominacionDivisa, TurnoCaja, inicializar_denominaciones_default

SALDOS_CERO = {f"saldo_inicial_{d}": "0" for d in ("pyg", "usd", "eur", "brl", "ars")}


class TurnoSaldoCeroTests(TestCase):
    """El turno se abre en cero y el efectivo de apertura se registra con desglose de billetes."""

    def setUp(self):
        """Crea la caja, el cajero y las denominaciones de billetes."""
        inicializar_denominaciones_default()
        self.cajero = User.objects.create_user(username="cajero_cero", password="password123")
        self.caja = Caja.objects.create(nombre="Caja Cero", codigo="CC01", activa=True)
        self.caja.cajeros_asignados.add(self.cajero)
        self.client.force_login(self.cajero)

    def _abrir(self, **saldos):
        """Abre el turno con los saldos indicados (cero por defecto)."""
        return self.client.post(reverse("caja:abrir_turno", args=[self.caja.id]), {**SALDOS_CERO, **saldos}, follow=True)

    def test_abre_turno_con_saldo_cero(self):
        """Ya no se exige un saldo inicial mayor a cero."""
        self._abrir()
        turno = TurnoCaja.objects.get(caja=self.caja, estado="ABIERTO")
        self.assertEqual(turno.saldo_inicial_pyg, Decimal("0"))
        self.assertEqual(turno.saldo_inicial_usd, Decimal("0"))

    def test_rechaza_saldo_negativo(self):
        """Un saldo inicial negativo no abre el turno."""
        respuesta = self._abrir(saldo_inicial_usd="-10")
        self.assertFalse(TurnoCaja.objects.filter(caja=self.caja).exists())
        self.assertContains(respuesta, "no pueden ser negativos")

    def test_arqueo_cuadra_con_ingreso_y_salida_por_desglose(self):
        """Apertura en cero + ingreso de billetes + salida de billetes: el arqueo del cierre cuadra."""
        self._abrir()
        turno = TurnoCaja.objects.get(caja=self.caja, estado="ABIERTO")
        billete = DenominacionDivisa.objects.filter(divisa="PYG", activa=True).order_by("-valor_facial").first()
        url = reverse("caja:movimiento_efectivo", args=[turno.id])
        self.client.post(url, {"tipo_operacion": "INGRESO", "divisa": "PYG", f"denom_{billete.id}": "10"})
        self.client.post(url, {"tipo_operacion": "SALIDA", "divisa": "PYG", f"denom_{billete.id}": "4"})

        self.client.post(reverse("caja:cerrar_turno", args=[turno.id]), {"saldo_final_pyg": str(billete.valor_facial * 6)})

        arqueo = ArqueoCaja.objects.get(turno=turno, divisa="PYG")
        self.assertEqual(arqueo.saldo_inicial, Decimal("0"))
        self.assertEqual(arqueo.saldo_teorico, billete.valor_facial * 6)
        self.assertEqual(arqueo.diferencia, Decimal("0"))
        self.assertEqual(arqueo.estado_arqueo, "CUADRADO")
