#!/bin/bash
# Despliegue automatizado de Global Exchange (PSE-40).
#
# Uso:
#   ./deploy.sh dev            Levanta el entorno de desarrollo (localhost:8000 / Keycloak localhost:8080)
#   ./deploy.sh prod           Construye y levanta producción (Caddy + HTTPS) usando .env.production
#   ./deploy.sh seed [dev|prod]  Re-ejecuta la población inicial dentro del contenedor web
#   ./deploy.sh logs [dev|prod]  Muestra los logs en vivo
#   ./deploy.sh down [dev|prod]  Detiene los contenedores (los datos se conservan en los volúmenes)
#
# Al iniciar, el contenedor web ejecuta automáticamente: migrate -> seed_data -> keycloak_bootstrap.
set -euo pipefail

cd "$(dirname "$0")"

ACCION="${1:-}"
ENTORNO="${2:-dev}"
if [ "$ACCION" = "dev" ] || [ "$ACCION" = "prod" ]; then
    ENTORNO="$ACCION"
fi

compose() {
    if [ "$ENTORNO" = "prod" ]; then
        docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production "$@"
    else
        docker compose -f docker-compose.yml "$@"
    fi
}

verificar_prod() {
    if [ ! -f .env.production ]; then
        echo "ERROR: falta .env.production. Copiá .env.production.example y completá los valores." >&2
        exit 1
    fi
    for var in APP_DOMAIN AUTH_DOMAIN SECRET_KEY DB_PASSWORD KEYCLOAK_ADMIN_PASSWORD KEYCLOAK_CLIENT_SECRET; do
        if ! grep -qE "^${var}=.+" .env.production; then
            echo "ERROR: ${var} no está definido en .env.production" >&2
            exit 1
        fi
    done
    if grep -qE '^SECRET_KEY=(your-|django-insecure)' .env.production; then
        echo "ERROR: SECRET_KEY de .env.production es un valor de ejemplo. Generá uno nuevo." >&2
        exit 1
    fi
}

case "$ACCION" in
    dev)
        compose up -d --build
        echo "Desarrollo levantado: http://localhost:8000  (Keycloak: http://localhost:8080)"
        ;;
    prod)
        verificar_prod
        compose up -d --build
        dominio=$(grep -E '^APP_DOMAIN=' .env.production | cut -d= -f2- | tr -d '\r')
        echo "Producción levantada: https://${dominio}"
        echo "Seguir el arranque con: ./deploy.sh logs prod"
        ;;
    seed)
        compose exec web python manage.py seed_data
        compose exec web python manage.py keycloak_bootstrap --timeout 30
        ;;
    logs)
        compose logs -f --tail=100
        ;;
    down)
        compose down
        ;;
    *)
        sed -n '2,11p' "$0"
        exit 1
        ;;
esac
