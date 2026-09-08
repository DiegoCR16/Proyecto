# Registro de Conversación IA - Interfaz Dinámica, Solicitud de Clientes y Roles Keycloak

**Fecha:** 06 de Septiembre de 2026
**Historia / Requerimiento:**
1. Flujo de solicitud para ser cliente (creación de grupo cliente corporativo / físico/jurídico pendiente de aprobación por el Administrador).
2. Roles generales del sistema: Administrador, Analista, Cajero y Cliente.
3. Roles internos del grupo cliente: Cliente (dueño/solicitante con rol JEFE), Operador y Analista.
4. Tarjeta en la interfaz del cliente para solicitar asociar un usuario al grupo cliente (Operador o Analista), pendiente de aprobación manual por el Administrador.
5. Panel de administración con listado de solicitudes pendientes de clientes y miembros, con botones de aprobación que sincronizan automáticamente con Keycloak Admin API e insertan roles (`JEFE`, `OPERADOR`, `ANALISTA`, `CLIENTE`) al crear grupos.
6. Ajuste de categorías y visualización de beneficios (restringidos a modo cliente activo, sin mostrar "Minorista" a usuarios regulares sin grupo).

## Resumen de Cambios
- **Modelos (`ClientRegistrationRequest`, `MemberRequest`):** Gestión de solicitudes de creación de grupo cliente y asociación de operadores/analistas.
- **Vistas y Endpoints:** `register_view`, `request_member_view`, `admin_approve_client_request`, `admin_approve_member_request`.
- **Plantillas:** `register.html`, `client_dashboard.html`, `admin_client_list.html`.
- **Pruebas Unitarias:** 29/29 pruebas pasadas exitosamente.
