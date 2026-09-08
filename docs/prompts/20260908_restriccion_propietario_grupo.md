# Registro de Conversación IA (CHIA) - Restricción de Solicitud de Asociación de Miembros al Propietario

**Fecha:** 08/09/2026  
**Historia/Incidente:** Restricción en la funcionalidad de solicitud de asociación de usuarios a grupos de clientes.

## Solicitud del Usuario
"La solicitud de asociar usuario al grupo cliente lo tiene que hacer el propietario del grupo nomas no todos los usuario que lleguen a estar asociados a ese grupo."

## Diagnóstico y Análisis
Anteriormente, cualquier usuario que tuviera acceso al grupo (incluyendo operadores o analistas asociados mediante una membresía) podía visualizar el formulario "Solicitar Asociar Usuario al Grupo Cliente" y enviar solicitudes de nuevos miembros. Sin embargo, la regla de negocio establece que solo el **propietario o dueño del grupo** (el perfil jurídico `juridica_profile`) tiene la potestad de solicitar la adición de nuevos operadores o analistas al grupo.

## Solución Implementada
1. **Contexto de Interfaz (`get_user_interface_context`):** Se añadió la variable `is_group_owner` evaluando si el usuario actual coincide con el `juridica_profile` del `active_group`.
2. **Plantilla (`client_dashboard.html`):** Se restringió la visualización del bloque de formulario de solicitud de asociación para que aparezca únicamente si `active_group` está presente y `is_group_owner` es verdadero.
3. **Validación en Vista (`request_member_view`):** Se añadió una validación defensiva en el método de procesamiento POST para que, en caso de recibir una petición por otra vía, se verifique estrictamente que `active_group.juridica_profile == profile`. De lo contrario, la petición es rechazada redirigiendo al panel.
