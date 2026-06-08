# YVY Server — Bronze ingest layer

Servidor central de ingesta del sistema YVY. Recibe datos meteorológicos
de los edges (netbooks de IEAs en Misiones) vía HTTPS firmado HMAC-SHA256
y los persiste en una capa Bronze de PostgreSQL.

**Estado:** Fase 9.1 — scaffold + endpoints mínimos
(`/healthz`, `/serverinfo`, `/admin/healthz`, `/ingest/observation`, `/ingest/comment` stub 501).
Las rutas `/ingest/sensor`, `/ingest/event` y el resto de `/admin/*` llegan en 9.2.

Track A (este server) coexiste con Track B (arquitectura evolutiva Kafka)
desarrollado en paralelo. Ver `YVY_FASE9_Arquitectura_v2.md` §11.

## Stack

- **Python:** 3.12
- **HTTP:** FastAPI + Uvicorn (async)
- **DB:** PostgreSQL 16 + SQLAlchemy 2.x async + asyncpg
- **Migrations:** Alembic
- **Validation:** Pydantic v2
- **CLI:** Typer (sync)
- **Tests:** pytest + pytest-asyncio + httpx
- **Quality:** ruff + mypy strict

## Estructura

Hexagonal (Ports & Adapters), espejo del edge:

```
src/yvy_server/
├── domain/         # Lógica pura, sin imports externos
├── application/    # Use cases y orquestación
├── infrastructure/ # Adaptadores: DB, publishers, etc.
├── presentation/   # FastAPI routers + Pydantic schemas
└── cli/            # CLI Typer (sync)
```

## Setup local (sin Docker)

```bash
# 1. Levantar Postgres (recomendado: docker-compose para no ensuciar el host)
docker compose up -d postgres

# 2. Copiar variables de entorno
cp .env.example .env
# Editar .env si querés cambiar credenciales o puertos.

# 3. Instalar deps
poetry install

# 4. Aplicar migraciones
poetry run alembic upgrade head

# 5. Crear primera station
poetry run yvy-server create-station \
    --name "IEA 7 - Cerro Azul" \
    --location-name "Cerro Azul, Misiones" \
    --lat -27.0333 --lon -55.4833

# ▲ La hmac_key se imprime UNA sola vez. Guardarla.

# 6. Levantar el server
poetry run uvicorn yvy_server.main:app --reload --host 0.0.0.0 --port 8000
```

Healthcheck:

```bash
curl http://localhost:8000/api/v1/healthz
# {"status":"ok"}
```

## Setup con Docker

```bash
cp .env.example .env
docker compose up --build
```

El entrypoint del container corre `alembic upgrade head` antes de
arrancar Uvicorn (ver `docker-entrypoint.sh`).

## Tests

Requieren Postgres corriendo (los integration usan un schema temporal).

```bash
# Asegurarse de que postgres está up
docker compose up -d postgres

# Unit tests (sin I/O)
poetry run pytest tests/unit -v

# Integration tests (contra postgres real)
poetry run pytest tests/integration -v

# Todo + coverage
poetry run pytest --cov=yvy_server --cov-report=term-missing
```

## Quality

```bash
poetry run ruff check .
poetry run ruff format --check .
poetry run mypy src
```

## CLI

```bash
poetry run yvy-server --help
poetry run yvy-server create-station --help
```

## Documentos relacionados

- `YVY_FASE9_Arquitectura_v2.md` — arquitectura completa de la fase.
- `HANDOFF_FASE9.md` — contexto del proyecto y convenciones.
- `YVY_Anexo_Tecnico_v4.md` — anexo técnico institucional.
- `docs/yvy_FASE91_RESUMEN.md` — estado y decisiones cerradas de esta subfase.

## Desarrollo en Windows

El server se desarrolla en Windows y se deploya en Debian 12 (vía
Docker). Docker abstrae el OS — lo que pasa los tests en Windows va a
correr idéntico en el server.

**Fricciones conocidas y mitigaciones:**

### 1. Line endings (CRLF)

`.gitattributes` fuerza LF en archivos críticos (`.sh`, `Dockerfile`,
`.py`, etc). Si Git fue clonado *antes* de que existiera `.gitattributes`,
hace falta renormalizar una vez:

```bash
git add --renormalize .
git commit -m "chore: normalize line endings"
```

### 2. Terminal: Git Bash o WSL2, no PowerShell ni CMD

Los comandos del README asumen bash. En CMD/PowerShell muchos no
funcionan (heredoc, export, comillas, etc).

- **Git Bash:** viene con [Git for Windows](https://git-scm.com/download/win).
  Suficiente para todo el flujo de este server.
- **WSL2:** mejor performance de Docker si trabajás con muchos volumes,
  pero overhead de setup inicial. Opcional.

### 3. Python en el host es opcional

`asyncpg` en Windows requiere compilar `libpq`, lo cual da dolores de
cabeza. Tres opciones:

| Opción | Pros | Contras |
|---|---|---|
| **A · Todo en container:** `docker compose run --rm app pytest` | Cero deps en host | Más lento, sin autocompletado en IDE |
| **B · Poetry + Python 3.12 en host solo para IDE; tests en container** | Autocompletado/linter en VS Code; container para correr | Requiere lidiar con instalación de asyncpg para satisfacer el linter (workaround: `poetry install --without main` y agregar tipos vía `mypy --install-types`) |
| **C · WSL2 con Python 3.12** | Linux nativo en Windows | Setup inicial más largo |

**Recomendación para esta máquina:** opción B. Te da autocompletado/type
hints en VS Code mientras tests + run pesado siempre va por container.

### 4. Flujo de dev

```bash
# Dentro de Git Bash en Windows:
git pull
# editás código

# Levantar postgres (queda corriendo en background)
docker compose up -d postgres

# Correr tests (dentro del container, sin tocar el host)
docker compose run --rm app pytest -v

# O si elegiste opción B, también podés:
poetry run pytest -v

# Quality checks
docker compose run --rm app ruff check .
docker compose run --rm app mypy src

# Levantar la app completa (a partir del paso 7 de 9.1)
docker compose up --build

# Commit
git add . && git commit -m "..."
git push
```

### 5. Deploy al server

El primer deploy al server Debian es **al cerrar 9.1 entera**, no
intermedio. Hasta entonces, todo el trabajo va en Windows + push a Git.

Cuando llegue ese momento:

```bash
ssh server.tecnicatura
cd ~/yvy-server
git pull
docker compose pull && docker compose up -d --build
docker compose logs -f app
```

## Notas de desarrollo

- **Canonical JSON v1.0:** `src/yvy_server/domain/auth/canonical_json.py` es
  copia byte-a-byte del archivo equivalente en el edge. Cualquier cambio
  requiere actualizar ambos lados + bump de `CANONICAL_JSON_SPEC_VERSION`
  + coordinación con Track B.
- **Versioning:** `__version__` se inyecta en build time vía
  `scripts/embed_version.py` (genera `_version.py`). En dev sin build,
  cae a `"0.9.1-dev"`.
- **Migraciones:** se aplican automáticamente al startup del container
  (ver `docker-entrypoint.sh`). En dev local, correr manualmente con
  `alembic upgrade head`.
