# Despliegue de Global Exchange (PSE-40)

Guía para levantar los entornos de **desarrollo** y **producción** con Docker Compose.
Producción está pensada para una VM gratuita de **Oracle Cloud Always Free** con un
subdominio gratuito de **DuckDNS** y HTTPS automático mediante **Caddy**.

## Qué se ejecuta automáticamente

Cada vez que arranca el contenedor `web` (`entrypoint.sh` / `entrypoint-prod.sh`):

1. Espera a PostgreSQL.
2. `python manage.py migrate` (migraciones).
3. `python manage.py seed_data`: población inicial **idempotente**:
   - Divisas: USD, EUR, BRL, ARS, PYG con tasas iniciales.
   - Denominaciones de billetes por divisa.
   - Métodos de pago: tarjeta de crédito/débito, transferencia, billetera y efectivo.
   - Tabla de comisiones/beneficios por categoría: Minorista 0 %, VIP 2 %, Corporativo 4 %.
   - Roles, cajas C01/C02, cliente demo y usuarios de prueba.
4. `python manage.py keycloak_bootstrap` (en segundo plano): registra en Keycloak la URL
   de callback del dominio y crea los usuarios de prueba con su rol.
5. (solo prod) `collectstatic` y Gunicorn.

Solo crea lo que falta: no duplica registros ni pisa tasas, roles o contraseñas modificadas.

| Variable | Default | Efecto |
|---|---|---|
| `SEED_DATA` | `true` | `false` desactiva `seed_data` |
| `SEED_TEST_USERS` | `true` | `false` carga solo catálogos, sin usuarios de prueba |
| `KEYCLOAK_BOOTSTRAP` | `true` | `false` desactiva `keycloak_bootstrap` |

### Usuarios de prueba (login vía Keycloak)

| Usuario | Rol | Contraseña |
|---|---|---|
| `adminuser` | Admin (pide iToken `123456`) | `password123` |
| `cajero` | Cajero (asignado a caja C01) | `password123` |
| `analista` | Analista | `password123` |
| `cliente` | Cliente | `password123` |

## Desarrollo

```bash
./deploy.sh dev
```

- Django: http://localhost:8000
- Keycloak: http://localhost:8080 (admin / admin)
- Mailpit: http://localhost:8025

## Producción (Oracle Cloud + DuckDNS)

### 1. Infraestructura (una sola vez, manual)

1. Crear cuenta en Oracle Cloud y una VM **Ampere A1** (Ubuntu 22.04/24.04, 2 OCPU / 12 GB).
2. En la *Security List* de la VCN agregar reglas de ingreso TCP **80** y **443** desde `0.0.0.0/0`.
3. En la VM abrir el firewall local (las imágenes de Ubuntu de Oracle traen iptables restrictivo):
   ```bash
   sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
   sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
   sudo netfilter-persistent save
   ```
4. En https://www.duckdns.org crear el subdominio (ej. `globalexchange`) y apuntarlo a la IP
   pública de la VM. `auth.globalexchange.duckdns.org` resuelve a la misma IP automáticamente.
5. Instalar Docker:
   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker $USER   # cerrar sesión y volver a entrar
   ```

### 2. Desplegar

```bash
git clone <repo> globalexchange && cd globalexchange
cp .env.production.example .env.production
nano .env.production    # completar dominios, SECRET_KEY, contraseñas, claves de Stripe/SIFEN
bash deploy.sh prod
bash deploy.sh logs prod   # ver migraciones, seed y emisión del certificado HTTPS
```

- Sistema: `https://<APP_DOMAIN>`
- Keycloak: `https://<AUTH_DOMAIN>` (usuario `KEYCLOAK_ADMIN`)
- Mailpit: solo desde el servidor → `ssh -L 8025:localhost:8025 ubuntu@<ip>` y abrir http://localhost:8025

`KEYCLOAK_CLIENT_SECRET` debe coincidir con el secreto del cliente `global-exchange-client`
del realm (`docker/global-exchange-realm-realm.json`). Si se regenera el secreto en la consola de
Keycloak, actualizarlo en `.env.production` y reiniciar con `bash deploy.sh prod`.

### 3. Integraciones externas

- **Stripe**: en el panel de Stripe registrar el webhook `https://<APP_DOMAIN>/integracion/webhook/stripe/`.

### Actualizar a una nueva versión

```bash
git pull
bash deploy.sh prod      # reconstruye la imagen; migrate + seed corren solos
```

### Comandos útiles

```bash
bash deploy.sh seed prod   # re-ejecutar la población inicial manualmente
bash deploy.sh down prod   # detener (los datos quedan en los volúmenes de Docker)
```
