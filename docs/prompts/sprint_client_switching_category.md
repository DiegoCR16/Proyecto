# Registro de Conversación IA (CHIA) - Cambio Dinámico de Cliente y Categoría en Dashboard

## Problema Reportado
Un usuario con múltiples clientes asociados en el sistema no visualizaba el cambio de categoría (ej. Corporativo vs Minorista) al alternar entre dichos clientes en su panel (dashboard). La categoría dependía estáticamente del perfil del usuario y no del cliente activo seleccionado en ese momento.

## Solución Implementada
1. **Contexto de Clientes Asociados:** Se actualizó `get_user_interface_context` en `gestion_clientes/views.py` para recuperar dinámicamente todos los clientes asociados al usuario mediante `UsuarioClienteRelacion` (o todos en caso de administradores).
2. **Selección y Cambio de Cliente Activo:** Se implementó la vista `switch_client_view` y la ruta `/auth/switch-client/<client_id>/` para alternar el cliente activo en la sesión del usuario (`request.session['active_client_id']`).
3. **Dinámica de Categoría y Beneficios:** Se modificó `dashboard_redirect_view` en `authentication/views.py` para que evalúe y utilice la categoría del **cliente activo** (`active_client.categoria`) en lugar del perfil general del usuario, aplicando de forma inmediata los descuentos y beneficios arancelarios correspondientes.
4. **Interfaz de Usuario (Dashboards):** Se incorporó el selector de cliente en las barras de navegación de los paneles (cliente, corporativo y administrativo).
5. **Pruebas Unitarias:** Se añadió una prueba unitaria (`test_switch_between_multiple_clients_category`) validando la actualización correcta de la categoría y beneficios al cambiar de cliente.
