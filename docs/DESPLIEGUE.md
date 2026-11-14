# Despliegue de Global Exchange (PSE-40 / SCR)

Scripts de despliegue automático para los ambientes de **desarrollo** y **producción**:
código fuente, base de datos y población de la base de datos.

| Ambiente | Comando | Dónde se ejecuta |
|---|---|---|
| Desarrollo | `bash deploy.sh dev` | PC del desarrollador (Docker Desktop / WSL) |
| Producción | `bash deploy-remoto.sh ubuntu@<ip> --clave <clave-ssh>` | PC del desarrollador → servidor AWS por SSH |

Producción actual: servidor **AWS EC2 t4g.small** (Ubuntu 24.04 ARM, 2 GB RAM, región Ohio), IP elástica
`16.58.240.129`, dominio gratuito **DuckDNS** `globalexchange-g05.duckdns.org` y HTTPS automático con **Caddy** (Let's Encrypt).

## Qué hace cada arranque del contenedor web

`entrypoint.sh` (dev) y `entrypoint-prod.sh` (prod):

1. Espera a PostgreSQL.
2. `python manage.py migrate`: crea o actualiza la **base de datos**.
3. `python manage.py seed_data`: **población inicial** idempotente:
   divisas (USD, EUR, BRL, ARS, PYG), denominaciones de billetes, métodos de pago, tabla de comisiones
   (Minorista 0 %, VIP 2 %, Corporativo 4 %), roles, cajas C01/C02, cliente demo y usuarios de prueba.
4. `python manage.py keycloak_bootstrap` (en segundo plano): URL de callback del dominio en el cliente OIDC,
   usuarios de prueba en Keycloak y servidor SMTP (Gmail) del realm para los correos de verificación.
5. (prod) `collectstatic` y Gunicorn.

Solo crea lo que falta: no duplica registros ni pisa datos modificados por el administrador.

| Variable | Default | Efecto |
|---|---|---|
| `SEED_DATA` | `true` | `false` desactiva `seed_data` |
| `SEED_TEST_USERS` | `true` | `false` carga solo catálogos, sin usuarios de prueba |
| `KEYCLOAK_BOOTSTRAP` | `true` | `false` desactiva `keycloak_bootstrap` |
| `GUNICORN_WORKERS` | `3` | procesos de Gunicorn (2 en el servidor de 2 GB) |
| `KEYCLOAK_JAVA_OPTS` | `-Xms128m -Xmx512m` | memoria máxima de Keycloak |

### Usuarios de prueba (login vía Keycloak)

| Usuario | Rol | Contraseña |
|---|---|---|
| `adminuser` | Admin (pide iToken por correo*) | `password123` |
| `cajero` | Cajero (asignado a caja C01) | `password123` |
| `analista` | Analista | `password123` |
| `cliente` | Cliente | `password123` |

\* Los correos de los usuarios de prueba (`@globalexchange.com`) no existen: en desarrollo el código iToken
aparece en la consola (`bash deploy.sh logs dev`). En producción se recomienda desplegar `--sin-usuarios-prueba`.

## Verificación en dos pasos (iToken por correo)

Los usuarios que requieren MFA (Admin y clientes corporativos) reciben **en cada inicio de sesión** un código
aleatorio de 6 dígitos en su correo. Vence a los 5 minutos, es de un solo uso, se bloquea tras 5 intentos fallidos
y se puede reenviar una vez por minuto. Mientras no se verifica, el sistema solo permite la pantalla del iToken y
cerrar sesión. Requiere el correo SMTP (Gmail) configurado.

## Desarrollo

```bash
bash deploy.sh dev
```

- Django: http://localhost:8000
- Keycloak: http://localhost:8080 (admin / admin)
- Correos: sin `EMAIL_HOST` en `.env` se muestran en la consola (`bash deploy.sh logs dev`).

## Producción: `deploy-remoto.sh`

Un solo comando desde la PC (en Windows: WSL o Git Bash):

```bash
# Primer despliegue (el servidor todavía no tiene .env.production), sin usuarios ni clientes de prueba
bash deploy-remoto.sh ubuntu@16.58.240.129 --clave ~/.ssh/oracle_ge \
  --dominio globalexchange-g05.duckdns.org --sin-usuarios-prueba

# Despliegues siguientes
bash deploy-remoto.sh ubuntu@16.58.240.129 --clave ~/.ssh/oracle_ge
```

Solo en el **primer despliegue** el script pide la contraseña del administrador de Keycloak (usuario `admin`),
dos veces y sin mostrarla: mínimo 12 caracteres, letras, números y `!%&*+,-./:;<=>?@^_~`
(sin espacios, comillas, `$` ni `#`). Viaja al servidor por SSH y queda solo en su `.env.production`.
Keycloak la usa al crear el admin la primera vez; para cambiarla después se usa la consola de Keycloak.
`--sin-usuarios-prueba` deja `SEED_TEST_USERS=false`: se cargan divisas, métodos de pago, comisiones, roles y cajas,
pero ningún usuario ni cliente.

Pasos que ejecuta:

1. Verifica la conexión SSH.
2. Prepara el servidor si hace falta: 4 GB de swap e instalación de Docker (si ya está, lo saltea).
3. Respalda la base de datos de producción en `~/backups/` (conserva los 5 últimos).
4. Sube el **código fuente del último commit** (`git archive HEAD`). Los cambios sin commitear no se despliegan.
5. Conserva el `.env.production` del servidor; la primera vez lo genera con `scripts/generar_env_produccion.py`
   (dominios, `SECRET_KEY` y contraseña de base de datos aleatorias, contraseña elegida del admin de Keycloak,
   secreto del cliente de Keycloak).
6. Ejecuta `deploy.sh prod`: PostgreSQL + Keycloak + Django + Caddy, migraciones y población de datos.
7. Espera a que `https://<dominio>/auth/login/` responda 200. Si falla, muestra los logs; la versión anterior
   queda en `~/globalexchange.old`.

Requisitos: la clave SSH del servidor y que el dominio de DuckDNS apunte a la IP del servidor.

### Credenciales que se cargan a mano (una sola vez)

El `.env.production` del servidor nunca se sube a git. Las claves externas se cargan directamente en el servidor:

```bash
ssh -i ~/.ssh/oracle_ge ubuntu@16.58.240.129
nano ~/globalexchange/.env.production
```

- **Correo (Gmail del grupo):** `EMAIL_HOST=smtp.gmail.com`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`
  (contraseña de aplicación de 16 letras) y `DEFAULT_FROM_EMAIL=Global Exchange <cuenta@gmail.com>`.
- **SIFEN:** `SIFEN_API_EMAIL`, `SIFEN_API_PASSWORD`.
- **Stripe:** `STRIPE_SECRET_KEY`.

Después aplicar con `bash deploy-remoto.sh ...` (o en el servidor: `cd ~/globalexchange && bash deploy.sh prod`).

### Accesos

- Sistema: https://globalexchange-g05.duckdns.org
- Keycloak: https://auth.globalexchange-g05.duckdns.org (usuario `admin`, la contraseña elegida en el primer despliegue)
- Webhook Stripe: `https://globalexchange-g05.duckdns.org/integracion/webhook/stripe/`

### Arranque sin usuarios: crear el primer administrador

1. Cargar el Gmail del grupo en `.env.production` (sin correo no llegan la verificación de Keycloak ni el iToken).
2. Registrarse en https://globalexchange-g05.duckdns.org (botón de registro de Keycloak) y verificar el correo.
3. En la consola de Keycloak: cambiar el realm de **master** a **global-exchange-realm** → **Users** → el usuario →
   **Role mapping** → **Assign role** → filtrar por realm roles → **Admin**.
4. Cerrar sesión y volver a ingresar: el sistema lee los roles de Keycloak en cada inicio de sesión y pide el iToken por correo.

Los roles de cualquier usuario (Admin, Cajero, Analista, Cliente) se cambian de la misma forma y se aplican en su próximo inicio de sesión.

### Comandos útiles en el servidor

```bash
cd ~/globalexchange
bash deploy.sh logs prod     # logs en vivo
bash deploy.sh seed prod     # re-ejecutar la población inicial
bash deploy.sh down prod     # detener (los datos quedan en los volúmenes de Docker)
cat .desplegado              # rama y commit desplegados
```

Restaurar un respaldo de la base de datos:

```bash
gunzip -c ~/backups/db-AAAAMMDD-HHMMSS.sql.gz | docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  --env-file .env.production exec -T db sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"'
```
