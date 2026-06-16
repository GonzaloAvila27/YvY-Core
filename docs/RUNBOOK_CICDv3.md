# YVY · RUNBOOK · CI/CD + Migración Canónica (M0)

**Versión:** 3.0
**Fecha:** 15-Jun-2026
**Aplica a:** servidor Debian 12 con stack YVY (CICDv2 vigente).
**Audiencia:** Luis (SysAdmin/DevOps), Gonzalo (admin del proyecto).
**Reemplaza/extiende:** `RUNBOOK_CICDv2.md` (el proceso de deploy de v2 sigue vigente; este
documento agrega la migración canónica M0 y eleva la versión del proceso CI/CD).

---

## 0. Qué es este documento

Dos cosas en uno:

1. **El proceso CI/CD** (secciones 1-7) — idéntico en espíritu a CICDv2, con el añadido de que
   migraciones que tocan estructura de datos requieren backup verificado + plan de rollback de
   schema explícito.
2. **La migración M0** (sección 8) — el cambio estructural que introduce el modelo canónico,
   migra Bronze a `raw_record`, backfillea los datos actuales e instala pgvector + PostGIS.
   M0 es **bloqueante** de todo el Track DATA, del refactor de Web/Core a v3 y del DQS v3.

Las secciones 1-7 de CICDv2 se dan por incorporadas. Acá solo se documenta lo nuevo y la
migración. Para el proceso de deploy estándar, seguir CICDv2 §3 (pre-deploy, backup, build,
smoke tests, rollback).

---

## 1. Cambios del proceso CI/CD respecto a v2

### 1.1 Migraciones que tocan estructura → gate adicional

Una migración que crea schemas, mueve datos entre tablas o dropea tablas (como M0) requiere,
**antes** del flujo normal de deploy:

- [ ] Backup verificado (no solo creado — restaurado en un entorno de prueba o validado con
      `pg_restore --list`).
- [ ] Migración probada en CI contra una copia del schema de producción (no solo schema vacío).
- [ ] Plan de rollback de schema escrito (el `downgrade()` de Alembic debe estar implementado y
      probado, no auto-generado y sin revisar).
- [ ] Ventana de mantenimiento acordada (M0 detiene la ingesta unos minutos).

### 1.2 CI: probar migración contra datos, no solo contra schema vacío

Extender `.github/workflows/ci.yml` con un job que cargue un fixture representativo (algunas
filas en las 3 tablas bronze actuales) y corra `alembic upgrade head` seguido de
`alembic downgrade -1` y de nuevo `upgrade head`. Esto detecta migraciones no reversibles antes
de producción.

---

## 2. Pre-condiciones de M0

- [ ] CICDv2 §3 Paso 1 (estado del servidor) verde.
- [ ] Stack en pie, `healthz` OK, 443 ALLOW.
- [ ] Backup pre-migración tomado y **verificado**.
- [ ] DQS Q1 **no implementado en producción** (confirmado — solo existe en documentación).
      Si estuviera implementado, parar y revisar `YVY_DQS_Q1_RUNBOOK_v3.md` primero.
- [ ] Web/Core: C10 y C11 cerradas en código pero el refactor v3 va junto con M0.
- [ ] Confirmado: ~10k filas totales en bronze actual (verificar abajo).

```bash
docker compose exec postgres psql -U yvyserver -d yvy -c "
SELECT 'sensor_raw' AS tabla, COUNT(*) FROM bronze.sensor_raw
UNION ALL SELECT 'empirical_observation_raw', COUNT(*) FROM bronze.empirical_observation_raw
UNION ALL SELECT 'empirical_event_raw', COUNT(*) FROM bronze.empirical_event_raw;
"
```

---

## 3. Instalación de extensiones (pgvector + PostGIS)

Las extensiones se instalan a nivel de imagen Postgres y se crean a nivel de base. Dado que el
stack usa la imagen oficial `postgres:16`, conviene cambiar a una imagen que incluya ambas
extensiones, o instalarlas en el contenedor.

### 3.1 Imagen Postgres con extensiones

Opción recomendada (menos fricción): usar `postgis/postgis:16-3.4` como base (incluye PostGIS)
y agregar pgvector. Editar el `docker-compose.yml`:

```yaml
  postgres:
    # antes: image: postgres:16
    build:
      context: ./postgres
      dockerfile: Dockerfile
    # ... resto igual (env, volumes, healthcheck)
```

