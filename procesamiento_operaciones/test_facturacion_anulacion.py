# -*- coding: utf-8 -*-
"""Pruebas unitarias del ciclo de vida de la factura: reintento, cancelación, nota de crédito y solicitudes."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from authentication.models import Cliente, Role, UserProfile, UsuarioClienteRelacion
from procesamiento_operaciones import facturacion, views
from procesamiento_operaciones.facturacion import FacturacionError
from procesamiento_operaciones.models import (
    AnulacionFactura, CurrencyPurchaseTransaction, SifenSecuencia, SolicitudAnulacionFactura,
)
from tasas_cambio.models import NotificationLog

CDC_FACTURA = "1" * 44
CDC_NUEVO = "2" * 44
CDC_NC = "5" * 44
SERVICIO_FACTURACION = "procesamiento_operaciones.facturacion.FacturaSeguraService"
SERVICIO_VIEWS = "procesamiento_operaciones.views.FacturaSeguraService"


class BaseFacturacionTest(TestCase):
    """Cliente, administrador y una compra facturada."""

    def setUp(self):
        """Prepara los usuarios y el cliente de la operación."""
        self.admin = User.objects.create_user(username="admin_fact", password="password123", email="admin@test.com")
        UserProfile.objects.create(user=self.admin, role=Role.objects.get_or_create(name="Admin")[0])
        self.usuario = User.objects.create_user(username="cliente_fact", password="password123", email="usuario@test.com")
        UserProfile.objects.create(user=self.usuario, role=Role.objects.get_or_create(name="Cliente")[0])
        self.cliente = Cliente.objects.create(
            nombre_o_razon_social="Cliente Factura", documento_identidad="4567890", email="cliente@test.com",
            tipo_cliente="FISICA",
        )
        UsuarioClienteRelacion.objects.create(keycloak_user_id=str(self.usuario.id), cliente=self.cliente, rol_en_cliente="CLIENTE")

    def compra(self, marcas=f"SIFEN CDC: {CDC_FACTURA} | SIFEN Estado: Emitida | SIFEN Estado: Aprobada", horas=1, status="SUCCESS"):
        """Crea una compra con el desglose indicado, realizada hace `horas` horas."""
        tx = CurrencyPurchaseTransaction.objects.create(
            user=self.usuario, cliente=self.cliente, from_currency="PYG", to_currency="USD",
            amount=Decimal("730000"), converted_amount=Decimal("100"), applied_rate=Decimal("7300"),
            standard_rate=Decimal("7300"), total_pyg=Decimal("730000"), status=status,
            transparent_breakdown=f"Desglose | {marcas}",
        )
        CurrencyPurchaseTransaction.objects.filter(pk=tx.pk).update(timestamp=timezone.now() - timedelta(hours=horas))
        tx.refresh_from_db()
        return tx

    def mensajes(self, response):
        """Textos de las notificaciones de la respuesta."""
        return " ".join(str(m) for m in response.context["messages"])


class EstadoFacturaTests(BaseFacturacionTest):
    """El estado vigente es el último registrado en el intento actual."""

    def test_reintento_reemplaza_el_error_anterior(self):
        """Tras un reintento exitoso la factura deja de estar en Error, y el historial se conserva."""
        tx = self.compra(f"SIFEN Estado: Error: sin token | SIFEN Reintento (admin) | SIFEN CDC: {CDC_NUEVO} | SIFEN Estado: Emitida")
        self.assertEqual(tx.sifen_estado, "Emitida")
        self.assertEqual(tx.sifen_cdc, CDC_NUEVO)
        self.assertIn("sin token", tx.transparent_breakdown)

    def test_estados_de_anulacion(self):
        """Cancelada y Anulada prevalecen sobre la aprobación anterior."""
        self.assertEqual(self.compra(f"SIFEN CDC: {CDC_FACTURA} | SIFEN Estado: Aprobada | SIFEN Estado: Cancelada (x)").sifen_estado, "Cancelada")
        self.assertEqual(self.compra(f"SIFEN CDC: {CDC_FACTURA} | SIFEN Estado: Aprobada | SIFEN Estado: Anulación en proceso (nc)").sifen_estado, "Anulación en proceso")


class OpcionesFacturaTests(BaseFacturacionTest):
    """Las acciones disponibles dependen del estado y del plazo de SIFEN."""

    def test_factura_con_error_se_reintenta(self):
        """Una factura que falló por la API se puede reintentar, pero no anular."""
        opciones = facturacion.opciones_factura(self.compra("SIFEN Estado: Error: No se pudo obtener el token"))
        self.assertTrue(opciones["reintentar"])
        self.assertFalse(opciones["nota_credito"])

    def test_aprobada_dentro_del_plazo_se_cancela(self):
        """Dentro de las 48 h la factura se puede cancelar o anular con nota de crédito."""
        opciones = facturacion.opciones_factura(self.compra(horas=2))
        self.assertTrue(opciones["cancelar"])
        self.assertTrue(opciones["nota_credito"])
        self.assertFalse(opciones["reintentar"])

    def test_aprobada_fuera_del_plazo_solo_nota_de_credito(self):
        """Pasadas las 48 h solo queda la nota de crédito."""
        opciones = facturacion.opciones_factura(self.compra(horas=49))
        self.assertFalse(opciones["cancelar"])
        self.assertTrue(opciones["nota_credito"])

    def test_emitida_se_consulta(self):
        """Una factura emitida sin resultado ofrece consultar su estado."""
        opciones = facturacion.opciones_factura(self.compra(f"SIFEN CDC: {CDC_FACTURA} | SIFEN Estado: Emitida"))
        self.assertTrue(opciones["consultar"])

    def test_operacion_no_exitosa_no_se_factura(self):
        """Una operación cancelada no ofrece acciones de facturación."""
        opciones = facturacion.opciones_factura(self.compra("SIFEN Estado: Error: x", status="CANCELLED"))
        self.assertFalse(any(opciones[k] for k in ("reintentar", "consultar", "cancelar", "nota_credito")))


@patch.object(views.sys, "argv", ["manage.py"])
class EstadosFacturaSeguraTests(BaseFacturacionTest):
    """Interpretación de get_estado_sifen según la documentación de la API ESI."""

    def test_mapeo_de_estados(self):
        """Los estados de proceso siguen pendientes; los ERROR_* se tratan como error."""
        casos = {
            "SOL.APROBACION": "Pendiente", "ENVIADO_A_SIFEN": "Pendiente", "REINTENTAR_LOTE": "Pendiente",
            "Aprobado": "Aprobada", "Aprobado con observación": "Aprobada",
            "Rechazado": "Rechazada", "Reingresado": "Rechazada",
            "ERROR_SIFEN": "Error", "ERROR_ENVIO_LOTE": "Error", "ERROR_CONSULTA_LOTE": "Error",
        }
        for valor, esperado in casos.items():
            self.assertEqual(views._mapear_estado_sifen(valor), esperado, valor)

    @patch(SERVICIO_VIEWS)
    def test_error_sifen_libera_la_numeracion_y_permite_reintentar(self, servicio):
        """Una factura trabada en ERROR_SIFEN no bloquea la numeración y queda para reintentar."""
        servicio.obtener_token.return_value = "token"
        servicio.get_estado_sifen.return_value = {"code": 0, "results": [{
            "estado_sifen": "ERROR_SIFEN", "desc_sifen": "Error de comunicacion", "error_sifen": "timeout",
        }]}
        tx = self.compra(f"SIFEN CDC: {CDC_FACTURA} | SIFEN Estado: Emitida")
        SifenSecuencia.objects.create(
            clave=views._clave_secuencia(), ultimo_numero=10, numero_en_proceso=11, cdc_en_proceso=CDC_FACTURA,
        )

        views._sincronizar_facturas_sifen(CurrencyPurchaseTransaction.objects.all())

        tx.refresh_from_db()
        self.assertEqual(tx.sifen_estado, "Error")
        self.assertIn("ERROR_SIFEN", tx.sifen_error)
        self.assertTrue(facturacion.opciones_factura(tx)["reintentar"])
        secuencia = SifenSecuencia.objects.get(clave=views._clave_secuencia())
        self.assertIsNone(secuencia.numero_en_proceso)
        self.assertEqual(secuencia.ultimo_numero, 10)


@patch.object(views.sys, "argv", ["manage.py"])
class ReintentoTests(BaseFacturacionTest):
    """El administrador vuelve a emitir una factura que falló."""

    @patch(SERVICIO_VIEWS)
    def test_reintento_tras_caida_de_la_api(self, servicio):
        """Si la API cayó, el reintento emite la factura con un nuevo CDC."""
        servicio.obtener_token.return_value = "token"
        servicio.generar_de.return_value = {"code": 0, "results": [{"CDC": CDC_NUEVO}]}
        tx = self.compra("SIFEN Estado: Error: No se pudo obtener el token de Factura Segura")

        estado = facturacion.reintentar_facturacion(tx, self.admin)

        self.assertEqual(estado, "Emitida")
        self.assertEqual(tx.sifen_cdc, CDC_NUEVO)
        servicio.generar_de.assert_called_once()

    @patch(SERVICIO_VIEWS)
    def test_reintento_masivo_se_detiene_si_la_api_sigue_caida(self, servicio):
        """Con la API caída se intenta una sola factura y se avisa, sin esperar por cada una."""
        servicio.obtener_token.return_value = None
        for _ in range(3):
            self.compra("SIFEN Estado: Error: sin token")
        self.client.force_login(self.admin)
        response = self.client.post(reverse("procesamiento_operaciones:admin_facturacion"), {"accion": "reintentar_todas"}, follow=True)
        self.assertIn("Reintento detenido", self.mensajes(response))
        self.assertEqual(servicio.obtener_token.call_count, 1)

    @patch(SERVICIO_VIEWS)
    def test_timeout_queda_sin_confirmar_y_no_se_reenvia_en_masa(self, servicio):
        """Si generar_de se corta, la factura queda sin confirmar y el reintento masivo no la reenvía."""
        servicio.obtener_token.return_value = "token"
        servicio.generar_de.return_value = {"code": -1, "error": "Read timed out"}
        tx = self.compra("Inicio")
        CurrencyPurchaseTransaction.objects.filter(pk=tx.pk).update(transparent_breakdown="Desglose")
        tx.refresh_from_db()
        views._generar_factura_sifen_async(tx)
        tx.refresh_from_db()
        self.assertTrue(tx.sifen_sin_confirmar)
        self.assertIsNone(SifenSecuencia.objects.get(clave=views._clave_secuencia()).numero_en_proceso)

        servicio.generar_de.reset_mock()
        self.client.force_login(self.admin)
        response = self.client.post(reverse("procesamiento_operaciones:admin_facturacion"), {"accion": "reintentar_todas"}, follow=True)
        self.assertIn("sin confirmar", self.mensajes(response))
        servicio.generar_de.assert_not_called()

    def test_no_se_reintenta_una_factura_aprobada(self):
        """Una factura aprobada no se vuelve a emitir."""
        with self.assertRaises(FacturacionError):
            facturacion.reintentar_facturacion(self.compra(), self.admin)


class CancelacionTests(BaseFacturacionTest):
    """Evento de cancelación de SIFEN (sol_cancelacion, asíncrono, dentro del plazo)."""

    def _estado_can(self, servicio, estado_can, desc="", error=""):
        """Configura la respuesta de get_estado_sifen para el evento de cancelación."""
        servicio.get_estado_sifen.return_value = {"code": 0, "results": [{
            "estado_sifen": "Aprobado", "estado_can": estado_can, "desc_can": desc, "error_can": error,
        }]}

    @patch(SERVICIO_FACTURACION)
    def test_cancelacion_queda_en_proceso_y_luego_cancelada(self, servicio):
        """sol_cancelacion solo registra el pedido; la factura se cancela cuando SIFEN aprueba el evento."""
        servicio.obtener_token.return_value = "token"
        servicio.sol_cancelacion.return_value = {"code": 0, "description": "OK", "results": []}
        tx = self.compra(horas=3)

        anulacion = facturacion.cancelar_factura(tx, "Error en el monto cargado", self.admin)

        servicio.sol_cancelacion.assert_called_once_with("token", CDC_FACTURA, views.SIFEN_RUC_EMISOR)
        tx.refresh_from_db()
        self.assertEqual(tx.sifen_estado, "Cancelación en proceso")
        self.assertEqual(anulacion.estado, AnulacionFactura.EMITIDA)

        self._estado_can(servicio, "")  # todavía sin respuesta de SIFEN
        self.assertEqual(facturacion.sincronizar_anulaciones(), 0)
        self._estado_can(servicio, "Aprobado", "0600 - Evento registrado correctamente")
        self.assertEqual(facturacion.sincronizar_anulaciones(), 1)

        tx.refresh_from_db()
        anulacion.refresh_from_db()
        self.assertEqual(tx.sifen_estado, "Cancelada")
        self.assertEqual(anulacion.estado, AnulacionFactura.APROBADA)
        self.assertTrue(NotificationLog.objects.filter(user=self.usuario, title="Factura cancelada").exists())

    @patch(SERVICIO_FACTURACION)
    def test_cancelacion_rechazada_por_sifen_vuelve_a_aprobada(self, servicio):
        """Si SIFEN rechaza el evento, la factura vuelve a Aprobada con el motivo y puede anularse con NC."""
        servicio.obtener_token.return_value = "token"
        servicio.sol_cancelacion.return_value = {"code": 0, "results": []}
        tx = self.compra()
        facturacion.cancelar_factura(tx, "Error en el monto", self.admin)
        self._estado_can(servicio, "Rechazado", "4003 - Plazo de cancelación vencido")

        facturacion.sincronizar_anulaciones()

        tx.refresh_from_db()
        self.assertEqual(tx.sifen_estado, "Aprobada")
        self.assertIn("Plazo de cancelación vencido", tx.sifen_detalle)
        self.assertEqual(AnulacionFactura.objects.get().estado, AnulacionFactura.RECHAZADA)
        self.assertTrue(facturacion.opciones_factura(tx)["nota_credito"])

    @patch(SERVICIO_FACTURACION)
    def test_factura_segura_no_registra_la_solicitud(self, servicio):
        """Un code negativo de sol_cancelacion deja la factura Aprobada y muestra el motivo."""
        servicio.obtener_token.return_value = "token"
        servicio.sol_cancelacion.return_value = {"code": -10321, "description": "No se pudo realizar la solicitud"}
        tx = self.compra()
        with self.assertRaisesMessage(FacturacionError, "No se pudo realizar la solicitud"):
            facturacion.cancelar_factura(tx, "Error en el monto", self.admin)
        tx.refresh_from_db()
        self.assertEqual(tx.sifen_estado, "Aprobada")
        self.assertEqual(AnulacionFactura.objects.get().estado, AnulacionFactura.ERROR)

    @patch(SERVICIO_FACTURACION)
    def test_cancelacion_fuera_del_plazo_se_bloquea(self, servicio):
        """Vencidas las 48 h no se llama a Factura Segura y se indica usar nota de crédito."""
        with self.assertRaisesMessage(FacturacionError, "nota de crédito"):
            facturacion.cancelar_factura(self.compra(horas=50), "Error en el monto", self.admin)
        servicio.sol_cancelacion.assert_not_called()

    def test_motivo_obligatorio(self):
        """El motivo es obligatorio para registrar la anulación."""
        with self.assertRaises(FacturacionError):
            facturacion.cancelar_factura(self.compra(), "  ", self.admin)


class NotaCreditoTests(BaseFacturacionTest):
    """Nota de crédito electrónica (fuera del plazo de cancelación)."""

    def _servicio(self, servicio, respuesta=None):
        """Configura el servicio simulado."""
        servicio.obtener_token.return_value = "token"
        servicio.generar_de.return_value = respuesta or {"code": 0, "results": [{"CDC": CDC_NC}]}
        servicio.descargar_kude.return_value = b"%PDF-nc"

    @patch(SERVICIO_FACTURACION)
    def test_nota_de_credito_sigue_la_estructura_de_factura_segura(self, servicio):
        """El DE tipo 5 lleva iMotEmi y gCamDEAsoc completo, y no los campos de pago de la factura."""
        self._servicio(servicio)
        tx = self.compra(horas=72)

        anulacion = facturacion.emitir_nota_credito(tx, "Devolución de la operación", self.admin, devolucion=True)

        de = servicio.generar_de.call_args[0][1]["DE"]
        self.assertEqual(de["iTiDE"], "5")
        self.assertEqual(de["iMotEmi"], "2")
        self.assertIn("iTipTra", de)
        asociado = de["gCamDEAsoc"]
        self.assertEqual(len(asociado), 1)
        self.assertEqual(asociado[0]["dCdCDERef"], CDC_FACTURA)
        self.assertEqual(asociado[0]["iTipDocAso"], "1")
        for campo in ("dNTimDI", "dEstDocAso", "dPExpDocAso", "dNumDocAso", "dNumComRet", "dNumResCF", "iTipCons", "dNumCons", "dNumControl"):
            self.assertEqual(asociado[0][campo], "0", campo)
        for campo in ("gPaConEIni", "iCondOpe", "iIndPres", "dDesMotEmi"):
            self.assertNotIn(campo, de)
        tx.refresh_from_db()
        self.assertEqual(tx.sifen_estado, "Anulación en proceso")
        self.assertEqual(anulacion.cdc_nota_credito, CDC_NC)
        self.assertEqual(len(mail.outbox), 0)  # se envía cuando SIFEN la aprueba
        secuencia = SifenSecuencia.objects.get(clave=views._clave_secuencia(views.SIFEN_TIDE_NOTA_CREDITO))
        self.assertEqual(secuencia.cdc_en_proceso, CDC_NC)

    @patch(SERVICIO_FACTURACION)
    def test_nota_de_credito_aprobada_anula_la_factura(self, servicio):
        """Cuando SIFEN aprueba la nota, la factura queda Anulada y la numeración avanza."""
        self._servicio(servicio)
        tx = self.compra(horas=72)
        anulacion = facturacion.emitir_nota_credito(tx, "Devolución de la operación", self.admin)
        servicio.get_estado_sifen.return_value = {"code": 0, "results": [{"estado_sifen": "APROBADO"}]}

        self.assertEqual(facturacion.sincronizar_anulaciones(), 1)

        tx.refresh_from_db()
        anulacion.refresh_from_db()
        self.assertEqual(tx.sifen_estado, "Anulada")
        self.assertEqual(anulacion.estado, AnulacionFactura.APROBADA)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("cliente@test.com", mail.outbox[0].to)
        self.assertIn(CDC_NC, mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].attachments[0][2], "application/pdf")
        secuencia = SifenSecuencia.objects.get(clave=views._clave_secuencia(views.SIFEN_TIDE_NOTA_CREDITO))
        self.assertEqual(secuencia.ultimo_numero, anulacion.numero_nota_credito)
        self.assertIsNone(secuencia.numero_en_proceso)

    @patch(SERVICIO_FACTURACION)
    def test_nota_de_credito_rechazada_devuelve_la_factura_a_aprobada(self, servicio):
        """Si SIFEN rechaza la nota, la factura vuelve a estar Aprobada y puede anularse otra vez."""
        self._servicio(servicio)
        tx = self.compra(horas=72)
        facturacion.emitir_nota_credito(tx, "Devolución de la operación", self.admin)
        servicio.get_estado_sifen.return_value = {"code": 0, "results": [{"estado_sifen": "RECHAZADO", "desc_sifen": "Motivo inválido"}]}

        facturacion.sincronizar_anulaciones()

        tx.refresh_from_db()
        self.assertEqual(tx.sifen_estado, "Aprobada")
        self.assertTrue(facturacion.opciones_factura(tx)["nota_credito"])
        self.assertEqual(len(mail.outbox), 0)

    @patch(SERVICIO_FACTURACION)
    def test_falla_al_emitir_libera_el_numero(self, servicio):
        """Si la API no emite la nota, el número no se consume y la factura sigue Aprobada."""
        self._servicio(servicio, {"code": -1, "error": "Connection refused"})
        tx = self.compra(horas=72)
        with self.assertRaisesMessage(FacturacionError, "Connection refused"):
            facturacion.emitir_nota_credito(tx, "Devolución de la operación", self.admin)
        tx.refresh_from_db()
        self.assertEqual(tx.sifen_estado, "Aprobada")
        self.assertIsNone(SifenSecuencia.objects.get(clave=views._clave_secuencia(views.SIFEN_TIDE_NOTA_CREDITO)).numero_en_proceso)
        self.assertEqual(AnulacionFactura.objects.get().estado, AnulacionFactura.ERROR)
        self.assertEqual(len(mail.outbox), 0)


class SolicitudesAnulacionTests(BaseFacturacionTest):
    """El cliente pide la anulación y el administrador la resuelve."""

    def solicitar(self, tx, motivo="Me cobraron dos veces"):
        """Envía la solicitud como el cliente."""
        self.client.force_login(self.usuario)
        return self.client.post(reverse("procesamiento_operaciones:solicitar_anulacion_factura"),
                                {"tipo": "COMPRA", "id": tx.pk, "motivo": motivo}, follow=True)

    def test_cliente_solicita_anulacion(self):
        """La solicitud queda pendiente y no se puede duplicar."""
        tx = self.compra()
        self.assertIn("Solicitud de anulación enviada", self.mensajes(self.solicitar(tx)))
        self.assertEqual(SolicitudAnulacionFactura.objects.get().estado, SolicitudAnulacionFactura.PENDIENTE)
        self.assertIn("pendiente", self.mensajes(self.solicitar(tx)))
        self.assertEqual(SolicitudAnulacionFactura.objects.count(), 1)

    def test_no_puede_pedir_anular_operaciones_ajenas(self):
        """Un cliente no puede pedir la anulación de la operación de otro."""
        otro = User.objects.create_user(username="otro_fact", password="password123")
        UserProfile.objects.create(user=otro, role=Role.objects.get(name="Cliente"))
        tx = self.compra()
        self.client.force_login(otro)
        response = self.client.post(reverse("procesamiento_operaciones:solicitar_anulacion_factura"),
                                    {"tipo": "COMPRA", "id": tx.pk, "motivo": "Quiero anularla"}, follow=True)
        self.assertIn("sus propias operaciones", self.mensajes(response))
        self.assertFalse(SolicitudAnulacionFactura.objects.exists())

    def test_solo_facturas_aprobadas(self):
        """No se puede pedir la anulación de una factura que todavía no fue aprobada."""
        tx = self.compra(f"SIFEN CDC: {CDC_FACTURA} | SIFEN Estado: Emitida")
        self.assertIn("aprobadas", self.mensajes(self.solicitar(tx)))

    @patch(SERVICIO_FACTURACION)
    def test_admin_aprueba_y_se_solicita_la_cancelacion(self, servicio):
        """Al aprobar dentro del plazo se pide la cancelación a SIFEN y se avisa al cliente."""
        servicio.obtener_token.return_value = "token"
        servicio.sol_cancelacion.return_value = {"code": 0, "results": []}
        tx = self.compra()
        self.solicitar(tx)
        solicitud = SolicitudAnulacionFactura.objects.get()

        self.client.force_login(self.admin)
        response = self.client.post(reverse("procesamiento_operaciones:admin_facturacion"),
                                    {"accion": "aprobar_solicitud", "solicitud_id": solicitud.pk, "metodo": "auto"}, follow=True)

        self.assertIn("aprobada", self.mensajes(response))
        solicitud.refresh_from_db()
        tx.refresh_from_db()
        self.assertEqual(solicitud.estado, SolicitudAnulacionFactura.APROBADA)
        self.assertEqual(tx.sifen_estado, "Cancelación en proceso")
        self.assertTrue(NotificationLog.objects.filter(user=self.usuario, title="Solicitud de anulación aprobada").exists())

    @patch(SERVICIO_FACTURACION)
    def test_si_sifen_falla_la_solicitud_sigue_pendiente(self, servicio):
        """Si la API está caída, la solicitud no se pierde: queda pendiente para reintentar."""
        servicio.obtener_token.return_value = None
        tx = self.compra()
        self.solicitar(tx)
        self.client.force_login(self.admin)
        response = self.client.post(reverse("procesamiento_operaciones:admin_facturacion"),
                                    {"accion": "aprobar_solicitud", "solicitud_id": SolicitudAnulacionFactura.objects.get().pk}, follow=True)
        self.assertIn("No se pudo conectar con Factura Segura", self.mensajes(response))
        self.assertEqual(SolicitudAnulacionFactura.objects.get().estado, SolicitudAnulacionFactura.PENDIENTE)

    def test_admin_rechaza_con_motivo(self):
        """El rechazo exige un motivo y se informa al cliente."""
        tx = self.compra()
        self.solicitar(tx)
        solicitud = SolicitudAnulacionFactura.objects.get()
        self.client.force_login(self.admin)
        url = reverse("procesamiento_operaciones:admin_facturacion")
        self.client.post(url, {"accion": "rechazar_solicitud", "solicitud_id": solicitud.pk, "respuesta": ""})
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, SolicitudAnulacionFactura.PENDIENTE)

        self.client.post(url, {"accion": "rechazar_solicitud", "solicitud_id": solicitud.pk, "respuesta": "La operación ya fue liquidada"})
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, SolicitudAnulacionFactura.RECHAZADA)
        self.assertTrue(NotificationLog.objects.filter(user=self.usuario, title="Solicitud de anulación rechazada").exists())


class PantallasFacturacionTests(BaseFacturacionTest):
    """Acceso y opciones visibles en las pantallas."""

    def test_panel_solo_para_quien_gestiona_facturas(self):
        """El administrador entra al panel; el cliente es rechazado."""
        self.compra()
        self.client.force_login(self.admin)
        response = self.client.get(reverse("procesamiento_operaciones:admin_facturacion"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cancelar en SIFEN")
        self.client.force_login(self.usuario)
        response = self.client.get(reverse("procesamiento_operaciones:admin_facturacion"))
        self.assertRedirects(response, reverse("dashboard_redirect"), fetch_redirect_response=False)

    def test_tarjeta_en_panel_de_administrador(self):
        """El panel del administrador muestra la tarjeta de facturación con las solicitudes pendientes."""
        tx = self.compra()
        SolicitudAnulacionFactura.objects.create(compra=tx, cdc=CDC_FACTURA, solicitante=self.usuario, motivo="Prueba de motivo")
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard_redirect"))
        self.assertContains(response, "Facturación Electrónica")
        self.assertContains(response, "1 solicitud(es)")

    def test_historial_del_cliente_ofrece_solicitar_anulacion(self):
        """El historial del cliente muestra la opción solo para facturas aprobadas."""
        self.compra()
        self.client.force_login(self.usuario)
        response = self.client.get(reverse("procesamiento_operaciones:currency_transactions_history"))
        self.assertContains(response, "Solicitar anulación")
