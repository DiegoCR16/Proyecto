# Prompt: Configuración de Ambiente de Producción con Docker Compose y Multi-Stage Build

- **Fecha:** 2026-09-25
- **Objetivo:** Extender la configuración de Docker existente (desarrollo) para soportar un ambiente de producción robusto y optimizado utilizando multi-stage builds y archivos de Docker Compose separados.

## Estructura de Archivos Sugerida

```text
Proyecto/
├── Dockerfile                  # Dockerfile con multi-stage build (builder, development, production)
├── docker-compose.yml          # Docker Compose base / desarrollo (con live-reload, bind mounts, DEBUG=True)
├── docker-compose.prod.yml     # Docker Compose para producción (sin bind mounts, restart policies, Gunicorn)
├── entrypoint.sh               # Script de inicio para desarrollo (runserver)
├── entrypoint-prod.sh          # Script de inicio para producción (collectstatic + Gunicorn)
├── requirements.txt            # Dependencias del proyecto (incluye gunicorn)
└── .env.production.example     # Plantilla de variables de entorno para producción
```

## Diferencias Clave entre Ambientes

1. **Desarrollo (`docker-compose.yml` + `Dockerfile` [development]):**
   - Bind mounts de código local (`.:/app`) para live-reload automático.
   - Servidor de desarrollo integrado de Django (`runserver`).
   - Puertos expuestos para depuración (`8000`, `5432`, `8080`).
   - `DEBUG=True`.

2. **Producción (`docker-compose.prod.yml` + `Dockerfile` [production]):**
   - Sin bind mounts de código local; imagen empaquetada con el código optimizado.
   - Servidor WSGI robusto (**Gunicorn**) con múltiples workers (`--workers 3`).
   - Políticas de reinicio automático (`restart: always`).
   - Variables de entorno cargadas desde `.env.production`.
   - Limitar exposición de puertos (ej. base de datos interna, Mailpit en localhost).
   - Recopilación automática de archivos estáticos (`collectstatic`).
   - Configuración de logging con rotación de archivos.

## Comandos de Ejecución

### Ambiente de Desarrollo
```bash
docker compose up --build
```

### Ambiente de Producción
```bash
# 1. Copiar y configurar las variables de producción
cp .env.production.example .env.production
# (Editar .env.production con claves seguras)

# 2. Levantar el entorno en segundo plano
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```
