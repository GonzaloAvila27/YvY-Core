# Guía de Ensamblaje — YVY Server (Fase 9.1)

> **Para quién:** ayudantes que continúan el proyecto YVY. No asume contexto previo sobre el server; sí asume Python básico, conceptos generales de bases de datos y HTTP.
>
> **Cómo leerla:** primera vez, leer en orden A → F. Después, usar como referencia. El documento `yvy_FASE91_RESUMEN.md` tiene los detalles técnicos exhaustivos (decisiones numeradas, archivo por archivo) — esta guía es la vista panorámica.

---

## 0. Para qué sirve esta guía

YVY Server es el componente central que recibe los datos meteorológicos firmados desde las netbooks de las IEAs (Bonpland, Loreto, Garupá). La Fase 9.1 implementó todo el server: API HTTP, validación HMAC, persistencia en Postgres, CLI operativa, tests.

Esta guía permite:

1. **Reproducir el server en tu máquina** desde cero (Parte A — Setup).
2. **Entender cómo está armado** capa por capa (Parte B — Tour).
3. **Saber qué reglas no romper** al modificar código (Parte C).
4. **Resolver problemas comunes** (Parte E — Troubleshooting).

Si ven una decisión rara, busquen en `yvy_FASE91_RESUMEN.md` por número de decisión (1 a 94). Cada decisión tiene su justificación documentada.

---

## 1. Prerrequisitos

| Herramienta | Versión | Para qué |
|---|---|---|
| Python | 3.12+ | Lenguaje del server |
| Poetry | 1.7+ | Manejo de dependencias |
| Docker + Docker Compose | reciente | Postgres local en contenedor |
| Git | cualquiera | Clonar repo |

Para verificar:

```bash
python --version       # 3.12.x
poetry --version       # 1.7.x o más
docker --version       # 20.x o más
docker compose version # v2.x
git --version
```

Si falta algo, instalar antes de seguir. En Windows usar WSL2 con Ubuntu 24. En Linux nativo, lo de arriba es suficiente.

---

# Parte A — Setup (cómo dejar el proyecto andando)

Tiempo estimado: 30-45 minutos la primera vez.

## A.1. Clonar el repo y dependencias

```bash
git clone <url-del-repo> yvy-server
cd yvy-server
poetry install
```

Si `poetry install` falla con algún paquete, verificar que Python sea 3.12 (no 3.11 ni 3.10 — usamos sintaxis nueva como `Mapped[T]`, `frozenset[str]`, `int | None`).

**Verificación:** debe poder importar el package.

```bash
poetry run python -c "from yvy_server import __version__; print(__version__)"
# Esperado: algo como "0.9.1-dev+abc1234" (commit corto al final)
```

## A.2. Levantar Postgres

El `docker-compose.yml` del repo levanta un Postgres 16 listo para usar.

```bash
docker compose up -d postgres
```

**Verificación:** debe responder a ping.

```bash
docker compose exec postgres pg_isready -U yvy
# Esperado: "/var/run/postgresql:5432 - accepting connections"
```

Si lo querés verificar manualmente con psql:

```bash
docker compose exec postgres psql -U yvy -d yvy -c "SELECT version();"
```

## A.3. Variables de entorno

El server lee config desde variables de entorno o un archivo `.env` en la raíz del repo. Para desarrollo local, crear `.env`:

```bash
cat > .env <<'EOF'
YVY_ENV=local
YVY_DB_DSN=postgresql+asyncpg://yvy:yvy@localhost:5432/yvy
YVY_ADMIN_TOKEN=dev-admin-token-cambiar-en-produccion
YVY_LOG_LEVEL=DEBUG
EOF
```

**Importante:** este archivo NO se commitea (está en `.gitignore`). En `staging` o `production` el `YVY_ADMIN_TOKEN` se valida más estricto (mínimo 32 chars). En `local` se acepta cualquier string no vacío.

## A.4. Migrar el schema

El schema de la base se gestiona con Alembic. Hay una sola migración (`0001_init`) que crea las 7 tablas.

```bash
poetry run alembic upgrade head
```

**Verificación:** debe haber 7 tablas creadas.

```bash
docker compose exec postgres psql -U yvy -d yvy -c "
SELECT table_schema, table_name FROM information_schema.tables
WHERE table_schema IN ('public', 'bronze')
ORDER BY table_schema, table_name;
"
```

Esperado:

```
 table_schema |              table_name
--------------+--------------------------------------
 bronze       | empirical_event_raw
 bronze       | empirical_observation_raw
 bronze       | ingest_failures
 bronze       | ingest_log
 bronze       | sensor_raw
 public       | station
 public       | station_credentials
```

Además, `alembic check` debe decir "no drift":

```bash
poetry run alembic check
# Esperado: "No new upgrade operations detected."
```

