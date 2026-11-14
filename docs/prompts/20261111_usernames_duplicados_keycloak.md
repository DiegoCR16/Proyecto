# Registro de Conversación IA (CHIA) - Usuarios duplicados por mayúsculas con Keycloak

**Fecha:** 11 de Noviembre de 2026  
**Rama:** `actualizacion` (creada desde `main`)  
**Relacionado con:** PSE-4 (Autenticación SSO Keycloak) y CRUD de usuarios del administrador  
**Asistente:** Claude Code (claude-opus-5-5)  

---

## 1. Resumen de la Solicitud
> "Cuando me registro en Keycloak el username se registra todo con minúsculas, pero a la hora de crear un usuario desde la interfaz administrador del sistema se distingue entre mayúsculas y minúsculas, por lo cual en la base de datos registra ej: Diego, y luego Keycloak lo guarda todo con minúsculas, y cuando voy a listar los usuarios del sistema aparecen dos: Diego y diego."

---

## 2. Causa
1. El administrador creaba el usuario de Django tal como se escribía: `Diego`.
2. Keycloak siempre guarda el username en minúsculas: `diego`.
3. Al iniciar sesión, Keycloak devuelve `preferred_username = "diego"` y el callback hacía `User.objects.get_or_create(username="diego")`. Como Django distingue mayúsculas, no encontraba a `Diego` y creaba un segundo usuario.

La sincronización de clientes desde Keycloak (`gestion_clientes/views.py`) tenía el mismo problema, y la edición de usuarios no verificaba si el nuevo nombre ya existía.

---

## 3. Desarrollo e Implementación
- **Nuevo módulo `authentication/usuarios.py`**:
  - `normalizar_username`: sin espacios alrededor y en minúsculas, igual que Keycloak.
  - `existe_username`: verifica duplicados sin distinguir mayúsculas (con opción de excluir al usuario en edición).
  - `obtener_o_crear_usuario_keycloak`: busca primero por el ID de Keycloak del perfil, después por username sin distinguir mayúsculas, y solo si no encuentra ninguno crea el usuario en minúsculas.
- **Login SSO (`authentication/views.py` → `keycloak_callback_view`)**: usa `obtener_o_crear_usuario_keycloak`.
- **Sincronización de clientes desde Keycloak (`gestion_clientes/views.py`)**: usa `obtener_o_crear_usuario_keycloak`.
- **Administración de usuarios (`gestion_clientes/views.py`)**: alta de usuario, alta y asociación a un cliente, y edición normalizan el username a minúsculas y rechazan nombres repetidos sin distinguir mayúsculas. La edición ahora también valida que el nombre no pertenezca a otro usuario.
- **Pruebas (`authentication/test_usernames_keycloak.py`)**: normalización, búsqueda sin mayúsculas, búsqueda por ID de Keycloak, login que reutiliza al usuario `Diego` cuando Keycloak devuelve `diego` (sin el arreglo, esta prueba reproduce el duplicado: `2 != 1`), alta en minúsculas, y rechazo de nombres repetidos en alta y edición.

---

## 4. Comandos Ejecutados
```bash
python manage.py test authentication.test_usernames_keycloak
python manage.py test
```

---

## 5. Evidencia de Pruebas Exitosas
```text
Found 198 test(s).
System check identified no issues (0 silenced).
Ran 198 tests in 330.394s
OK
```