`./postgres/Dockerfile`:

```dockerfile
FROM postgis/postgis:16-3.4
# pgvector
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/*
```

> **Nota de red:** la instalación de paquetes apt durante el build requiere acceso a los
> repositorios Debian (`archive.ubuntu.com`, `security.ubuntu.com`). Si el build corre en un
> entorno con red restringida, verificar acceso o construir la imagen donde haya red y
> exportarla.

### 3.2 Crear las extensiones en la base

Tras rebuild del contenedor postgres, dentro de la migración M0 (Alembic) o manualmente una vez:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS postgis;
```

PostGIS queda **latente**: instalada, sin tablas geoespaciales todavía. Solo ocupa espacio en
el catálogo de Postgres. No tiene costo de uso hasta que se cree la primera columna `geometry`.

**[VERIFICAR]**

```bash
docker compose exec postgres psql -U yvyserver -d yvy -c "
SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector','postgis');
"
# Esperado: vector y postgis listadas
```

---

## 4. Migración M0 — modelo canónico

Archivo: `alembic/versions/003_canonical_model.py`.

La migración crea schemas, tablas canónicas, Bronze genérico, el resto de schemas vacíos, y
backfillea. El `downgrade()` debe revertir limpio (probado en CI).

### 4.1 Schemas

```sql
CREATE SCHEMA IF NOT EXISTS canonical;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;
CREATE SCHEMA IF NOT EXISTS knowledge;
CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS exports;
CREATE SCHEMA IF NOT EXISTS catalog;
-- bronze ya existe
```

### 4.2 Catálogo canónico

```sql
CREATE TABLE canonical.unit (
    unit_key    text PRIMARY KEY,        -- 'Cel','%','hPa','m/s','deg','pH','mm','idx'
    ucum_code   text,
    display     text NOT NULL
);

CREATE TABLE canonical.source_type (
    source_type_key text PRIMARY KEY,    -- 'weather_station','soil_sensor','water_sensor',
                                         -- 'human_observation','external_api','satellite',
                                         -- 'gis_layer','document'
    domain          text NOT NULL,       -- 'meteorology','soil','water','territorial',
                                         -- 'education','reference','knowledge'
    is_ground_truth boolean NOT NULL,    -- true=medición propia, false=referencia externa
    description     jsonb
);

CREATE TABLE canonical.variable (
    variable_key    text PRIMARY KEY,    -- 'air_temperature','relative_humidity',...
    domain          text NOT NULL,
    display_name    jsonb NOT NULL,      -- {es,en,pt}
    canonical_unit  text NOT NULL REFERENCES canonical.unit,
    value_type      text NOT NULL CHECK (value_type IN
                        ('float','int','categorical','boolean','angle')),
    physical_min    numeric,             -- rango físicamente posible (NO QC)
    physical_max    numeric,
    is_derived      boolean NOT NULL DEFAULT false,
    description     jsonb
);

CREATE TABLE canonical.source (
    source_id       uuid PRIMARY KEY,
    source_type_key text NOT NULL REFERENCES canonical.source_type,
    external_ref    text,                -- station_uuid (Edge) o clave de API externa
    name            text NOT NULL,
    metadata        jsonb,               -- fabricante, modelo, lat/lon, altitud, etc.
    active          boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_type_key, external_ref)
);

CREATE TABLE canonical.sensor (
    sensor_id       uuid PRIMARY KEY,
    source_id       uuid NOT NULL REFERENCES canonical.source,
    sensor_type     text,                -- 'WH1081','RS485-pH','Davis-VP2'
    variables       text[] NOT NULL,     -- variable_keys que produce
    metadata        jsonb,               -- modelo, firmware (on-request del nodo)
    active          boolean NOT NULL DEFAULT true
);