**Esto es importante.** Si dice algo distinto, significa que los modelos ORM (en `infrastructure/db/models.py`) están desincronizados con el schema real. NO modifiquen las migraciones existentes; si necesitan cambiar el schema, generen una migración nueva con `alembic revision --autogenerate -m "descripción"`.

## A.5. Crear una station de prueba

Para que el server acepte requests de ingest, necesita conocer la station (UUID + credenciales HMAC). La CLI hace esto:

```bash
poetry run yvy-server create-station \
    --name "IEA Test" \
    --location-name "Posadas, Misiones" \
    --output-file /tmp/iea-test.json
```

Salida esperada en stdout (las credentials):

```json
{
  "station_uuid": "...",
  "key_version": 1,
  "key_hmac_hex": "...",
  "name": "IEA Test",
  "created_at_utc": "2026-..."
}
```

Y en stderr (log estructurado de auditoría):

```json
{"ts":"...","level":"INFO","logger":"yvy.cli","msg":"station_created","station_uuid":"...","display_name":"IEA Test","key_version":1,"key_length_bytes":32}
```

El archivo `/tmp/iea-test.json` contiene lo mismo que stdout — lo usamos en A.7 para firmar requests.

## A.6. Levantar el server HTTP

```bash
poetry run uvicorn yvy_server.main:app --host 127.0.0.1 --port 8000
```

Logs esperados al startup:

```json
{"ts":"...","level":"INFO","logger":"yvy.main","msg":"starting up","version":"...","env":"local","log_level":"DEBUG","supported_schemas":["1.0"],"clock_skew_max_seconds":1800}
{"ts":"...","level":"INFO","logger":"yvy.main","msg":"startup complete; ready to serve requests"}
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000
```

El server queda corriendo en foreground. Para detenerlo: `Ctrl+C`.

## A.7. Smoke manual (verificación end-to-end)

En otra terminal, con el server corriendo:

```bash
# 1. Healthz público
curl http://127.0.0.1:8000/api/v1/healthz
# Esperado: {"status":"ok"}

# 2. Server info
curl http://127.0.0.1:8000/api/v1/serverinfo
# Esperado: JSON con version, git_commit_short, schema_versions_supported

# 3. Admin healthz CON token (lee del .env)
curl -H "Authorization: Bearer dev-admin-token-cambiar-en-produccion" \
     http://127.0.0.1:8000/api/v1/admin/healthz
# Esperado: JSON con db_responsive=true y counts de cada tabla

# 4. Admin healthz SIN token → 401
curl -i http://127.0.0.1:8000/api/v1/admin/healthz
# Esperado: HTTP 401, body con error_class=AUTH
```

Para el endpoint `/ingest/observation` hay que firmar un envelope. Está más involucrado — el script de smoke E2E en `scripts/smoke.py` (si existe) o el que se arme en paso 11 cubre eso. Por ahora alcanza con confirmar que los 4 endpoints anteriores funcionan.

**Si llegaron acá con todo verde:** felicitaciones, el server está operativo. Pueden detenerlo y pasar a la Parte B para entender qué es cada cosa.

---

# Parte B — Tour por el código (capa por capa)

El server está organizado en capas siguiendo **arquitectura hexagonal** (a.k.a. "ports and adapters"). La idea: el "dominio" (las reglas del negocio) no depende de nada externo (DB, HTTP, etc). Las cosas externas son "adaptadores" que se conectan al dominio.

Dirección de dependencias (más adentro depende solo de lo más adentro):

```
presentation  →  application  →  domain
infrastructure  →  application  →  domain
                              ↓
                           (nadie)
```

Vista desde el repo:

```
src/yvy_server/
├── __init__.py              # exports __version__, __build_date__, __git_commit_short__
├── config.py                # Settings (pydantic-settings)
├── logging_setup.py         # JSON structured logging
├── main.py                  # FastAPI app factory + lifespan (composition root)
├── cli.py                   # CLI: create-station + version
│
├── domain/                  # CORE: reglas, VOs, exceptions
│   ├── auth/                # Canonical JSON + HMAC exceptions
│   ├── ingest/              # IngestEvent VO + EntityType + Publisher Protocol
│   └── station/             # Station, StationCredential VOs
│
├── application/             # CASOS DE USO: orquestan el dominio
│   ├── auth/                # EnvelopeValidator
│   └── use_cases/           # IngestObservationUseCase
│
├── infrastructure/          # ADAPTADORES de salida (DB)
│   ├── db/                  # SQLAlchemy engine + ORM models
│   ├── persistence/         # Repositories
│   └── publisher/           # PostgresDirectPublisher
│
└── presentation/            # ADAPTADORES de entrada (HTTP)
    └── api/
        ├── schemas.py       # Pydantic models de payloads + responses
        ├── dependencies.py  # FastAPI Depends
        ├── middleware.py    # ServerHeadersMiddleware
        └── routers/         # health, admin, ingest
```

