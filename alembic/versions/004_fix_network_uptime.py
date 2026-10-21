"""Corrige uptime_30d de v_network_status (buckets de 5 minutos desalineados).

Problema (vista de la 0003): la serie de buckets de 30 días se generaba desde
``date_trunc('minute', now() - 30 days)`` en pasos de 5 minutos, sin alinear,
mientras los datos se agrupaban en múltiplos de 5 minutos del reloj. Los dos
lados solo coincidían cuando el minuto de la consulta era múltiplo de 5; el
resto del tiempo el join no encontraba nada y uptime_30d daba 0.0 (confirmado
en producción: 18:49:49 → 0.0, 18:50:03 → 0.3907).

Cambio:
  - Nueva función ``public.yvy_network_uptime_30d(ref timestamptz)``: calcula el
    uptime con la serie y los datos alineados al mismo piso de 5 minutos
    (``date_bin`` con origen fijo 2000-01-01 00:00 UTC), incluido el límite
    superior. Recibe el instante de referencia para poder testearla con un
    timestamp fijo.
  - ``v_network_status`` se recrea igual que en la 0003, salvo el CTE
    ``uptime``, que llama a la función con ``now()``. Mismas columnas, mismo
    tipo (numeric).
  - No toca ninguna tabla.

Downgrade: restaura exactamente la definición de v_network_status de la 0003
y borra la función.

Revision ID: 0004_fix_network_uptime
Revises: 0003_m0_canonical_model
Create Date: 2026-10-22
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_fix_network_uptime"
down_revision: str | None = "0003_m0_canonical_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_UPTIME_FN = """
CREATE OR REPLACE FUNCTION public.yvy_network_uptime_30d(ref timestamptz)
RETURNS numeric
LANGUAGE sql
STABLE
AS $fn$
  -- Fracción de buckets de 5 minutos de los últimos 30 días (hasta ref) con al
  -- menos una lectura de sensor de una estación activa. La serie y los datos
  -- usan el mismo piso de 5 minutos (date_bin, origen fijo en UTC), así que el
  -- resultado no depende del minuto en que se consulta.
  WITH bounds AS (
    SELECT
      date_bin('5 minutes', ref - interval '30 days',
               TIMESTAMPTZ '2000-01-01 00:00:00+00') AS lo,
      date_bin('5 minutes', ref,
               TIMESTAMPTZ '2000-01-01 00:00:00+00') AS hi
  ),
  series AS (
    SELECT generate_series(b.lo, b.hi, interval '5 minutes') AS bucket
    FROM bounds b
  ),
  data AS (
    SELECT DISTINCT
      date_bin('5 minutes', r.received_at,
               TIMESTAMPTZ '2000-01-01 00:00:00+00') AS bucket
    FROM bronze.raw_record r, bounds b
    WHERE r.source_type = 'weather_station'
      AND r.record_kind = 'sensor_reading'
      AND r.received_at >= b.lo
      AND r.received_at <= ref
      AND r.source_id IN (
        SELECT source_id FROM canonical.source
        WHERE source_type_key = 'weather_station' AND active = true
      )
  )
  SELECT COALESCE(
    ROUND(
      (SELECT COUNT(*) FROM series s JOIN data d ON d.bucket = s.bucket)::numeric
      / NULLIF((SELECT COUNT(*) FROM series), 0),
      4
    ),
    0
  )
$fn$
"""

_DROP_UPTIME_FN = "DROP FUNCTION IF EXISTS public.yvy_network_uptime_30d(timestamptz)"


# v_network_status corregida: idéntica a la 0003 salvo el CTE "uptime".
_V_NETWORK_STATUS_0004 = """
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
  synced_ever AS (
    SELECT DISTINCT source_id
    FROM bronze.raw_record
    WHERE record_kind = 'sensor_reading'
  ),
  last_sync AS (
    SELECT MAX(received_at) AS max_received_at
    FROM bronze.raw_record
    WHERE source_type = 'weather_station'
      AND source_id IN (SELECT source_id FROM active_sources)
  ),
  total_records AS (
    SELECT COUNT(*) AS n
    FROM bronze.raw_record
    WHERE source_type IN ('weather_station', 'human_observation')
      AND source_id IN (SELECT source_id FROM active_sources)
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
    SELECT public.yvy_network_uptime_30d(now()) AS ratio
  )
