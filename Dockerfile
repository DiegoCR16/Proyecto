FROM python:3.11-slim AS builder

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Se instala en /install y se copia a /usr/local: así los paquetes son legibles por cualquier usuario
# (en producción el contenedor corre como appuser, que no puede leer /root/.local).
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Development image
FROM python:3.11-slim AS development

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x entrypoint.sh entrypoint-prod.sh

EXPOSE 8000
CMD ["./entrypoint.sh"]

# Stage 3: Production image
FROM python:3.11-slim AS production

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

COPY . .
RUN chmod +x entrypoint.sh entrypoint-prod.sh

# staticfiles debe existir en la imagen con dueño appuser: el volumen static_volume toma esos permisos
# y collectstatic puede escribir en él.
RUN mkdir -p /app/staticfiles && useradd -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["./entrypoint-prod.sh"]