Recorremos cada capa.

## B.1. Configuración y logging

**Archivos:** `config.py`, `logging_setup.py`.

`config.py` define `Settings` con pydantic-settings: lee `.env` y variables de entorno. Hace validaciones (DSN debe empezar con `postgresql+asyncpg://`, admin token strict en staging/production, etc).

`logging_setup.py` configura logging JSON estructurado. Cada log line es un JSON object con `ts`, `level`, `logger`, `msg` y cualquier campo extra que se pase con `extra={...}`.

**Cómo usar logging en código nuevo:**

```python
import logging
log = logging.getLogger("yvy.mi_modulo")  # convención: prefijo "yvy."

log.info(
    "algo ocurrió",
    extra={
        "station_uuid": str(station_uuid),
        "event_id": str(event_id),
        # NO usar "name", "msg", "level" — son reserved keys
    },
)
```

**Trampa documentada:** `extra={"name": ...}` levanta `KeyError: "Attempt to overwrite 'name' in LogRecord"`. Reserved keys de Python logging que NO pueden ir en `extra`: `name`, `msg`, `args`, `levelname`, `levelno`, `module`, `message`, `pathname`, `filename`, `lineno`, `funcName`, `created`, `thread`, `process`, `asctime`. Si querés loggear el "nombre" de algo, usá `display_name` o `entity_name`, no `name`.

## B.2. Persistencia (schema + Alembic)

**Archivos:** `alembic/versions/0001_init.py`, `infrastructure/db/models.py`.

Hay 7 tablas, organizadas en 2 schemas Postgres:

| Schema | Tabla | Para qué |
|---|---|---|
| `public` | `station` | Registro de IEAs (1 fila por IEA) |
| `public` | `station_credentials` | Claves HMAC versionadas (1 o más por station) |
| `bronze` | `sensor_raw` | Lecturas automáticas pywws (futuro endpoint sensor) |
| `bronze` | `empirical_observation_raw` | Observaciones de la app empírica (endpoint observation) |
| `bronze` | `empirical_event_raw` | Eventos puntuales del edge (futuro endpoint event) |
| `bronze` | `ingest_log` | TODO intento HTTP de ingest (éxito y fallo) |
| `bronze` | `ingest_failures` | DLQ: envelopes que pasaron HMAC pero el payload tipa mal |

**Regla importante:** `bronze.ingest_log` es para **intentos HTTP de ingest**. NO escribir ahí desde el CLI ni desde acciones administrativas. Si en algún momento hace falta auditoría de acciones admin (rotate-key, deactivate-station), se crea `admin_audit_log` aparte. Mantener `ingest_log` semánticamente puro.

**Migraciones:**

- NO modificar `0001_init.py` (ya está en producción).
- Para cambios futuros: `poetry run alembic revision --autogenerate -m "descripción"`.
- Antes de cada commit, correr `alembic check` para verificar que los modelos ORM están sincronizados con el schema real.

`models.py` declara los modelos ORM SQLAlchemy 2.x con `Mapped[T]`. Las 3 tablas raw bronze comparten un mixin `_BronzeRawMixin` (las 12 columnas comunes están definidas una sola vez).

**Todos los índices están declarados en `__table_args__`.** Esto incluye:

- Índices multi-columna con `DESC`.
- Expression indexes sobre JSONB (e.g. `(payload->>'observed_at_utc')`).
- Partial indexes con `WHERE` (e.g. `WHERE failure_class IS NOT NULL`).

Si agregan un índice en una migración nueva, **también** declararlo en el ORM model o `alembic check` va a marcar drift.

## B.3. Dominio

**Archivos:** `domain/auth/`, `domain/ingest/`, `domain/station/`.

El dominio es **stdlib-only** — no importa SQLAlchemy, FastAPI, Pydantic ni httpx. Son dataclasses frozen + exceptions + un Protocol + funciones puras.

### `domain/auth/canonical_json.py`

La función `canonicalize(obj)` serializa un dict a bytes UTF-8 de forma determinística:

- Keys ordenadas alfabéticamente.
- Sin whitespace.
- UTF-8 nativo (acentos como `ñ` son bytes UTF-8, no escapes `\u00f1`).
- Rechaza `NaN`, `Infinity`.

Este es el byte stream que se firma con HMAC-SHA256.

**Este archivo es la fuente de verdad.** Cuando 9.2 implemente el sync agent en el edge, el edge debe copiar este archivo byte-a-byte y replicar los 29 golden tests (`tests/unit/test_canonical_json.py`).

**No modificar** `canonical_json.py` sin entender las implicancias:

1. Cualquier cambio que produzca bytes distintos para los mismos inputs invalida TODAS las firmas en `bronze.*_raw`.
2. Si necesitan cambiarlo, hay que bumpear `CANONICAL_JSON_SPEC_VERSION` y planificar una migración del edge.

