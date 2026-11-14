#!/bin/bash
# Despliegue automático a PRODUCCIÓN desde la PC del desarrollador (PSE-40, SCR).
#
# Con un solo comando:
#   1. Verifica la conexión SSH con el servidor.
#   2. Prepara el servidor si hace falta (memoria swap y Docker).
#   3. Respalda la base de datos de producción (si ya existe).
#   4. Sube el código fuente del último commit (git archive HEAD).
#   5. Conserva el .env.production del servidor o lo genera la primera vez.
#   6. Levanta PostgreSQL, Keycloak, Django y Caddy (HTTPS) con deploy.sh prod,
#      que ejecuta migraciones y la población inicial de datos.
#   7. Comprueba que el sistema responda por HTTPS.
#
# Uso:
#   bash deploy-remoto.sh <usuario@servidor> [--clave ~/.ssh/clave] [--dominio mi.dominio.org] [--sin-usuarios-prueba]
#
# Ejemplo (primer despliegue, sin usuarios ni clientes de prueba):
#   bash deploy-remoto.sh ubuntu@16.58.240.129 --clave ~/.ssh/oracle_ge --dominio globalexchange-g05.duckdns.org --sin-usuarios-prueba
#
# Solo en el primer despliegue (el servidor no tiene .env.production):
#   --dominio es obligatorio, se pide la contraseña del admin de Keycloak (usuario "admin")
#   y --sin-usuarios-prueba deja SEED_TEST_USERS=false. Después se conserva la configuración.
# Variables opcionales: DEPLOY_HOST, DEPLOY_SSH_KEY, DEPLOY_DOMAIN, DEPLOY_KEYCLOAK_ADMIN_PASSWORD.
# Desde Windows ejecutarlo en WSL o Git Bash.
set -euo pipefail
cd "$(dirname "$0")"

DESTINO="${DEPLOY_HOST:-}"
CLAVE="${DEPLOY_SSH_KEY:-}"
DOMINIO="${DEPLOY_DOMAIN:-}"
APP_DIR="globalexchange"          # carpeta en el home del servidor (también nombre del proyecto Compose)
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production"
ESPERA_MAXIMA=600                 # segundos para que responda HTTPS (build + certificado + Keycloak)

SIN_USUARIOS_PRUEBA=0
PASSWORD_ADMIN="${DEPLOY_KEYCLOAK_ADMIN_PASSWORD:-}"
# Misma regla que scripts/generar_env_produccion.py (sin espacios, comillas, $ ni #).
REGEX_PASSWORD='^[A-Za-z0-9!%&*+,./:;<=>?@^_~-]{12,128}$'

uso() { sed -n '2,24p' "$0"; exit 1; }
paso() { echo; echo "==> $*"; }
error() { echo "ERROR: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --clave) CLAVE="${2:-}"; shift 2 ;;
        --dominio) DOMINIO="${2:-}"; shift 2 ;;
        --sin-usuarios-prueba) SIN_USUARIOS_PRUEBA=1; shift ;;
        -h|--help) uso ;;
        -*) error "opción desconocida: $1" ;;
        *) DESTINO="$1"; shift ;;
    esac
done
[ -n "$DESTINO" ] || uso

SSH_OPTS=(-o ConnectTimeout=15 -o ServerAliveInterval=30 -o StrictHostKeyChecking=accept-new)
[ -n "$CLAVE" ] && SSH_OPTS+=(-i "$CLAVE")
remoto() { ssh "${SSH_OPTS[@]}" "$DESTINO" "$@"; }

# --- 0. Código a desplegar ------------------------------------------------------
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || error "ejecutar dentro del repositorio git."
COMMIT="$(git rev-parse --short HEAD)"
RAMA="$(git rev-parse --abbrev-ref HEAD)"
paso "Desplegando rama ${RAMA}, commit ${COMMIT}"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "AVISO: hay cambios sin commitear; NO se incluyen (solo se despliega el commit ${COMMIT})."
fi

