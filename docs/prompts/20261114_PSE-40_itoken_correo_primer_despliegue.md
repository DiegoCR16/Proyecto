# Registro de Conversación IA (CHIA) - iToken por correo y opciones del primer despliegue

**Fecha:** 14 de Noviembre, 2026
**Historia:** PSE-40 (despliegue) + mejora del MFA de PSE-4
**Rama:** `feature/PSE-40`
**Herramienta IA:** Claude Code (Claude Opus 5.5)

## Conversación (resumen)
1. El equipo quiere desplegar producción **sin usuarios ni clientes de prueba** e ir cargándolos desde el servidor,
   asignando roles en la consola de Keycloak. Se confirmó que el sistema lee los roles de Keycloak en cada inicio de sesión.
2. Se consultó por qué la contraseña del admin de Keycloak ya no era la predefinida: en producción la consola es pública,
   por lo que se decidió que el script **pida la contraseña la primera vez** en lugar de usar `admin`/`admin` o una del repositorio.
3. El iToken era fijo (`123456`) y quedaba verificado para siempre en el perfil (`itoken_verified`). Se pidió un
   **código distinto por correo en cada inicio de sesión** (verificación en dos pasos real).

## Resumen de Cambios
### Primer despliegue (C0)
- `deploy-remoto.sh`: opción `--sin-usuarios-prueba`; en el primer despliegue pide (dos veces, sin mostrar) la contraseña del
  admin de Keycloak y la envía por SSH (stdin) a un archivo temporal que se borra al terminar.
- `scripts/generar_env_produccion.py`: `--keycloak-admin-password-file`, `--sin-usuarios-prueba` (`SEED_TEST_USERS=false`)
  y validación de la contraseña (≥ 12 caracteres, sin espacios, comillas, `$` ni `#`).

### iToken por correo
- `authentication/mfa.py` (nuevo): código aleatorio de 6 dígitos (`secrets`), guardado en la sesión solo como hash HMAC-SHA256,
  vence a los 5 minutos, uso único, bloqueo tras 5 intentos, reenvío limitado a uno por minuto, envío con `send_mail`.
- `authentication/views.py`: el callback de Keycloak reinicia `itoken_verified` y envía el código en cada login;
  `mfa_verify_view` valida el código, permite reenviar y audita `MFA_SUCCESS`, `MFA_FAILED` y `MFA_CODE_RESENT`.
- `authentication/middleware.py`: `MFAPendienteMiddleware` impide acceder a otras páginas con el iToken pendiente.
- `mfa_verify.html`: muestra el correo enmascarado, botón "Reenviar" y "Cancelar y cerrar sesión"; se quitó la ayuda `123456`.
- `docs/DESPLIEGUE.md`: primer despliegue, arranque sin usuarios (crear el primer Admin en Keycloak) e iToken por correo.

## Pruebas Unitarias
- `authentication/test_mfa_correo.py` (17): envío en cada login, código correcto, uso único, hash sin código en claro,
  intentos restantes, bloqueo, vencimiento, reenvío y su límite, middleware, logout, usuario sin correo, usuario sin MFA, fallo SMTP.
- `authentication/tests_PSE_4.py`: pruebas de MFA adaptadas al código por correo.
- `scripts/test_generar_env_produccion.py`: +7 pruebas (contraseña elegida/aleatoria/inválida, sin usuarios de prueba, archivo).
- Suite completa: 336 pruebas OK.