### `domain/auth/exceptions.py`

Jerarquía:

```
EnvelopeError                # base
├── EnvelopeShapeError       # SCHEMA 400 (pre-HMAC)
├── HmacMismatchError        # AUTH 401
├── StationNotFoundError     # AUTH 401
├── CredentialNotFoundError  # AUTH 401
├── ClockSkewError           # CLOCK 422
└── PayloadSchemaError       # SCHEMA 400 (post-HMAC, va a DLQ)
```

Cada subclass declara `error_class` y `http_status` como `ClassVar`. La base tiene un `__init__` unificado que acepta `station_uuid`, `event_id`, `skew_seconds` opcionales — los raisers setean lo que saben en cada punto del flow.

### `domain/ingest/events.py`

`EntityType` StrEnum con 4 valores: `sensor_reading`, `empirical_observation`, `empirical_event`, `comment`. El edge tiene un 5º valor legacy (`empirical_record` pre-Fase 8.5) que el server no conoce — el sync agent en 9.2 debe filtrar esos jobs.

`IngestEvent` es el VO frozen que cruza el sistema. Se construye en el use case con `IngestEvent.from_envelope(envelope, entity_type=..., ...)`. **El `entity_type` se pasa explícito** por el handler (NO se lee del envelope — el envelope no lo tiene).

`TOPIC_NAMES` mapea cada `EntityType` a un topic name lógico (`yvy.sensor.raw`, etc). Track A no los usa en runtime, pero Track B (Kafka) sí.

### `domain/ingest/publisher.py`

Protocol `IngestEventPublisher` con un solo método `async def publish(event) -> PublishResult`. Es el "port" en la arquitectura hexagonal. Las implementaciones concretas viven en `infrastructure/publisher/`.

`PublishResult` tiene 4 campos: `persisted`, `transport`, `topic`, `was_duplicate`. El último permite al handler devolver `ingest_result: "duplicate"` cuando el INSERT detecta `event_id` repetido.

## B.4. Infraestructura

**Archivos:** `infrastructure/db/`, `infrastructure/persistence/`, `infrastructure/publisher/`.

### `infrastructure/db/`

`engine.py` construye el async engine de SQLAlchemy + session factory desde `Settings`. Config notable:

- `expire_on_commit=False`: los ORM instances siguen usables después de commit. Importante porque los repos mapean ORM → VO leyendo atributos post-commit.
- `autoflush=False`, `autocommit=False`: comportamiento explícito. Para INSERTs con FK al mismo statement (ej: station + credential), hay que llamar `await session.flush()` manualmente entre adds.

`models.py` declara los 7 ORM models con `Mapped[T]`. Mixin `_BronzeRawMixin` para las 3 raw.

### `infrastructure/persistence/`

5 repositories stateless. Cada método recibe `session: AsyncSession` como primer arg — el caller maneja la transacción.

| Repo | Métodos |
|---|---|
| `StationRepository` | `get_by_uuid`, `insert` |
| `CredentialRepository` | `get_active_by_station_and_version` (filtra `deactivated_at IS NULL`), `insert` |
| `BronzeRepository` | `upsert(event)` — dispatch por `entity_type` a 1 de 3 tablas + `INSERT ON CONFLICT DO NOTHING RETURNING event_id` |
| `IngestLogRepository` | `record_attempt(...)` kw-only |
| `IngestFailuresRepository` | `record_failure(...)` kw-only — DLQ post-HMAC |

**Los repos devuelven VOs del dominio**, NO ORM rows. Cada repo tiene un helper `_orm_to_xxx()` que mapea. Esto evita que el ORM se filtre hacia application/presentation.

`BronzeRepository.upsert` levanta `NotImplementedError` si recibe `EntityType.COMMENT` (porque no hay tabla bronze para comments en 9.1 — el endpoint `/ingest/comment` es stub 501).

### `infrastructure/publisher/`

`PostgresDirectPublisher` implementa el Protocol `IngestEventPublisher` por **structural typing** (no inheritance). Recibe `session_factory` y `bronze_repo` en `__init__`, abre su propia transacción en cada `publish()`.

Cuando Track B (Kafka) llegue, el composition root en `main.py` reemplaza `PostgresDirectPublisher(...)` por `DualPublisher(...)` o `KafkaPublisher(...)` — el use case NO cambia (depende del Protocol).

## B.5. Aplicación

**Archivos:** `application/auth/envelope_validator.py`, `application/use_cases/ingest_observation.py`.

### `EnvelopeValidator`

Orquesta toda la validación pre-publish en este orden (importante, no cambiar):