CREATE TABLE canonical.indicator (
    indicator_key   text PRIMARY KEY,    -- 'vpd_daily','frost_risk','fire_risk',...
    domain          text NOT NULL,
    display_name    jsonb NOT NULL,
    method_version  text NOT NULL,
    inputs          text[],              -- variable_keys o indicator_keys consumidos
    output_type     text NOT NULL CHECK (output_type IN
                        ('score','category','boolean','index')),
    description     jsonb,
    method_doc_url  text
);
```

### 4.3 Seed del catálogo (variables meteorológicas actuales)

Mapea las 8 variables del `quality_range_config` del DQS al modelo canónico. Los `physical_min/
max` son límites físicos, NO los rangos de QC del DQS (esos siguen viviendo en la config de QC).

```sql
INSERT INTO canonical.unit (unit_key, ucum_code, display) VALUES
    ('Cel','Cel','°C'), ('pct','%','%'), ('hPa','hPa','hPa'),
    ('m/s','m/s','m/s'), ('deg','deg','°'), ('idx',NULL,'índice'),
    ('kPa','kPa','kPa'), ('mm','mm','mm');

INSERT INTO canonical.source_type
    (source_type_key, domain, is_ground_truth, description) VALUES
    ('weather_station','meteorology', true,
        '{"es":"Estación meteorológica vía YVY Edge"}'),
    ('external_api','reference', false,
        '{"es":"Fuente externa de referencia (Open-Meteo, etc.)"}'),
    ('human_observation','meteorology', true,
        '{"es":"Observación empírica humana"}');

INSERT INTO canonical.variable
    (variable_key, domain, display_name, canonical_unit, value_type,
     physical_min, physical_max, is_derived) VALUES
    ('air_temperature','meteorology','{"es":"Temperatura del aire"}','Cel','float',-90,60,false),
    ('temp_indoor','meteorology','{"es":"Temperatura interior"}','Cel','float',-40,80,false),
    ('relative_humidity','meteorology','{"es":"Humedad relativa"}','pct','float',0,100,false),
    ('hum_indoor','meteorology','{"es":"Humedad interior"}','pct','float',0,100,false),
    ('atmospheric_pressure','meteorology','{"es":"Presión atmosférica"}','hPa','float',850,1100,false),
    ('wind_speed','meteorology','{"es":"Velocidad de viento"}','m/s','float',0,120,false),
    ('wind_gust','meteorology','{"es":"Ráfaga de viento"}','m/s','float',0,150,false),
    ('wind_direction','meteorology','{"es":"Dirección de viento"}','deg','angle',0,360,false),
    -- derivadas (pobladas por promoción Silver)
    ('dewpoint','meteorology','{"es":"Punto de rocío"}','Cel','float',-90,60,true),
    ('vpd','meteorology','{"es":"Déficit de presión de vapor"}','kPa','float',0,15,true);
```

> **Nota:** `wind_direction` del WH1081 viene como índice 0-15. El mapeo a grados (0-360) se
> hace en la promoción a Silver. La variable canónica se define en grados (interoperable); la
> conversión es responsabilidad del adapter de promoción.

### 4.4 Bronze genérico

```sql
CREATE TABLE bronze.raw_record (
    record_id       uuid PRIMARY KEY,        -- = event_id actual (idempotencia)
    source_id       uuid NOT NULL REFERENCES canonical.source,
    source_type     text NOT NULL,           -- denormalizado (partición/filtro)
    record_kind     text NOT NULL,           -- 'sensor_reading','empirical_observation',
                                             -- 'empirical_event','csv_row','api_response',
                                             -- 'document_meta'
    payload         jsonb NOT NULL,
    schema_version  text NOT NULL,
    observed_at     timestamptz,             -- NULL para documentos
    received_at     timestamptz NOT NULL,
    ingestion_meta  jsonb                     -- IP, key_version, kafka offsets futuros
) PARTITION BY RANGE (received_at);

-- partición catch-all + mensuales hacia adelante (script genera 24 meses)
CREATE TABLE bronze.raw_record_default PARTITION OF bronze.raw_record DEFAULT;

CREATE INDEX idx_raw_source_time ON bronze.raw_record (source_id, received_at DESC);
CREATE INDEX idx_raw_kind        ON bronze.raw_record (record_kind, received_at DESC);
CREATE INDEX idx_raw_observed    ON bronze.raw_record (observed_at)
    WHERE observed_at IS NOT NULL;
```

### 4.5 Backfill — mapear stations a sources y mover datos

```sql
-- 1. Cada station actual se vuelve un source canónico
INSERT INTO canonical.source (source_id, source_type_key, external_ref, name, metadata, active)
SELECT
    gen_random_uuid(),                       -- source_id nuevo
    'weather_station',
    station_uuid::text,                      -- external_ref = station_uuid v5
    name,
    jsonb_build_object('locality', locality, 'latitude', latitude,
                       'longitude', longitude, 'code', code),
    COALESCE(active, true)
