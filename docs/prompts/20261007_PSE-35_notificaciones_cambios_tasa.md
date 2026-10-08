# Registro de Conversación IA (CHIA) - Historia de Usuario PSE-35: Notificaciones por Cambios de Tasa

**Fecha:** 08/10/2026  
**Historia de Usuario:** PSE-35: Notificaciones por cambios de tasa (Epic: Tasas de Cambio).  
**Rama Git:** `feature/PSE-35`

## Resumen de Requerimientos Implementados

1. **Modelos de Datos y Estructura (PSE-35):**
   - **`CurrencyAlert`**: Almacena las alertas configuradas por cada usuario cliente, asociando divisa (USD, EUR, BRL, ARS, PYG), condición de tasa (Compra o Venta), tasa objetivo, canal de notificación deseado (Email, Push, Ambos), estado de activación (`is_active`) y estado de disparo (`triggered`).
   - **`NotificationLog`**: Registra de forma persistente todas las notificaciones enviadas a los usuarios por cumplimiento de alertas de tasa objetivo o por detecciones de variaciones abruptas en el mercado cambiario.

2. **Lógica de Negocio y Servicios:**
   - **`check_and_trigger_rate_alerts(currency_code, new_buy_rate, new_sell_rate)`**:
     - Verifica automáticamente las alertas activas al actualizarse una cotización de mercado (alcanzar o superar el valor objetivo configurado).
     - Detecta variaciones porcentuales abruptas en la tasa de mercado (cambio $\ge 1.5\%$ respecto a la cotización previa registrada en el historial) y emite notificaciones masivas (`broadcast`) a todos los usuarios registrados en el sistema.

3. **Interfaz de Usuario ("Corporate Modern"):**
   - **Formulario de Configuración de Alertas**: Selector de divisa, condición de tasa, tasa objetivo numérica y canales de notificación (Email / Push / Ambos).
   - **Panel / Tabla de Alertas Vigentes**: Listado interactivo de alertas personales con botones de activación/desactivación (toggle) y eliminación.
   - **Banner / Toast de Notificación en Vivo**: Componente flotante en tiempo real para visualizar y marcar como leídas las alertas recibidas.

4. **Pruebas Unitarias Exclusivas (PUD):**
   - Creación del archivo `tasas_cambio/test_PSE_35.py` de forma aditiva y exclusiva, validando:
     - Ciclo CRUD completo de alertas (creación, consulta, edición, desactivación y eliminación).
     - Disparo automático de notificaciones cuando el mercado alcanza o supera la tasa objetivo.
     - Detección de variaciones porcentuales abruptas en corto tiempo y emisión de alertas a usuarios registrados.
   - Verificación de que la totalidad de las pruebas unitarias de `tasas_cambio` pasaron exitosamente (22 tests ejecutados y aprobados).

5. **Documentación de Código (PDO):**
   - Incorporación de Docstrings en formato Google/Sphinx en todas las clases, modelos, vistas y funciones creadas o modificadas.
