# -*- coding: utf-8 -*-
"""Pruebas unitarias de la emisión y sincronización de facturas SIFEN."""
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase

from procesamiento_operaciones import views
from procesamiento_operaciones.models import CurrencyPurchaseTransaction, CurrencySaleTransaction, SifenSecuencia

CDC_VALIDO = "0" * 44
SERVICIO = "procesamiento_operaciones.views.FacturaSeguraService"
INICIAL = settings.FACTURASEGURA_NUMERO_INICIAL
PRIMER_DOC = f"{INICIAL:07d}"
SEGUNDO_DOC = f"{INICIAL + 1:07d}"


def crear_compra(breakdown="Desglose"):
    """Crea una compra exitosa de USD con guaraníes para las pruebas."""
    return CurrencyPurchaseTransaction.objects.create(
        from_currency="PYG",
        to_currency="USD",
        amount=Decimal("730000"),
        converted_amount=Decimal("100"),
        applied_rate=Decimal("7300"),
        standard_rate=Decimal("7300"),
        total_pyg=Decimal("730000"),
        transparent_breakdown=breakdown,
    )


@patch.object(views.sys, "argv", ["manage.py"])
class GenerarFacturaSifenTests(TestCase):
    """Verifica que el resultado de la emisión siempre quede registrado."""

    @patch(SERVICIO)
    def test_emision_exitosa_guarda_cdc(self, servicio):
        """Una respuesta OK guarda el CDC y deja la factura como Emitida."""
        servicio.obtener_token.return_value = "token"
        servicio.generar_de.return_value = {"code": 0, "results": [{"CDC": CDC_VALIDO}]}
        tx = crear_compra()

        views._generar_factura_sifen_async(tx)

        tx.refresh_from_db()
        self.assertEqual(tx.sifen_cdc, CDC_VALIDO)
        self.assertEqual(tx.sifen_estado, "Emitida")

    @patch(SERVICIO)
    def test_actividad_economica_conserva_acentos(self, servicio):
        """La descripción debe coincidir con el catálogo de SIFEN (error 1262)."""
        servicio.obtener_token.return_value = "token"
        servicio.generar_de.return_value = {"code": 0, "results": [{"CDC": CDC_VALIDO}]}

        views._generar_factura_sifen_async(crear_compra())

        de = servicio.generar_de.call_args.args[1]["DE"]
        self.assertEqual(de["gActEco"][0]["dDesActEco"], "Actividades de programación informática")

    @patch(SERVICIO)
    def test_sin_token_registra_error_y_no_gasta_numero(self, servicio):
        """Sin token se informa el error y no se consume un número de documento."""
        servicio.obtener_token.return_value = None
        tx = crear_compra()

        views._generar_factura_sifen_async(tx)

        tx.refresh_from_db()
        self.assertEqual(tx.sifen_estado, "Error")
        self.assertIn("token", tx.sifen_error)
        self.assertFalse(SifenSecuencia.objects.exists())
        servicio.generar_de.assert_not_called()

    @patch(SERVICIO)
    def test_rechazo_muestra_motivo(self, servicio):
        """El motivo informado por la API queda visible en la transacción."""
        servicio.obtener_token.return_value = "token"
        servicio.generar_de.return_value = {"code": -10002, "description": "Falta dRucEm"}
        tx = crear_compra()

        views._generar_factura_sifen_async(tx)

        tx.refresh_from_db()
        self.assertEqual(tx.sifen_estado, "Error")
        self.assertIn("Falta dRucEm", tx.sifen_error)


@patch.object(views.sys, "argv", ["manage.py"])
class SincronizarFacturasSifenTests(TestCase):
    """Verifica la actualización del estado consultado en SIFEN."""

    def _sincronizar(self, servicio, estado):
        servicio.obtener_token.return_value = "token"
        servicio.get_estado_sifen.return_value = {"code": 0, "results": [{"estado_sifen": estado}]}
        tx = crear_compra(f"Desglose | SIFEN CDC: {CDC_VALIDO} | SIFEN Estado: Emitida")
        views._sincronizar_facturas_sifen(CurrencyPurchaseTransaction.objects.all())
        tx.refresh_from_db()
        return tx

    @patch(SERVICIO)
    def test_solicitud_sigue_pendiente(self, servicio):
        """SOL.APROBACION aún no es un resultado final."""
        self.assertEqual(self._sincronizar(servicio, "SOL.APROBACION").sifen_estado, "Emitida")

    @patch(SERVICIO)
    def test_aprobada(self, servicio):
        """Una factura aprobada habilita sus descargas."""
        self.assertEqual(self._sincronizar(servicio, "APROBADO").sifen_estado, "Aprobada")

    @patch(SERVICIO)
    def test_rechazada(self, servicio):
        """Una factura rechazada queda marcada como tal."""
        self.assertEqual(self._sincronizar(servicio, "RECHAZADO").sifen_estado, "Rechazada")

    @patch(SERVICIO)
    def test_cdc_antiguo_no_se_consulta(self, servicio):
        """Los CDC con formato inválido no generan consultas a la API."""
        crear_compra("SIFEN CDC: 123 | SIFEN Estado: Emitida")
        views._sincronizar_facturas_sifen(CurrencyPurchaseTransaction.objects.all())
        servicio.obtener_token.assert_not_called()

    @patch(SERVICIO)
    def test_falla_no_interrumpe_la_pagina(self, servicio):
        """Una excepción al consultar no debe propagarse a la vista."""
        servicio.obtener_token.side_effect = RuntimeError("falla")
        crear_compra(f"SIFEN CDC: {CDC_VALIDO}")
        views._sincronizar_facturas_sifen(CurrencySaleTransaction.objects.all(), CurrencyPurchaseTransaction.objects.all())


