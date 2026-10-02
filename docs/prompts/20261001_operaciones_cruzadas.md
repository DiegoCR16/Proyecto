# Registro de Conversación IA - Soporte de Operaciones Cruzadas en Compra/Venta de Divisas

## Contexto
- **Solicitud:** Modificar el módulo de compra y venta de divisas para soportar operaciones cruzadas (compra de divisa extranjera pagando con otra divisa extranjera) según instrucciones de `prompt.txt`.
- **Adaptaciones Realizadas:**
  1. **Modelo / Base de Datos (`CurrencyPurchaseTransaction`):** Se añadieron los campos `moneda_origen_id`, `monto_origen`, `moneda_destino_id`, `monto_destino`, `tipo_cambio_cruzado`, `tipo_cambio_local_origen` y `monto_moneda_local`.
  2. **Lógica de Cálculo (Triangulación de Divisas):** Se integró el cálculo de tasa cruzada `(Tasa Moneda Origen / Tasa Moneda Destino)` en `SimuladorConversionService` y servicios transaccionales.
  3. **Interfaz de Usuario (UI) y Totales:** Soporte en formularios, recibos e historial para calcular y mostrar el **total a pagar y total debitado en función de la moneda de origen elegida** (`total_origen` en su respectiva divisa), reservando los Guaraníes (`total_pyg` / `monto_moneda_local`) exclusivamente para auditoría y contabilidad interna.
  4. **Movimientos de Caja:** Registro explícito en el desglose de auditoría sobre el ingreso de stock de la moneda origen y egreso de stock de la moneda destino.
  5. **Pruebas Unitarias:** Creación de `procesamiento_operaciones/test_operaciones_cruzadas.py` para verificar las operaciones cruzadas y la integridad con el sistema existente.
