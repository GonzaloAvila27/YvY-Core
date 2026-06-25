# syntax=docker/dockerfile:1.7
# ─────────────────────────────────────────────────────────────────────
# YVY Server — Dockerfile multi-stage
#
# Stage 1 (builder): instala poetry, exporta requirements.txt, instala
#   dependencies en un venv aislado.
# Stage 2 (runtime): copia el venv + el código + el entrypoint. Liviano.
# ─────────────────────────────────────────────────────────────────────

ARG PYTHON_VERSION=3.12

# ============================================================================
# Stage 1: builder
# ============================================================================
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_VERSION=1.8.3 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false

# Build deps (asyncpg needs libpq-dev, alembic ok with stdlib)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install "poetry==${POETRY_VERSION}"

WORKDIR /build

# Solo los archivos de dependencies para aprovechar cache de Docker
COPY pyproject.toml README.md ./

# Generar venv en /opt/venv (más fácil de copiar al runtime stage)
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Instalar deps de producción (sin dev). --without dev requiere poetry >= 1.2
RUN poetry export --only main --without-hashes -f requirements.txt -o /tmp/requirements.txt && \
    pip install -r /tmp/requirements.txt

# Copiar código y instalar el package en sí
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY README.md ./

RUN pip install --no-deps .

# ─── Inyectar versión desde build args (no copiamos .git al image) ───
# El CI/build script extrae estos valores de git y los pasa con
# --build-arg. Si no se pasan, queda el fallback ("unknown") en __init__.py.
#
# Ejemplo de uso en CI:
#   docker build \
#     --build-arg GIT_COMMIT=$(git rev-parse HEAD) \
#     --build-arg GIT_COMMIT_SHORT=$(git rev-parse --short HEAD) \
#     --build-arg BUILD_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ") \
#     -t yvy-server:latest .
ARG GIT_COMMIT=unknown
ARG GIT_COMMIT_SHORT=unknown
ARG BUILD_DATE=unknown

# El script honra estas env vars en lugar de llamar a git.
# Ver scripts/embed_version.py.
ENV YVY_BUILD_GIT_COMMIT=${GIT_COMMIT} \
    YVY_BUILD_GIT_COMMIT_SHORT=${GIT_COMMIT_SHORT} \
    YVY_BUILD_DATE=${BUILD_DATE}

RUN python scripts/embed_version.py

RUN poetry install --only main --no-cache

# ============================================================================
# Stage 2: runtime
# ============================================================================
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    YVY_HOST=0.0.0.0 \
    YVY_PORT=8000

# Runtime deps (solo libpq para asyncpg, nada de build-essential)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash yvy

WORKDIR /app

# Copiar venv y código desde builder
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /build/src ./src
COPY --from=builder /build/scripts ./scripts

# Alembic config + migraciones
COPY alembic.ini ./
COPY alembic/ ./alembic/

# Entrypoint
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Permisos
RUN chown -R yvy:yvy /app
USER yvy

EXPOSE 8000

# Healthcheck: usar /api/v1/healthz que no toca DB
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:${YVY_PORT}/api/v1/healthz || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "yvy_server.main:app", "--host", "0.0.0.0", "--port", "8000"]