# --- 1. Conexión ----------------------------------------------------------------
paso "1/7 Verificando conexión SSH con ${DESTINO}"
remoto true || error "no se pudo conectar por SSH a ${DESTINO}."

PRIMERA_VEZ=1
remoto "test -f ${APP_DIR}/.env.production" && PRIMERA_VEZ=0
if [ "$PRIMERA_VEZ" = 1 ]; then
    echo "Primer despliegue: se generará .env.production en el servidor."
    [ -n "$DOMINIO" ] || error "primer despliegue: indicar --dominio (ej. --dominio globalexchange-g05.duckdns.org)."
    if [ -z "$PASSWORD_ADMIN" ] && [ -t 0 ]; then
        echo "Contraseña para el administrador de Keycloak (usuario: admin)."
        echo "Mínimo 12 caracteres: letras, números y !%&*+,-./:;<=>?@^_~ (sin espacios, comillas, \$ ni #)."
        while true; do
            read -r -s -p "Contraseña: " PASSWORD_ADMIN; echo
            read -r -s -p "Repetir contraseña: " repetida; echo
            if [ "$PASSWORD_ADMIN" != "$repetida" ]; then echo "No coinciden, intente de nuevo."
            elif [[ ! "$PASSWORD_ADMIN" =~ $REGEX_PASSWORD ]]; then echo "No cumple los requisitos, intente de nuevo."
            else break; fi
        done
    fi
    if [ -n "$PASSWORD_ADMIN" ]; then
        [[ "$PASSWORD_ADMIN" =~ $REGEX_PASSWORD ]] || error "la contraseña de Keycloak no cumple los requisitos."
    else
        echo "Sin terminal interactiva: la contraseña del admin de Keycloak se generará al azar."
    fi
    if [ "$SIN_USUARIOS_PRUEBA" = 1 ]; then echo "Sin usuarios ni clientes de prueba (SEED_TEST_USERS=false)."; fi
elif [ "$SIN_USUARIOS_PRUEBA" = 1 ] || [ -n "$DOMINIO" ]; then
    echo "AVISO: el servidor ya tiene .env.production; --dominio y --sin-usuarios-prueba solo aplican al primer despliegue."
fi

# --- 2. Preparar servidor (idempotente) -----------------------------------------
paso "2/7 Preparando servidor (swap y Docker)"
remoto 'bash -s' <<'PREPARAR'
set -euo pipefail
if [ -z "$(swapon --show --noheadings)" ] && [ "$(awk '/MemTotal/ {print $2}' /proc/meminfo)" -lt 4000000 ]; then
    echo "Creando 4 GB de swap..."
    sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
    sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
    echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swap.conf >/dev/null
    sudo sysctl -q -p /etc/sysctl.d/99-swap.conf
fi
if ! command -v docker >/dev/null 2>&1; then
    echo "Instalando Docker..."
    . /etc/os-release
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ca-certificates curl >/dev/null
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
        | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
fi
id -nG | grep -qw docker || sudo usermod -aG docker "$USER"
echo "Swap: $(free -h | awk '/Swap/ {print $2}')  |  $(docker --version)"
PREPARAR

