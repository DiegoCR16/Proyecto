# Registro de Conversación y Desarrollo (CHIA) - PSE-4: Inicio de Sesión Único (SSO) y Autenticación de Doble Factor

- **Fecha:** 28 de Agosto de 2026
- **Historia de Usuario:** PSE-4 (SSO & MFA / iToken)
- **Rama Git:** `feature/PSE-4`
- **Asignatura:** Ingeniería de Software 2 - FPUNA

## Resumen de Implementación
1. **Configuración y Modelos:**
   - Creación de modelos `Role`, `UserProfile` (con soporte para cliente corporativo, estado MFA/iToken y Keycloak ID) y `AuditLog` para auditoría de eventos de seguridad.
   - Configuración de parámetros de Keycloak OIDC en `settings.py`.
2. **Lógica de Autenticación y SSO:**
   - Vista de inicio de sesión local y redirección al flujo OIDC de Keycloak (`/auth/sso/` y `/auth/callback/`).
   - Requisito obligatorio de MFA/iToken (`requires_mfa()`) para usuarios con roles administrativos y clientes corporativos.
   - Vista de verificación de iToken (`/auth/mfa/`).
3. **Redirección por Roles:**
   - Redirección personalizada al panel correspondiente (`admin_dashboard`, `corporate_dashboard`, `client_dashboard`).
4. **Pruebas Unitarias (PUN):**
   - Implementación y ejecución exitosa de 7 pruebas unitarias cubriendo autenticación local, SSO, fallos de credenciales, verificación MFA y redirección por roles.
5. **Auditoría:**
   - Registro automático de intentos exitosos y fallidos en `AuditLog` con IP del cliente.
