# Registro de Conversación IA (CHIA) - Migración a PostgreSQL y Release con Docker Compose

**Fecha:** 06 de Septiembre de 2026  
**Proyecto:** Global Exchange (Casa de Cambios - IS2 FPUNA)  
**Objetivo:** Migrar la base de datos a PostgreSQL, configurar el despliegue mediante el comando único `docker compose up --build`, e integrar la importación automática del realm de Keycloak para entornos de release.

---

## Solicitud del Usuario
1. Cambiar la base de datos a PostgreSQL.
2. Permitir que otra persona pueda ejecutar el proyecto utilizando exclusivamente el comando `docker compose up --build`.
3. Asegurar que los datos del realm de Keycloak (incluyendo usuarios, clientes, roles y grupos) se carguen automáticamente al levantar el contenedor (exportación/importación del realm).

---

## Cambios Realizados
1. **Dependencias (`requirements.txt`):**
   - Se añadió `psycopg2-binary>=2.9.0` para la conexión de Django con PostgreSQL.
2. **Configuración de Base de Datos (`globalexchange/settings.py`):**
   - Se actualizó el parámetro `DATABASES` para detectar automáticamente las variables de entorno de PostgreSQL (`DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_PORT`), manteniendo compatibilidad con SQLite para pruebas locales cuando no se especifica `DB_HOST`.
3. **Contenedor Web (`Dockerfile` & `entrypoint.sh`):**
   - Se creó un `Dockerfile` basado en `python:3.11-slim` con las librerías de desarrollo de PostgreSQL (`libpq-dev`).
   - Se creó el script `entrypoint.sh` para verificar la disponibilidad de la base de datos PostgreSQL, ejecutar automáticamente `python manage.py migrate`, y levantar el servidor Django.
4. **Orquestación (`docker-compose.yml`):**
   - Se añadió el servicio `db` (PostgreSQL 15 con healthcheck y volumen persistente).
   - Se añadió el servicio `web` (Django) dependiendo del estado saludable de la base de datos y de Keycloak.
   - Se configuró el servicio `keycloak` con el volumen de importación apuntando a `./docker` y el argumento `--import-realm` para cargar automáticamente el realm exportado (`global-exchange-realm-realm.json`).
5. **Pruebas y Verificación:**
   - Se ejecutaron las pruebas unitarias del sistema (`python manage.py test`), confirmando que las 29 pruebas pasaron con éxito.
   - Se ejecutó `docker compose up --build` verificando que los servicios inician correctamente y aplican las migraciones en PostgreSQL.
