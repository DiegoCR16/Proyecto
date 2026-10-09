# Registro de Conversación IA (CHIA) - Integración de Alertas de Tasas al Dashboard del Cliente

**Fecha:** 08/10/2026  
**Historia / Requerimiento:** Integración de Alertas de Tasas (PSE-35) al Dashboard del Cliente.  
**Rama Git:** `feature/PSE-35`

## Resumen de Requerimientos Implementados

1. **Integración en la Vista del Panel (`authentication/views.py`):**
   - Actualización de `dashboard_redirect_view` para consultar y adjuntar al contexto los contadores de alertas activas (`active_alerts_count`) y las notificaciones no leídas (`unread_notifications`, `unread_notifications_count`) del usuario autenticado.

2. **Integración en las Plantillas de Dashboard (`client_dashboard.html`, `corporate_dashboard.html`, `user_dashboard.html`):**
   - **Tarjeta de Acceso Rápido ("Alertas de Tasas"):** Inclusión de una tarjeta interactiva en la grilla de navegación rápida de cada dashboard de cliente/usuario, indicando el número de alertas activas y permitiendo el acceso directo a la gestión de alertas (`{% url 'tasas_cambio:currency_alerts' %}`).
   - **Banderas / Toasts de Notificación en Tiempo Real:** Visualización automática de notificaciones no leídas en la parte superior del panel del cliente con opción de marcarlas como leídas.

3. **Pruebas Unitarias (PUD):**
   - Incorporación del test unitario `test_client_dashboard_rate_alerts_integration` en `tasas_cambio/test_PSE_35.py` para validar que la vista del dashboard del cliente responde correctamente (status 200) y contiene la sección de alertas de tasas y el enlace de redirección.
   - Verificación de la ejecución exitosa de la suite de pruebas unitarias.

4. **Documentación de Código (PDO):**
   - Mantenimiento de la estructura de docstrings en funciones y vistas modificadas.
