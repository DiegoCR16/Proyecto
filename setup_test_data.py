"""
Script heredado de población de datos de prueba.

PSE-40: la lógica vive ahora en el comando `python manage.py seed_data`
(authentication/management/commands/seed_data.py), que se ejecuta
automáticamente al iniciar los contenedores. Este script se mantiene por
compatibilidad y simplemente delega en ese comando.
"""
import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'globalexchange.settings')
django.setup()

from django.core.management import call_command  # noqa: E402


def create_sample_data():
    """Ejecuta la población inicial completa (catálogos + usuarios de prueba)."""
    call_command('seed_data')


if __name__ == '__main__':
    create_sample_data()
