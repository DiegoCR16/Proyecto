# Registro de Conversación IA (CHIA) - Anulación de facturas, notas de crédito y reintentos

**Fecha:** 13 de Noviembre de 2026  
**Rama:** `actualizacion` (creada desde `main`)  
**Asistente:** Claude Code (claude-opus-5-5)  

---

## 1. Resumen de la Solicitud
> "Quiero que agregues funciones para facturación: que el administrador tenga la tarjeta donde pueda anular una factura y las opciones correspondientes; que el cliente pueda solicitar anular su factura; tener en cuenta que SIFEN tiene un límite para anular; generar una nota de crédito y pasarla al cliente por correo; contemplar los casos de anulación, cancelación o rechazo según los estados de la factura; y que si la factura falla porque la API cayó, el administrador pueda reintentarla."

---

## 2. Diagnóstico
- El estado de la factura se guarda como marcas en `transparent_breakdown`, y se deducía por prioridad: si alguna vez hubo "Error", quedaba en Error para siempre. Eso hacía imposible reintentar.
- No existían anulación, nota de crédito, solicitudes del cliente ni panel de facturación.
- La numeración (`SifenSecuencia`) solo contemplaba facturas (iTiDE 1).

---

## 3. Desarrollo e Implementación

### Estados de la factura
- Hay un mixin común `SifenFacturaMixin` para compras y ventas.
- El estado vigente es **el último registrado en el intento actual**. Cada reintento agrega la marca `SIFEN Reintento`, y el intento anterior queda como historial.
- Estados: En cola → Emitida/Pendiente → Aprobada | Rechazada | Error. Una factura aprobada puede pasar a Cancelada, o a Anulación en proceso → Anulada.

### Reglas de SIFEN (`procesamiento_operaciones/facturacion.py`)
- **Cancelación** (evento de SIFEN): solo para facturas aprobadas y dentro de `SIFEN_PLAZO_CANCELACION_HORAS` (48 h). El plazo se cuenta desde la fecha de la operación, que es un criterio conservador porque no se guarda la hora de aprobación.
- **Nota de crédito** (iTiDE 5):
  - Se emite por el total, asociada al CDC de la factura (`gCamDEAsoc`), con el motivo "2 = Devolución".
  - Tiene numeración propia (`001-003-5`, `FACTURASEGURA_NC_NUMERO_INICIAL/FINAL`).
  - Se envía por correo al cliente, con el KuDE adjunto si ya está disponible.
  - Al aprobarse, la factura queda **Anulada**. Si SIFEN la rechaza, la factura vuelve a **Aprobada**.
- **Reintento**: disponible para facturas con Error (por ejemplo, API caída), Rechazada o En cola. El reintento masivo se detiene si la API sigue fallando.
- **No hace falta inutilización**: la numeración solo avanza cuando SIFEN aprueba un documento.

### Modelos nuevos
- `SolicitudAnulacionFactura`: pedido del cliente, con estado PENDIENTE, APROBADA o RECHAZADA.
- `AnulacionFactura`: registro de una cancelación o una nota de crédito, con su estado, el CDC de la nota, su número y la fecha de envío del correo.
- Migraciones:
  - `procesamiento_operaciones/0012`.
  - `authentication/0023`: permisos nuevos.
  - `tasas_cambio/0013`: notificación de tipo FACTURACION.

### Pantallas
- **Administrador**: tarjeta "Facturación Electrónica", con el número de solicitudes pendientes, que lleva a `/operaciones/facturacion/`. El panel tiene:
  - Resumen por estado y filtros.
  - Acciones según el estado: reintentar, consultar en SIFEN, cancelar, emitir nota de crédito, reenviar la nota por correo y "Reintentar todas las fallidas".
  - Solicitudes de clientes para aprobar o rechazar (el rechazo exige motivo).
  - Historial de anulaciones y solicitudes.
- **Cliente**: en los tres historiales, la factura muestra su estado. Las aprobadas ofrecen "Solicitar anulación" con un motivo. Además se ve el estado de la solicitud y la respuesta del administrador.
- **Permisos nuevos**: "Gestionar Facturación Electrónica" (administrativo) y "Solicitar Anulación de Factura" (Cliente, Jefe y Operador; el Analista corporativo no lo tiene).

### Servicio
- `FacturaSeguraService.sol_cancelacion` (ver sección 4).

---

## 4. Revisión contra "API de Factura Segura para ESI v01" (docs/)
> "Ahí te puse el documento de API Factura Segura en el archivo de doc para que revises y veas si está todo bien."

Correcciones aplicadas según el documento:
- **Cancelación**:
  - La operación es `sol_cancelacion` y recibe solo `CDC` y `dRucEm`; la API no recibe el motivo.
  - Es **asíncrona**: `code` 0 solo confirma que se registró el pedido. La factura queda "Cancelación en proceso" y `sincronizar_anulaciones` lee `estado_can`, `desc_can` y `error_can` de `get_estado_sifen`.
  - Si SIFEN la aprueba, la factura queda Cancelada. Si la rechaza o da error, vuelve a "Aprobada" con el motivo, y puede anularse con nota de crédito.
- **Nota de crédito**, según la estructura del documento:
  - Conserva `iTipTra` y quita `dDesMotEmi`, que no existe en Factura Segura.
  - `gCamDEAsoc` lleva un solo elemento con todos sus campos fijos para `iTipDocAso = 1`.
  - Sin `iIndPres` ni condición o forma de pago.
- **Correo de la nota de crédito**: se envía cuando SIFEN la aprueba, no al emitirla. Factura Segura también envía el KuDE al receptor (`dEmailRec`) cuando un documento se aprueba.
- **Estados de `get_estado_sifen`**:
  - `ENVIADO_A_SIFEN` y `REINTENTAR_LOTE` siguen pendientes.
  - `ERROR_SIFEN`, `ERROR_ENVIO_LOTE` y `ERROR_CONSULTA_LOTE` pasan a Error: se libera la numeración, que antes quedaba bloqueada, y la factura puede reintentarse.
  - "Reingresado" se trata como rechazado.
- **Respuestas**: según la API, `code` mayor o igual a 0 indica éxito.
- **Timeout en `generar_de`**: la API advierte que el servidor pudo haber procesado la solicitud. La factura queda "Error: Sin confirmar", el reintento masivo no la reenvía, y el reintento individual pide confirmación.
- **Inutilización (`sol_inutilizacion`)**: no hace falta. La numeración solo avanza con documentos aprobados, y el siguiente envío reutiliza el número; Factura Segura marca el anterior como "Reingresado".
- **Factura electrónica**: se compararon los campos de `_construir_de` con la estructura del documento y coinciden. Solo faltan campos opcionales (`dInfAdic`) y los de pago con cheque o tarjeta, que no se usan.

## 5. Comandos Ejecutados
```bash
python manage.py makemigrations procesamiento_operaciones -n anulacion_factura
python manage.py makemigrations tasas_cambio -n notificacion_facturacion
python manage.py test procesamiento_operaciones.test_facturacion_anulacion
python manage.py test
```

---

## 6. Evidencia de Pruebas Exitosas
```text
Ran 257 tests in 464.901s
OK
```
