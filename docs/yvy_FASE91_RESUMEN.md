# Fase 9.1 — Server FastAPI mínimo · Resumen de cambios

**Fecha de inicio:** 16-May-2026
**Estado:** EN PROGRESO. Paso 1/11 cerrado.
**Bloqueante para piloto:** sí — habilita el server central contra el cual operan el sync agent (9.2) y el wiring del edge (9.3).
**Documento arquitectónico:** `YVY_FASE9_Arquitectura_v2.md` (autoritativo, v1 archivado).

---

## Qué cubre 9.1

Server FastAPI mínimo con stack async (asyncpg + SQLAlchemy 2.x + Alembic + Pydantic v2), arquitectura hexagonal espejo del edge. Endpoints implementados al cierre:

- `GET  /api/v1/healthz` — liveness, sin DB.
- `GET  /api/v1/serverinfo` — server time + supported schemas + version visible.
- `GET  /api/v1/admin/healthz` — con bearer token, cuenta filas en bronze tables.
- `POST /api/v1/ingest/observation` — HMAC + skew + UPSERT a `bronze.empirical_observation_raw`.
- `POST /api/v1/ingest/comment` — stub 501 hasta que existan comentarios.

Schema PostgreSQL completo en una sola migración (las 7 tablas del v2 §6.3, no incremental). CLI `create-station` (sync). Logging JSON estructurado desde el día 1. Tests unit + integration con Postgres real.

## Qué NO cubre 9.1 (queda para 9.2/9.3)

- Endpoints `/api/v1/ingest/sensor` y `/api/v1/ingest/event` → 9.2.
- Endpoints `/api/v1/admin/stats` y `/api/v1/admin/failures` (acciones de resolución) → 9.2.
- Edge sync agent + migración v13 → 9.2.
- nginx + TLS termination → 9.3.
- Wiring de `MainApplication._wire_and_run()` → 9.3.
- Smoke E2E con edge real → 9.3.
- Rate limiting (429 + Retry-After) → diferido, no apuntado a una fase.

---

## Decisiones cerradas en sesión arquitectónica (14-16 May)

Todas decididas, no se reabren sin razón fuerte. Si una IA en otro chat propone cambiar algo de esta lista, marcar el cierre y referenciar este resumen.

1. **Async stack confirmado.** asyncpg + SQLAlchemy 2.x async + Alembic con async engine. Mi reserva (sync sería suficiente para la carga real esperada de < 5k req/día) queda registrada como item a revisar a 6 meses, no como decisión pendiente.

2. **Hexagonal espejo del edge.** `domain/application/infrastructure/presentation` en el server. Reserva: el server hace poco — disciplina para no inventar entidades de dominio sin contenido.

3. **`yvy_server` como nombre del package.** Edge es `yvy`, server es `yvy_server`. `yvy_api` y `yvy_central` descartados.

4. **Canonical JSON v1.0 con vendor copy** del archivo del edge (no submodule todavía). Test golden file en ambos lados + constante `CANONICAL_JSON_SPEC_VERSION = "1.0"` para detectar drift. Migración futura a submodule cuando exista repo compartido con Track B.

5. **Validación de envelope: shape mínima con set ops antes del HMAC, Pydantic tipado después.** Un solo modelo `TypedEnvelope` Pydantic, no dos. El SCHEMA fail post-HMAC aterriza en `bronze.ingest_failures`.

6. **`IngestEvent.entity_type` se setea explícito por handler**, no se infiere del payload. Corrige bug del v2 §11.2 (que asumía un campo `entity_type` en payload que no existe). El doc se corregirá a v2.1 cuando se actualice.

7. **EntityType del server: 4 valores.** `SENSOR_READING`, `EMPIRICAL_OBSERVATION`, `EMPIRICAL_EVENT`, `COMMENT`. Sin `empirical_record` (que es legacy del edge pre-Fase 8.5).

8. **`received_at_utc` se computa una vez al entrar al handler** y se pasa explícito al INSERT. `DEFAULT now()` queda como fallback defensivo (por si alguna vez se hace INSERT manual desde psql). Garantiza coherencia entre logs, IngestEvent en memoria, bronze tables, e ingest_log.

9. **`SUPPORTED_SCHEMAS = frozenset({"1.0"})`** en 9.1. Cualquier otro valor → 400 SCHEMA. Cuando llegue v1.1, simplemente extender el frozenset.