@patch.object(views.sys, "argv", ["manage.py"])
class NumeracionSifenTests(TestCase):
    """Verifica el rango permitido y que el número solo avance al aprobarse."""

    def _preparar(self, servicio):
        """Configura respuestas de emisión con un CDC distinto por documento."""
        servicio.obtener_token.return_value = "token"
        servicio.generar_de.side_effect = [
            {"code": 0, "results": [{"CDC": str(i) * 44}]} for i in range(1, 4)
        ]

    def _numeros(self, servicio):
        """Devuelve los dNumDoc enviados a SIFEN, en orden."""
        return [c.args[1]["DE"]["dNumDoc"] for c in servicio.generar_de.call_args_list]

    def _resultado(self, servicio, estado):
        """Simula el estado informado por SIFEN y sincroniza."""
        servicio.get_estado_sifen.return_value = {"code": 0, "results": [{"estado_sifen": estado}]}
        views._sincronizar_facturas_sifen(CurrencyPurchaseTransaction.objects.all())

    @patch(SERVICIO)
    def test_primer_numero_y_no_avanza_sin_aprobacion(self, servicio):
        """El rango empieza en el número inicial y el contador no cambia mientras espera resultado."""
        self._preparar(servicio)
        views._generar_factura_sifen_async(crear_compra())
        self.assertEqual(self._numeros(servicio), [PRIMER_DOC])
        sec = SifenSecuencia.objects.get()
        self.assertEqual(sec.ultimo_numero, INICIAL - 1)
        self.assertEqual(sec.numero_en_proceso, INICIAL)

    @patch(SERVICIO)
    def test_segunda_operacion_espera_y_usa_siguiente_si_se_aprueba(self, servicio):
        """Con un documento en espera la siguiente va a la cola, y sale al aprobarse."""
        self._preparar(servicio)
        servicio.get_estado_sifen.return_value = {"code": 0, "results": [{"estado_sifen": "SOL.APROBACION"}]}
        views._generar_factura_sifen_async(crear_compra())
        segunda = crear_compra()
        views._generar_factura_sifen_async(segunda)
        segunda.refresh_from_db()
        self.assertEqual(segunda.sifen_estado, "En cola")
        self.assertEqual(servicio.generar_de.call_count, 1)

        self._resultado(servicio, "Aprobado")

        segunda.refresh_from_db()
        self.assertEqual(self._numeros(servicio), [PRIMER_DOC, SEGUNDO_DOC])
        self.assertEqual(segunda.sifen_estado, "Emitida")
        self.assertEqual(SifenSecuencia.objects.get().ultimo_numero, INICIAL)

    @patch(SERVICIO)
    def test_rechazada_reutiliza_el_numero(self, servicio):
        """Un rechazo de SIFEN deja el mismo número para la siguiente operación."""
        self._preparar(servicio)
        views._generar_factura_sifen_async(crear_compra())
        self._resultado(servicio, "Rechazado")
        views._generar_factura_sifen_async(crear_compra())
        self.assertEqual(self._numeros(servicio), [PRIMER_DOC, PRIMER_DOC])
        self.assertEqual(SifenSecuencia.objects.get().ultimo_numero, INICIAL - 1)

    @patch(SERVICIO)
    def test_error_al_emitir_libera_el_numero(self, servicio):
        """Si generar_de falla, el número queda disponible de inmediato."""
        servicio.obtener_token.return_value = "token"
        servicio.generar_de.return_value = {"code": -10002, "description": "Falta dato"}
        views._generar_factura_sifen_async(crear_compra())
        views._generar_factura_sifen_async(crear_compra())
        self.assertEqual(self._numeros(servicio), [PRIMER_DOC, PRIMER_DOC])
        self.assertIsNone(SifenSecuencia.objects.get().numero_en_proceso)

    @patch(SERVICIO)
    def test_rango_agotado_no_emite(self, servicio):
        """Con el ultimo numero del rango aprobado no se emite y se informa el error."""
        servicio.obtener_token.return_value = "token"
        SifenSecuencia.objects.create(clave="001-003-1", ultimo_numero=settings.FACTURASEGURA_NUMERO_FINAL)
        tx = crear_compra()
        views._generar_factura_sifen_async(tx)
        tx.refresh_from_db()
        self.assertEqual(tx.sifen_estado, "Error")
        self.assertIn("rango", tx.sifen_error)
        servicio.generar_de.assert_not_called()
