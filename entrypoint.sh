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

echo "Starting Django server..."
exec python manage.py runserver 0.0.0.0:8000