10. **Tests integration: docker-compose con Postgres permanentemente corriendo + schema temporal por sesión.** Fixture crea schema con UUID en el nombre, corre Alembic apuntando ahí, dropea al final. Testcontainers descartado para piloto.

11. **Migraciones se aplican al startup del container** vía `docker-entrypoint.sh` que corre `alembic upgrade head` antes del CMD. Para piloto con un solo admin. Cuando lleguen migraciones críticas en Fase 11+, pasar a aplicarlas manualmente antes del deploy.

12. **Migración `001_init.sql` crea el schema completo del v2 §6.3.** Las 7 tablas: `station`, `station_credentials`, `bronze.sensor_raw`, `bronze.empirical_observation_raw`, `bronze.empirical_event_raw`, `bronze.ingest_log`, `bronze.ingest_failures`. 9.1 solo usa `empirical_observation_raw` activamente; el resto queda ready-to-use para 9.2.

13. **`IngestEventPublisher` Protocol** con `PublishResult(persisted: bool, transport: str, topic: str)`. Definido por nosotros según el v2 §11.1, se ajusta si el profesor (Track B) pide cambios.

14. **`X-Server-Version` visible con git commit corto** vía `scripts/embed_version.py` automático (no hardcodeado). Dos modos: lee git en dev, lee env vars `YVY_BUILD_*` en build container.

15. **Bearer token admin único** en `YVY_ADMIN_TOKEN` env var. Sistema multi-admin con login queda para Fase 12.

16. **`bronze.ingest_failures` poblada desde 9.1.** SCHEMA failures que pasan HMAC pero fallan al tipar el payload se persisten ahí. Conceptualmente equivalente al topic `yvy.dlq` de Track B.

---

## Estructura de carpetas del server

```
yvy-server/
├── pyproject.toml
├── README.md
├── .gitignore
├── .env.example
├── Dockerfile                          # multi-stage, runtime slim
├── docker-compose.yml                  # postgres + app
├── docker-entrypoint.sh                # espera DB → alembic → exec uvicorn
├── alembic.ini                         # paso 3
├── alembic/
│   ├── env.py                          # paso 3 — async engine
│   └── versions/
│       └── 001_init.py                 # paso 3 — schema completo v2 §6.3
├── scripts/
│   ├── embed_version.py                # genera _version.py en build
│   └── smoke_e2e.py                    # paso 11
├── src/yvy_server/
│   ├── __init__.py                     # versioning con fallback
│   ├── config.py                       # paso 2 — pydantic-settings
│   ├── logging_setup.py                # paso 2 — StructuredFormatter JSON
│   ├── main.py                         # paso 7 — FastAPI app factory + lifespan
│   ├── domain/
│   │   ├── auth/                       # canonical_json + exceptions (paso 4)
│   │   ├── ingest/                     # IngestEvent + Protocol + TOPIC_NAMES (paso 4)
│   │   └── station/                    # Station + Credential VOs (paso 4)
│   ├── application/
│   │   ├── auth/                       # validate_envelope service (paso 6)
│   │   └── use_cases/                  # ingest_observation, etc (paso 6)
│   ├── infrastructure/
│   │   ├── db/                         # engine + session + ORM models (paso 5)
│   │   ├── persistence/                # repositories (paso 5)
│   │   └── publisher/                  # PostgresDirectPublisher (paso 5)
│   ├── presentation/
│   │   └── api/
│   │       ├── routers/                # health, ingest, admin (paso 7)
│   │       └── schemas.py              # Pydantic envelope (paso 7)
│   └── cli/                            # typer CLI (paso 8)
└── tests/
    ├── conftest.py
    ├── unit/                           # paso 9
    └── integration/                    # paso 10
```

## Stack final

| Capa | Lib | Versión | Razón |
|---|---|---|---|
| HTTP | FastAPI | ^0.115 | Async-native, Pydantic v2 nativo, OpenAPI auto |
| ASGI | Uvicorn[standard] | ^0.32 | Producción + reload en dev |
| Validation | Pydantic | ^2.9 | Coerción tipada después del HMAC |
| Settings | pydantic-settings | ^2.5 | Env vars con type checking |
| ORM | SQLAlchemy[asyncio] | ^2.0.36 | API 2.x async |
| DB driver | asyncpg | ^0.30 | Driver async puro |
| Migrations | Alembic | ^1.13 | Async support |
| CLI | Typer | ^0.13 | Sync, fácil de testear |
| Tests | pytest + pytest-asyncio + httpx | — | TestClient sobre ASGI |
| Quality | ruff + mypy strict | ^0.7 / ^1.13 | Mismas rulesets que edge |

