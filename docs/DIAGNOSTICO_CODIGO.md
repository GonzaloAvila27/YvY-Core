# YVY Server · Diagnóstico exhaustivo del código

**Versión del código:** Fase 9.1 · commit inicial
**Fecha del diagnóstico:** 28-May-2026
**Propósito:** referencia técnica completa — qué hace cada función, de qué depende, cómo fluye una request de punta a punta.

---

## Índice

1. [Arquitectura general](#1-arquitectura-general)
2. [Árbol de módulos](#2-árbol-de-módulos)
3. [Capa Domain](#3-capa-domain)
4. [Capa Application](#4-capa-application)
5. [Capa Infrastructure](#5-capa-infrastructure)
6. [Capa Presentation](#6-capa-presentation)
7. [Composition root — main.py](#7-composition-root--mainpy)
8. [CLI](#8-cli)
9. [Configuración — config.py](#9-configuración--configpy)
10. [Logging — logging_setup.py](#10-logging--logging_setuppy)
11. [Base de datos — schema completo](#11-base-de-datos--schema-completo)
12. [Flujo completo de una request POST /ingest/observation](#12-flujo-completo-de-una-request-post-ingestobservation)
13. [Mapa de dependencias entre módulos](#13-mapa-de-dependencias-entre-módulos)
14. [Tabla completa de funciones y clases](#14-tabla-completa-de-funciones-y-clases)
15. [Reglas de negocio embebidas en el código](#15-reglas-de-negocio-embebidas-en-el-código)
16. [Superficie HTTP](#16-superficie-http)

---

## 1. Arquitectura general

El server sigue **arquitectura hexagonal** estricta. La dirección de dependencias es unidireccional hacia el centro:

```
┌─────────────────────────────────────────────────────────────┐
│  PRESENTATION  (FastAPI, Pydantic schemas, middleware)       │
│  INFRASTRUCTURE  (SQLAlchemy ORM, repos, publisher)          │
│            │                │                                │
│            └────────────────┘                                │
│                     │                                        │
│              APPLICATION  (use cases, validator)             │
│                     │                                        │
│               DOMAIN  (VOs, Protocol, canonical JSON)        │
│               ↑ NADIE IMPORTA DESDE DOMAIN HACIA AFUERA      │
└─────────────────────────────────────────────────────────────┘
```

**Invariante clave**: `domain/` solo usa stdlib Python. Ni SQLAlchemy, ni FastAPI, ni Pydantic aparecen en esa capa.

**Contexto del proyecto**: YVY es una red de estaciones meteorológicas en IEAs (escuelas agrotécnicas) de Misiones, Argentina. Este server es la capa Bronze de ingesta — recibe envelopes HMAC-SHA256 firmados desde dispositivos edge (netbooks en las IEAs), los valida y los persiste en PostgreSQL.

---

## 2. Árbol de módulos

```
src/yvy_server/
│
├── __init__.py              ← exports: __version__, __git_commit_short__, __build_date__
├── config.py                ← Settings (pydantic-settings, prefijo YVY_)
├── logging_setup.py         ← JSON structured logging
├── main.py                  ← composition root + FastAPI app factory
│
├── cli/
│   ├── __init__.py
│   └── cli.py               ← Typer CLI: create-station, version
│
├── domain/                  ← stdlib-only, cero dependencias externas
│   ├── auth/
│   │   ├── __init__.py      ← re-exports: canonicalize + toda la jerarquía de excepciones
│   │   ├── canonical_json.py ← función canonicalize() — fuente de verdad del spec
│   │   └── exceptions.py    ← EnvelopeError y 6 subclases
│   ├── ingest/
│   │   ├── __init__.py      ← re-exports: EntityType, IngestEvent, TOPIC_NAMES, Protocol
│   │   ├── events.py        ← IngestEvent (dataclass), EntityType (StrEnum), TOPIC_NAMES
│   │   └── publisher.py     ← IngestEventPublisher (Protocol), PublishResult (dataclass)
│   └── station/
│       ├── __init__.py      ← re-exports: Station, StationCredential
│       ├── station.py       ← Station (dataclass frozen)
│       └── credential.py    ← StationCredential (dataclass frozen + props is_active, key_bytes)
│
├── application/
│   ├── auth/
│   │   ├── __init__.py      ← re-exports: EnvelopeValidator
│   │   ├── dependencies.py  ← FastAPI Depends functions (lee app.state)
│   │   └── envelope_validator.py ← ValidatedEnvelope, validate_shape(), verify_hmac(),
│   │                              check_clock_skew(), EnvelopeValidator
│   └── use_cases/
│       ├── __init__.py      ← re-exports: IngestObservationUseCase, IngestSuccess, IngestFailure
│       └── ingest_observation.py ← IngestSuccess, IngestFailure, IngestObservationUseCase
│
├── infrastructure/
│   ├── db/
│   │   ├── __init__.py      ← re-exports: engine, session factory, todos los ORM models
│   │   ├── engine.py        ← create_engine_and_session_factory(), dispose_engine(), get_session()
│   │   └── models.py        ← Base, 7 ORM models, _BronzeRawMixin
│   ├── persistence/
│   │   ├── __init__.py      ← re-exports: los 5 repositories
│   │   ├── station_repository.py      ← StationRepository
│   │   ├── credential_repository.py   ← CredentialRepository
│   │   ├── bronze_repository.py       ← BronzeRepository
│   │   ├── ingest_log_repository.py   ← IngestLogRepository
│   │   └── ingest_failures_repository.py ← IngestFailuresRepository
│   └── publisher/
│       ├── __init__.py      ← re-exports: PostgresDirectPublisher
│       ├── publisher.py     ← re-exports desde domain/ingest/publisher.py
│       └── postgres_direct.py ← PostgresDirectPublisher (Track A)
│
└── presentation/
    └── api/
        ├── __init__.py      ← re-exports: ServerHeadersMiddleware
        ├── dependencies.py  ← re-exports desde application/auth/dependencies.py
        ├── middleware.py    ← ServerHeadersMiddleware
        ├── schemas.py       ← modelos Pydantic: payloads + responses
        └── routers/
            ├── __init__.py
            ├── health.py    ← GET /healthz, GET /serverinfo
            ├── admin.py     ← GET /admin/healthz (bearer token)
            └── ingest.py    ← POST /ingest/observation, POST /ingest/comment (501)
```

---

## 3. Capa Domain

> Todo en `src/yvy_server/domain/`. Solo usa Python stdlib. Sin dependencias externas.

### 3.1 `domain/auth/canonical_json.py`

**Propósito**: fuente de verdad del Canonical JSON v1.0 — la serialización byte-exacta usada para firmar con HMAC-SHA256.

```
CANONICAL_JSON_SPEC_VERSION = "1.0"   ← constante de versión del spec
```

#### `canonicalize(obj: Any) -> bytes`

| | |
|---|---|
| **Entrada** | Cualquier objeto JSON-serializable (típicamente el envelope dict sin el campo `signature`) |
| **Salida** | Bytes UTF-8 del JSON canónico |
| **Algoritmo** | `json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False, allow_nan=False).encode("utf-8")` |
| **Raises** | `ValueError` si contiene NaN/Infinity; `TypeError` si contiene tipos no serializables |
| **Invariante** | Mismo input → siempre mismo output (determinístico). Es lo que se firma con HMAC. |
| **Depende de** | Solo `json` (stdlib) |
| **Usado por** | `envelope_validator.verify_hmac()`, tests unit golden (29 casos) |

**Regla crítica**: este archivo no puede cambiar sin bumpear `CANONICAL_JSON_SPEC_VERSION` y actualizar edge + tests en lockstep. Un cambio rompería todas las firmas históricas.

---

### 3.2 `domain/auth/exceptions.py`

Jerarquía de excepciones para errores de validación de envelopes. Todas heredan de `EnvelopeError`.

**Atributos de instancia comunes** (en `EnvelopeError.__init__`):
- `station_uuid: UUID | None` — lo que se sabía al momento del fallo
- `event_id: UUID | None`
- `skew_seconds: int | None`

**Atributos de clase** (declarados en cada subclase):
- `error_class: ClassVar[str]` — código que el edge usa para decidir comportamiento
- `http_status: ClassVar[int]` — código HTTP a retornar

| Clase | `error_class` | `http_status` | Cuándo se lanza |
|---|---|---|---|
| `EnvelopeShapeError` | `"SCHEMA"` | `400` | Campos faltantes, schema_version no soportado. **Antes** de HMAC. |
| `StationNotFoundError` | `"AUTH"` | `401` | `station_uuid` no existe en la DB |
| `CredentialNotFoundError` | `"AUTH"` | `401` | No hay credencial activa para `(station_uuid, key_version)` |
| `HmacMismatchError` | `"AUTH"` | `401` | Firma HMAC no coincide |
| `ClockSkewError` | `"CLOCK"` | `422` | `sent_at_utc` demasiado lejos del reloj del server (> max_skew) |
| `PayloadSchemaError` | `"SCHEMA"` | `400` | Payload no pasa validación Pydantic **después** de HMAC. Va al DLQ. |

**Comportamiento del edge según `error_class`**:
- `SCHEMA` → DLQ inmediato (no reintentar)
- `AUTH` → latch (deshabilitar envíos hasta fix manual)
- `CLOCK` → recalibrar con `X-Server-Time` header y reintentar
- `SERVER` → backoff exponencial y reintentar

---

### 3.3 `domain/ingest/events.py`

#### `class EntityType(StrEnum)`

```python
SENSOR_READING        = "sensor_reading"
EMPIRICAL_OBSERVATION = "empirical_observation"
EMPIRICAL_EVENT       = "empirical_event"
COMMENT               = "comment"
```

Mapeo al endpoint URL: `/api/v1/ingest/<value>`.

#### `TOPIC_NAMES: dict[EntityType, str]`

```python
{
  EntityType.SENSOR_READING:        "yvy.sensor.raw",
  EntityType.EMPIRICAL_OBSERVATION: "yvy.empirical.observation.raw",
  EntityType.EMPIRICAL_EVENT:       "yvy.empirical.event.raw",
  EntityType.COMMENT:               "yvy.comment.raw",
}
```

Nombres de topics Kafka para Track B. Track A (Postgres) los guarda en `PublishResult.topic` por consistencia aunque no los usa realmente.

#### `@dataclass(frozen=True, slots=True) class IngestEvent`

El objeto central que fluye a través del pipeline. Inmutable una vez construido.

| Campo | Tipo | Origen |
|---|---|---|
| `event_id` | `UUID` | `envelope["event_id"]` (coercionado) |
| `station_uuid` | `UUID` | `envelope["station_uuid"]` |
| `entity_type` | `EntityType` | Inyectado por el use case (no viene en el envelope) |
| `schema_version` | `str` | `envelope["schema_version"]` |
| `payload` | `dict[str, Any]` | `envelope["payload"]` (opaco en el dominio) |
| `sent_at_utc` | `datetime` | `_parse_iso_utc(envelope["sent_at_utc"])` |
| `received_at_utc` | `datetime` | Timestamp del handler (decisión 8) |
| `received_from_ip` | `str | None` | `request.client.host` |
| `key_version_used` | `int` | `envelope["key_version"]` |
| `producer_version` | `str` | `yvy_server.__version__` |

**Property**:
- `topic -> str`: retorna `TOPIC_NAMES[self.entity_type]`

**Classmethod**:
- `from_envelope(envelope, *, entity_type, received_at_utc, received_from_ip, producer_version) -> IngestEvent`: construye desde el dict del envelope ya verificado. Pre-condición: HMAC y clock skew ya pasados.

---

### 3.4 `domain/ingest/publisher.py`

Define el **puerto** (interface) que cualquier publisher debe implementar.

#### `@dataclass(frozen=True, slots=True) class PublishResult`

| Campo | Tipo | Descripción |
|---|---|---|
| `persisted` | `bool` | `True` si la persistencia fue confirmada |
| `transport` | `str` | `"postgres-direct"` / `"kafka"` / `"dual"` |
| `topic` | `str` | Nombre lógico del topic |
| `was_duplicate` | `bool` | `True` si el `event_id` ya existía (UPSERT no insertó) |

#### `class IngestEventPublisher(Protocol)`

```python
async def publish(self, event: IngestEvent) -> PublishResult: ...
```

Protocol de duck typing — cualquier clase con este método satisface la interface, sin herencia explícita. Permite swap transparente entre `PostgresDirectPublisher` (Track A) y `KafkaPublisher` (Track B futuro).

---

### 3.5 `domain/station/station.py`

#### `@dataclass(frozen=True, slots=True) class Station`

| Campo | Tipo |
|---|---|
| `station_uuid` | `UUID` |
| `name` | `str` |
| `description` | `str | None` |
| `location_lat` | `float | None` |
| `location_lon` | `float | None` |
| `location_name` | `str | None` |
| `created_at` | `datetime | None` |
| `deactivated_at` | `datetime | None` |

VO (Value Object) puro. Sin métodos de negocio en Fase 9.1.

---

### 3.6 `domain/station/credential.py`

#### `@dataclass(frozen=True, slots=True) class StationCredential`

| Campo | Tipo |
|---|---|
| `credential_id` | `int | None` |
| `station_uuid` | `UUID` |
| `key_version` | `int` |
| `key_hmac_hex` | `str` (64 chars hex) |
| `created_at` | `datetime | None` |
| `deactivated_at` | `datetime | None` |
| `reason` | `str | None` |

**Properties**:
- `is_active -> bool`: retorna `self.deactivated_at is None`
- `key_bytes -> bytes`: retorna `bytes.fromhex(self.key_hmac_hex)` — los bytes listos para HMAC

---

## 4. Capa Application

> Orquesta el dominio. No toca HTTP ni SQLAlchemy directamente.

### 4.1 `application/auth/envelope_validator.py`

#### `@dataclass(frozen=True, slots=True) class ValidatedEnvelope`

Resultado exitoso de la validación. Lleva todo lo resuelto para que el use case no repita queries.

| Campo | Tipo | Contenido |
|---|---|---|
| `envelope` | `dict[str, Any]` | El dict original del request body |
| `station` | `Station` | VO del dominio resuelto desde DB |
| `credential` | `StationCredential` | VO del dominio resuelto desde DB |
| `skew_seconds` | `int` | Desfase medido entre `sent_at_utc` y `received_at_utc` |
| `received_at_utc` | `datetime` | Timestamp de recepción pasado por el use case |

---

#### Funciones puras (sin DB, testeables en aislamiento)

##### `validate_shape(envelope, supported_schemas) -> None`

| | |
|---|---|
| **Propósito** | Check pre-HMAC: campos requeridos presentes, `schema_version` soportado |
| **Raises** | `EnvelopeShapeError` si falta algún campo o version no soportada |
| **Campos requeridos** | `schema_version`, `event_id`, `station_uuid`, `key_version`, `sent_at_utc`, `nonce`, `payload`, `signature` |
| **Depende de** | `domain.auth.exceptions.EnvelopeShapeError` |
| **NO hace** | NO verifica tipos de valores, NO toca la DB |

##### `verify_hmac(envelope, key_bytes, *, station_uuid=None, event_id=None) -> None`

| | |
|---|---|
| **Propósito** | Verificar firma HMAC-SHA256 |
| **Algoritmo** | 1. Quitar campo `signature` del dict → 2. `canonicalize()` → 3. `hmac.new(key_bytes, canonical, sha256).hexdigest()` → 4. `hmac.compare_digest(computed, declared)` (constant-time) |
| **Raises** | `HmacMismatchError` si no coincide |
| **Depende de** | `domain.auth.canonical_json.canonicalize`, `domain.auth.exceptions.HmacMismatchError`, `hashlib`, `hmac` (stdlib) |

##### `check_clock_skew(envelope, received_at_utc, max_seconds, *, station_uuid=None, event_id=None) -> int`

| | |
|---|---|
| **Propósito** | Verificar que `sent_at_utc` no esté demasiado lejos del reloj del server |
| **Retorna** | Skew medido en segundos (int) si pasa |
| **Raises** | `ClockSkewError(skew_seconds=N)` si excede `max_seconds` |
| **Llamada** | DESPUÉS de HMAC (confiamos en el timestamp una vez que la firma es válida) |

##### `_parse_iso_utc(value: str) -> datetime` (interno)

Parsea ISO 8601 con sufijo `Z` o `+00:00`. Retorna `datetime` timezone-aware UTC.

---

#### `class EnvelopeValidator`

**Constructor** (todos kwargs):
```python
EnvelopeValidator(
    station_repo: StationRepository,
    credential_repo: CredentialRepository,
    supported_schemas: frozenset[str],
    max_skew_seconds: int,
)
```

Instanciado una vez en `main.py` al startup. Stateless (no guarda estado entre requests).

##### `async validate(session, envelope, received_at_utc) -> ValidatedEnvelope`

Pipeline completo en orden:
1. `validate_shape()` — pre-HMAC, no usa DB
2. Extraer `station_uuid`, `event_id`, `key_version` del envelope
3. `station_repo.get_by_uuid(session, station_uuid)` — buscar estación
4. `credential_repo.get_active_by_station_and_version(session, station_uuid, key_version)` — buscar credencial activa
5. `verify_hmac(envelope, credential.key_bytes)` — verificar firma
6. `check_clock_skew(envelope, received_at_utc, max_skew_seconds)` — verificar reloj
7. Retornar `ValidatedEnvelope`

Cualquier fallo en pasos 1-6 levanta la `EnvelopeError` correspondiente.

---

### 4.2 `application/auth/dependencies.py`

Funciones FastAPI `Depends` que leen `app.state`. **No contienen lógica de negocio** — solo acceso a estado de la app.

| Función | Retorna | Lee de |
|---|---|---|
| `get_settings_dep(request)` | `Settings` | `request.app.state.settings` |
| `get_session_factory_dep(request)` | `SessionFactory` | `request.app.state.session_factory` |
| `get_session_dep(factory)` | `AsyncIterator[AsyncSession]` | `factory()` — nueva sesión por request, yield, cierre automático |
| `get_observation_use_case(request)` | `IngestObservationUseCase` | `request.app.state.ingest_observation_use_case` |
| `verify_admin_token(settings, authorization)` | `None` | Header `Authorization: Bearer <token>` vs `settings.admin_token` |

`verify_admin_token` usa `secrets.compare_digest` (constant-time). Levanta `HTTPException(401)` si el header falta, tiene esquema incorrecto, o el token no coincide.

---

### 4.3 `application/use_cases/ingest_observation.py`

#### `@dataclass(frozen=True, slots=True) class IngestSuccess`

| Campo | Tipo |
|---|---|
| `event_id` | `UUID` |
| `was_duplicate` | `bool` |
| `received_at_utc` | `datetime` |

#### `@dataclass(frozen=True, slots=True) class IngestFailure`

| Campo | Tipo | Valores posibles de `error_class` |
|---|---|---|
| `http_status` | `int` | 400, 401, 422, 500 |
| `error_class` | `str` | `"SCHEMA"` / `"AUTH"` / `"CLOCK"` / `"SERVER"` |
| `error_message` | `str` | Mensaje humano |

`IngestOutcome = IngestSuccess | IngestFailure` (type alias para el union discriminado).

---

#### `class IngestObservationUseCase`

**Constantes de clase**:
- `ENDPOINT = "/api/v1/ingest/observation"`
- `ENTITY_TYPE = EntityType.EMPIRICAL_OBSERVATION`

**Constructor** (todos kwargs):
```python
IngestObservationUseCase(
    validator: EnvelopeValidator,
    publisher: IngestEventPublisher,
    log_repo: IngestLogRepository,
    failures_repo: IngestFailuresRepository,
    session_factory: SessionFactory,
    producer_version: str,
)
```

##### `async execute(*, envelope, received_at_utc, received_from_ip, user_agent, payload_validator=None) -> IngestOutcome`

**El use case NUNCA levanta excepciones**. Toda excepción esperada se traduce a `IngestFailure`. Excepciones inesperadas (ej: Postgres caído mid-publish) también se capturan y devuelven como `IngestFailure(500, "SERVER")`.

**Pipeline interno** (los 4 pasos):

```
1. VALIDAR ENVELOPE
   async with session_factory() as session:
       validated = await validator.validate(session, envelope, received_at_utc)
   └─ Si EnvelopeError: _log_attempt(failure) + return IngestFailure(err.http_status, err.error_class)

2. VALIDAR PAYLOAD (post-HMAC, opcional)
   payload_validator(envelope["payload"])
   └─ Si falla: failures_repo.record_failure() → ingest_failures (DLQ)
                _log_attempt(400, "SCHEMA")
                return IngestFailure(400, "SCHEMA")

3. PUBLICAR EVENTO
   event = IngestEvent.from_envelope(...)
   publish_result = await publisher.publish(event)
   └─ Si Exception: _log_attempt(500, "SERVER") + return IngestFailure(500, "SERVER")

4. LOGGEAR ÉXITO
   await _log_attempt(200, None)
   return IngestSuccess(event_id, was_duplicate, received_at_utc)
```

##### `async _log_attempt(...)` (interno)

Abre una sesión propia y llama a `log_repo.record_attempt()`. Todos los parámetros son kwargs.

---

## 5. Capa Infrastructure

> Adaptadores hacia la base de datos. Implementan los puertos definidos en el dominio.

### 5.1 `infrastructure/db/engine.py`

#### `SessionFactory = async_sessionmaker[AsyncSession]`

Type alias. Exportado desde `infrastructure/db/__init__.py` para que otros módulos no importen `async_sessionmaker` directamente.

#### `create_engine_and_session_factory(settings: Settings) -> tuple[AsyncEngine, SessionFactory]`

| | |
|---|---|
| **Propósito** | Crear el engine async y la session factory. Llamado una vez en el startup (lifespan). |
| **Config del engine** | `pool_size=settings.db_pool_size`, `max_overflow=settings.db_max_overflow`, `pool_pre_ping=False`, `future=True` |
| **Config de la factory** | `expire_on_commit=False` (para poder leer attrs post-commit), `autoflush=False`, `autocommit=False` |
| **Depende de** | `Settings.db_dsn`, `Settings.db_pool_size`, `Settings.db_max_overflow` |

#### `async dispose_engine(engine: AsyncEngine) -> None`

Cierra todas las conexiones del pool. Llamado en el shutdown del lifespan. Idempotente.

#### `async get_session(session_factory) -> AsyncIterator[AsyncSession]`

Generator que crea, yield, y cierra una `AsyncSession`. Usado internamente.

---

### 5.2 `infrastructure/db/models.py`

Modelos ORM SQLAlchemy 2.x con `Mapped[T]` y `mapped_column`.

**Naming convention** (para Alembic autogenerate y constraints predecibles):
```python
{
  "ix": "ix_%(table_name)s_%(column_0_N_name)s",
  "uq": "uq_%(table_name)s_%(column_0_N_name)s",
  "ck": "ck_%(table_name)s_%(constraint_name)s",
  "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
  "pk": "pk_%(table_name)s",
}
```

#### `class StationORM(Base)` → tabla `public.station`

| Columna | Tipo SQL | Notas |
|---|---|---|
| `station_uuid` | `UUID` PK | `as_uuid=True` — Python ve `UUID`, no string |
| `name` | `TEXT` NOT NULL | |
| `description` | `TEXT` nullable | |
| `location_lat` | `REAL` nullable | |
| `location_lon` | `REAL` nullable | |
| `location_name` | `TEXT` nullable | |
| `created_at` | `TIMESTAMPTZ` NOT NULL | `server_default=now()` |
| `deactivated_at` | `TIMESTAMPTZ` nullable | NULL = activa |

#### `class StationCredentialORM(Base)` → tabla `public.station_credentials`

| Columna | Tipo SQL | Notas |
|---|---|---|
| `credential_id` | `BIGINT` PK autoincrement | |
| `station_uuid` | `UUID` FK→station.station_uuid | NOT NULL |
| `key_version` | `INTEGER` NOT NULL | |
| `key_hmac_hex` | `TEXT` NOT NULL | 64 chars hex (32 bytes HMAC-SHA256) |
| `created_at` | `TIMESTAMPTZ` NOT NULL | server_default now() |
| `deactivated_at` | `TIMESTAMPTZ` nullable | NULL = activa |
| `reason` | `TEXT` nullable | Motivo de rotación/desactivación |

**Constraint**: `UNIQUE(station_uuid, key_version)` — nombrado `uq_station_credentials_station_uuid_key_version`.

#### `class _BronzeRawMixin` (mixin, no tabla directa)

Columnas compartidas por los 3 `*_raw` tables:

| Columna | Tipo SQL | Notas |
|---|---|---|
| `event_id` | `UUID` PK | Idempotency key — UPSERT ON CONFLICT |
| `station_uuid` | `UUID` FK→station | NOT NULL |
| `schema_version` | `TEXT` NOT NULL | |
| `payload` | `JSONB` NOT NULL | El payload del envelope |
| `received_at` | `TIMESTAMPTZ` NOT NULL | Timestamp del handler |
| `sent_at_utc` | `TIMESTAMPTZ` nullable | Del envelope |
| `key_version_used` | `INTEGER` NOT NULL | |
| `received_from_ip` | `INET` nullable | |
| `kafka_topic` | `TEXT` nullable | NULL durante Track A |
| `kafka_partition` | `INTEGER` nullable | NULL durante Track A |
| `kafka_offset` | `BIGINT` nullable | NULL durante Track A |
| `kafka_published_at` | `TIMESTAMPTZ` nullable | NULL durante Track A |

#### `class SensorRawORM(Base, _BronzeRawMixin)` → `bronze.sensor_raw`

Índices adicionales:
- `idx_sensor_raw_station_recv` — `(station_uuid, received_at DESC)`
- `idx_sensor_raw_payload_obs` — expression index sobre `(payload->>'observed_at_utc')`

#### `class EmpiricalObservationRawORM(Base, _BronzeRawMixin)` → `bronze.empirical_observation_raw`

Sin índices adicionales en Fase 9.1.

#### `class EmpiricalEventRawORM(Base, _BronzeRawMixin)` → `bronze.empirical_event_raw`

Sin índices adicionales en Fase 9.1.

#### `class IngestLogORM(Base)` → `bronze.ingest_log`

Audit log de TODO intento HTTP. Append-only.

| Columna | Tipo SQL | Notas |
|---|---|---|
| `id` | `BIGINT` PK autoincrement | |
| `received_at` | `TIMESTAMPTZ` NOT NULL | server_default now() |
| `station_uuid` | `UUID` nullable | NULL si AUTH falló antes de identificar estación |
| `endpoint` | `TEXT` NOT NULL | Ej: `"/api/v1/ingest/observation"` |
| `http_status` | `INTEGER` NOT NULL | |
| `failure_class` | `TEXT` nullable | NULL si éxito (200) |
| `event_id` | `UUID` nullable | |
| `skew_seconds` | `INTEGER` nullable | |
| `request_ip` | `INET` nullable | |
| `user_agent` | `TEXT` nullable | |

Índices:
- `idx_ingest_log_recv` — `(received_at DESC)`
- `idx_ingest_log_failures` — `(failure_class, received_at DESC) WHERE failure_class IS NOT NULL` (partial)

#### `class IngestFailureORM(Base)` → `bronze.ingest_failures`

DLQ del server. Solo recibe fallos post-HMAC (payload inválido, error de schema).

| Columna | Tipo SQL | Notas |
|---|---|---|
| `id` | `BIGINT` PK autoincrement | |
| `event_id` | `UUID` nullable | |
| `station_uuid` | `UUID` nullable | |
| `entity_type` | `TEXT` nullable | |
| `endpoint` | `TEXT` NOT NULL | |
| `failure_class` | `TEXT` NOT NULL | |
| `failure_reason` | `TEXT` NOT NULL | Mensaje de error Pydantic |
| `raw_envelope` | `JSONB` NOT NULL | Envelope completo para replay |
| `received_at` | `TIMESTAMPTZ` NOT NULL | |
| `received_from_ip` | `INET` nullable | |
| `resolved_at` | `TIMESTAMPTZ` nullable | NULL = pendiente |
| `resolved_action` | `TEXT` nullable | CHECK: `reprocessed`, `discarded`, `fixed_at_source` |
| `resolution_notes` | `TEXT` nullable | |

Índice: `idx_failures_unresolved` — `(failure_class, received_at DESC) WHERE resolved_at IS NULL` (partial)

---

### 5.3 Repositories

Todos son **stateless** — sin atributos de instancia que guarden estado entre requests. La `AsyncSession` se pasa por parámetro en cada método.

**Patrón de mapping ORM → VO**: cada repo tiene una función privada `_orm_to_xxx()` que construye el VO del dominio desde la fila ORM. Nada del ORM sale fuera del paquete `infrastructure/`.

#### `class StationRepository`

##### `async get_by_uuid(session, station_uuid: UUID) -> Station | None`

```sql
SELECT * FROM station WHERE station_uuid = :uuid LIMIT 1
```
Retorna `Station` VO o `None`. No filtra por `deactivated_at` — la lógica de estación activa queda en la app si fuera necesaria.

##### `async insert(session, station: Station) -> None`

`session.add(StationORM(...))`. El `created_at` usa el `server_default` de Postgres (no se pasa explícitamente).

#### `class CredentialRepository`

##### `async get_active_by_station_and_version(session, station_uuid, key_version) -> StationCredential | None`

```sql
SELECT * FROM station_credentials
WHERE station_uuid = :uuid
  AND key_version = :ver
  AND deactivated_at IS NULL
LIMIT 1
```
Retorna `StationCredential` VO o `None`. El filtro `deactivated_at IS NULL` define "activa".

##### `async insert(session, credential: StationCredential) -> None`

`session.add(StationCredentialORM(...))`. `credential_id` se ignora (BIGSERIAL). `created_at` usa server_default.

#### `class BronzeRepository`

##### `async upsert(session, event: IngestEvent) -> bool`

**Idempotente.** Usa PostgreSQL `INSERT ... ON CONFLICT (event_id) DO NOTHING RETURNING event_id`.

- Retorna `True` → fila insertada (event_id nuevo)
- Retorna `False` → ON CONFLICT activado (event_id duplicado)

Dispatch por `event.entity_type`:

| `EntityType` | ORM model destino |
|---|---|
| `SENSOR_READING` | `SensorRawORM` |
| `EMPIRICAL_OBSERVATION` | `EmpiricalObservationRawORM` |
| `EMPIRICAL_EVENT` | `EmpiricalEventRawORM` |
| `COMMENT` | `NotImplementedError` (stub 501) |

#### `class IngestLogRepository`

##### `async record_attempt(session, *, received_at, station_uuid, endpoint, http_status, failure_class, event_id, skew_seconds, request_ip, user_agent) -> None`

Append-only insert a `bronze.ingest_log`. Todos los parámetros son kwargs. `session.add(IngestLogORM(...))`.

#### `class IngestFailuresRepository`

##### `async record_failure(session, *, event_id, station_uuid, entity_type, endpoint, failure_class, failure_reason, raw_envelope, received_at, received_from_ip) -> None`

Append-only insert a `bronze.ingest_failures`. `session.add(IngestFailureORM(...))`. Guarda el `raw_envelope` completo como JSONB para permitir replay.

---

### 5.4 `infrastructure/publisher/postgres_direct.py`

#### `class PostgresDirectPublisher`

**Constante de clase**: `TRANSPORT_NAME = "postgres-direct"`

**Constructor**:
```python
PostgresDirectPublisher(
    session_factory: SessionFactory,
    bronze_repo: BronzeRepository,
)
```

Implementa `IngestEventPublisher` Protocol por **structural typing** (sin herencia explícita).

##### `async publish(event: IngestEvent) -> PublishResult`

```python
async with self._session_factory() as session, session.begin():
    inserted = await self._bronze_repo.upsert(session, event)

return PublishResult(
    persisted=True,
    transport="postgres-direct",
    topic=event.topic,
    was_duplicate=not inserted,
)
```

Abre su **propia transacción** (independiente de la transacción del use case para `ingest_log`). Si `upsert` falla, la excepción se propaga al use case que la captura como `SERVER` error.

---

## 6. Capa Presentation

### 6.1 `presentation/api/schemas.py`

Modelos Pydantic. El envelope en sí **NO** se valida con Pydantic pre-HMAC (rompería la firma).

#### `class EmpiricalObservationPayload(BaseModel)` — payload del POST /ingest/observation

`model_config = ConfigDict(extra="allow")` — campos desconocidos se aceptan (forward compatibility).

Campos requeridos: `observed_at_utc: datetime`, `observer_id: str (1-100 chars)`

Campos opcionales (todos): `rain_mm`, `temp_c_out`, `hum_pct_perceived`, `wind_beaufort`, `wind_dir_8pt`, `cloud_cover_pct`, `cloud_type`, `soil_state`, `visibility`, `precipitation_now`, `phenology_notes`, `cattle_state`, `cattle_notes`, `general_notes`

#### Response models

| Clase | Endpoint | Campos clave |
|---|---|---|
| `HealthzResponse` | `GET /healthz` | `status: Literal["ok"]` |
| `ServerInfoResponse` | `GET /serverinfo` | `version`, `git_commit_short`, `build_date`, `server_time_utc`, `schema_versions_supported` |
| `AdminHealthzResponse` | `GET /admin/healthz` | `status`, `db_responsive: bool`, `counts: dict[str, int]` |
| `IngestSuccessResponse` | `POST /ingest/observation` 200 | `ingest_result: Literal["inserted","duplicate"]`, `event_id`, `received_at_utc` |
| `IngestErrorResponse` | `POST /ingest/observation` 4xx/5xx | `error: str`, `error_class: Literal["SCHEMA","AUTH","CLOCK","SERVER"]` |

---

### 6.2 `presentation/api/middleware.py`

#### `class ServerHeadersMiddleware(BaseHTTPMiddleware)`

Se ejecuta en TODA response (incluyendo errores).

##### `async dispatch(request, call_next) -> Response`

Agrega 3 headers:
1. `X-Server-Time` → ISO 8601 UTC con precisión de milisegundos y sufijo `Z` (ej: `2026-05-28T18:00:00.123Z`)
2. `X-Schema-Versions-Supported` → CSV de schemas (ej: `"1.0"`)
3. `X-Server-Version` → `yvy_server.__version__`

`X-Schema-Versions-Supported` se lee desde `request.app.state.settings`. Si `state.settings` no existe (raro), se omite silenciosamente sin crashear.

El edge usa `X-Server-Time` para recalibrar su reloj cuando recibe un error `CLOCK`.

---

### 6.3 `presentation/api/routers/health.py`

Endpoints públicos, sin autenticación.

#### `GET /api/v1/healthz` → `HealthzResponse`

Retorna `{"status": "ok"}`. **No toca la DB.** Es el endpoint de liveness para load balancers y systemd.

`Depends`: ninguno.

#### `GET /api/v1/serverinfo` → `ServerInfoResponse`

Retorna version, git commit, build date, server time, schemas soportados.

`Depends`: `get_settings_dep` (para leer `supported_schemas`).

Importa: `__version__`, `__git_commit_short__`, `__build_date__` desde `yvy_server.__init__` (o fallback `"unknown"` si `_version.py` no fue generado).

---

### 6.4 `presentation/api/routers/admin.py`

#### `GET /api/v1/admin/healthz` → `AdminHealthzResponse`

Bearer token requerido a nivel de router (`dependencies=[Depends(verify_admin_token)]`).

Ejecuta `COUNT(*)` sobre 6 tablas:
- `public.station`
- `bronze.sensor_raw`
- `bronze.empirical_observation_raw`
- `bronze.empirical_event_raw`
- `bronze.ingest_log`
- `bronze.ingest_failures`

Si la DB no responde, `db_responsive=False`, `counts={}` (no crashea el endpoint).

`Depends`: `get_session_dep` (para las queries), `verify_admin_token` (a nivel de router).

---

### 6.5 `presentation/api/routers/ingest.py`

#### `POST /api/v1/ingest/observation`

El handler más importante del sistema.

**Flujo del handler** (thin — toda lógica en el use case):
1. Captura `received_at_utc = datetime.now(UTC)` (una sola vez — decisión 8)
2. Lee raw bytes con `await request.body()`
3. `json.loads(raw_bytes)` → si falla: `400 SCHEMA` inmediato (sin llegar al use case)
4. Verifica que sea un dict → si no: `400 SCHEMA` inmediato
5. Define `_validate_payload` como closure que corre `EmpiricalObservationPayload.model_validate(payload)`
6. `await use_case.execute(envelope=..., received_at_utc=..., received_from_ip=..., user_agent=..., payload_validator=_validate_payload)`
7. Pattern-match sobre `IngestSuccess` / `IngestFailure` → HTTP response

`Depends`: `get_observation_use_case` (inyecta el use case).

Responses documentadas: `200`, `400 SCHEMA`, `401 AUTH`, `422 CLOCK`, `500 SERVER`.

#### `POST /api/v1/ingest/comment`

**Stub 501**. Retorna `IngestErrorResponse(error_class="SCHEMA")` para que el edge lo trate como DLQ. Se implementa en Fase 9.2.

---

## 7. Composition root — `main.py`

El único lugar donde todo se conecta. Se ejecuta en el `lifespan` async de FastAPI (startup + shutdown).

### `@asynccontextmanager async def lifespan(app: FastAPI)`

**Orden de construcción al startup**:

```
1. get_settings()                              → Settings (lru_cache)
2. setup_logging(settings.log_level)           → configura JSON logging
3. create_engine_and_session_factory(settings) → (AsyncEngine, SessionFactory)
4. StationRepository()                         → stateless
   CredentialRepository()                      → stateless
   BronzeRepository()                          → stateless
   IngestLogRepository()                       → stateless
   IngestFailuresRepository()                  → stateless
5. EnvelopeValidator(                          → combina repos + config
       station_repo, credential_repo,
       supported_schemas, max_skew_seconds)
6. PostgresDirectPublisher(                    → Track A
       session_factory, bronze_repo)
7. IngestObservationUseCase(                   → combina todo
       validator, publisher, log_repo,
       failures_repo, session_factory,
       producer_version=__version__)
8. app.state.settings = settings
   app.state.engine = engine
   app.state.session_factory = session_factory
   app.state.ingest_observation_use_case = use_case
```

**Al shutdown**: `dispose_engine(engine)` — cierra el pool de conexiones.

### `def create_app() -> FastAPI`

Construye la instancia de FastAPI:
- Agrega `ServerHeadersMiddleware`
- Registra routers: `health.router`, `admin.router`, `ingest.router`
- `redoc_url=None` (solo `/docs` habilitado en dev)

### `app = create_app()`

Instancia a nivel de módulo para `uvicorn yvy_server.main:app`.

---

## 8. CLI

### `src/yvy_server/cli/cli.py`

Entry point: `yvy-server` (definido en `pyproject.toml` → `yvy_server.cli.cli:app`).

Typer sync con `asyncio.run()` interno para operaciones de DB.

#### `@app.command("create-station") def create_station(...)`

**Argumentos CLI**:

| Arg | Flag | Requerido | Tipo |
|---|---|---|---|
| name | `--name / -n` | Sí | `str` |
| description | `--description` | No | `str | None` |
| location_lat | `--location-lat` | No | `float | None` |
| location_lon | `--location-lon` | No | `float | None` |
| location_name | `--location-name` | No | `str | None` |
| output_file | `--output-file / -o` | No | `Path | None` |

**Flujo**:
1. `get_settings()` + `setup_logging(stream=sys.stderr)` (logs van a stderr, stdout libre para JSON)
2. Genera `station_uuid = uuid4()`, `key_bytes = secrets.token_bytes(32)`, `key_hmac_hex = key_bytes.hex()`
3. `asyncio.run(_persist_station(...))` → abre engine, inserta `Station` + flush + inserta `StationCredential` en una transacción
4. Imprime JSON a stdout: `{"station_uuid", "key_version", "key_hmac_hex", "name", "created_at_utc"}`
5. Si `--output-file`: escribe el mismo JSON al archivo
6. `log.info("station_created", extra={...})` → va a stderr

**Transacción interna** (`_persist_station`):
```python
session.add(StationORM)
await session.flush()     ← forzar INSERT antes del FK en station_credentials
session.add(StationCredentialORM)
```

#### `@app.command("version") def version()`

Imprime `yvy_server.__version__` y sale.

---

## 9. Configuración — `config.py`

### `class Settings(BaseSettings)`

Prefijo de env vars: `YVY_`. Lee `.env` del directorio de trabajo (opcionalmente). `extra="ignore"` para tolerar vars de otros servicios en el mismo `.env`.

| Var de entorno | Campo | Default | Tipo | Validación |
|---|---|---|---|---|
| `YVY_ENV` | `env` | `"local"` | `Literal["local","staging","production"]` | |
| `YVY_LOG_LEVEL` | `log_level` | `"INFO"` | `Literal["DEBUG","INFO","WARNING","ERROR","CRITICAL"]` | |
| `YVY_SUPPORTED_SCHEMAS` | `supported_schemas` | `frozenset({"1.0"})` | CSV → `frozenset[str]` | No vacío |
| `YVY_CLOCK_SKEW_MAX_SECONDS` | `clock_skew_max_seconds` | `1800` | `int >= 1` | warn < max |
| `YVY_CLOCK_SKEW_WARN_SECONDS` | `clock_skew_warn_seconds` | `300` | `int >= 1` | warn < max |
| `YVY_DB_DSN` | `db_dsn` | `"postgresql+asyncpg://yvy:yvy@localhost:5432/yvy"` | `str` | Debe empezar con `postgresql+asyncpg://` |
| `YVY_DB_POOL_SIZE` | `db_pool_size` | `10` | `int >= 1` | |
| `YVY_DB_MAX_OVERFLOW` | `db_max_overflow` | `5` | `int >= 0` | |
| `YVY_ADMIN_TOKEN` | `admin_token` | `"CHANGE_ME_..."` | `SecretStr` | Si env != local: >= 32 chars y sin "CHANGE_ME" |
| `YVY_HOST` | `host` | `"0.0.0.0"` | `str` | |
| `YVY_PORT` | `port` | `8000` | `int 1-65535` | |

**Validaciones de modelo** (`@model_validator`):
- `clock_skew_warn_seconds < clock_skew_max_seconds` — si no, `ValueError`
- En `env != "local"`: `admin_token` debe tener >= 32 chars y no contener "CHANGE_ME"

### `@lru_cache(maxsize=1) def get_settings() -> Settings`

Singleton: lee env vars una sola vez. Para forzar re-lectura en tests: `get_settings.cache_clear()`.

---

## 10. Logging — `logging_setup.py`

### `class StructuredJsonFormatter(logging.Formatter)`

Formatea cada log record como una línea JSON. Campos fijos: `ts`, `level`, `logger`, `msg`. Campos adicionales: cualquier key en `record.extra` (con restricción de keys reservadas).

**Encoders custom** para serializar tipos especiales a JSON:
- `UUID` → `str`
- `datetime` → ISO 8601 string
- `date` → ISO 8601 date string
- `bytes` → UTF-8 decode (con `errors="replace"`)
- `set` / `frozenset` → lista ordenada

### `def setup_logging(level: str = "INFO", stream=None) -> None`

Configura el root logger con `StructuredJsonFormatter`. `stream` permite redirigir a `sys.stderr` (usado por el CLI para mantener stdout limpio).

**Idempotente**: si el handler ya está configurado, no duplica. Silencia librerías ruidosas (`sqlalchemy.engine`, `uvicorn.access`, `alembic`) a `WARNING`.

**Keys reservadas en `extra={}`** que NO se deben usar (son atributos de `LogRecord`):
`name`, `msg`, `args`, `levelname`, `levelno`, `module`, `message`, `pathname`, `filename`, `lineno`, `funcName`, `created`, `thread`, `threadName`, `process`, `processName`, `asctime`, `relativeCreated`, `exc_info`, `exc_text`, `stack_info`

---

## 11. Base de datos — schema completo

### Schemas PostgreSQL

| Schema | Propósito |
|---|---|
| `public` | Configuración operativa (estaciones y credenciales) |
| `bronze` | Datos crudos de ingesta — nunca se modifican |

### Tablas y relaciones

```
public.station (PK: station_uuid)
  │
  ├─── public.station_credentials (FK: station_uuid)
  │
  ├─── bronze.sensor_raw (FK: station_uuid)
  ├─── bronze.empirical_observation_raw (FK: station_uuid)
  ├─── bronze.empirical_event_raw (FK: station_uuid)
  │
  bronze.ingest_log (station_uuid nullable — FK laxo, no constraint)
  bronze.ingest_failures (station_uuid nullable)
```

### Índices relevantes para queries operativas

| Índice | Tabla | Columnas | Uso |
|---|---|---|---|
| PK | `station` | `station_uuid` | lookup por UUID |
| PK | `station_credentials` | `credential_id` | |
| `uq_station_credentials_...` | `station_credentials` | `(station_uuid, key_version)` | UNIQUE — un solo key_version activo por estación |
| PK | `sensor_raw` | `event_id` | idempotency |
| `idx_sensor_raw_station_recv` | `sensor_raw` | `(station_uuid, received_at DESC)` | queries por estación en rango de tiempo |
| `idx_sensor_raw_payload_obs` | `sensor_raw` | `(payload->>'observed_at_utc')` | queries por timestamp de observación |
| PK | `empirical_observation_raw` | `event_id` | idempotency |
| PK | `empirical_event_raw` | `event_id` | idempotency |
| `idx_ingest_log_recv` | `ingest_log` | `received_at DESC` | queries temporales |
| `idx_ingest_log_failures` | `ingest_log` | `(failure_class, received_at DESC) WHERE failure_class IS NOT NULL` | dashboard de errores |
| `idx_failures_unresolved` | `ingest_failures` | `(failure_class, received_at DESC) WHERE resolved_at IS NULL` | DLQ pendientes |

### Migración `alembic/versions/001_init.py`

Única migración en Fase 9.1. Crea los 2 schemas (`bronze`), las 7 tablas, todos los índices, y el check constraint de `resolved_action`. `downgrade()` hace `DROP TABLE` en orden inverso (respeta FKs).

---

## 12. Flujo completo de una request POST /ingest/observation

```
┌─── EDGE (IEA) ──────────────────────────────────────────────┐
│ 1. Construye envelope dict (sin "signature")                 │
│ 2. canonicalize(envelope) → bytes                            │
│ 3. HMAC-SHA256(key_bytes, canonical_bytes) → signature       │
│ 4. POST https://server/api/v1/ingest/observation             │
│    body = {envelope + signature}                             │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─── nginx (reverse proxy) ───────────────────────────────────┐
│ rate_limit: 60r/min burst=30                                 │
│ proxy_pass → http://yvy-api:8000                             │
│ agrega headers: X-Real-IP, X-Forwarded-For, X-Forwarded-Proto│
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─── ServerHeadersMiddleware ─────────────────────────────────┐
│ (ejecuta DESPUÉS de call_next — agrega headers a la response)│
│ + X-Server-Time, X-Schema-Versions-Supported, X-Server-Version│
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─── ingest_observation handler ──────────────────────────────┐
│ received_at_utc = datetime.now(UTC)  ← capturado UNA sola vez│
│ raw_bytes = await request.body()                             │
│ envelope = json.loads(raw_bytes)    ← si falla: 400 SCHEMA   │
│ _validate_payload = closure sobre EmpiricalObservationPayload│
│ outcome = await use_case.execute(...)                        │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─── IngestObservationUseCase.execute() ──────────────────────┐
│                                                              │
│ ┌── PASO 1: VALIDAR ENVELOPE ──────────────────────────────┐ │
│ │ async with session_factory() as session:                  │ │
│ │   validated = await validator.validate(session, envelope) │ │
│ │                                                           │ │
│ │   1a. validate_shape() — campos requeridos, schema_version│ │
│ │   1b. station_repo.get_by_uuid()      → Station VO       │ │
│ │   1c. credential_repo.get_active...() → Credential VO    │ │
│ │   1d. verify_hmac()    — canonicalize + HMAC-SHA256       │ │
│ │   1e. check_clock_skew() → skew_seconds                  │ │
│ │                                                           │ │
│ │   Si cualquiera falla → EnvelopeError                    │ │
│ └───────────────────────────────────────────────────────────┘ │
│         │ si EnvelopeError:                                  │
│         │  _log_attempt(ingest_log, failure)                 │
│         │  return IngestFailure(err.http_status, err.class)  │
│         │                                                     │
│ ┌── PASO 2: VALIDAR PAYLOAD (post-HMAC) ───────────────────┐ │
│ │ _validate_payload(envelope["payload"])                    │ │
│ │ = EmpiricalObservationPayload.model_validate(...)         │ │
│ └───────────────────────────────────────────────────────────┘ │
│         │ si Pydantic ValidationError:                       │
│         │  failures_repo.record_failure() → ingest_failures  │
│         │  _log_attempt(ingest_log, 400 SCHEMA)              │
│         │  return IngestFailure(400, "SCHEMA")                │
│         │                                                     │
│ ┌── PASO 3: PUBLICAR ──────────────────────────────────────┐ │
│ │ event = IngestEvent.from_envelope(...)                    │ │
│ │ publish_result = await publisher.publish(event)           │ │
│ │                                                           │ │
│ │   PostgresDirectPublisher.publish():                      │ │
│ │   async with session_factory() as s, s.begin():           │ │
│ │     inserted = await bronze_repo.upsert(s, event)         │ │
│ │     → INSERT INTO empirical_observation_raw               │ │
│ │         ON CONFLICT (event_id) DO NOTHING RETURNING ...   │ │
│ └───────────────────────────────────────────────────────────┘ │
│         │ si Exception:                                      │
│         │  _log_attempt(500, "SERVER")                       │
│         │  return IngestFailure(500, "SERVER")               │
│         │                                                     │
│ ┌── PASO 4: LOGGEAR ÉXITO ─────────────────────────────────┐ │
│ │ _log_attempt(ingest_log, 200, was_duplicate)              │ │
│ │ log.info("envelope persisted", ...)                       │ │
│ │ return IngestSuccess(event_id, was_duplicate, received_at)│ │
│ └───────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─── handler (mapeo outcome → HTTP) ──────────────────────────┐
│ IngestSuccess → 200 {"ingest_result": "inserted"|"duplicate"}│
│ IngestFailure → 4xx/5xx {"error": ..., "error_class": ...}  │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─── ServerHeadersMiddleware (agrega headers) ────────────────┐
│ X-Server-Time: 2026-05-28T18:00:00.123Z                     │
│ X-Schema-Versions-Supported: 1.0                            │
│ X-Server-Version: 0.9.1-dev+abc1234                         │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼ Response al edge
```

---

## 13. Mapa de dependencias entre módulos

**Quién importa a quién** (flechas = "importa de"):

```
presentation/api/routers/health.py
  → yvy_server (version constants)
  → presentation/api/dependencies (get_settings_dep)
  → presentation/api/schemas (HealthzResponse, ServerInfoResponse)

presentation/api/routers/admin.py
  → infrastructure/db (__init__: ORM models)
  → presentation/api/dependencies (get_session_dep, verify_admin_token)
  → presentation/api/schemas (AdminHealthzResponse)

presentation/api/routers/ingest.py
  → application/use_cases (__init__: IngestObservationUseCase, outcomes)
  → presentation/api/dependencies (get_observation_use_case)
  → presentation/api/schemas (EmpiricalObservationPayload, responses)

presentation/api/dependencies.py
  → application/auth/dependencies (re-export total)

presentation/api/__init__.py
  → presentation/api/middleware (ServerHeadersMiddleware)

application/auth/dependencies.py
  → application/use_cases (IngestObservationUseCase)
  → config (Settings)
  → infrastructure/db (SessionFactory)

application/auth/envelope_validator.py
  → domain/auth (__init__: canonicalize, excepciones)
  → domain/station (__init__: Station, StationCredential)
  → infrastructure/persistence (__init__: StationRepository, CredentialRepository)

application/use_cases/ingest_observation.py
  → application/auth/envelope_validator (EnvelopeValidator)
  → domain/auth (__init__: EnvelopeError)
  → domain/ingest (__init__: EntityType, IngestEvent, IngestEventPublisher)
  → infrastructure/db (SessionFactory)
  → infrastructure/persistence (__init__: IngestFailuresRepository, IngestLogRepository)

infrastructure/publisher/postgres_direct.py
  → domain/ingest (__init__: IngestEvent, PublishResult)
  → infrastructure/db (SessionFactory)
  → infrastructure/persistence (__init__: BronzeRepository)

infrastructure/persistence/bronze_repository.py
  → domain/ingest (__init__: EntityType, IngestEvent)
  → infrastructure/db (__init__: 3 ORM models, _BronzeRawMixin)

infrastructure/persistence/station_repository.py
  → domain/station (__init__: Station)
  → infrastructure/db (__init__: StationORM)

infrastructure/persistence/credential_repository.py
  → domain/station (__init__: StationCredential)
  → infrastructure/db (__init__: StationCredentialORM)

infrastructure/persistence/ingest_log_repository.py
  → infrastructure/db (__init__: IngestLogORM)

infrastructure/persistence/ingest_failures_repository.py
  → infrastructure/db (__init__: IngestFailureORM)

main.py
  → yvy_server (__version__)
  → application/auth (__init__: EnvelopeValidator)
  → application/use_cases (__init__: IngestObservationUseCase)
  → config (get_settings)
  → infrastructure/db (__init__: create_engine_and_session_factory, dispose_engine)
  → infrastructure/persistence (__init__: 5 repos)
  → infrastructure/publisher (__init__: PostgresDirectPublisher)
  → logging_setup (setup_logging)
  → presentation/api (__init__: ServerHeadersMiddleware)
  → presentation/api/routers (admin, health, ingest)

cli/cli.py
  → yvy_server (__version__)
  → config (get_settings)
  → domain/station (__init__: Station, StationCredential)
  → infrastructure/db (__init__: create_engine_and_session_factory, dispose_engine)
  → infrastructure/persistence (__init__: CredentialRepository, StationRepository)
  → logging_setup (setup_logging)
```

**Módulos sin dependencias externas (solo stdlib)**:
- `domain/auth/canonical_json.py` → solo `json`
- `domain/auth/exceptions.py` → solo `typing`, `uuid`
- `domain/ingest/events.py` → solo `dataclasses`, `datetime`, `enum`, `uuid`
- `domain/ingest/publisher.py` → solo `dataclasses`, `typing` + `domain/ingest/events`
- `domain/station/station.py` → solo `dataclasses`, `datetime`, `uuid`
- `domain/station/credential.py` → solo `dataclasses`, `datetime`, `uuid`

---

## 14. Tabla completa de funciones y clases

| Módulo | Símbolo | Tipo | Propósito en una línea |
|---|---|---|---|
| `canonical_json` | `canonicalize(obj)` | función | Serializa a JSON canónico UTF-8 determinístico |
| `exceptions` | `EnvelopeError` | clase base | Base de validación con `error_class`, `http_status`, contexto opcional |
| `exceptions` | `EnvelopeShapeError` | excepción | Estructura/versión inválida pre-HMAC (400 SCHEMA) |
| `exceptions` | `StationNotFoundError` | excepción | UUID no registrado (401 AUTH) |
| `exceptions` | `CredentialNotFoundError` | excepción | Credencial inactiva o inexistente (401 AUTH) |
| `exceptions` | `HmacMismatchError` | excepción | Firma no coincide (401 AUTH) |
| `exceptions` | `ClockSkewError` | excepción | Reloj del edge demasiado desincronizado (422 CLOCK) |
| `exceptions` | `PayloadSchemaError` | excepción | Payload inválido post-HMAC → DLQ (400 SCHEMA) |
| `events` | `EntityType` | StrEnum | 4 tipos de entidad: sensor_reading, empirical_observation, empirical_event, comment |
| `events` | `TOPIC_NAMES` | dict | Mapeo EntityType → nombre de topic Kafka |
| `events` | `IngestEvent` | dataclass frozen | Unidad de dato que fluye por el pipeline |
| `events` | `IngestEvent.from_envelope()` | classmethod | Construye desde envelope verificado |
| `events` | `IngestEvent.topic` | property | Retorna TOPIC_NAMES[entity_type] |
| `publisher` | `PublishResult` | dataclass frozen | Resultado del publish (persisted, transport, topic, was_duplicate) |
| `publisher` | `IngestEventPublisher` | Protocol | Interface del publisher — duck typed |
| `station` | `Station` | dataclass frozen | VO de estación |
| `credential` | `StationCredential` | dataclass frozen | VO de credencial con propiedades `is_active`, `key_bytes` |
| `envelope_validator` | `ValidatedEnvelope` | dataclass frozen | Resultado de validación exitosa |
| `envelope_validator` | `validate_shape()` | función pura | Verifica campos requeridos y schema_version |
| `envelope_validator` | `verify_hmac()` | función pura | HMAC-SHA256 constant-time |
| `envelope_validator` | `check_clock_skew()` | función pura | Verifica desfase temporal |
| `envelope_validator` | `EnvelopeValidator` | clase | Orquesta las 5 etapas de validación con repos |
| `envelope_validator` | `EnvelopeValidator.validate()` | async método | Pipeline completo: shape → station → credential → HMAC → clock |
| `dependencies` | `get_settings_dep()` | FastAPI Depends | Lee `app.state.settings` |
| `dependencies` | `get_session_dep()` | FastAPI Depends | Yield AsyncSession con autoclose |
| `dependencies` | `get_observation_use_case()` | FastAPI Depends | Lee `app.state.ingest_observation_use_case` |
| `dependencies` | `verify_admin_token()` | FastAPI Depends | Verifica Bearer token (constant-time) |
| `ingest_observation` | `IngestSuccess` | dataclass frozen | Outcome exitoso: event_id, was_duplicate, received_at |
| `ingest_observation` | `IngestFailure` | dataclass frozen | Outcome fallido: http_status, error_class, error_message |
| `ingest_observation` | `IngestObservationUseCase` | clase | Use case principal del endpoint /ingest/observation |
| `ingest_observation` | `IngestObservationUseCase.execute()` | async método | Orquesta los 4 pasos del pipeline, nunca levanta excepciones |
| `engine` | `create_engine_and_session_factory()` | función | Crea AsyncEngine + SessionFactory al startup |
| `engine` | `dispose_engine()` | función async | Cierra pool de conexiones al shutdown |
| `models` | `StationORM` | ORM model | `public.station` |
| `models` | `StationCredentialORM` | ORM model | `public.station_credentials` |
| `models` | `SensorRawORM` | ORM model | `bronze.sensor_raw` |
| `models` | `EmpiricalObservationRawORM` | ORM model | `bronze.empirical_observation_raw` |
| `models` | `EmpiricalEventRawORM` | ORM model | `bronze.empirical_event_raw` |
| `models` | `IngestLogORM` | ORM model | `bronze.ingest_log` |
| `models` | `IngestFailureORM` | ORM model | `bronze.ingest_failures` |
| `station_repository` | `StationRepository.get_by_uuid()` | async método | SELECT por UUID, retorna Station VO o None |
| `station_repository` | `StationRepository.insert()` | async método | INSERT station |
| `credential_repository` | `CredentialRepository.get_active_by_station_and_version()` | async método | SELECT con deactivated_at IS NULL |
| `credential_repository` | `CredentialRepository.insert()` | async método | INSERT credential |
| `bronze_repository` | `BronzeRepository.upsert()` | async método | INSERT ON CONFLICT DO NOTHING, retorna True/False |
| `ingest_log_repository` | `IngestLogRepository.record_attempt()` | async método | INSERT append-only a ingest_log |
| `ingest_failures_repository` | `IngestFailuresRepository.record_failure()` | async método | INSERT a ingest_failures (DLQ) |
| `postgres_direct` | `PostgresDirectPublisher` | clase | Track A: escribe a bronze via BronzeRepository |
| `postgres_direct` | `PostgresDirectPublisher.publish()` | async método | Abre transacción propia + upsert |
| `schemas` | `EmpiricalObservationPayload` | Pydantic model | Validación post-HMAC del payload |
| `schemas` | `HealthzResponse` | Pydantic model | `{"status": "ok"}` |
| `schemas` | `ServerInfoResponse` | Pydantic model | version, git, build_date, server_time, schemas |
| `schemas` | `AdminHealthzResponse` | Pydantic model | db_responsive + counts por tabla |
| `schemas` | `IngestSuccessResponse` | Pydantic model | ingest_result (inserted/duplicate), event_id |
| `schemas` | `IngestErrorResponse` | Pydantic model | error, error_class |
| `middleware` | `ServerHeadersMiddleware` | ASGI middleware | Agrega X-Server-Time, X-Schema-Versions-Supported, X-Server-Version |
| `health` | `healthz()` | FastAPI endpoint | GET /api/v1/healthz — liveness, sin DB |
| `health` | `serverinfo()` | FastAPI endpoint | GET /api/v1/serverinfo — versión y schemas soportados |
| `admin` | `admin_healthz()` | FastAPI endpoint | GET /api/v1/admin/healthz — DB connectivity + counts |
| `ingest` | `ingest_observation()` | FastAPI endpoint | POST /api/v1/ingest/observation — pipeline principal |
| `ingest` | `ingest_comment()` | FastAPI endpoint | POST /api/v1/ingest/comment — 501 stub |
| `main` | `lifespan()` | async context manager | Startup + shutdown del stack |
| `main` | `create_app()` | función | Construye la instancia FastAPI |
| `config` | `Settings` | pydantic-settings | Todas las variables de configuración con validaciones |
| `config` | `get_settings()` | función (lru_cache) | Singleton de Settings |
| `logging_setup` | `setup_logging()` | función | Configura JSON structured logging |
| `cli` | `create_station()` | comando Typer | Registra nueva IEA + genera credenciales HMAC |
| `cli` | `version()` | comando Typer | Imprime __version__ |

---

## 15. Reglas de negocio embebidas en el código

Decisiones que no están en documentación externa sino en el código:

1. **`received_at_utc` se captura una sola vez en el handler** (no se usa `DEFAULT now()` de Postgres para este campo). Garantiza coherencia entre `ingest_log`, `bronze.*_raw`, y el `IngestSuccess` retornado.

2. **El envelope no se valida con Pydantic pre-HMAC**. `json.loads()` → dict → use case. Pydantic aplicaría sus propios tipos/coerciones antes de que el canonicalizer calcule la firma, rompiendo el HMAC.

3. **`ingest_log` es SOLO para intentos HTTP de ingest**. El CLI y acciones admin NO escriben ahí. Si se requiere audit de acciones admin, se crea una tabla separada `admin_audit_log`.

4. **El publisher abre su propia transacción**. Si `publish()` falla, la transacción del `ingest_log` ya commiteó (o commitea en el catch). Los datos bronze son más valiosos que el log de auditoría.

5. **`entity_type` NO viene en el envelope**. Lo inyecta el use case según su constante `ENTITY_TYPE`. Evita que el edge declare falsamente qué tipo de dato está enviando.

6. **`schema_version` se verifica pre-HMAC** pero la validación tipada del payload (Pydantic) ocurre post-HMAC. Separación de responsabilidades: el envelope puede ser estructuralmente válido pero el payload puede no cumplir el schema declarado.

7. **`key_bytes` es una property del VO**, no se almacena. Se deriva de `key_hmac_hex` en el momento de uso. El VO es inmutable — no puede corromperse.

8. **El admin token se valida con `secrets.compare_digest`** (constant-time). Evita timing attacks en la comparación.

9. **El DLQ (`ingest_failures`) solo recibe fallos post-HMAC**. Fallos pre-HMAC solo van a `ingest_log`. La distinción es: post-HMAC el envelope está autenticado y vale guardarlo para replay; pre-HMAC podría ser ruido de Internet.

10. **`flush()` obligatorio en el CLI entre station insert y credential insert**. La FK `station_credentials.station_uuid → station.station_uuid` necesita que la station esté flusheada antes de agregar la credencial en la misma transacción.

---

## 16. Superficie HTTP

### Endpoints públicos (sin autenticación)

| Método | Path | Response | Descripción |
|---|---|---|---|
| `GET` | `/api/v1/healthz` | `200 {"status":"ok"}` | Liveness probe, no toca DB |
| `GET` | `/api/v1/serverinfo` | `200 ServerInfoResponse` | Versión, git commit, schemas soportados |

### Endpoints de ingest (autenticación HMAC en el body)

| Método | Path | Responses | Descripción |
|---|---|---|---|
| `POST` | `/api/v1/ingest/observation` | `200`, `400`, `401`, `422`, `500` | Ingesta de observación empírica firmada |
| `POST` | `/api/v1/ingest/comment` | `501` | Stub — implementación en Fase 9.2 |

### Endpoints de admin (Bearer token)

| Método | Path | Header requerido | Response | Descripción |
|---|---|---|---|---|
| `GET` | `/api/v1/admin/healthz` | `Authorization: Bearer <token>` | `200 AdminHealthzResponse` | DB connectivity + row counts |

### Headers en toda response (middleware)

| Header | Ejemplo | Uso del edge |
|---|---|---|
| `X-Server-Time` | `2026-05-28T18:00:00.123Z` | Recalibrar reloj si recibe `422 CLOCK` |
| `X-Schema-Versions-Supported` | `1.0` | Verificar compatibilidad al arrancar |
| `X-Server-Version` | `0.9.1-dev+abc1234` | Trazabilidad en logs |

### OpenAPI

Disponible en `/docs` (Swagger UI). `/redoc` está deshabilitado. Deshabilitar `/docs` en producción cuando se configure Fase 9.3.

---

**Fin del diagnóstico · YVY Server Fase 9.1**