1. **Shape** (set ops sobre required fields, schema_version en supported).
2. **Station resolution** (lookup en DB).
3. **Credential resolution** (lookup activo por station + key_version).
4. **HMAC verify** (canonicalize + hmac.new + `compare_digest`).
5. **Clock skew** (después del HMAC, "no confiamos en el timestamp hasta que la firma esté validada").

Devuelve `ValidatedEnvelope` o levanta `EnvelopeError`. Las funciones puras (`validate_shape`, `verify_hmac`, `check_clock_skew`) están expuestas a nivel módulo para tests sin DB.

### `IngestObservationUseCase`

El caso de uso completo del endpoint `/ingest/observation`. Recibe el envelope + metadata del request + un `payload_validator` callable inyectado por el handler.

**El use case NUNCA raisa.** Atrapa toda excepción y devuelve un `IngestOutcome` (union discriminada):

- `IngestSuccess(event_id, was_duplicate, received_at_utc)` → handler manda 200.
- `IngestFailure(http_status, error_class, error_message)` → handler manda 4xx/5xx.

**Por qué `payload_validator` inyectado:** los Pydantic models por endpoint viven en `presentation/api/schemas.py`. Si el use case importara esos models, rompería la dirección de deps (application no debe depender de presentation). El handler le pasa una función que internamente llama `EmpiricalObservationPayload.model_validate(payload)`. Si raisa cualquier exception, el use case captura → `ingest_failures` + log + `IngestFailure(400, "SCHEMA")`.

## B.6. Presentación

**Archivos:** `presentation/api/`, `main.py`.

### `schemas.py`

Pydantic models para:

- **Payloads tipados** (validación post-HMAC): `EmpiricalObservationPayload`. Tiene `extra="allow"` para forward compatibility (v2 §2.6).
- **Response models**: `HealthzResponse`, `ServerInfoResponse`, `AdminHealthzResponse`, `IngestSuccessResponse`, `IngestErrorResponse`.

**El envelope completo NO se valida como Pydantic model.** Si lo hiciéramos pre-HMAC, Pydantic re-serializaría y rompería la firma. Los handlers reciben bytes raw → `json.loads` → dict → use case.

### `dependencies.py`

Funciones que leen `app.state` (poblado por el lifespan) y exponen objetos via `Depends`:

- `get_settings_dep`, `get_session_factory_dep`, `get_session_dep`
- `get_observation_use_case`
- `verify_admin_token` (bearer + `secrets.compare_digest`)

### `middleware.py`

`ServerHeadersMiddleware` agrega 3 headers a TODA response (incluso errores):

- `X-Server-Time`: ISO UTC ms+Z. El edge lo usa para recalibrar su clock cuando recibe 422 CLOCK.
- `X-Schema-Versions-Supported`: CSV (e.g. `1.0`). El edge sabe qué versions puede mandar.
- `X-Server-Version`: del build.

### `routers/`

3 modules con `APIRouter`:

| Router | Endpoints | Auth |
|---|---|---|
| `health` | `GET /api/v1/healthz`, `GET /api/v1/serverinfo` | público |
| `admin` | `GET /api/v1/admin/healthz` | bearer token (router-level Depends) |
| `ingest` | `POST /api/v1/ingest/observation`, `POST /api/v1/ingest/comment` (501) | HMAC en el payload |

### `main.py` (composition root)

El **lifespan** construye TODAS las dependencies una sola vez al startup:

```
1. Load Settings desde env
2. setup_logging(level, stream=sys.stdout)
3. create_engine_and_session_factory(settings)
4. Instanciar repos (stateless, una vez)
5. Build EnvelopeValidator
6. Build PostgresDirectPublisher
7. Build IngestObservationUseCase
8. Todo en app.state
```

`create_app()` registra middleware + routers. Variable module-level `app = create_app()` para `uvicorn yvy_server.main:app`.

## B.7. CLI

**Archivo:** `cli.py` (módulo plano, no subpackage).

Typer **sync** con 2 comandos:

```bash
yvy-server version
yvy-server create-station --name "X" [--description ...] [--location-lat ...] [--output-file ...]
```

Sync handler usa `asyncio.run(_persist_station(...))` internamente para hablar con la DB async.

**Reglas del CLI:**

- **No escribe a `bronze.ingest_log`.** Esa tabla es para ingest HTTP. Audit admin va por log estructurado JSON a stderr.
- **stdout limpio**: solo el JSON de credentials (pipe-friendly: `yvy-server create-station --name X | jq .station_uuid`).
- **stderr**: log estructurado + notice de archivo escrito.
- **Si la DB falla, exit code 1.** Si todo OK, exit 0.

---

# Parte C — Reglas que NO se rompen

Estas reglas son invariantes del proyecto. Romperlas tiene consecuencias documentadas:

## C.1. `canonical_json.py` es inmutable sin proceso

Si necesitan cambiar `domain/auth/canonical_json.py`:

