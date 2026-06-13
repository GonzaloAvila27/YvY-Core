"""C10 — capa de agregación pública: campos station + views + seed IEAs

Adds:
- New columns on ``public.station``: code, locality, province,
  latitude, longitude, active.
- View ``v_network_status``: single-row KPIs for the full network.
- View ``v_station_summary``: one row per station with derived status
  and last sensor values extracted from ``bronze.sensor_raw``.
- Seed: 18 IEA stations in Misiones. Only IEA Nº7 Garupá has
  ``active=true`` (the one already in production as of 12-Jun-2026);
  the rest are ``active=false`` and will be activated as nodes are
  deployed.

Revision ID: 0003_c10_public_views
Revises: 0002_edge_releases
Create Date: 2026-06-12
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_c10_public_views"
down_revision: str | None = "0002_edge_releases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ─── IEA seed data ───────────────────────────────────────────────────────
# Coordinates are approximate for most stations; update with exact values
# when confirmed. IEA Nº7 Garupá is the only one confirmed precise.
_IEA_STATIONS = [
    ("IEA-01", "IEA Nº1 San Ignacio",          "San Ignacio",          -27.2574, -55.5353, False),
    ("IEA-02", "IEA Nº2 Oberá",                "Oberá",                -27.4878, -55.1203, False),
    ("IEA-03", "IEA Nº3 Eldorado",             "Eldorado",             -26.4033, -54.6292, False),
    ("IEA-04", "IEA Nº4 Bonpland",             "Bonpland",             -27.4820, -55.4640, False),
    ("IEA-05", "IEA Nº5 Apóstoles",            "Apóstoles",            -27.9156, -55.7551, False),
    ("IEA-06", "IEA Nº6 Puerto Rico",          "Puerto Rico",          -26.8011, -55.0261, False),
    ("IEA-07", "IEA Nº7 Garupá",               "Garupá",               -27.4869, -55.8276, True),
    ("IEA-08", "IEA Nº8 Montecarlo",           "Montecarlo",           -26.5680, -54.7630, False),
    ("IEA-09", "IEA Nº9 Aristóbulo del Valle", "Aristóbulo del Valle", -27.0985, -54.9410, False),
    ("IEA-10", "IEA Nº10 El Soberbio",         "El Soberbio",          -27.2930, -54.1980, False),
    ("IEA-11", "IEA Nº11 San Pedro",           "San Pedro",            -26.6230, -54.1080, False),
    ("IEA-12", "IEA Nº12 Jardín América",      "Jardín América",       -27.0350, -55.2280, False),
    ("IEA-13", "IEA Nº13 25 de Mayo",          "25 de Mayo",           -27.3730, -54.7410, False),
    ("IEA-14", "IEA Nº14 Leandro N. Alem",     "Leandro N. Alem",      -27.5980, -55.3220, False),
    ("IEA-15", "IEA Nº15 Cerro Azul",          "Cerro Azul",           -27.6340, -55.4810, False),
    ("IEA-16", "IEA Nº16 San Javier",          "San Javier",           -27.8760, -55.1320, False),
    ("IEA-17", "IEA Nº17 Concepción Sierra",   "Concepción de la Sierra", -27.9960, -55.5220, False),
    ("IEA-18", "IEA Nº18 Posadas",             "Posadas",              -27.3671, -55.8963, False),
]

# ─── View DDL ────────────────────────────────────────────────────────────

_V_NETWORK_STATUS = """
CREATE OR REPLACE VIEW v_network_status AS
WITH
  active_stations AS (
    SELECT station_uuid FROM station WHERE active = true
  ),
  recent_syncs AS (
    SELECT DISTINCT station_uuid
    FROM bronze.sensor_raw
    WHERE received_at >= now() - interval '15 minutes'
  ),
  synced_2h AS (
    SELECT DISTINCT station_uuid
    FROM bronze.sensor_raw
    WHERE received_at >= now() - interval '2 hours'
  ),
  last_sync AS (
    SELECT MAX(received_at) AS max_received_at
    FROM bronze.sensor_raw
  ),
  total_records AS (
    SELECT
      (SELECT COUNT(*) FROM bronze.sensor_raw) +
      (SELECT COUNT(*) FROM bronze.empirical_observation_raw) AS n
  ),
  localities AS (
    SELECT COUNT(DISTINCT locality) AS cnt
    FROM station
    WHERE active = true AND locality IS NOT NULL
      AND station_uuid IN (SELECT station_uuid FROM synced_2h)
  ),
  uptime AS (
    -- % of 5-min buckets in the last 30 days with >=1 sync from any station
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
      FROM bronze.sensor_raw
      WHERE received_at >= now() - interval '30 days'
    ) b ON b.bucket = g.bucket
  )
