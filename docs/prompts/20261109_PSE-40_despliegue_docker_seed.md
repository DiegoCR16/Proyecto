# Registro de Conversación IA (CHIA) - Despliegue con Docker Compose y Población de Datos

**Fecha:** 9 de Noviembre, 2026
**Historia / Tarea:** PSE-40 - Automatización de Despliegue de Entornos con Docker Compose y Población de Datos (SCR).
**Rama:** `feature/PSE-40`
**Herramienta IA:** Claude Code (Claude Opus 5.5)

## Conversación
1. **Pregunta:** cuál sería el mejor dominio/hosting gratuito para levantar el proyecto.
   - **Análisis:** el sistema necesita 4 servicios (Django + Gunicorn, PostgreSQL, Keycloak y Mailpit); Keycloak (Java) necesita ~1 GB de RAM.
     Los PaaS gratuitos (Render, Railway, Fly.io, PythonAnywhere) no alcanzan o no soportan docker-compose.
   - **Recomendación:** VM de Oracle Cloud Always Free (ARM, hasta 24 GB RAM) + subdominio gratuito de DuckDNS + Caddy para HTTPS automático (Let's Encrypt).
2. **Problemas detectados que impedían el despliegue:** `DEBUG`, `SECRET_KEY` y `ALLOWED_HOSTS` fijos en `settings.py`; faltaba `STATIC_ROOT`
   (`collectstatic` fallaba y abortaba `entrypoint-prod.sh`); faltaba `CSRF_TRUSTED_ORIGINS`; Keycloak arrancaba con `--optimized` sin build previo.
3. **Pedido:** implementar la historia PSE-40 en una rama local (los commits y el push los realiza el desarrollador).

## Resumen de Cambios
1. **`globalexchange/settings.py`:** `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` y cabeceras de proxy (`USE_X_FORWARDED_PROTO`) leídos del entorno
   (helpers `env_bool` / `env_list`); `STATIC_ROOT` y middleware de WhiteNoise. Los valores por defecto mantienen el comportamiento de desarrollo.
2. **`authentication/management/commands/seed_data.py`:** comando idempotente que carga divisas, denominaciones, métodos de pago,
   tabla de comisiones por categoría, roles, cajas, cliente demo y usuarios de prueba. Opción `--sin-usuarios`.
   `setup_test_data.py` ahora delega en este comando.
3. **`authentication/management/commands/keycloak_bootstrap.py`:** usa la cuenta de servicio (realm-admin) para registrar la URL de callback
   de `KEYCLOAK_REDIRECT_URI` en el cliente OIDC y crear los usuarios de prueba en Keycloak (el login es exclusivo vía Keycloak).
4. **`entrypoint.sh` / `entrypoint-prod.sh`:** `migrate` → `seed_data` → `keycloak_bootstrap` (en segundo plano), controlables con `SEED_DATA`, `SEED_TEST_USERS`, `KEYCLOAK_BOOTSTRAP`.
5. **`docker-compose.prod.yml`:** servicio Caddy (80/443), puertos internos con `!reset`, Keycloak con `start --import-realm --http-enabled=true --proxy-headers=xforwarded`,
   variables obligatorias validadas con `${VAR:?}`.
6. **`docker/caddy/Caddyfile`**, **`deploy.sh`** (`dev`, `prod`, `seed`, `logs`, `down`), **`.env.production.example`** y guía **`docs/DESPLIEGUE.md`**.
7. **`requirements.txt`:** se agrega `whitenoise`.

## Pruebas Unitarias
- `authentication/test_PSE_40.py`: 21 pruebas (seed completo, idempotencia, no sobrescribir cambios del admin, `--sin-usuarios`,
  Admin API de Keycloak simulada con `unittest.mock`, tolerancia a Keycloak caído, helpers de entorno).
- Suite completa: 202 pruebas OK.