1. Discutirlo con el responsable del proyecto.
2. Bumpear `CANONICAL_JSON_SPEC_VERSION`.
3. Regenerar los 29 golden tests.
4. Coordinar la actualización del edge en lockstep.

Sin esos pasos, todas las firmas existentes en bronze quedan inválidas.

## C.2. `alembic check` debe estar en verde antes de cada commit

```bash
poetry run alembic check
# debe decir: "No new upgrade operations detected."
```

Si dice algo distinto, hay drift entre ORM models y migraciones. Resolver antes de commitear.

## C.3. `bronze.ingest_log` es solo para intentos HTTP de ingest

NO escribir desde:

- CLI (acciones admin van por log estructurado)
- Tareas batch
- Cualquier código que no sea el handler del ingest

Si hace falta audit admin persistido, crear `admin_audit_log` tabla aparte.

## C.4. El dominio (domain/) es stdlib-only

NO importar desde `domain/`:

- SQLAlchemy
- Pydantic
- FastAPI
- httpx
- ninguna lib externa

Si una funcionalidad del dominio "necesita" Pydantic, va en application/ o presentation/.

## C.5. Los repos devuelven VOs, no ORM rows

Cada repo tiene un helper `_orm_to_xxx()`. Si agregan un método nuevo a un repo y devuelve directamente un ORM model, están filtrando infraestructura hacia arriba — refactorizar antes de mergear.

## C.6. El envelope NO se valida con Pydantic pre-HMAC

El handler hace: bytes raw → `json.loads` → dict → use case. Si en algún momento alguien propone "modelar el envelope con Pydantic para validación de tipos", recordar que **rompería las firmas HMAC** (Pydantic re-serializa con orden/spacing distinto que el canonical_json del edge).

## C.7. Reserved keys de logging

NO usar en `extra={...}`:

```
name, msg, args, levelname, levelno, module, message,
pathname, filename, lineno, funcName, created, thread,
threadName, process, processName, asctime, relativeCreated,
exc_info, exc_text, stack_info
```

Usar `display_name`, `level_label`, `module_name`, etc.

## C.8. Tests deben estar verdes antes de cada PR

```bash
poetry run pytest tests/unit
# Esperado: 113 passed
```

Si bajan, hay regresión. Investigar.

---

# Parte D — Comandos útiles

## D.1. Operaciones cotidianas

```bash
# Levantar todo (postgres + server)
docker compose up -d postgres
poetry run alembic upgrade head
poetry run uvicorn yvy_server.main:app --reload

# Bajar todo
docker compose down

# Reset completo de la DB (¡destruye datos!)
docker compose down -v
docker compose up -d postgres
poetry run alembic upgrade head

# Crear una nueva station
poetry run yvy-server create-station --name "IEA X" -o /tmp/iea-x.json

# Ver version
poetry run yvy-server version
```

## D.2. Testing

```bash
# Suite completa
poetry run pytest tests/unit

# Suite con detalle
poetry run pytest tests/unit -v

# Un módulo específico
poetry run pytest tests/unit/test_envelope_validator.py -v

# Un test específico
poetry run pytest tests/unit/test_envelope_validator.py::TestVerifyHmac::test_invalid_signature_raises

# Modo "stop on first failure"
poetry run pytest tests/unit -x
```

## D.3. Migraciones

```bash
# Aplicar migraciones pendientes
poetry run alembic upgrade head

# Generar migración nueva (después de cambiar models.py)
poetry run alembic revision --autogenerate -m "add column X to station"

# Ver historia de migraciones
poetry run alembic history

# Verificar zero-drift
poetry run alembic check
```

## D.4. Debug

```bash
# Logs del server con DEBUG level
YVY_LOG_LEVEL=DEBUG poetry run uvicorn yvy_server.main:app

# Conectar a la DB con psql
docker compose exec postgres psql -U yvy -d yvy

# Ver counts en cada tabla
docker compose exec postgres psql -U yvy -d yvy -c "
SELECT 'station' AS t, COUNT(*) FROM station
UNION ALL SELECT 'station_credentials', COUNT(*) FROM station_credentials
UNION ALL SELECT 'sensor_raw', COUNT(*) FROM bronze.sensor_raw
UNION ALL SELECT 'empirical_observation_raw', COUNT(*) FROM bronze.empirical_observation_raw
UNION ALL SELECT 'empirical_event_raw', COUNT(*) FROM bronze.empirical_event_raw
UNION ALL SELECT 'ingest_log', COUNT(*) FROM bronze.ingest_log
UNION ALL SELECT 'ingest_failures', COUNT(*) FROM bronze.ingest_failures;
"

# OpenAPI interactivo (con server up)
open http://127.0.0.1:8000/docs
```

---

# Parte E — Troubleshooting

## E.1. "no module named 'yvy_server'"

Olvidaron `poetry install` o no están en el venv. Probar:

