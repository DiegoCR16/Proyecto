#!/bin/bash
set -e

echo "Waiting for PostgreSQL..."
python << END
import socket
import time
import os

host = os.environ.get('DB_HOST', 'db')
port = int(os.environ.get('DB_PORT', 5432))

while True:
    try:
        s = socket.create_connection((host, port), timeout=2)
        s.close()
        print("PostgreSQL is available!")
        break
    except (socket.error, socket.timeout):
        print("Waiting for database connection...")
        time.sleep(1)
END

echo "Running database migrations..."
python manage.py migrate

# PSE-40: población inicial idempotente (divisas, métodos de pago, comisiones, roles, cajas, usuarios).
# SEED_DATA=false la desactiva; SEED_TEST_USERS=false carga solo catálogos sin usuarios de prueba.
if [ "${SEED_DATA:-true}" = "true" ]; then
    echo "Seeding initial data..."
    if [ "${SEED_TEST_USERS:-true}" = "true" ]; then
        python manage.py seed_data
    else
        python manage.py seed_data --sin-usuarios
    fi
fi

# PSE-40: registra la URL de callback y los usuarios de prueba en Keycloak.
# Corre en segundo plano porque Keycloak tarda en iniciar; no bloquea el servidor web.
if [ "${KEYCLOAK_BOOTSTRAP:-true}" = "true" ]; then
    if [ "${SEED_TEST_USERS:-true}" = "true" ]; then
        python manage.py keycloak_bootstrap &
    else
        python manage.py keycloak_bootstrap --sin-usuarios &
    fi
fi

echo "Collecting static files..."
python manage.py collectstatic --noinput

echo "Starting Gunicorn server..."
exec gunicorn globalexchange.wsgi:application --bind 0.0.0.0:8000 --workers "${GUNICORN_WORKERS:-3}" --timeout 120