# --- 3. Respaldo de la base de datos ----------------------------------------------
paso "3/7 Respaldando base de datos (si existe)"
remoto "APP_DIR=${APP_DIR} COMPOSE='${COMPOSE}' bash -s" <<'RESPALDO'
set -euo pipefail
if [ -f "$APP_DIR/.env.production" ] && (cd "$APP_DIR" && $COMPOSE ps -q db 2>/dev/null | grep -q .); then
    mkdir -p backups
    archivo="backups/db-$(date +%Y%m%d-%H%M%S).sql.gz"
    (cd "$APP_DIR" && $COMPOSE exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"') | gzip > "$archivo"
    echo "Respaldo: ~/$archivo"
    ls -1t backups/db-*.sql.gz | tail -n +6 | xargs -r rm -f   # conserva los 5 últimos
else
    echo "Primer despliegue: no hay base de datos para respaldar."
fi
RESPALDO

# --- 4. Subir código fuente ---------------------------------------------------------
paso "4/7 Subiendo código fuente (commit ${COMMIT})"
git archive --format=tar HEAD | remoto "rm -rf ${APP_DIR}.new && mkdir ${APP_DIR}.new && tar -xf - -C ${APP_DIR}.new"

# --- 5. Configuración (.env.production) -------------------------------------------
paso "5/7 Configuración de producción (.env.production)"
if [ "$PRIMERA_VEZ" = 1 ] && [ -n "$PASSWORD_ADMIN" ]; then
    # La contraseña viaja por la conexión SSH (stdin), nunca como argumento de un comando.
    printf '%s' "$PASSWORD_ADMIN" | remoto "umask 077 && cat > .kc_admin_password"
fi
remoto "APP_DIR=${APP_DIR} DOMINIO='${DOMINIO}' SIN_USUARIOS_PRUEBA=${SIN_USUARIOS_PRUEBA} COMMIT=${COMMIT} RAMA='${RAMA}' bash -s" <<'CONFIG'
set -euo pipefail
trap 'rm -f "$HOME/.kc_admin_password"' EXIT
nuevo="$APP_DIR.new"
rm -f "$nuevo/.env.production"            # nunca se usa el .env.production del repositorio
if [ -f "$APP_DIR/.env.production" ]; then
    cp -p "$APP_DIR/.env.production" "$nuevo/.env.production"
    echo "Se conserva el .env.production existente del servidor."
else
    [ -n "$DOMINIO" ] || { echo "ERROR: primer despliegue: indicar --dominio." >&2; exit 1; }
    opciones=(--dominio "$DOMINIO")
    [ "$SIN_USUARIOS_PRUEBA" = 1 ] && opciones+=(--sin-usuarios-prueba)
    [ -f "$HOME/.kc_admin_password" ] && opciones+=(--keycloak-admin-password-file "$HOME/.kc_admin_password")
    (cd "$nuevo" && python3 scripts/generar_env_produccion.py "${opciones[@]}")
    echo "Completar luego SIFEN, Stripe y Gmail con: nano ~/$APP_DIR/.env.production"
fi
rm -rf "$APP_DIR.old"
[ -d "$APP_DIR" ] && mv "$APP_DIR" "$APP_DIR.old"
mv "$nuevo" "$APP_DIR"
echo "$(date '+%Y-%m-%d %H:%M') rama $RAMA commit $COMMIT" > "$APP_DIR/.desplegado"
CONFIG

# --- 6. Levantar servicios -----------------------------------------------------------
paso "6/7 Construyendo y levantando servicios (puede tardar varios minutos)"
remoto "cd ${APP_DIR} && bash deploy.sh prod"

# --- 7. Verificación -------------------------------------------------------------------
DOMINIO_APP="$(remoto "grep -E '^APP_DOMAIN=' ${APP_DIR}/.env.production | cut -d= -f2- | tr -d '\r'")"
paso "7/7 Esperando respuesta en https://${DOMINIO_APP} (máx. ${ESPERA_MAXIMA}s)"
inicio=$SECONDS
until codigo="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://${DOMINIO_APP}/auth/login/")" \
        && [ "$codigo" = "200" ]; do
    if [ $((SECONDS - inicio)) -ge $ESPERA_MAXIMA ]; then
        echo "El sistema no respondió (último código HTTP: ${codigo:-sin respuesta}). Últimos logs:" >&2
        remoto "cd ${APP_DIR} && ${COMPOSE} logs --tail=40 web keycloak caddy" >&2 || true
        echo "La versión anterior quedó en ~/${APP_DIR}.old en el servidor." >&2
        exit 1
    fi
    sleep 10
done
remoto "rm -rf ${APP_DIR}.old && docker image prune -f >/dev/null"

echo
echo "Despliegue completado (commit ${COMMIT})."
echo "  Sistema:  https://${DOMINIO_APP}"
echo "  Keycloak: https://auth.${DOMINIO_APP}"