```bash
poetry env info  # muestra el venv activo
poetry install
```

## E.2. `alembic upgrade head` falla

Causas comunes:

- **Postgres no está corriendo.** `docker compose up -d postgres` y verificar `pg_isready`.
- **DSN incorrecto en `.env`.** Verificar que `YVY_DB_DSN` apunte a `localhost:5432` (o donde sea que tengan postgres).
- **DB no existe.** Crear con `docker compose exec postgres psql -U postgres -c "CREATE DATABASE yvy OWNER yvy;"`.

## E.3. `alembic check` dice "would add..."

Hay drift entre ORM models (`infrastructure/db/models.py`) y el schema real en DB. Causas:

- Agregaron un índice en una migración pero olvidaron declararlo en `__table_args__`.
- Agregaron una columna en el ORM pero no generaron migración.

Para diagnóstico: `poetry run alembic check` muestra exactamente qué difiere.

## E.4. Tests pasan local pero fallan en CI

Causa probable: `__pycache__` stale o tests dependientes del orden. Limpiar:

```bash
find . -name "__pycache__" -type d -exec rm -rf {} +
find . -name ".pytest_cache" -type d -exec rm -rf {} +
poetry run pytest tests/unit
```

## E.5. El server arranca pero `/healthz` devuelve 500

Mirar los logs del lifespan. Probablemente:

- DB no responde.
- `Settings` validation falló (variable de entorno mal).

Logs en formato JSON, fáciles de filtrar:

```bash
poetry run uvicorn yvy_server.main:app 2>&1 | grep ERROR
```

## E.6. `POST /ingest/observation` devuelve 401 AUTH

3 razones posibles, mirar el `error` en el body:

- "station_uuid=X not registered" → la station no fue creada. Correr `yvy-server create-station`.
- "no active credential for station=X key_version=N" → la station existe pero no tiene cred con esa version. Verificar `key_version` en el envelope (debe ser 1 en setup default).
- "HMAC signature mismatch" → el envelope se firmó con una key distinta a la registrada en `station_credentials`. Verificar que el JSON de credentials sea el correcto.

## E.7. `POST /ingest/observation` devuelve 422 CLOCK

El `sent_at_utc` está demasiado lejos del clock del server (> 1800s = 30 min). El edge debe usar el `X-Server-Time` del response para recalibrar.

En tests locales: confirmar que el reloj de la máquina está sincronizado (`timedatectl` en Linux).

## E.8. "extra='allow' rejected" / "field required"

Pydantic está rechazando un payload por shape. Esto pasa POST-HMAC, así que el envelope queda en `bronze.ingest_failures` para inspección:

```sql
SELECT failure_reason, raw_envelope->>'event_id' FROM bronze.ingest_failures ORDER BY received_at DESC LIMIT 5;
```

---

# Parte F — Lecciones aprendidas

Bugs encontrados durante el desarrollo de Fase 9.1. Documentados para no repetirlos:

## F.1. `extra={"name": ...}` en logger crashea

`LogRecord.name` es reserved. Usar `display_name`, `entity_name`, etc.

## F.2. HTTP headers son case-insensitive en wire pero NO en `dict(resp.headers)`

uvicorn (HTTP/1.1) emite headers lowercase. `dict(resp.headers).get("X-Server-Time")` devuelve `None`. Usar `resp.headers.get("X-Server-Time")` directo (es `email.message.Message`, case-insensitive).

## F.3. INSERTs con FK necesitan `flush()` entre adds

Con `autoflush=False`, agregar `station` y `station_credentials` al mismo session sin flush intermedio levanta FK violation. Patrón:

```python
session.add(station_orm)
await session.flush()  # forza el INSERT antes del siguiente add
session.add(credential_orm)
```

## F.4. `target_metadata = None` desactiva autogenerate

Cuando el plan original tenía `target_metadata = None` en `alembic/env.py` (paso 3), `alembic revision --autogenerate` no veía cambios. Activarlo a `Base.metadata` (hecho en paso 5.1) es lo que permite que autogenerate funcione.

## F.5. La middleware lee `app.state.settings` en cada request

Si la middleware leyera settings en su `__init__`, requeriría que `Settings` se pudiera construir en import time (necesita env vars válidas). Leer de `app.state.settings` en cada request hace que `create_app()` funcione en cualquier contexto sin env vars.

## F.6. Stdout vs stderr en el CLI

El CLI emite credentials a **stdout** y logs/notices a **stderr**. Sin esa separación, `yvy-server create-station ... | jq` falla. Reverso: el server emite logs a stdout (12-factor, systemd captura).

---

# Matriz de pasos cerrados (Fase 9.1)

Para que sepan dónde está cada cosa en el código y en el resumen exhaustivo.

