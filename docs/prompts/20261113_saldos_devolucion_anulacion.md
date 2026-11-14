# Registro de Conversación IA (CHIA) - Saldos del cliente al anular facturas

**Fecha:** 13 de Noviembre de 2026  
**Rama:** `actualizacion` (creada desde `main`)  
**Asistente:** Claude Code (claude-opus-5-5)  

---

## 1. Resumen de la Solicitud
> "¿Y qué sucede con el saldo del cliente con estas nuevas funcionalidades?"

Decisiones del usuario:
1. Al anular, el administrador elige entre devolver los fondos o solo corregir la factura.
2. La solicitud de anulación del cliente se trata siempre como devolución.
3. Se corrige el error de la caja del negocio en la venta.

---

## 2. Diagnóstico
- La anulación (cancelación o nota de crédito) solo anulaba el documento fiscal: no revertía ningún saldo. La nota de crédito declaraba "Devolución" ante SIFEN, aunque el dinero no volvía.
- El volumen transaccional del cliente quedaba inflado con operaciones anuladas.
- Una factura anulada por error de datos dejaba la operación sin comprobante, sin forma de volver a facturarla.
- **Error previo en la venta:** la caja del negocio (`PaymentMethod.balance`) sumaba los guaraníes que el negocio le paga al cliente, en lugar de restarlos.
- La conversión entre monedas (`_convert_amount`) usa la tasa del día, así que no sirve para revertir exactamente una operación pasada.

---

## 3. Desarrollo e Implementación
- **`MovimientoSaldoOperacion`** (migración `0013_devolucion_fondos`): cada compra y venta registra, en el momento, el monto exacto que movió:
  - Cuenta origen y cuenta destino del cliente.
  - Caja del negocio.
  - Volumen transaccional.

  Se registra en los 4 lugares donde se mueve dinero: compra y venta directas, y confirmación de compra y de venta.
- **Corrección de la venta:** la caja del negocio ahora resta los guaraníes pagados y verifica antes que haya fondos. Si no alcanzan, la venta se rechaza sin mover las cuentas del cliente.
- **Anulación con devolución** (`facturacion.devolver_fondos`):
  - Se ejecuta cuando SIFEN **aprueba** la cancelación o la nota de crédito, no antes. Así, si SIFEN la rechaza, no hay que volver a mover el dinero.
  - Aplica el inverso exacto de los movimientos registrados, dentro de una transacción con bloqueo de filas. La operación pasa a `ANULADA`: se agregó la transición permitida SUCCESS → ANULADA en el modelo inmutable.
  - Antes de anular verifica que el cliente todavía tenga los fondos recibidos (por ejemplo, los dólares comprados). Si el saldo cambió antes de la aprobación, la devolución queda **pendiente** y se puede reintentar desde el panel.
  - Las operaciones anteriores a este cambio, que no tienen movimientos registrados, se reconstruyen con los datos de la operación.
- **Anulación para corregir:**
  - No toca los saldos y la operación sigue vigente.
  - Habilita "Volver a facturar", que emite una nueva factura con los datos actuales del cliente.
  - La nota de crédito usa `iMotEmi = 1` (Devolución y ajuste de precios) en lugar de `2` (Devolución).
- **Ventanilla:** la devolución en efectivo se hace en la caja. Desde el panel solo se puede corregir la factura, y el cliente no puede pedir la anulación en línea.
- **Solicitud del cliente:** siempre con devolución. Al pedirla y al aprobarla se verifica que la devolución sea posible.
- **Panel:**
  - Al anular hay que elegir "Devolver los fondos" o "Solo corregir la factura".
  - Botones "Volver a facturar" y "Reintentar devolución de fondos".
  - Sección de devoluciones pendientes.
  - Indicador de operación anulada.
- **Pruebas:** `procesamiento_operaciones/test_devolucion_fondos.py` (11 pruebas).

---

## 4. Comandos Ejecutados
```bash
python manage.py makemigrations procesamiento_operaciones -n devolucion_fondos
python manage.py test procesamiento_operaciones.test_devolucion_fondos procesamiento_operaciones.test_facturacion_anulacion
python manage.py test
```

---

## 5. Evidencia de Pruebas Exitosas
```text
Ran 268 tests in 464.309s
OK
```
