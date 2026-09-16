# Prompt: Corrección en Gestión de Usuarios (Roles en Keycloak y Validación de Contraseñas)

## Fecha
15 de Septiembre de 2026

## Requerimiento del Usuario
- En la gestión de usuarios, al agregar un nuevo usuario no se le asignaba el rol elegido en Keycloak.
- La validación y advertencia de contraseñas no funcionaba (mínimo 8 caracteres, carácter especial, números, mayúsculas).
- Al editar un usuario y cambiar su rol, en la interfaz se actualizaba pero en Keycloak no se aplicaba el cambio.

## Solución Implementada
1. **Validación y Advertencia de Contraseña:**
   - Se añadió texto descriptivo/advertencia de los requisitos de contraseña en `admin_user_form.html` (Mínimo 8 caracteres, mayúscula, número y carácter especial).
   - Se implementó la función de validación `validate_password_complexity` en `gestion_clientes/views.py` tanto para la creación como para la edición de usuarios.

2. **Sincronización de Roles en Keycloak (Creación y Edición):**
   - Se implementó la función `sync_user_roles_to_keycloak` que interactúa con la API Admin de Keycloak para:
     - Crear o actualizar la cuenta de usuario.
     - Obtener la lista completa de roles del realm de Keycloak y realizar una búsqueda insensible a mayúsculas/minúsculas para emparejar roles existentes (`Admin`, `Analista`, `Cajero`, `Cliente`, etc.).
     - Asignar correctamente los roles a nivel de realm (`role-mappings/realm`) utilizando asignación en lote, limpiando los roles anteriores y asignando el rol principal y roles adicionales seleccionados.
     - Manejar conflictos (409) si el usuario o rol ya existe.
   - Se otorgó el rol de cliente `realm-admin` al service account del cliente en el realm (`docker/global-exchange-realm-realm.json`) para conceder permisos suficientes de administración y asignación de roles de realm vía API REST.
   - Se integró `sync_user_roles_to_keycloak` en `admin_user_create_view` y `admin_user_edit_view`.
