# Registro de Conversación IA - Inclusión de Comisión en Backend y Ocultamiento Visual (Compra y Venta)

- **Fecha:** 06/10/2026
- **Objetivo:** 
  1. Integrar el cálculo de comisiones e impuestos de forma interna en el backend para operaciones de compra y venta de divisas sin adicionarlas ni restarlas del monto seleccionado por el cliente (el total a pagar en compra y el monto acreditado en venta corresponden exactamente al monto operado).
  2. Ocultar visualmente la comisión y los impuestos de la interfaz del cliente y del desglose transparente (`transparent_breakdown`), manteniéndolos exclusivamente en el backend para fines contables y de auditoría.
- **Cambios Realizados:**
  - `CurrencyPurchaseService`: Ajustado para que `total_cost_pyg` / `total_origen` no sumen la comisión ni los impuestos sobre el monto seleccionado por el cliente.
  - `CurrencySaleService`: Ajustado para que `total_pyg` no reste la comisión ni los impuestos del resultado de la conversión.
  - Desglose transparente (`transparent_breakdown`): Removidas las menciones explícitas a comisión e impuestos para el cliente.
  - Gestión de saldos multimoneda en cuentas de origen y destino (`ClientAccreditationMethod`): Se implementó la conversión automática de montos a la moneda nativa de la cuenta de origen (`origin_method.moneda`) para el débito de fondos y a la moneda de la cuenta de destino (`destination_method.moneda`) para la acreditación, evitando errores de validación de saldo al operar en divisas extranjeras (ej. USD, EUR).
  - Pruebas unitarias: Actualización de `test_PSE_14.py` e incorporación de `test_multi_currency_account_balances_and_debits` en `test_medios_acreditacion_operaciones.py`.
