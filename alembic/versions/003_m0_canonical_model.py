"""M0 — Modelo canónico: schemas, Bronze genérico, Silver/Gold/Knowledge vacíos,
re-apuntado de views C10.

Aplica las decisiones B1-B8 del Plan Maestro 24m. Este es el cambio estructural
bloqueante de todo el Track DATA y del refactor Web/Core v3.

Qué hace:
  1. Agrega columnas a public.station (code, locality, province, lat/lon, active).
  2. Siembra 18 estaciones IEA en public.station.
  3. Crea schemas: canonical, silver, gold, knowledge, auth, exports, catalog.
  4. Instala extensiones: pgvector (requerido), PostGIS (latente, graceful si no está).
  5. Crea tablas canónicas (unit, source_type, variable, source, sensor, indicator).
  6. Siembra catálogo canónico (unidades, source_types, 10 variables meteorológicas).
  7. Crea bronze.raw_record (SIN particionar — decisión D-1 2026-07-07: PK simple
     record_id para preservar idempotencia por event_id vía ON CONFLICT).
  8. Backfillea bronze.*_raw → bronze.raw_record (con verificación de conteos).
  9. Dropea bronze.sensor_raw, empirical_observation_raw, empirical_event_raw.
 10. Crea silver.measurement, gold.indicator_value, knowledge.*, auth.*, exports.*,
     catalog.dataset — vacías, listas para sus fases respectivas.
 11. Re-apunta views v_network_status y v_station_summary a canonical.source +
     bronze.raw_record. Contrato externo (DATA-CONTRACT) intacto.

Downgrade (para CI roundtrip testing):
  - Dropa views nuevas.
  - Dropa schemas nuevos (CASCADE).
  - Recrea bronze.*_raw vacías y views apuntando a ellas.
  - Dropa columnas de public.station.

Revision ID: 0003_m0_canonical_model
Revises: 0002_edge_releases
Create Date: 2026-06-15
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_m0_canonical_model"
down_revision: str | None = "0002_edge_releases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ─── IEA seed data ───────────────────────────────────────────────────────────
_IEA_STATIONS = [
    ("IEA-01", "IEA Nº1 San Ignacio", "San Ignacio", -27.2574, -55.5353, False),
    ("IEA-02", "IEA Nº2 Oberá", "Oberá", -27.4878, -55.1203, False),
    ("IEA-03", "IEA Nº3 Eldorado", "Eldorado", -26.4033, -54.6292, False),
    ("IEA-04", "IEA Nº4 Bonpland", "Bonpland", -27.4820, -55.4640, False),
    ("IEA-05", "IEA Nº5 Apóstoles", "Apóstoles", -27.9156, -55.7551, False),
    ("IEA-06", "IEA Nº6 Puerto Rico", "Puerto Rico", -26.8011, -55.0261, False),
    ("IEA-07", "IEA Nº7 Garupá", "Garupá", -27.4869, -55.8276, True),
    ("IEA-08", "IEA Nº8 Montecarlo", "Montecarlo", -26.5680, -54.7630, False),
    ("IEA-09", "IEA Nº9 Aristóbulo del Valle", "Aristóbulo del Valle", -27.0985, -54.9410, False),
    ("IEA-10", "IEA Nº10 El Soberbio", "El Soberbio", -27.2930, -54.1980, False),
    ("IEA-11", "IEA Nº11 San Pedro", "San Pedro", -26.6230, -54.1080, False),
    ("IEA-12", "IEA Nº12 Jardín América", "Jardín América", -27.0350, -55.2280, False),
    ("IEA-13", "IEA Nº13 25 de Mayo", "25 de Mayo", -27.3730, -54.7410, False),
    ("IEA-14", "IEA Nº14 Leandro N. Alem", "Leandro N. Alem", -27.5980, -55.3220, False),
    ("IEA-15", "IEA Nº15 Cerro Azul", "Cerro Azul", -27.6340, -55.4810, False),
    ("IEA-16", "IEA Nº16 San Javier", "San Javier", -27.8760, -55.1320, False),
    (
        "IEA-17",
        "IEA Nº17 Concepción de la Sierra",
        "Concepción de la Sierra",
        -27.9960,
        -55.5220,
        False,
    ),
    ("IEA-18", "IEA Nº18 Posadas", "Posadas", -27.3671, -55.8963, False),
]


# ─── View DDL (v3 — apuntadas a canonical.source + bronze.raw_record) ────────

_V_STATION_SUMMARY_DROP = "DROP VIEW IF EXISTS v_station_summary"

_V_NETWORK_STATUS_DROP = "DROP VIEW IF EXISTS v_network_status"

_V_STATION_SUMMARY = """
CREATE OR REPLACE VIEW v_station_summary AS
WITH latest_raw AS (
  SELECT DISTINCT ON (source_id)
    source_id,
    payload,
    received_at
  FROM bronze.raw_record
  WHERE record_kind = 'sensor_reading'
  ORDER BY source_id, received_at DESC
)
SELECT
  c.external_ref::uuid                               AS station_uuid,
  c.metadata->>'code'                                AS code,
  c.name                                             AS name,
  c.metadata->>'locality'                            AS locality,
  COALESCE(c.metadata->>'province', 'Misiones')      AS province,
  (c.metadata->>'latitude')::numeric                 AS latitude,
  (c.metadata->>'longitude')::numeric                AS longitude,
  c.active                                           AS active,
  lr.received_at                                     AS last_sync_at,
  CASE
    WHEN lr.received_at IS NULL                           THEN 'pending'
    WHEN lr.received_at >= now() - interval '15 minutes'  THEN 'online'
    WHEN lr.received_at >= now() - interval '2 hours'     THEN 'sync_pending'
    ELSE                                                       'offline'
  END                                                AS status,
  CASE
    WHEN lr.payload IS NULL THEN NULL
    ELSE jsonb_build_object(
      'temp',          (lr.payload->>'temp_outdoor')::numeric,
      'humidity',      (lr.payload->>'hum_outdoor')::int,
      'pressure',      (lr.payload->>'pressure_rel')::numeric,
      'windSpeed',     (lr.payload->>'wind_speed')::numeric,
      'windGust',      (lr.payload->>'wind_gust')::numeric,
      'windDirection', (lr.payload->>'wind_dir')::numeric,
      'timestamp',     lr.received_at
    )
  END                                                AS last_values
