# Corrección de Doble Asignación de Usuario Operativo en Cuenta Cliente

## Contexto del Problema
Cuando un usuario en una cuenta cliente (dueño/administrador del grupo corporativo) solicitaba asociar a otro usuario (por ejemplo, "Jorge" como Analista u Operador), el administrador aprobaba la solicitud (`admin_approve_member_request`). Debido a un bucle que intentaba registrar tanto el `keycloak_id` como el ID de la base de datos de usuario (`str(target_user.id)`), se creaban dos registros en `UsuarioClienteRelacion` para el mismo usuario y cliente. Esto ocasionaba una **doble asignación** visualizada en el panel de detalle del cliente ("USUARIOS ACTUALMENTE ASOCIADOS").

## Solución Implementada
1. **Prevención en Aprobación de Miembros:** En `admin_approve_member_request` (`gestion_clientes/views.py`), se eliminó el bucle redundante y se unificó la asignación utilizando `target_profile.keycloak_id or str(target_user.id)`, creando una única relación `UsuarioClienteRelacion`.
2. **Deduplicación Automática en Vista de Detalle:** En `admin_client_detail_view`, se añadió lógica para detectar y eliminar automáticamente cualquier registro duplicado preexistente en la base de datos al renderizar la ficha del cliente.
3. **Pruebas Unitarias:** Se agregó la prueba unitaria `test_admin_approve_member_request_no_double_assignment` en `gestion_clientes/test_PSE_7.py` para validar que la aprobación de solicitudes de miembros cree exactamente una relación sin duplicados.
