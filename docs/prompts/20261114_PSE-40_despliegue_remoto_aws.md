# Registro de Conversación IA (CHIA) - Script de despliegue remoto a producción (AWS)

**Fecha:** 14 de Noviembre, 2026
**Historia:** PSE-40 - Automatización de Despliegue de Entornos con Docker Compose y Población de Datos (SCR)
**Rama:** `feature/PSE-40`
**Herramienta IA:** Claude Code (Claude Opus 5.5)

## Contexto
- Hito 7 (SCR): script de despliegue automático en desarrollo y producción (código fuente, base de datos y población).
- Hito 8: funcionamiento correcto del script de despliegue automático para el entorno de producción.
- Oracle Cloud Always Free no tenía capacidad ARM en São Paulo, por lo que se usó **AWS EC2** (plan gratuito, 100 USD de crédito):
  instancia `t4g.small` (el plan gratuito no permite `t4g.medium`), Ubuntu 24.04 ARM, IP elástica `16.58.240.129`
  y dominio DuckDNS `globalexchange-g05.duckdns.org`.

## Conversación (resumen)
1. Se integró `main` (rama `actualizacion`, V5.1 con correo SMTP) en `feature/PSE-40`. Los conflictos eran solo de fin de línea
   (CRLF) en archivos que PSE-40 no modificaba: se resolvieron con la versión de `main`; `settings.py` combina ambos cambios.
2. Se decidió que el despliegue a producción se haga **desde la PC con un solo comando** por SSH (no requiere clonar el repositorio en el servidor).
3. Con 2 GB de RAM se agregó swap de 4 GB y límites de memoria para Keycloak y Gunicorn.
4. Mailpit se retiró también de producción; el correo de Keycloak (verificación de correo) usa el mismo Gmail del grupo que Django.

## Resumen de Cambios
1. **`deploy-remoto.sh`** (nuevo): conexión SSH → preparación del servidor (swap, Docker) → respaldo de la BD → subida del código
   (`git archive HEAD`) → `.env.production` (se conserva o se genera) → `deploy.sh prod` → verificación HTTPS.
2. **`scripts/generar_env_produccion.py`** (nuevo): genera `.env.production` con dominios, secretos aleatorios y el secreto del cliente de Keycloak.
3. **`keycloak_bootstrap`**: configura el SMTP del realm desde `MAILERS`/`DEFAULT_FROM_EMAIL` (`smtp_realm_desde_settings`, `ensure_smtp`).
4. **`docker-compose.prod.yml`**: sin Mailpit; `JAVA_OPTS_APPEND` de Keycloak configurable (`KEYCLOAK_JAVA_OPTS`).
5. **`entrypoint-prod.sh`**: cantidad de workers de Gunicorn configurable (`GUNICORN_WORKERS`).
6. **`.env.production.example`**: dominio `globalexchange-g05`, variables de correo y de recursos.
7. **`docs/DESPLIEGUE.md`**: guía actualizada para AWS y el despliegue remoto.

## Pruebas Unitarias
- `authentication/test_PSE_40.py`: +6 pruebas (traducción SMTP Django → Keycloak, PUT del realm, comando con correo real, compose sin Mailpit).
- `scripts/test_generar_env_produccion.py`: 10 pruebas (validación de dominio, valores, plantilla, secreto del realm, permisos 600, no sobrescribir).
- Suite completa: 312 pruebas OK.