FROM canonical.source c
LEFT JOIN latest_raw lr ON lr.source_id = c.source_id
WHERE c.source_type_key = 'weather_station'
"""

_V_NETWORK_STATUS = """
CREATE OR REPLACE VIEW v_network_status AS
WITH
  active_sources AS (
    SELECT source_id FROM canonical.source
    WHERE source_type_key = 'weather_station' AND active = true
  ),
  recent_syncs AS (
    SELECT DISTINCT source_id
    FROM bronze.raw_record
    WHERE record_kind = 'sensor_reading'
      AND received_at >= now() - interval '15 minutes'
  ),
  synced_2h AS (
    SELECT DISTINCT source_id
    FROM bronze.raw_record
    WHERE record_kind = 'sensor_reading'
      AND received_at >= now() - interval '2 hours'
  ),
  last_sync AS (
    SELECT MAX(received_at) AS max_received_at
    FROM bronze.raw_record
    WHERE source_type = 'weather_station'
  ),
  total_records AS (
    SELECT COUNT(*) AS n
    FROM bronze.raw_record
    WHERE source_type IN ('weather_station', 'human_observation')
  ),
  localities AS (
    SELECT COUNT(DISTINCT c.metadata->>'locality') AS cnt
    FROM canonical.source c
    WHERE c.source_type_key = 'weather_station'
      AND c.active = true
      AND c.source_id IN (SELECT source_id FROM synced_2h)
      AND c.metadata->>'locality' IS NOT NULL
  ),
  uptime AS (
    SELECT
      ROUND(
        COUNT(DISTINCT b.bucket)::numeric /
        NULLIF(COUNT(DISTINCT g.bucket), 0),
        4
      ) AS ratio
    FROM (
      SELECT generate_series(
        date_trunc('minute', now() - interval '30 days'),
        date_trunc('minute', now()),
        '5 minutes'::interval
      ) AS bucket
    ) g
    LEFT JOIN (
      SELECT date_trunc('minute', received_at) -
             (EXTRACT(MINUTE FROM received_at)::int % 5) *
             interval '1 minute' AS bucket
      FROM bronze.raw_record
      WHERE source_type = 'weather_station'
        AND record_kind = 'sensor_reading'
        AND received_at >= now() - interval '30 days'
    ) b ON b.bucket = g.bucket
  )
SELECT
  (SELECT COUNT(*) FROM active_sources)::int             AS total_nodes,
  (SELECT COUNT(*) FROM recent_syncs
   WHERE source_id IN (SELECT source_id FROM active_sources))::int
                                                         AS active_nodes,
  (SELECT COUNT(*) FROM active_sources
   WHERE source_id NOT IN (SELECT source_id FROM synced_2h))::int
                                                         AS offline_nodes,
  (SELECT COUNT(*) FROM canonical.source
   WHERE source_type_key = 'weather_station' AND active = false)::int
                                                         AS pending_nodes,
  (SELECT n FROM total_records)::bigint                  AS total_records,
  (SELECT cnt FROM localities)::int                      AS localities_count,
  EXTRACT(EPOCH FROM (now() - (SELECT max_received_at FROM last_sync)))::int
                                                         AS last_sync_seconds,
  COALESCE((SELECT ratio FROM uptime), 0)                AS uptime_30d,
  now()                                                  AS generated_at