-- Solo cuentan estaciones activas (active = true). Las inactivas (p. ej.
-- TEST-00) no aparecen en ningún indicador público.
-- offline: activas que alguna vez enviaron, pero no en las últimas 2 h.
-- pending: activas que nunca enviaron (mismo criterio que status = 'pending'
-- en v_station_summary).
SELECT
  (SELECT COUNT(*) FROM active_sources)::int             AS total_nodes,
  (SELECT COUNT(*) FROM recent_syncs
   WHERE source_id IN (SELECT source_id FROM active_sources))::int
                                                         AS active_nodes,
  (SELECT COUNT(*) FROM active_sources
   WHERE source_id IN (SELECT source_id FROM synced_ever)
     AND source_id NOT IN (SELECT source_id FROM synced_2h))::int
                                                         AS offline_nodes,
  (SELECT COUNT(*) FROM active_sources
   WHERE source_id NOT IN (SELECT source_id FROM synced_ever))::int
                                                         AS pending_nodes,
  (SELECT n FROM total_records)::bigint                  AS total_records,
  (SELECT cnt FROM localities)::int                      AS localities_count,
  EXTRACT(EPOCH FROM (now() - (SELECT max_received_at FROM last_sync)))::int
                                                         AS last_sync_seconds,
  COALESCE((SELECT ratio FROM uptime), 0)                AS uptime_30d,
  now()                                                  AS generated_at
"""

# Copia literal de _V_NETWORK_STATUS de la 0003 (para el downgrade). Un test
# unitario verifica que sea idéntica a la de 003_m0_canonical_model.py.
_V_NETWORK_STATUS_0003 = """
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
  synced_ever AS (
    SELECT DISTINCT source_id
    FROM bronze.raw_record
    WHERE record_kind = 'sensor_reading'
  ),
  last_sync AS (
    SELECT MAX(received_at) AS max_received_at
    FROM bronze.raw_record
    WHERE source_type = 'weather_station'
      AND source_id IN (SELECT source_id FROM active_sources)
  ),
  total_records AS (
    SELECT COUNT(*) AS n
    FROM bronze.raw_record
    WHERE source_type IN ('weather_station', 'human_observation')
      AND source_id IN (SELECT source_id FROM active_sources)
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
        AND source_id IN (SELECT source_id FROM active_sources)
    ) b ON b.bucket = g.bucket
  )
-- Solo cuentan estaciones activas (active = true). Las inactivas (p. ej.
-- TEST-00) no aparecen en ningún indicador público.
-- offline: activas que alguna vez enviaron, pero no en las últimas 2 h.
-- pending: activas que nunca enviaron (mismo criterio que status = 'pending'
-- en v_station_summary).
SELECT
  (SELECT COUNT(*) FROM active_sources)::int             AS total_nodes,
  (SELECT COUNT(*) FROM recent_syncs
   WHERE source_id IN (SELECT source_id FROM active_sources))::int
                                                         AS active_nodes,
  (SELECT COUNT(*) FROM active_sources
   WHERE source_id IN (SELECT source_id FROM synced_ever)
     AND source_id NOT IN (SELECT source_id FROM synced_2h))::int
                                                         AS offline_nodes,
  (SELECT COUNT(*) FROM active_sources
   WHERE source_id NOT IN (SELECT source_id FROM synced_ever))::int
                                                         AS pending_nodes,
  (SELECT n FROM total_records)::bigint                  AS total_records,
  (SELECT cnt FROM localities)::int                      AS localities_count,
  EXTRACT(EPOCH FROM (now() - (SELECT max_received_at FROM last_sync)))::int
                                                         AS last_sync_seconds,
  COALESCE((SELECT ratio FROM uptime), 0)                AS uptime_30d,
  now()                                                  AS generated_at
"""


def upgrade() -> None:
    op.execute(_UPTIME_FN)
    op.execute(_V_NETWORK_STATUS_0004)


def downgrade() -> None:
    # Primero la vista (deja de depender de la función), después la función.
    op.execute(_V_NETWORK_STATUS_0003)
    op.execute(_DROP_UPTIME_FN)
