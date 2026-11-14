# Registro de Conversación IA (CHIA) - Correo real por SMTP y retiro de Mailpit

**Fecha:** 13 de Noviembre, 2026
**Rama:** `actualizacion`
**Herramienta IA:** Claude Code (Claude Opus 5.5)

## Contexto
Con el dominio público `globalexchange-g05.duckdns.org` (servidor AWS EC2) el sistema debe enviar correos reales
(facturas, alertas de tasas, avisos) a direcciones como Gmail. Mailpit, el capturador de correos de desarrollo, deja de ser necesario.

## Conversación
1. Se analizó cómo enviaba correos el proyecto: `settings.py` tenía la configuración SMTP comentada (apuntando a `mailpit`) y
   `MAILERS` (formato de Django 6.1) con el backend de consola. Keycloak también tenía `mailpit` como servidor SMTP del realm.
2. Como DuckDNS no permite configurar correo propio (SPF/DKIM), se eligió enviar con **Gmail + contraseña de aplicación**
   desde una cuenta del grupo (≈500 correos/día, puerto 587 permitido por AWS).
3. Se acordó que sin `EMAIL_HOST` los correos sigan saliendo por consola, para no romper el entorno de desarrollo de los compañeros.
4. La configuración SMTP de Keycloak (verificación de correo) queda para la rama `feature/PSE-40` (`keycloak_bootstrap`).

## Resumen de Cambios
1. **`globalexchange/email_config.py`** (nuevo): `configurar_mailers(env)` construye `MAILERS` desde variables de entorno
   (`EMAIL_HOST`, `EMAIL_PORT` 587 por defecto, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` true por defecto, timeout 10 s).
2. **`globalexchange/settings.py`**: `MAILERS = configurar_mailers(os.environ)`; se elimina la configuración comentada de Mailpit.
3. **`docker-compose.yml`**: se elimina el servicio `mailpit` y las variables `EMAIL_HOST=mailpit` / `EMAIL_PORT=1025` del servicio `web`.
4. **`.env.example`**: nuevas variables de correo (sin valores reales).

## Pruebas Unitarias
- `globalexchange/test_email_config.py`: 7 pruebas (consola sin host, SMTP con credenciales, valores por defecto, TLS desactivado,
  puerto personalizado, captura de correos en `mail.outbox` durante las pruebas).
- Suite completa: 275 pruebas OK.