"""

# ─── Downgrade: recrear tablas Bronze viejas vacías + views apuntando a ellas ─

_RECREATE_SENSOR_RAW = """
CREATE TABLE IF NOT EXISTS bronze.sensor_raw (
    event_id         uuid        PRIMARY KEY,
    station_uuid     uuid        NOT NULL REFERENCES station(station_uuid),
    schema_version   text        NOT NULL,
    payload          jsonb       NOT NULL,
    received_at      timestamptz NOT NULL DEFAULT now(),
    sent_at_utc      timestamptz,
    key_version_used integer     NOT NULL,
    received_from_ip inet,
    kafka_topic      text,
    kafka_partition  integer,
    kafka_offset     bigint,
    kafka_published_at timestamptz
)
"""

_RECREATE_EMPIRICAL_OBS_RAW = """
CREATE TABLE IF NOT EXISTS bronze.empirical_observation_raw (
    event_id         uuid        PRIMARY KEY,
    station_uuid     uuid        NOT NULL REFERENCES station(station_uuid),
    schema_version   text        NOT NULL,
    payload          jsonb       NOT NULL,
    received_at      timestamptz NOT NULL DEFAULT now(),
    sent_at_utc      timestamptz,
    key_version_used integer     NOT NULL,
    received_from_ip inet,
    kafka_topic      text,
    kafka_partition  integer,
    kafka_offset     bigint,
    kafka_published_at timestamptz
)
"""

_RECREATE_EMPIRICAL_EVENT_RAW = """
CREATE TABLE IF NOT EXISTS bronze.empirical_event_raw (
    event_id         uuid        PRIMARY KEY,
    station_uuid     uuid        NOT NULL REFERENCES station(station_uuid),
    schema_version   text        NOT NULL,
    payload          jsonb       NOT NULL,
    received_at      timestamptz NOT NULL DEFAULT now(),
    sent_at_utc      timestamptz,
    key_version_used integer     NOT NULL,
    received_from_ip inet,
    kafka_topic      text,
    kafka_partition  integer,
    kafka_offset     bigint,
    kafka_published_at timestamptz
)
"""

_V_STATION_SUMMARY_V2 = """
CREATE OR REPLACE VIEW v_station_summary AS
WITH latest_raw AS (
  SELECT DISTINCT ON (station_uuid)
    station_uuid, payload, received_at
  FROM bronze.sensor_raw
  ORDER BY station_uuid, received_at DESC
)
SELECT
  s.station_uuid,
  s.code,
  s.name,
  s.locality,
  s.province,
  s.latitude,
  s.longitude,
  s.active,
  lr.received_at AS last_sync_at,
  CASE
    WHEN lr.received_at IS NULL                          THEN 'pending'
    WHEN lr.received_at >= now() - interval '15 minutes' THEN 'online'
    WHEN lr.received_at >= now() - interval '2 hours'    THEN 'sync_pending'
    ELSE                                                      'offline'
  END AS status,
  CASE
    WHEN lr.payload IS NULL THEN NULL
    ELSE jsonb_build_object(
      'temp',          (lr.payload->>'temp_outdoor')::numeric,
      'humidity',      (lr.payload->>'hum_outdoor')::int,
      'pressure',      (lr.payload->>'pressure_rel')::numeric,
      'windSpeed',     (lr.payload->>'wind_speed')::numeric,
      'windGust',      (lr.payload->>'wind_gust')::numeric,
      'windDirection', (lr.payload->>'wind_dir')::numeric,
      'timestamp',     lr.received_at
    )
  END AS last_values
FROM station s
LEFT JOIN latest_raw lr ON lr.station_uuid = s.station_uuid
"""

_V_NETWORK_STATUS_V2 = """
CREATE OR REPLACE VIEW v_network_status AS
WITH
  active_stations AS (SELECT station_uuid FROM station WHERE active = true),
  recent_syncs    AS (SELECT DISTINCT station_uuid FROM bronze.sensor_raw
                      WHERE received_at >= now() - interval '15 minutes'),
  synced_2h       AS (SELECT DISTINCT station_uuid FROM bronze.sensor_raw
                      WHERE received_at >= now() - interval '2 hours'),
  last_sync       AS (SELECT MAX(received_at) AS max_received_at FROM bronze.sensor_raw),
  total_records   AS (
    SELECT (SELECT COUNT(*) FROM bronze.sensor_raw) +
           (SELECT COUNT(*) FROM bronze.empirical_observation_raw) AS n
  ),
  localities AS (
    SELECT COUNT(DISTINCT locality) AS cnt FROM station
    WHERE active = true AND locality IS NOT NULL
      AND station_uuid IN (SELECT station_uuid FROM synced_2h)
  ),
  uptime AS (
    SELECT ROUND(COUNT(DISTINCT b.bucket)::numeric /
                 NULLIF(COUNT(DISTINCT g.bucket), 0), 4) AS ratio
    FROM (SELECT generate_series(date_trunc('minute', now() - interval '30 days'),
                                 date_trunc('minute', now()),
                                 '5 minutes'::interval) AS bucket) g
    LEFT JOIN (
      SELECT date_trunc('minute', received_at) -
             (EXTRACT(MINUTE FROM received_at)::int % 5) * interval '1 minute' AS bucket
      FROM bronze.sensor_raw WHERE received_at >= now() - interval '30 days'
    ) b ON b.bucket = g.bucket
  )
