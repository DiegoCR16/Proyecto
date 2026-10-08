# Registro de Conversación IA (CHIA) - Detección y Diferenciación de Tipos de Instrumentos Financieros

**Fecha:** 07/10/2026  
**Historia / Mejora:** Diferenciación y enrutamiento por tipo de instrumento y cuenta financiera (Tarjetas, Cuentas Locales SIPAP, Billeteras Electrónicas Tigo Money, Cuentas Extranjeras SWIFT/IBAN).

## Resumen de Requerimientos Implementados
1. **Estructura de Datos y Tipos de Cuenta:**
   - Actualización de `PaymentMethod` y `ClientAccreditationMethod` para incluir y categorizar explícitamente:
     - `TARJETA_CREDITO` (Procesado por Stripe / Bancard)
     - `TARJETA_DEBITO` (Débito Directo)
     - `CUENTA_BANCARIA_LOCAL` (Caja de Ahorro / Cta. Cte. - SIPAP)
     - `BILLETERA_ELECTRONICA` (Móvil - Tigo Money / Personal)
     - `CUENTA_BANCARIA_EXTRANJERA` (Transferencia Internacional SWIFT / IBAN)
   - Adición del campo `swift_iban` en los modelos correspondientes.

2. **Lógica de Validación y Enrutamiento en el Backend:**
   - Validación robusta en métodos `clean()` y servicios de procesamiento de órdenes (`process_purchase`, `process_sale`) exigiendo campos específicos según el tipo de instrumento (número de tarjeta y expiración para tarjetas, teléfono para billeteras, cuenta y banco para cuentas locales, y código SWIFT/IBAN para cuentas extranjeras).
   - Conexión mediante `PaymentGatewayFactory` con las pasarelas correspondientes (`StripeGateway`, `BancardGateway`, `TigoMoneyGateway`, `SIPAPGateway`).
   - Asignación automática de estado `PENDING` para liquidación bancaria internacional en transacciones con `CUENTA_BANCARIA_EXTRANJERA`.

3. **Impacto en Base de Datos y Dashboard:**
   - Adición de campos `payment_method_type`, `gateway_reference`, y `gateway_status` en los modelos de transacciones (`CurrencyPurchaseTransaction` y `CurrencySaleTransaction`).
   - Migraciones aplicadas exitosamente (`authentication.0018`, `procesamiento_operaciones.0008`, `tasas_cambio.0008`).

4. **Pruebas Unitarias:**
   - Creación de suite de pruebas unitarias (`procesamiento_operaciones/test_financial_instruments.py`) verificando validación y enrutamiento para cada uno de los 5 tipos de instrumentos financieros.