SELECT
  (SELECT COUNT(*) FROM active_stations)::int            AS total_nodes,
  (SELECT COUNT(*) FROM recent_syncs
   WHERE station_uuid IN (SELECT station_uuid FROM active_stations))::int
                                                          AS active_nodes,
  (SELECT COUNT(*) FROM active_stations
   WHERE station_uuid NOT IN (SELECT station_uuid FROM synced_2h))::int
                                                          AS offline_nodes,
  (SELECT COUNT(*) FROM station WHERE active = false)::int AS pending_nodes,
  (SELECT n FROM total_records)::bigint                  AS total_records,
  (SELECT cnt FROM localities)::int                      AS localities_count,
  EXTRACT(EPOCH FROM (now() - (SELECT max_received_at FROM last_sync)))::int
                                                          AS last_sync_seconds,
  COALESCE((SELECT ratio FROM uptime), 0)                AS uptime_30d,
  now()                                                  AS generated_at
;
"""

_V_NETWORK_STATUS_DROP = "DROP VIEW IF EXISTS v_network_status"

_V_STATION_SUMMARY = """
CREATE OR REPLACE VIEW v_station_summary AS
WITH latest_raw AS (
  SELECT DISTINCT ON (station_uuid)
    station_uuid,
    payload,
    received_at
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
  lr.received_at                                          AS last_sync_at,
  CASE
    WHEN lr.received_at IS NULL                         THEN 'pending'
    WHEN lr.received_at >= now() - interval '15 minutes' THEN 'online'
    WHEN lr.received_at >= now() - interval '2 hours'   THEN 'sync_pending'
    ELSE                                                     'offline'
  END                                                     AS status,
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
  END                                                     AS last_values
FROM station s
LEFT JOIN latest_raw lr ON lr.station_uuid = s.station_uuid
;
"""

_V_STATION_SUMMARY_DROP = "DROP VIEW IF EXISTS v_station_summary"


def upgrade() -> None:
    # ─── Add columns to public.station ───────────────────────────────────
    op.add_column("station", sa.Column("code",      sa.Text(), nullable=True))
    op.add_column("station", sa.Column("locality",  sa.Text(), nullable=True))
    op.add_column("station", sa.Column(
        "province", sa.Text(), nullable=True, server_default="Misiones"
    ))
    op.add_column("station", sa.Column(
        "latitude",  sa.Numeric(9, 6), nullable=True
    ))
    op.add_column("station", sa.Column(
        "longitude", sa.Numeric(9, 6), nullable=True
    ))
    op.add_column("station", sa.Column(
        "active", sa.Boolean(), nullable=False, server_default="true"
    ))

    op.create_unique_constraint("uq_station_code", "station", ["code"])
    op.create_index("ix_station_active", "station", ["active"])

    # ─── Seed 18 IEA stations ─────────────────────────────────────────────
    conn = op.get_bind()
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

    # ─── Views ────────────────────────────────────────────────────────────
    op.execute(_V_NETWORK_STATUS)
    op.execute(_V_STATION_SUMMARY)


def downgrade() -> None:
    op.execute(_V_STATION_SUMMARY_DROP)
    op.execute(_V_NETWORK_STATUS_DROP)

    op.drop_index("ix_station_active", table_name="station")
    op.drop_constraint("uq_station_code", "station", type_="unique")

    op.drop_column("station", "active")
    op.drop_column("station", "longitude")
    op.drop_column("station", "latitude")
    op.drop_column("station", "province")
    op.drop_column("station", "locality")
    op.drop_column("station", "code")
