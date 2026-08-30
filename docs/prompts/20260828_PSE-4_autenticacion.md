# Registro de Conversación y Desarrollo (CHIA) - PSE-4: Inicio de Sesión Único (SSO) y Autenticación de Doble Factor

- **Fecha:** 28 de Agosto de 2026 (Actualizado: autenticación exclusiva Keycloak SSO e interfaz de registro)
- **Historia de Usuario:** PSE-4 (SSO & MFA / iToken)
- **Rama Git:** `feature/PSE-4`
- **Asignatura:** Ingeniería de Software 2 - FPUNA

## Resumen de Implementación
1. **Configuración y Modelos:**
   - Creación de modelos `Role`, `UserProfile` (con soporte para cliente corporativo, estado MFA/iToken y Keycloak ID) y `AuditLog` para auditoría de eventos de seguridad.
   - Configuración de parámetros de Keycloak OIDC en `settings.py`.
2. **Lógica de Autenticación y SSO Exclusivo:**
   - Eliminación del formulario de credenciales locales; autenticación centralizada a través de Keycloak OIDC (`/auth/sso/` y `/auth/callback/`).
   - Interfaz de inicio de sesión actualizada: botón principal "Iniciar Sesión" dirigido a Keycloak SSO y botón "Registrarse" (para nuevos usuarios).
   - Requisito obligatorio de MFA/iToken (`requires_mfa()`) para usuarios con roles administrativos y clientes corporativos.
   - Vista de verificación de iToken (`/auth/mfa/`).
3. **Redirección por Roles:**
   - Redirección personalizada al panel correspondiente (`admin_dashboard`, `corporate_dashboard`, `client_dashboard`).
4. **Pruebas Unitarias (PUN):**
   - Implementación y ejecución exitosa de pruebas unitarias cubriendo redirección SSO Keycloak, verificación de MFA, callback SSO y redirección por roles.
5. **Auditoría:**
   - Registro automático de eventos exitosos y fallidos en `AuditLog` con IP del cliente.

## Criterios Probados
1. **Requisito de MFA:** Verificación de que los roles administrativos (`Admin`) y clientes corporativos (`Corporate`) requieran obligatoriamente verificación de iToken/MFA, mientras que usuarios individuales no.
2. **Redirección SSO Keycloak:** Comprobación de que el acceso al login redirige correctamente al servidor OIDC de Keycloak con los parámetros de cliente y redirección configurados.
3. **Callback OIDC Keycloak:** Simulación y validación del intercambio de código de autorización por parte de Keycloak, generando el registro de auditoría (`SSO_LOGIN_SUCCESS`).
4. **Verificación de iToken / MFA:** Prueba de introducción de iToken correcto (`123456`) con redirección exitosa al panel, y manejo de error ante iToken inválido.
5. **Redirección por Roles:** Verificación de que el acceso a `/auth/dashboard/` renderiza la plantilla de panel correcta según el rol asignado al usuario.

## Comandos Utilizados
- Ejecución de pruebas unitarias de Django:
  ```bash
  python manage.py test
  ```
- Ejecución con opción de verbosidad:
  ```bash
  python manage.py test --verbosity=2
  ```

## Evidencia de las Pruebas Exitosas
```text
Creating test database for alias 'default'...
......Found 6 test(s).
System check identified no issues (0 silenced).

----------------------------------------------------------------------
Ran 6 tests in 12.976s

OK
Destroying test database for alias 'default'...
```