Python 3.12 obligatorio (asume `datetime.UTC`, `match`, type hint `X | None`).

---

## CLI (planeado, completo en paso 8)

```bash
poetry run yvy-server --help

# Crear nueva estación
poetry run yvy-server create-station \
    --name "IEA 7 - Cerro Azul" \
    --location-name "Cerro Azul, Misiones" \
    --lat -27.0333 --lon -55.4833 \
    [--output-file /mnt/pendrive/iea7_credentials.txt]
```

**Output esperado:**

```
Station creada exitosamente.

station_uuid:      550e8400-e29b-41d4-a716-446655440000
station_name:      IEA 7 - Cerro Azul
key_version:       1
hmac_key (hex):    a1b2c3d4e5f6789012345678901234567890abcdef1234567890abcdef123456

────────────────────────────────────────────────────────────────────────
IMPORTANTE — LEER ANTES DE CERRAR ESTA TERMINAL:

1. La hmac_key NO se va a mostrar otra vez. Si la perdés, hay que
   rotar generando una key_version=2.
2. Guardala AHORA en el pendrive de onboarding de esta IEA.
3. NO la mandes por mail, chat, o cualquier canal no seguro.
────────────────────────────────────────────────────────────────────────

Saved to DB: station + station_credentials (key_version=1).
Audit log: STATION_CREATED registered (key value NOT in audit).
```

Si se pasa `--output-file`, el contenido completo se escribe también al archivo (para transporte físico en pendrive).

CLI es **sync** (no async), no necesita asyncpg para uso ocasional manual. Usa `psycopg` síncrono o engine sync de SQLAlchemy.

---

## Guía de instalación operativa

### Para arrancar desde cero (PC nueva, sesión limpia)

**Requisitos previos:**

- Python 3.12 instalado (`python --version`).
- Poetry 1.8+ (`pipx install poetry==1.8.3`).
- Docker y Docker Compose v2 (`docker compose version`).

**Pasos:**

```bash
# 1. Clonar y entrar al server
git clone <repo-url> yvy
cd yvy/yvy-server

# 2. Copiar y editar variables de entorno
cp .env.example .env

# 3. CRÍTICO: generar el admin token
python -c "import secrets; print(secrets.token_urlsafe(48))"
# → copiar el output a YVY_ADMIN_TOKEN en .env

# 4. Instalar deps de desarrollo
poetry install

# 5. Levantar Postgres (la app completa llega en paso 7)
docker compose up -d postgres
docker compose ps   # esperar "healthy"

# 6. Validar versioning
poetry run python scripts/embed_version.py
cat src/yvy_server/_version.py

# A partir del paso 3 (Alembic):
poetry run alembic upgrade head

# A partir del paso 8 (CLI):
poetry run yvy-server create-station --name "..." ...

# A partir del paso 7 (FastAPI completa):
poetry run uvicorn yvy_server.main:app --reload --port 8000

# A partir del paso 10 (tests integration):
poetry run pytest -v
```

### Para abrir nuevo chat con otra IA y continuar trabajo

Adjuntá estos archivos en orden:

1. `HANDOFF_FASE9.md` — contexto general del proyecto, convenciones, mi forma de trabajo.
2. `YVY_FASE9_Arquitectura_v2.md` — arquitectura completa de la fase.
3. `yvy_FASE91_RESUMEN.md` (este archivo) — estado actual y decisiones cerradas.
4. (Opcional) `YVY_Anexo_Tecnico_v4.md` — para contexto institucional/histórico.

**Indicaciones explícitas a la IA:**

- Las decisiones del §1 (1 a 16) están cerradas. No re-abrir sin razón fuerte.
- Antes de tirar código, sesión arquitectónica si hay zona gris.
- Marcar sobre-diseño cuando lo detectes (recordatorio: mi tendencia es sobre-diseñar).
- Voseo, directo, sin amabilidad innecesaria.

---

## Estado de pasos 9.1