FROM public.station;

-- 2. Tabla de mapeo temporal station_uuid -> source_id (para el backfill)
CREATE TEMP TABLE _station_source_map AS
SELECT s.station_uuid, c.source_id
FROM public.station s
JOIN canonical.source c ON c.external_ref = s.station_uuid::text;

-- 3. Backfill sensor_raw -> raw_record
INSERT INTO bronze.raw_record
    (record_id, source_id, source_type, record_kind, payload,
     schema_version, observed_at, received_at, ingestion_meta)
SELECT
    sr.event_id,
    m.source_id,
    'weather_station',
    'sensor_reading',
    sr.payload,
    sr.schema_version,
    (sr.payload->>'observed_at_utc')::timestamptz,
    sr.received_at,
    jsonb_build_object('key_version', sr.key_version_used,
                       'from_ip', sr.received_from_ip)
FROM bronze.sensor_raw sr
JOIN _station_source_map m ON m.station_uuid = sr.station_uuid;

-- 4. Backfill empirical_observation_raw
INSERT INTO bronze.raw_record
    (record_id, source_id, source_type, record_kind, payload,
     schema_version, observed_at, received_at, ingestion_meta)
SELECT
    er.event_id, m.source_id, 'human_observation', 'empirical_observation',
    er.payload, er.schema_version,
    (er.payload->>'observed_at_utc')::timestamptz, er.received_at,
    jsonb_build_object('key_version', er.key_version_used)
FROM bronze.empirical_observation_raw er
JOIN _station_source_map m ON m.station_uuid = er.station_uuid;

-- 5. Backfill empirical_event_raw
INSERT INTO bronze.raw_record
    (record_id, source_id, source_type, record_kind, payload,
     schema_version, observed_at, received_at, ingestion_meta)
SELECT
    ev.event_id, m.source_id, 'human_observation', 'empirical_event',
    ev.payload, ev.schema_version,
    (ev.payload->>'observed_at_utc')::timestamptz, ev.received_at,
    jsonb_build_object('key_version', ev.key_version_used)
FROM bronze.empirical_event_raw ev
JOIN _station_source_map m ON m.station_uuid = ev.station_uuid;
```

### 4.6 Verificar el backfill ANTES de dropear

```sql
-- Conteos deben coincidir
SELECT
    (SELECT COUNT(*) FROM bronze.sensor_raw) AS old_sensor,
    (SELECT COUNT(*) FROM bronze.raw_record WHERE record_kind='sensor_reading') AS new_sensor,
    (SELECT COUNT(*) FROM bronze.empirical_observation_raw) AS old_obs,
    (SELECT COUNT(*) FROM bronze.raw_record WHERE record_kind='empirical_observation') AS new_obs,
    (SELECT COUNT(*) FROM bronze.empirical_event_raw) AS old_ev,
    (SELECT COUNT(*) FROM bronze.raw_record WHERE record_kind='empirical_event') AS new_ev;
-- old_X debe == new_X en las 3 parejas
```

**Si los conteos no coinciden, PARAR.** No dropear nada. Revisar el join (probablemente una
station sin mapeo).

### 4.7 Drop de tablas viejas

Solo tras verificar 4.6. Las tablas `ingest_log` e `ingest_failures` **NO se tocan** (siguen
siendo el audit log y la DLQ del flujo de ingesta).

```sql
DROP TABLE bronze.sensor_raw;
DROP TABLE bronze.empirical_observation_raw;
DROP TABLE bronze.empirical_event_raw;
-- bronze.ingest_log y bronze.ingest_failures permanecen intactas
```

> **Importante:** el publisher de ingesta (`PostgresDirectPublisher` / `BronzeRepository`) debe
> actualizarse en el mismo deploy para escribir en `bronze.raw_record` con `source_id` resuelto
> desde `station_uuid`. Ver sección 5.

### 4.8 Silver y resto de schemas (estructura, sin datos)

Crear las tablas de Silver, Gold, Knowledge, auth, exports vacías. Se pueblan en sus fases
respectivas (D1+). Se crean ahora para fijar el modelo y evitar migraciones dispersas.

```sql
-- silver.measurement (poblada en D1)
CREATE TABLE silver.measurement (
    measurement_id   uuid PRIMARY KEY,
    observation_id   uuid NOT NULL,
    source_id        uuid NOT NULL REFERENCES canonical.source,
    variable_key     text NOT NULL REFERENCES canonical.variable,
    observed_at      timestamptz NOT NULL,
    value_num        numeric,
    value_text       text,
    value_bool       boolean,
    unit_key         text NOT NULL REFERENCES canonical.unit,
    quality_flags    text[] NOT NULL DEFAULT '{}',
    quality_score    numeric(5,2),
    is_imputed       boolean NOT NULL DEFAULT false,
    is_derived       boolean NOT NULL DEFAULT false,
    bronze_record_id uuid NOT NULL,
    promoted_at      timestamptz NOT NULL DEFAULT now(),
    promotion_version text NOT NULL
) PARTITION BY RANGE (observed_at);
CREATE TABLE silver.measurement_default PARTITION OF silver.measurement DEFAULT;
CREATE INDEX idx_meas_source_var_time ON silver.measurement (source_id, variable_key, observed_at DESC);
CREATE INDEX idx_meas_var_time ON silver.measurement (variable_key, observed_at DESC);

