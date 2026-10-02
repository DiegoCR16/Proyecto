# Registro de Conversación / Implementación: Venta de Divisas Multidivisa / Cruzada (PSE-14)

- **Fecha:** 01 de Octubre de 2026
- **Objetivo:** Habilitar la selección de la moneda de destino en el dashboard y servicio de procesamiento de operaciones de venta de divisas (`procesamiento_operaciones`), permitiendo realizar operaciones de venta cruzada (de una moneda extranjera a otra o a moneda local), de forma idéntica a las operaciones de compra.

## Cambios Realizados:
1. **Modelo `CurrencySaleTransaction`:**
   - Se agregaron campos de auditoría y soporte multidivisa (`moneda_origen_id`, `monto_origen`, `moneda_destino_id`, `monto_destino`, `tipo_cambio_cruzado`, `tipo_cambio_local_origen`, `monto_moneda_local`, `total_origen`).
   - Se generó y aplicó la migración `0006_currencysaletransaction_moneda_destino_id_and_more.py`.

2. **Servicio `CurrencySaleService`:**
   - Actualización en `process_sale` y `initiate_sale` para soportar cualquier `to_currency` utilizando `SimuladorConversionService.simular(...)` con cálculo transparente y validación cruzada.

3. **Interfaz Web (`currency_sale.html`):**
   - Se reemplazó el campo fijo de moneda destino (PYG) por un selector dinámico (`to_currency`) con todas las monedas disponibles en el sistema, alineándolo con el formulario de compra.

4. **Pruebas Unitarias:**
   - Se añadió el test unitario `test_cross_currency_sale_operation` en `test_PSE_14.py` para verificar ventas cruzadas exitosas (ej. USD a EUR).
