# Registro de Conversación / Implementación: Aplicación de Beneficios y Tasas Diferenciales por Cliente en Compra y Venta de Divisas

- **Fecha:** 10 de Octubre de 2026
- **Objetivo:** Garantizar que las operaciones de compra y venta de divisas distingan correctamente entre usuarios y clientes (`Cliente` vs `User`/`UserProfile`), permitiendo que múltiples usuarios asociados a un mismo cliente corporativo o VIP (a través de `UsuarioClienteRelacion`) utilicen los beneficios y tasas de cambio preferenciales (VIP 2%, Corporativo 4%) de dicho cliente en sus transacciones.

## Cambios Realizados:
1. **Resolución Robusta de Clientes y Relaciones Multi-usuario (`get_active_client` y `_resolve_client_and_methods`):**
   - Se amplió la búsqueda en `UsuarioClienteRelacion` para soportar múltiples identificadores (`keycloak_id`, ID de usuario en texto `str(user.id)`, y `username`), asegurando que cualquier usuario autorizado perteneciente a un cliente herede correctamente la categoría y los beneficios cambiarios de ese cliente.
2. **Aplicación de Tasas Diferenciales y Beneficios en Compra y Venta:**
   - Tanto `CurrencyPurchaseService` como `CurrencySaleService` resuelven el cliente activo y consultan el servicio de simulación (`SimuladorConversionService.simular`) pasando la categoría específica del cliente (`cliente.categoria`), aplicando de esta forma las tasas de cambio preferenciales (VIP / Corporativo) en lugar de la tasa general (Minorista).
3. **Ciclo de Vida de Pasarelas de Pago (`PaymentGatewayLog`) en PSE-31:**
   - Las operaciones iniciadas en estado `PENDING` registran sus logs de pasarela como `PENDING` (Pendiente) en lugar de aprobarse prematuramente.
   - Al confirmar la transacción, el log de pasarela pasa a `SUCCESS` (Aprobado / Exitoso).
   - Al cancelar la transacción, el log de pasarela se marca como `CANCELLED` (Cancelado).
4. **Pruebas Unitarias:**
   - Se añadió la prueba unitaria `test_multiple_users_belong_to_client_benefit` en `procesamiento_operaciones/test_PSE_13.py` que valida que varios usuarios asociados al mismo cliente corporativo operen exitosamente aplicando el beneficio correspondiente.
5. **Selección Dinámica de Cuentas Multidivisa (`_resolve_client_and_methods`):**
   - El sistema ahora selecciona de forma inteligente las cuentas bancarias o medios de acreditación del cliente basándose en la divisa operada (`from_currency` / `to_currency`), evitando que saldos en moneda extranjera (ej. USD, EUR) sean interpretados como Guaraníes (PYG).
6. **Evaluación Correcta de Límites en Moneda Extranjera (`eval_amount_pyg`):**
   - Se ajustó el cálculo del equivalente en Guaraníes para la validación de límites operativos mínimos (50.000 PYG) y máximos (1.000.000.000 PYG) cuando la moneda de origen es extranjera (ej. USD, EUR), multiplicando el monto por el tipo de compra ajustado por el beneficio del cliente en lugar de utilizar ratios de cambio cruzados.