-- gold.indicator_value (poblada en D3)
CREATE TABLE gold.indicator_value (
    id              bigserial PRIMARY KEY,
    indicator_key   text NOT NULL REFERENCES canonical.indicator,
    source_id       uuid REFERENCES canonical.source,
    feature_ref     text,
    valid_at        timestamptz NOT NULL,
    computed_at     timestamptz NOT NULL DEFAULT now(),
    temporal_grain  text NOT NULL,
    value_num       numeric,
    value_category  text,
    confidence      numeric(5,2),
    method_version  text NOT NULL,
    inputs_snapshot jsonb
) PARTITION BY RANGE (valid_at);
CREATE TABLE gold.indicator_value_default PARTITION OF gold.indicator_value DEFAULT;

-- knowledge.* (pobladas en Track IA)
CREATE TABLE knowledge.document (
    document_id  uuid PRIMARY KEY,
    source_id    uuid REFERENCES canonical.source,
    doc_type     text NOT NULL,
    title        text NOT NULL,
    authors      text[],
    lang         text,
    published_at date,
    storage_uri  text NOT NULL,
    sha256       text NOT NULL,
    metadata     jsonb,
    ingested_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE knowledge.chunk (
    chunk_id    uuid PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES knowledge.document,
    chunk_index integer NOT NULL,
    content     text NOT NULL,
    token_count integer,
    metadata    jsonb,
    UNIQUE (document_id, chunk_index)
);
CREATE TABLE knowledge.embedding (
    chunk_id      uuid PRIMARY KEY REFERENCES knowledge.chunk,
    model         text NOT NULL,
    model_version text NOT NULL,
    dim           integer NOT NULL,
    embedding     vector,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE knowledge.entity (
    entity_id    uuid PRIMARY KEY,
    entity_type  text NOT NULL,
    canonical_ref text,
    name         jsonb,
    metadata     jsonb
);
CREATE TABLE knowledge.chunk_entity (
    chunk_id  uuid REFERENCES knowledge.chunk,
    entity_id uuid REFERENCES knowledge.entity,
    PRIMARY KEY (chunk_id, entity_id)
);

-- auth.* (pobladas en D8)
CREATE TABLE auth.organization (
    org_id        uuid PRIMARY KEY,
    name          text NOT NULL,
    slug          text NOT NULL UNIQUE,
    contact_email text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    active        boolean NOT NULL DEFAULT true
);
CREATE TABLE auth.api_key (
    key_id           uuid PRIMARY KEY,
    org_id           uuid NOT NULL REFERENCES auth.organization,
    key_prefix       text NOT NULL,
    key_hash         text NOT NULL,
    name             text NOT NULL,
    scopes           text[] NOT NULL,
    quota_minute     integer NOT NULL DEFAULT 60,
    quota_day        integer NOT NULL DEFAULT 10000,
    quota_rows_month bigint  NOT NULL DEFAULT 1000000,
    created_at       timestamptz NOT NULL DEFAULT now(),
    last_used_at     timestamptz,
    expires_at       timestamptz,
    revoked_at       timestamptz
);
CREATE TABLE auth.dataset_permission (
    permission_id bigserial PRIMARY KEY,
    org_id        uuid NOT NULL REFERENCES auth.organization,
    dataset_id    text NOT NULL,
    permission    text NOT NULL CHECK (permission IN ('read','export')),
    constraints   jsonb,
    granted_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz
);
CREATE TABLE auth.usage_log (
    id             bigserial,
    key_id         uuid NOT NULL,
    org_id         uuid NOT NULL,
    endpoint       text NOT NULL,
    method         text NOT NULL,
    status_code    integer NOT NULL,
    rows_returned  integer,
    bytes_returned integer,
    duration_ms    integer,
    occurred_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);
CREATE TABLE auth.usage_log_default PARTITION OF auth.usage_log DEFAULT;

-- exports.* (pobladas en D5)
CREATE TABLE exports.job (
    export_id     uuid PRIMARY KEY,
    org_id        uuid REFERENCES auth.organization,
    dataset_id    text NOT NULL,
    format        text NOT NULL CHECK (format IN ('csv','xlsx','parquet')),
    filters       jsonb NOT NULL,
    status        text NOT NULL CHECK (status IN
                    ('pending','running','ready','failed','expired')),
    rows          bigint,
    size_bytes    bigint,
    sha256        text,
    storage_uri   text,
    query_hash    text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    ready_at      timestamptz,
    expires_at    timestamptz,
    error         text
);

-- catalog.dataset (poblada en D7)
CREATE TABLE catalog.dataset (
    dataset_id   text PRIMARY KEY,
    title        jsonb NOT NULL,
    description  jsonb,
    domain       text NOT NULL,
    access_rights text NOT NULL CHECK (access_rights IN
                    ('public','institutional','restricted')),
    license      text,
    version      text NOT NULL,
    dcat         jsonb NOT NULL,          -- documento DCAT completo
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
```

---

## 5. Cambios de código que acompañan M0

La migración de schema sin el cambio de código deja la ingesta rota (escribiría a tablas que ya
no existen). Ambos van en el **mismo deploy**.

| Componente | Cambio |
|---|---|
| `infrastructure/db/models.py` | Nuevo ORM `RawRecordORM` → `bronze.raw_record`. Mantener `IngestLogORM`, `IngestFailureORM`. ORMs canónicos. Eliminar los 3 `*RawORM` viejos. |
| `infrastructure/db/repositories.py` | `BronzeRepository.upsert` escribe a `raw_record`. Resuelve `source_id` desde `station_uuid` (cache en memoria del mapeo station→source). |
| `domain/...` | Sin cambios (domain no conoce el schema). El `IngestEvent` sigue igual; el `entity_type` mapea a `record_kind`. |
| `presentation/api/routers/ingest.py` | Sin cambios de contrato. El edge sigue mandando el mismo envelope. |
| `presentation/api/routers/public.py` | C10/C11 views re-apuntadas (ver §6). |

**Resolución `station_uuid` → `source_id`:** el `BronzeRepository` carga al startup un dict
`{station_uuid: source_id}` desde `canonical.source WHERE source_type_key='weather_station'`.
En cada ingesta resuelve el source. Si llega un `station_uuid` sin source (estación nueva no
registrada en canonical), el `create-station` CLI debe crear **a la vez** la station y su source
(actualizar ese comando).

---

## 6. Re-apuntar las views públicas (C10) a la proyección canónica

Las views `v_network_status` y `v_station_summary` leían `bronze.sensor_raw`. Ahora leen
`bronze.raw_record` filtrando `source_type='weather_station'` y joineando a `canonical.source`
para los datos de estación (code, locality, coords vienen de `source.metadata`).

**El contrato externo (DATA-CONTRACT) NO cambia.** El JSON de `/public/network` y
`/public/stations` es idéntico byte a byte. Solo cambia el FROM interno de las views.

```sql
-- Ejemplo: v_station_summary re-apuntada (esquema, el detalle va en Web/Core v3)
CREATE OR REPLACE VIEW public.v_station_summary AS
SELECT
    c.external_ref::uuid           AS station_uuid,
    c.metadata->>'code'            AS code,
    c.name                         AS name,
    c.metadata->>'locality'        AS locality,
    'Misiones'                     AS province,
    (c.metadata->>'latitude')::numeric  AS latitude,
    (c.metadata->>'longitude')::numeric AS longitude,
    -- status, last_sync_at, lastValues derivados del último raw_record del source
    ...
FROM canonical.source c
WHERE c.source_type_key = 'weather_station' AND c.active;
```

Tests de regresión: el output de las views debe ser idéntico al de antes de M0 (comparar JSON
de los endpoints contra un snapshot tomado pre-migración).

---

## 7. Orden de ejecución de M0 (ventana de mantenimiento)

```
1.  Anunciar ventana. Las edges acumulan en su queue local (offline-first), no se pierde dato.
2.  CICDv2 §3 Paso 1 — verificar estado del servidor.
3.  CICDv2 §3 Paso 2 — backup + VERIFICAR restaurabilidad (pg_restore --list).
4.  Detener ingesta: docker compose stop yvy-api  (postgres y nginx quedan).
5.  Rebuild imagen postgres con pgvector + PostGIS (§3). docker compose up -d postgres.
6.  CREATE EXTENSION vector; CREATE EXTENSION postgis;  (§3.2) — verificar.
7.  Build yvy-api con el código nuevo (raw_record publisher).
8.  alembic upgrade head  → corre 003_canonical_model (schemas, catálogo, seed, backfill).
9.  VERIFICAR backfill (§4.6). Si falla, downgrade + restaurar backup + abortar.
10. Drop tablas viejas (§4.7) — solo si §4.6 verde. (Puede ir dentro de la misma migración
    tras un check, o como paso manual separado para máxima seguridad la primera vez.)
11. docker compose up -d --no-deps yvy-api  → ingesta vuelta a habilitar.
12. Smoke tests (CICDv2 §3 Paso 8) + verificación de views (§6).
13. Verificar que las edges drenan su queue acumulada correctamente a raw_record.
14. Registrar el deploy (CICDv2 §3 Paso 9).
```

**[VERIFICAR FINAL]**
- [ ] `pg_extension` muestra vector y postgis
- [ ] Conteos de backfill coinciden (§4.6)
- [ ] Tablas viejas dropeadas, `ingest_log`/`ingest_failures` intactas
- [ ] `/public/network` y `/public/stations` devuelven JSON idéntico al snapshot pre-M0
- [ ] Una ingesta de prueba desde una edge llega a `bronze.raw_record` con `source_id` correcto
- [ ] `alembic check` sin drift

---

## 8. Rollback de M0

M0 es una migración destructiva (dropea tablas). El rollback es **restaurar el backup completo**,
no `alembic downgrade` (aunque el downgrade exista y esté probado en CI, en producción con datos
el backup es más seguro).

```bash
# 1. Restaurar backup pre-M0
BACKUP_PRE_M0=$(cat /tmp/yvy_backup_pre_m0.txt)
./scripts/restore_postgres.sh "$BACKUP_PRE_M0"

# 2. Rollback de código a la imagen anterior (CICDv2 §4.1)
cd server-code && git reset --hard <commit-pre-m0>
cd /opt/yvy-server && docker compose build yvy-api && docker compose up -d --no-deps yvy-api

# 3. Smoke tests con el estado anterior
```

> El backup pre-M0 debe persistirse: `echo "$BACKUP_ANTES_DEPLOY" > /tmp/yvy_backup_pre_m0.txt`
> en el Paso 3 del orden de ejecución.

---

## 9. Documentos a actualizar tras M0

| Documento | Versión | Cambio |
|---|---|---|
| `RUNBOOK_CICDv2.md` | → referenciado por v3 | Sigue vigente para deploy estándar |
| `YVY_Plan_Fases_Web_Core_v2.md` | → v3 | C10 re-apunta a canónico; C12-C16 leen Silver |
| `YVY_DQS_Q1_RUNBOOK_v2.md` | → v3 | Lee Silver canónica; `station_quality_daily` → `source_quality_daily` |
| `DIAGNOSTICO_CODIGO.md` | → actualizar | Nuevo schema canónico + bronze raw_record |
| `endpointsFactico_al12_06.md` | → actualizar | Sin cambios de endpoint (contrato intacto) |
| `yvy_core_propósito.md` | → actualizar | Pipeline por capas: añadir Knowledge, modelo canónico |

---

**Fin del runbook · YVY CI/CD + Migración Canónica v3.0**

M0 es bloqueante. Aplicar antes de cualquier fase del Track DATA, del refactor Web/Core v3 o del
DQS v3. Mantener este archivo en el repo junto al código.