| Paso | Descripción | Estado |
|---|---|---|
| 1 | Scaffold del proyecto (pyproject, Dockerfile, compose, entrypoint, embed_version, estructura hexagonal vacía) | ✓ CERRADO 16-May-2026 |
| 2 | `config.py` (pydantic-settings) + `logging_setup.py` (StructuredFormatter JSON) | pendiente |
| 3 | Alembic config + migration 001_init (schema completo v2 §6.3) | pendiente |
| 4 | Domain layer: auth, ingest, station | pendiente |
| 5 | Infrastructure: db, persistence, publisher | pendiente |
| 6 | Application: validate_envelope service + ingest_observation use case | pendiente |
| 7 | Presentation: FastAPI factory + routers + schemas + main.py | pendiente |
| 8 | CLI: create_station con typer | pendiente |
| 9 | Tests unit (canonical JSON golden, HMAC validator, schemas) | pendiente |
| 10 | Tests integration (cada endpoint + happy + duplicate + 400/401/422 + ingest_failures) | pendiente |
| 11 | Smoke E2E local (compose up + create-station + POST firmado + verifica bronze + duplicate path) | pendiente |

---

## Archivos generados en paso 1

| Archivo | Propósito |
|---|---|
| `pyproject.toml` | Deps, ruff (10 rulesets), mypy strict, pytest async, coverage |
| `.gitignore` | Python + Docker + IDE. Excluye `poetry.lock` (decisión revisable) y `_version.py` (auto-generado) |
| `.gitattributes` | Fuerza LF en `.sh`, `Dockerfile`, `.py`, `.md`, etc. Previene el bug clásico de CRLF en `docker-entrypoint.sh` desde Windows |
| `.env.example` | Documenta todas las env vars que `config.py` leerá |
| `README.md` | Setup local + Docker + tests + CLI + estructura + **sección "Desarrollo en Windows"** (Git Bash vs WSL, opciones A/B/C para Python en host) |
| `Dockerfile` | Multi-stage (builder con poetry, runtime con libpq5 + curl). Build args para git commit. Healthcheck + usuario no-root |
| `docker-compose.yml` | postgres 16-alpine + app. `YVY_ADMIN_TOKEN` requerido con `:?...` (falla explícito si no se setea) |
| `docker-entrypoint.sh` | Espera DB (loop TCP defensivo) → `alembic upgrade head` → `exec` del CMD |
| `scripts/embed_version.py` | Dos modos: dev (lee git) y build (lee env vars `YVY_BUILD_*`). Genera `_version.py` |
| `src/yvy_server/__init__.py` | Versioning con fallback `"0.9.1-dev+unknown"` si `_version.py` no existe |
| 14 × `__init__.py` (capas hexagonales) | Docstrings orientadores 1-2 líneas. Sin imports todavía |
| `tests/__init__.py` + `tests/conftest.py` | Placeholders para pasos 9-10 |

**Total: 29 archivos.** Validación: todos los `.py` parsean OK, ambos modos de `embed_version.py` funcionan, import del package con fallback OK.

## Flujo de trabajo Windows → Git → Server

Decidido el 16-May-2026: desarrollo en Windows + Docker, no en el server.

- Git como source of truth.
- Server no se toca hasta que 9.1 cierre entera.
- `.gitattributes` con LF forzado previene el bug de CRLF en `docker-entrypoint.sh`.
- Recomendación de terminal: Git Bash (suficiente) o WSL2 (opcional).
- Recomendación de Python: opción B (Poetry en host solo para IDE, tests siempre en container).
- Detalles completos en `README.md` § "Desarrollo en Windows".

---

## Deuda técnica registrada hasta paso 1

1. **`poetry.lock` gitignored.** Cuando se monte CI estable que use `poetry install --no-update`, revertir esa línea del `.gitignore` y commitear el lock. Mientras tanto, cada `poetry install` puede resolver versiones distintas (no bloqueante para piloto).

2. **`scripts/embed_version.py` sin tests propios.** Script de build, falla rápido si rompe. No crítico.

3. **Sin pre-commit hooks aún.** El edge tiene; replicar en server cuando se complete 9.1 entera.

4. **Sin `Makefile` o `justfile`.** Comandos comunes en el README. Agregar `justfile` solo si los comandos se vuelven repetitivos en operación diaria.

5. **`X-Server-Version` requiere disciplina de build.** Si alguien levanta el container sin pasar `--build-arg GIT_COMMIT=...`, la versión queda `"unknown"`. Mitigación futura: script de deploy que automatice el `docker build` con los args.

---

## Lo que sigue

**Paso 2:** `src/yvy_server/config.py` con `pydantic-settings` leyendo las env vars de `.env.example` + `src/yvy_server/logging_setup.py` con `StructuredFormatter` JSON. Tanda corta, posiblemente 2 archivos + 1 test unit.

Cuando se complete 9.1 entera, este archivo queda como histórico CERRADO. Para 9.2 nace `yvy_FASE92_RESUMEN.md` separado.
