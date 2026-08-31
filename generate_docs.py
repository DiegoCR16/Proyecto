import os
import django
from pathlib import Path

# Configurar el entorno de Django para permitir la importación de modelos y vistas
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'globalexchange.settings')
django.setup()

import pdoc

if __name__ == '__main__':
    modules = ['authentication', 'gestion_clientes', 'tasas_cambio', 'globalexchange']
    output_dir = Path('docs/PDO')
    print(f"Generando documentación automática para: {modules}")
    pdoc.pdoc(*modules, output_directory=output_dir)
    print("¡Documentación generada exitosamente en docs/PDO!")
