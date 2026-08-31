# Registro de Conversación IA (CHIA) - Documentación Automática (PDO)

- **Fecha:** 31 de Agosto de 2026
- **Tarea:** Configuración, funcionamiento y utilización de documentación de código fuente generado automáticamente (PDO)
- **Asignatura:** Ingeniería de Software 2 - FPUNA
- **Asistente:** OpenCode (gemini-3.5-flash-lite)
- **Proyecto:** Global Exchange

## 1. Resumen de la Implementación
Se implementó el sistema de documentación automática del código fuente del proyecto Django utilizando la herramienta `pdoc`. 
Esta herramienta procesa los *docstrings* estructurados en formato Google/Sphinx presentes en las vistas, modelos y submódulos de las aplicaciones (`authentication`, `gestion_clientes`, `tasas_cambio`, `globalexchange`), generando un portal web de documentación en formato HTML interactivo y navegable.

## 2. Configuración Realizada
1. **Dependencia:** Se añadió `pdoc>=16.0.0` al archivo `requirements.txt`.
2. **Script de Automatización (`generate_docs.py`):**
   - Configura el entorno de Django (`DJANGO_SETTINGS_MODULE = 'globalexchange.settings'` y `django.setup()`) para permitir la correcta importación de los modelos y vistas que dependen del ORM y la configuración del proyecto.
   - Utiliza `pdoc.pdoc` para compilar los módulos principales (`authentication`, `gestion_clientes`, `tasas_cambio`, `globalexchange`).
   - Exporta los resultados estáticos al directorio de salida `docs/api/`.

## 3. Funcionamiento de la Generación de Documentación
- `pdoc` analiza el árbol sintáctico (AST) y los objetos importados en Python.
- Extrae automáticamente los *docstrings* (resúmenes, argumentos, tipos y retornos definidos bajo formato Sphinx/Google) de clases, métodos y funciones.
- Construye un sitio web estático autocontenido con índice de búsqueda (`search.js`), navegación por módulos y desglose jerárquico de clases y funciones.

## 4. Cómo Generar la Documentación (Instrucciones de Utilización)
Para generar o actualizar la documentación técnica del código fuente, ejecute el siguiente comando en la raíz del proyecto:

```bash
python generate_docs.py
```

O directamente mediante `pdoc` configurando el entorno de Django:
```bash
python -c "import os, django, pdoc; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'globalexchange.settings'); django.setup(); pdoc.pdoc('authentication', 'gestion_clientes', 'tasas_cambio', 'globalexchange', output_directory=__import__('pathlib').Path('docs/api'))"
```

Los archivos HTML generados quedan disponibles en `docs/api/index.html`. Para visualizarlos localmente, puede abrir dicho archivo en cualquier navegador web o levantar un servidor estático:
```bash
python -m http.server --directory docs/api 8000
```
Luego acceder a `http://localhost:8000`.

## 5. Conclusión y Estado
El Producto de Documentación (PDO) se encuentra completamente configurado, automatizado y operativo, cumpliendo con los estándares de calidad académica e industrial requeridos para el proyecto Global Exchange.