SELECT
  (SELECT COUNT(*) FROM active_stations)::int   AS total_nodes,
  (SELECT COUNT(*) FROM recent_syncs
   WHERE station_uuid IN (SELECT station_uuid FROM active_stations))::int AS active_nodes,
  (SELECT COUNT(*) FROM active_stations
   WHERE station_uuid NOT IN (SELECT station_uuid FROM synced_2h))::int AS offline_nodes,
  (SELECT COUNT(*) FROM station WHERE active = false)::int AS pending_nodes,
  (SELECT n FROM total_records)::bigint          AS total_records,
  (SELECT cnt FROM localities)::int              AS localities_count,
  EXTRACT(EPOCH FROM (now() - (SELECT max_received_at FROM last_sync)))::int AS last_sync_seconds,
  COALESCE((SELECT ratio FROM uptime), 0)        AS uptime_30d,
  now()                                          AS generated_at
"""


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Columnas en public.station ────────────────────────────────────────
    op.add_column("station", sa.Column("code", sa.Text(), nullable=True))
    op.add_column("station", sa.Column("locality", sa.Text(), nullable=True))
    op.add_column(
        "station", sa.Column("province", sa.Text(), nullable=True, server_default="Misiones")
    )
    op.add_column("station", sa.Column("latitude", sa.Numeric(9, 6), nullable=True))
    op.add_column("station", sa.Column("longitude", sa.Numeric(9, 6), nullable=True))
    op.add_column(
        "station", sa.Column("active", sa.Boolean(), nullable=False, server_default="true")
    )
    op.create_unique_constraint("uq_station_code", "station", ["code"])
    op.create_index("ix_station_active", "station", ["active"])

    # ── 2. Seed 18 IEAs en public.station ────────────────────────────────────
    for code, name, locality, lat, lon, active in _IEA_STATIONS:
        conn.execute(
            sa.text("""
                INSERT INTO station
                  (station_uuid, name, code, locality, province,
                   latitude, longitude, active)
                VALUES
                  (:uuid, :name, :code, :locality, 'Misiones',
                   :lat, :lon, :active)
                ON CONFLICT DO NOTHING
            """),
            {
                "uuid": uuid.uuid4(),
                "name": name,
                "code": code,
                "locality": locality,
                "lat": lat,
                "lon": lon,
                "active": active,
            },
        )

    # ── 3. Schemas ───────────────────────────────────────────────────────────
    for schema in ("canonical", "silver", "gold", "knowledge", "auth", "exports", "catalog"):
        conn.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))

    # ── 4. Extensiones ───────────────────────────────────────────────────────
    # pgvector: requerido para knowledge.embedding
    conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))

    # PostGIS: latente (sin tablas geoespaciales en M0). Graceful si no está.
    conn.execute(
        sa.text("""
        DO $$
        BEGIN
            CREATE EXTENSION IF NOT EXISTS postgis;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'PostGIS no disponible en este servidor. '
                         'Instalar el paquete postgis para funciones geoespaciales '
                         '(Track Fuentes F7/F8).';
        END;
        $$
    """)
    )

    # ── 5. Tablas canónicas ──────────────────────────────────────────────────
    conn.execute(
        sa.text("""
        CREATE TABLE canonical.unit (
            unit_key  text PRIMARY KEY,
            ucum_code text,
            display   text NOT NULL
        )
    """)
    )

    conn.execute(
        sa.text("""
        CREATE TABLE canonical.source_type (
            source_type_key text    PRIMARY KEY,
            domain          text    NOT NULL,
            is_ground_truth boolean NOT NULL,
            description     jsonb
        )
    """)
    )

    conn.execute(
        sa.text("""
        CREATE TABLE canonical.variable (
            variable_key   text    PRIMARY KEY,
            domain         text    NOT NULL,
            display_name   jsonb   NOT NULL,
            canonical_unit text    NOT NULL REFERENCES canonical.unit,
            value_type     text    NOT NULL
                CHECK (value_type IN ('float','int','categorical','boolean','angle')),
            physical_min   numeric,
            physical_max   numeric,
            is_derived     boolean NOT NULL DEFAULT false,
            description    jsonb
        )
    """)
    )

    conn.execute(
        sa.text("""
        CREATE TABLE canonical.source (
            source_id       uuid    PRIMARY KEY,
            source_type_key text    NOT NULL REFERENCES canonical.source_type,
            external_ref    text,
            name            text    NOT NULL,
            metadata        jsonb,
            active          boolean NOT NULL DEFAULT true,
            created_at      timestamptz NOT NULL DEFAULT now(),
            UNIQUE (source_type_key, external_ref)
        )
    """)
    )

    conn.execute(
        sa.text("""
        CREATE TABLE canonical.sensor (
            sensor_id   uuid    PRIMARY KEY,
            source_id   uuid    NOT NULL REFERENCES canonical.source,
            sensor_type text,
            variables   text[]  NOT NULL,
            metadata    jsonb,
            active      boolean NOT NULL DEFAULT true
        )
    """)
    )

    conn.execute(
        sa.text("""
        CREATE TABLE canonical.indicator (
            indicator_key text PRIMARY KEY,
            domain        text NOT NULL,
            display_name  jsonb NOT NULL,
            method_version text NOT NULL,
            inputs        text[],
            output_type   text NOT NULL
                CHECK (output_type IN ('score','category','boolean','index')),
            description   jsonb,
            method_doc_url text
        )
    """)
    )

    # ── 6. Seed catálogo canónico ────────────────────────────────────────────
    conn.execute(
        sa.text("""
        INSERT INTO canonical.unit (unit_key, ucum_code, display) VALUES
            ('Cel',  'Cel',  '°C'),
            ('pct',  '%',    '%'),
            ('hPa',  'hPa',  'hPa'),
            ('m/s',  'm/s',  'm/s'),
            ('deg',  'deg',  '°'),
            ('idx',  NULL,   'índice'),
            ('kPa',  'kPa',  'kPa'),
            ('mm',   'mm',   'mm')
        ON CONFLICT DO NOTHING
    """)
    )

    conn.execute(
        sa.text("""
        INSERT INTO canonical.source_type
            (source_type_key, domain, is_ground_truth, description) VALUES
            ('weather_station',  'meteorology', true,
                '{"es":"Estación meteorológica vía YVY Edge"}'),
            ('external_api',     'reference',   false,
                '{"es":"Fuente externa de referencia (Open-Meteo, etc.)"}'),
            ('human_observation','meteorology', true,
                '{"es":"Observación empírica humana"}')
        ON CONFLICT DO NOTHING
    """)
    )

    conn.execute(
        sa.text("""
        INSERT INTO canonical.variable
            (variable_key, domain, display_name, canonical_unit,
             value_type, physical_min, physical_max, is_derived) VALUES
            ('air_temperature',      'meteorology',
             '{"es":"Temperatura del aire"}',     'Cel',  'float', -90,  60,  false),
            ('temp_indoor',          'meteorology',
             '{"es":"Temperatura interior"}',     'Cel',  'float', -40,  80,  false),
            ('relative_humidity',    'meteorology',
             '{"es":"Humedad relativa"}',         'pct',  'float',   0, 100,  false),
            ('hum_indoor',           'meteorology',
             '{"es":"Humedad interior"}',         'pct',  'float',   0, 100,  false),
            ('atmospheric_pressure', 'meteorology',
             '{"es":"Presión atmosférica"}',      'hPa',  'float', 850,1100,  false),
            ('wind_speed',           'meteorology',
             '{"es":"Velocidad de viento"}',      'm/s',  'float',   0, 120,  false),
            ('wind_gust',            'meteorology',
             '{"es":"Ráfaga de viento"}',         'm/s',  'float',   0, 150,  false),
            ('wind_direction',       'meteorology',
             '{"es":"Dirección de viento"}',      'deg',  'angle',   0, 360,  false),
            ('dewpoint',             'meteorology',
             '{"es":"Punto de rocío"}',           'Cel',  'float', -90,  60,  true),
            ('vpd',                  'meteorology',
             '{"es":"Déficit de presión de vapor"}', 'kPa','float',  0,  15,  true)
        ON CONFLICT DO NOTHING
    """)
    )

    # ── 7. bronze.raw_record ──────────────────────────────────────────────────
    # Decisión D-1 (2026-07-07): SIN particionado. Una tabla particionada por
    # received_at obliga a PK compuesta (record_id, received_at), lo que rompe
    # la idempotencia por event_id (ON CONFLICT (record_id) requiere un índice
    # único sobre record_id solo, imposible en tabla particionada por otra
    # columna). Al volumen actual (~42k filas) el particionado no rinde; se
    # reintroduce como migración planificada cuando haya ≥6 IEAs activas.
    conn.execute(
        sa.text("""
        CREATE TABLE bronze.raw_record (
            record_id      uuid        PRIMARY KEY,
            source_id      uuid        NOT NULL REFERENCES canonical.source,
            source_type    text        NOT NULL,
            record_kind    text        NOT NULL,
            payload        jsonb       NOT NULL,
            schema_version text        NOT NULL,
            observed_at    timestamptz,
            received_at    timestamptz NOT NULL,
            ingestion_meta jsonb
        )
    """)
    )

    conn.execute(
        sa.text(
            "CREATE INDEX idx_raw_source_time " "ON bronze.raw_record (source_id, received_at DESC)"
        )
    )
    conn.execute(
        sa.text("CREATE INDEX idx_raw_kind " "ON bronze.raw_record (record_kind, received_at DESC)")
    )
    conn.execute(
        sa.text(
            "CREATE INDEX idx_raw_observed "
            "ON bronze.raw_record (observed_at) WHERE observed_at IS NOT NULL"
        )
    )

    # ── 8. Backfill station → canonical.source ───────────────────────────────
    conn.execute(
        sa.text("""
        INSERT INTO canonical.source
            (source_id, source_type_key, external_ref, name, metadata, active)
        SELECT
            gen_random_uuid(),
            'weather_station',
            station_uuid::text,
            name,
            jsonb_build_object(
                'code',      code,
                'locality',  locality,
                'province',  COALESCE(province, 'Misiones'),
                'latitude',  latitude,
                'longitude', longitude
            ),
            COALESCE(active, true)
        FROM station
        ON CONFLICT (source_type_key, external_ref) DO NOTHING
    """)
    )

    # ── 9. Backfill bronze.*_raw → bronze.raw_record ─────────────────────────
    # Tabla temporal de mapeo station_uuid → source_id
    conn.execute(
        sa.text("""
        CREATE TEMP TABLE _station_source_map AS
        SELECT s.station_uuid, c.source_id
        FROM station s
        JOIN canonical.source c ON c.external_ref = s.station_uuid::text
    """)
    )

    # sensor_raw → raw_record
    conn.execute(
        sa.text("""
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
            jsonb_build_object(
                'key_version', sr.key_version_used,
                'from_ip',     sr.received_from_ip::text
            )
        FROM bronze.sensor_raw sr
        JOIN _station_source_map m ON m.station_uuid = sr.station_uuid
        ON CONFLICT DO NOTHING
    """)
    )

    # empirical_observation_raw → raw_record
    conn.execute(
        sa.text("""
        INSERT INTO bronze.raw_record
            (record_id, source_id, source_type, record_kind, payload,
             schema_version, observed_at, received_at, ingestion_meta)
        SELECT
            er.event_id,
            m.source_id,
            'human_observation',
            'empirical_observation',
            er.payload,
            er.schema_version,
            (er.payload->>'observed_at_utc')::timestamptz,
            er.received_at,
            jsonb_build_object('key_version', er.key_version_used)
        FROM bronze.empirical_observation_raw er
        JOIN _station_source_map m ON m.station_uuid = er.station_uuid
        ON CONFLICT DO NOTHING
    """)
    )

    # empirical_event_raw → raw_record
    conn.execute(
        sa.text("""
        INSERT INTO bronze.raw_record
            (record_id, source_id, source_type, record_kind, payload,
             schema_version, observed_at, received_at, ingestion_meta)
        SELECT
            ev.event_id,
            m.source_id,
            'human_observation',
            'empirical_event',
            ev.payload,
            ev.schema_version,
            (ev.payload->>'observed_at_utc')::timestamptz,
            ev.received_at,
            jsonb_build_object('key_version', ev.key_version_used)
        FROM bronze.empirical_event_raw ev
        JOIN _station_source_map m ON m.station_uuid = ev.station_uuid
        ON CONFLICT DO NOTHING
    """)
    )

    # ── 10. Verificar backfill ────────────────────────────────────────────────
    counts = (
        conn.execute(
            sa.text("""
        SELECT
            (SELECT COUNT(*) FROM bronze.sensor_raw)              AS old_sensor,
            (SELECT COUNT(*) FROM bronze.raw_record
             WHERE record_kind = 'sensor_reading')                AS new_sensor,
            (SELECT COUNT(*) FROM bronze.empirical_observation_raw) AS old_obs,
            (SELECT COUNT(*) FROM bronze.raw_record
             WHERE record_kind = 'empirical_observation')         AS new_obs,
            (SELECT COUNT(*) FROM bronze.empirical_event_raw)     AS old_ev,
            (SELECT COUNT(*) FROM bronze.raw_record
             WHERE record_kind = 'empirical_event')               AS new_ev
    """)
        )
        .mappings()
        .one()
    )

    for kind, old_col, new_col in (
        ("sensor_reading", "old_sensor", "new_sensor"),
        ("empirical_observation", "old_obs", "new_obs"),
        ("empirical_event", "old_ev", "new_ev"),
    ):
        old, new = counts[old_col], counts[new_col]
        if old != new:
            raise RuntimeError(
                f"Backfill mismatch for {kind}: "
                f"{old} rows in old table vs {new} in raw_record. "
                "Aborting migration — check JOIN on _station_source_map."
            )

    conn.execute(sa.text("DROP TABLE _station_source_map"))

    # ── 11. Drop tablas bronze viejas ─────────────────────────────────────────
    # Solo tras verificar §10. ingest_log e ingest_failures permanecen intactas.
    conn.execute(sa.text("DROP TABLE IF EXISTS bronze.sensor_raw"))
    conn.execute(sa.text("DROP TABLE IF EXISTS bronze.empirical_observation_raw"))
    conn.execute(sa.text("DROP TABLE IF EXISTS bronze.empirical_event_raw"))

    # ── 12. Silver.measurement (vacía, particionada — poblada en D1) ──────────
    conn.execute(
        sa.text("""
        CREATE TABLE silver.measurement (
            measurement_id   uuid        NOT NULL,
            observation_id   uuid        NOT NULL,
            source_id        uuid        NOT NULL REFERENCES canonical.source,
            variable_key     text        NOT NULL REFERENCES canonical.variable,
            observed_at      timestamptz NOT NULL,
            value_num        numeric,
            value_text       text,
            value_bool       boolean,
            unit_key         text        NOT NULL REFERENCES canonical.unit,
            quality_flags    text[]      NOT NULL DEFAULT '{}',
            quality_score    numeric(5,2),
            is_imputed       boolean     NOT NULL DEFAULT false,
            is_derived       boolean     NOT NULL DEFAULT false,
            bronze_record_id uuid        NOT NULL,
            promoted_at      timestamptz NOT NULL DEFAULT now(),
            promotion_version text       NOT NULL,
            PRIMARY KEY (measurement_id, observed_at)
        ) PARTITION BY RANGE (observed_at)
    """)
    )
    conn.execute(
        sa.text(
            "CREATE TABLE silver.measurement_default " "PARTITION OF silver.measurement DEFAULT"
        )
    )
    conn.execute(
        sa.text(
            "CREATE INDEX idx_meas_source_var_time "
            "ON silver.measurement (source_id, variable_key, observed_at DESC)"
        )
    )
    conn.execute(
        sa.text(
            "CREATE INDEX idx_meas_var_time "
            "ON silver.measurement (variable_key, observed_at DESC)"
        )
    )

    # ── 13. Gold.indicator_value (vacía, particionada — poblada en D3) ────────
    conn.execute(
        sa.text("""
        CREATE TABLE gold.indicator_value (
            id             bigserial   NOT NULL,
            indicator_key  text        NOT NULL REFERENCES canonical.indicator,
            source_id      uuid        REFERENCES canonical.source,
            feature_ref    text,
            valid_at       timestamptz NOT NULL,
            computed_at    timestamptz NOT NULL DEFAULT now(),
            temporal_grain text        NOT NULL,
            value_num      numeric,
            value_category text,
            confidence     numeric(5,2),
            method_version text        NOT NULL,
            inputs_snapshot jsonb,
            PRIMARY KEY (id, valid_at)
        ) PARTITION BY RANGE (valid_at)
    """)
    )
    conn.execute(
        sa.text(
            "CREATE TABLE gold.indicator_value_default " "PARTITION OF gold.indicator_value DEFAULT"
        )
    )

    # ── 14. Knowledge.* (vacías — pobladas en Track IA) ──────────────────────
    conn.execute(
        sa.text("""
        CREATE TABLE knowledge.document (
            document_id  uuid  PRIMARY KEY,
            source_id    uuid  REFERENCES canonical.source,
            doc_type     text  NOT NULL,
            title        text  NOT NULL,
            authors      text[],
            lang         text,
            published_at date,
            storage_uri  text  NOT NULL,
            sha256       text  NOT NULL,
            metadata     jsonb,
            ingested_at  timestamptz NOT NULL DEFAULT now()
        )
    """)
    )
    conn.execute(
        sa.text("""
        CREATE TABLE knowledge.chunk (
            chunk_id    uuid    PRIMARY KEY,
            document_id uuid    NOT NULL REFERENCES knowledge.document,
            chunk_index integer NOT NULL,
            content     text    NOT NULL,
            token_count integer,
            metadata    jsonb,
            UNIQUE (document_id, chunk_index)
        )
    """)
    )
    conn.execute(
        sa.text("""
        CREATE TABLE knowledge.embedding (
            chunk_id      uuid  PRIMARY KEY REFERENCES knowledge.chunk,
            model         text  NOT NULL,
            model_version text  NOT NULL,
            dim           integer NOT NULL,
            embedding     vector,
            created_at    timestamptz NOT NULL DEFAULT now()
        )
    """)
    )
    conn.execute(
        sa.text("""
        CREATE TABLE knowledge.entity (
            entity_id     uuid PRIMARY KEY,
            entity_type   text NOT NULL,
            canonical_ref text,
            name          jsonb,
            metadata      jsonb
        )
    """)
    )
    conn.execute(
        sa.text("""
        CREATE TABLE knowledge.chunk_entity (
            chunk_id  uuid REFERENCES knowledge.chunk,
            entity_id uuid REFERENCES knowledge.entity,
            PRIMARY KEY (chunk_id, entity_id)
        )
    """)
    )

    # ── 15. Auth.* (vacías — pobladas en D8) ──────────────────────────────────
    conn.execute(
        sa.text("""
        CREATE TABLE auth.organization (
            org_id        uuid PRIMARY KEY,
            name          text NOT NULL,
            slug          text NOT NULL UNIQUE,
            contact_email text,
            created_at    timestamptz NOT NULL DEFAULT now(),
            active        boolean NOT NULL DEFAULT true
        )
    """)
    )
    conn.execute(
        sa.text("""
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
        )
    """)
    )
    conn.execute(
        sa.text("""
        CREATE TABLE auth.dataset_permission (
            permission_id bigserial PRIMARY KEY,
            org_id        uuid NOT NULL REFERENCES auth.organization,
            dataset_id    text NOT NULL,
            permission    text NOT NULL CHECK (permission IN ('read','export')),
            constraints   jsonb,
            granted_at    timestamptz NOT NULL DEFAULT now(),
            expires_at    timestamptz
        )
    """)
    )
    conn.execute(
        sa.text("""
        CREATE TABLE auth.usage_log (
            id             bigserial   NOT NULL,
            key_id         uuid        NOT NULL,
            org_id         uuid        NOT NULL,
            endpoint       text        NOT NULL,
            method         text        NOT NULL,
            status_code    integer     NOT NULL,
            rows_returned  integer,
            bytes_returned integer,
            duration_ms    integer,
            occurred_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (id, occurred_at)
        ) PARTITION BY RANGE (occurred_at)
    """)
    )
    conn.execute(
        sa.text("CREATE TABLE auth.usage_log_default " "PARTITION OF auth.usage_log DEFAULT")
    )

    # ── 16. Exports.* (vacías — pobladas en D5) ───────────────────────────────
    conn.execute(
        sa.text("""
        CREATE TABLE exports.job (
            export_id   uuid PRIMARY KEY,
            org_id      uuid REFERENCES auth.organization,
            dataset_id  text NOT NULL,
            format      text NOT NULL CHECK (format IN ('csv','xlsx','parquet')),
            filters     jsonb NOT NULL,
            status      text NOT NULL
                CHECK (status IN ('pending','running','ready','failed','expired')),
            rows        bigint,
            size_bytes  bigint,
            sha256      text,
            storage_uri text,
            query_hash  text,
            created_at  timestamptz NOT NULL DEFAULT now(),
            ready_at    timestamptz,
            expires_at  timestamptz,
            error       text
        )
    """)
    )

    # ── 17. Catalog.* (vacío — poblado en D7) ────────────────────────────────
    conn.execute(
        sa.text("""
        CREATE TABLE catalog.dataset (
            dataset_id    text PRIMARY KEY,
            title         jsonb NOT NULL,
            description   jsonb,
            domain        text NOT NULL,
            access_rights text NOT NULL
                CHECK (access_rights IN ('public','institutional','restricted')),
            license       text,
            version       text NOT NULL,
            dcat          jsonb NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now()
        )
    """)
    )

    # ── 18. Views re-apuntadas (C10 v3) ──────────────────────────────────────
    op.execute(_V_STATION_SUMMARY_DROP)
    op.execute(_V_NETWORK_STATUS_DROP)
    op.execute(_V_STATION_SUMMARY)
    op.execute(_V_NETWORK_STATUS)


def downgrade() -> None:
    conn = op.get_bind()

    # Drop views canónicas
    op.execute(_V_STATION_SUMMARY_DROP)
    op.execute(_V_NETWORK_STATUS_DROP)

    # Drop schemas nuevos (CASCADE elimina todas sus tablas)
    for schema in ("catalog", "exports", "auth", "knowledge", "gold", "silver", "canonical"):
        conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))

    # Drop bronze.raw_record (particionada)
    conn.execute(sa.text("DROP TABLE IF EXISTS bronze.raw_record CASCADE"))

    # Recrear tablas bronze viejas vacías (para coherencia de schema en CI)
    conn.execute(sa.text(_RECREATE_SENSOR_RAW))
    conn.execute(sa.text(_RECREATE_EMPIRICAL_OBS_RAW))
    conn.execute(sa.text(_RECREATE_EMPIRICAL_EVENT_RAW))

    # Recrear views apuntando a las tablas viejas
    op.execute(_V_STATION_SUMMARY_V2)
    op.execute(_V_NETWORK_STATUS_V2)

    # Drop columnas de public.station
    op.drop_index("ix_station_active", table_name="station")
    op.drop_constraint("uq_station_code", "station", type_="unique")
    op.drop_column("station", "active")
    op.drop_column("station", "longitude")
    op.drop_column("station", "latitude")
    op.drop_column("station", "province")
    op.drop_column("station", "locality")
    op.drop_column("station", "code")