| Paso | Componente | Archivos clave | Resumen sección |
|---|---|---|---|
| 1 | Scaffold | `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `.env.example` | "Archivos generados en paso 1" |
| 2 | Config + Logging | `config.py`, `logging_setup.py` | "paso 2" |
| 3 | Alembic + schema | `alembic/`, `alembic/versions/0001_init.py` | "paso 3" |
| 4 | Domain | `domain/auth/`, `domain/ingest/`, `domain/station/` | "paso 4" |
| 5.1 | Infra DB | `infrastructure/db/` | "paso 5.1" |
| 5.2 | Infra Persistence | `infrastructure/persistence/` | "paso 5.2" |
| 5.3 | Infra Publisher | `infrastructure/publisher/` | "paso 5.3" |
| 6 | Application | `application/auth/`, `application/use_cases/` | "paso 6" |
| 7 | Presentation | `presentation/api/`, `main.py` | "paso 7" |
| 8 | CLI | `cli.py` | "paso 8" |
| 9 | Tests unit | `tests/unit/test_envelope_validator.py` (y los 3 anteriores) | "paso 9" |
| 10 | Tests integration | **diferido a 9.3** (estrategia: regresiones puntuales en lugar de suite anticipada) | — |
| 11 | Smoke compose | **pendiente** | — |

Estado: **9 de 11 pasos cerrados**. 10 y 11 son trabajo de cierre/blindaje, no funcionalidad nueva. La función core del server está completa y validada end-to-end con `smoke E2E HTTP 12/12 verdes`.

---

# Anexos

## Anexo 1 — Endpoints HTTP

| Método | Path | Auth | Para qué |
|---|---|---|---|
| GET | `/api/v1/healthz` | público | Liveness probe sin DB |
| GET | `/api/v1/serverinfo` | público | Version, build, schemas soportados |
| GET | `/api/v1/admin/healthz` | bearer | DB connectivity + row counts |
| POST | `/api/v1/ingest/observation` | HMAC en payload | Ingest de observaciones empíricas |
| POST | `/api/v1/ingest/comment` | — | **501 stub** hasta 9.2 |
| GET | `/docs` | público | OpenAPI interactivo (Swagger) |

## Anexo 2 — Códigos de error que devuelve el server

| HTTP | error_class | Cuándo | Acción del edge |
|---|---|---|---|
| 200 | — | success o duplicate | continuar |
| 400 | SCHEMA | envelope malformado / payload tipa mal | DLQ permanente, no retry |
| 401 | AUTH | HMAC bad / station unknown / credential bad | latch, requiere intervención |
| 422 | CLOCK | sent_at_utc fuera de ±1800s | recalibrar con X-Server-Time y reintentar |
| 500 | SERVER | error interno del server | retry con backoff exponencial |
| 501 | SCHEMA | endpoint no implementado (ej: comment en 9.1) | DLQ permanente |

## Anexo 3 — Variables de entorno

| Variable | Default | Notas |
|---|---|---|
| `YVY_ENV` | `local` | `local` \| `staging` \| `production` |
| `YVY_DB_DSN` | (sin default) | Debe empezar con `postgresql+asyncpg://` |
| `YVY_ADMIN_TOKEN` | (sin default) | Strict (≥32 chars) en staging/production |
| `YVY_LOG_LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` |
| `YVY_DB_POOL_SIZE` | `5` | |
| `YVY_DB_MAX_OVERFLOW` | `10` | |
| `YVY_SUPPORTED_SCHEMAS` | `1.0` | CSV |
| `YVY_CLOCK_SKEW_MAX_SECONDS` | `1800` | 30 minutos |

## Anexo 4 — Glosario rápido

- **IEA**: Instituto de Enseñanza Agropecuaria. Las escuelas que operan las stations.
- **Edge**: la netbook de cada IEA que corre la app YVY + persiste local + sincroniza.
- **Envelope**: el JSON que el edge POSTea al server, incluyendo signature HMAC.
- **Canonical JSON**: representación byte-exact del envelope (sin signature) sobre la que se computa el HMAC.
- **DLQ** (Dead Letter Queue): tabla `bronze.ingest_failures` donde van los envelopes que pasaron HMAC pero fallaron en payload typing.
- **Bronze**: capa de data warehouse "raw" (cruda, sin transformar). En contraste con silver (limpio) y gold (agregado), que llegan en fases futuras.
- **Track A vs Track B**: A es el pipeline directo postgres (este 9.1). B es Kafka + consumers (trabajo paralelo del profesor especialista).

---

**Fin de la guía.** Para detalles técnicos exhaustivos (decisiones numeradas 1-94, archivo por archivo, validaciones E2E completas), consultar `yvy_FASE91_RESUMEN.md` en la misma carpeta `docs/`.

Si encuentran un bug o tienen dudas sobre alguna decisión, lo primero es buscar en el resumen por número de decisión. Si no está documentado, es momento de preguntar.
