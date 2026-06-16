# YVY · DQS — Actualización a v3 (modelo canónico)

**Versión:** 3.0
**Fecha:** 15-Jun-2026
**Reemplaza:** `YVY_DQS_Q1_RUNBOOK_v2.md`
**Depende de:** `RUNBOOK_CICDv3.md` (M0) aplicado.
**Estado:** PROPUESTA — el DQS Q1 NO está implementado en producción, por lo que esta
actualización solo modifica documentación. No hay migración de datos de DQS que revertir.

---

## 0. Por qué cambia

El DQS v2 fue diseñado para leer `bronze.sensor_raw` y escribir `station_quality_daily`. Tras
M0 (modelo canónico), esa tabla ya no existe y Bronze es genérico (`bronze.raw_record`). El DQS
v3 lee la **Silver canónica** y generaliza de "estación" a "source", de modo que el mismo
pipeline de calidad sirva mañana para sensores de suelo, agua o cualquier fuente, sin reescribir
la lógica de scoring.

**Lo que NO cambia:** la lógica de los scores (availability, gap, range, outlier), los systemd
timers, los runs parciales AM/PM, los umbrales, las clasificaciones. Todo eso es válido y se
conserva. Cambian las fuentes de lectura/escritura y el modelo de granularidad.

---

## 1. Diferencias v2 → v3

| Punto | v2 | v3 |
|---|---|---|
| Fuente de observaciones | `bronze.sensor_raw` | `silver_proj.meteorology` (proyección) o `silver.measurement` filtrada |
| Tabla de salida | `public.station_quality_daily` | `public.source_quality_daily` |
| Granularidad | por estación-día | por **source**-día (estación es un caso) |
| `range_config` | tabla `quality_range_config` con nombres `temp_outdoor`, etc. | sigue, pero las variables se referencian por `variable_key` canónico |
| Score por observación | no existía (deuda registrada) | **se computa en la promoción a Silver** y vive en `silver.measurement.quality_score` |
| `dqs_internal` | suma ponderada de 4 sub-scores | `AVG(quality_score)` de las observaciones del día (una sola fuente de verdad) |
| `reference_score`/`drift_score` | NULL hasta Q3 | poblados en D2 con Open-Meteo (`source_type='external_api'`) |
| Dependencia de Silver | ninguna (leía Bronze) | el DQS pasa a ser consumidor de Silver (fase D2) |

---

## 2. Reubicación temporal en el plan maestro

El DQS deja de ser una fase aislada "Q1" y se integra al Track DATA:

- **El cómputo de quality por observación** (flags + score por measurement) se hace en la
  **promoción a Silver (D1)**, no en un pipeline DQS separado.
- **El agregado source-día** (`source_quality_daily`) y la **referencia externa**
  (Open-Meteo, drift) son la fase **D2**.

Esto elimina la duplicación: en v2 el DQS recalculaba outliers/ranges leyendo Bronze; en v3 esos
cálculos ya están hechos a nivel observación en Silver, y el daily es agregación barata.

---

## 3. Quality por observación (nuevo, en promoción Silver — D1)

Durante la promoción Bronze→Silver, cada measurement recibe flags y score. Las reglas son las
mismas del DQS v2 pero aplicadas por fila en vez de por día:

```python
# flags por measurement (string array en silver.measurement.quality_flags)
'range_ok'          # dentro del range_config para esa variable_key
'range_violation'   # fuera del range_config
'frozen_sensor'     # variable sin cambio en ventana móvil (umbral por variable)
'outlier_local'     # >3σ vs últimas N obs de la misma source+variable
'outlier_neighbors' # >3σ vs vecinos en ventana (Gold-dependent, difiere a D9)
'reference_drift'   # difiere >X% de Open-Meteo del mismo bucket (D2)
'imputed'           # interpolado, no medido

quality_score = max(0, min(100,
    100
    - 30 * has('range_violation')
    - 25 * has('frozen_sensor')
    - 15 * has('outlier_local')
    - 15 * has('outlier_neighbors')
    - 10 * has('reference_drift')
    - 20 * has('imputed')
))
```

`range_config` se lee de `quality_range_config`, ahora referenciando `variable_key` canónico en
vez de los nombres meteorológicos planos. Actualizar el seed:

```sql
-- quality_range_config con variable_key canónico
-- (rangos de QC, distintos de los physical_min/max del catálogo canónico)
ALTER TABLE public.quality_range_config
    ADD COLUMN variable_key text REFERENCES canonical.variable;
-- backfill del mapeo nombre viejo -> variable_key, luego usar variable_key como referencia
UPDATE public.quality_range_config SET variable_key = CASE variable
    WHEN 'temp_outdoor'  THEN 'air_temperature'
    WHEN 'temp_indoor'   THEN 'temp_indoor'
    WHEN 'hum_outdoor'   THEN 'relative_humidity'
    WHEN 'hum_indoor'    THEN 'hum_indoor'
    WHEN 'pressure_abs'  THEN 'atmospheric_pressure'
    WHEN 'wind_ave'      THEN 'wind_speed'
    WHEN 'wind_gust'     THEN 'wind_gust'
    WHEN 'wind_dir'      THEN 'wind_direction'
END;
```

> **Frozen sensor**: el DQS v2 solo lo registraba en `notes` cuando `std=0`. En v3 es un flag
> por observación con umbrales por variable (temp idéntica >30min, humedad >60min, presión
> >120min → frozen). Mejora real para serving institucional.

---

## 4. Agregado source-día (D2) — `source_quality_daily`

Reemplaza `station_quality_daily`. Misma estructura, `station_uuid` → `source_id`.

```sql
CREATE TABLE public.source_quality_daily (
    id                      BIGSERIAL PRIMARY KEY,
    source_id               UUID NOT NULL REFERENCES canonical.source(source_id),
    date                    DATE NOT NULL,

    window_start            TIMESTAMPTZ NOT NULL,
    window_end              TIMESTAMPTZ NOT NULL,
    window_minutes          NUMERIC(8,2) NOT NULL,

    observations_expected   INTEGER NOT NULL,
    observations_received   INTEGER NOT NULL,
    availability_score      NUMERIC(5,2),
    gap_count               INTEGER,
    gap_total_minutes       NUMERIC(8,2),
    gap_score               NUMERIC(5,2),
    range_violations        INTEGER,
    range_score             NUMERIC(5,2),
    outlier_count           INTEGER,
    outlier_score           NUMERIC(5,2),

    reference_score         NUMERIC(5,2),   -- poblado en D2 (Open-Meteo)
    drift_score             NUMERIC(5,2),   -- poblado en D2

    dqs_internal            NUMERIC(5,2),   -- AVG(quality_score) de las obs del día
    dqs_total               NUMERIC(5,2),   -- con reference/drift (D2+)
    dqs_classification      TEXT CHECK (dqs_classification IN
                                ('EXCELENTE','BUENA','ACEPTABLE','SOSPECHOSA','CRITICA')),

    computed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    computation_version     TEXT NOT NULL,
    notes                   TEXT,

    UNIQUE (source_id, date)
);
CREATE INDEX idx_sqd_source_date   ON public.source_quality_daily (source_id, date DESC);
CREATE INDEX idx_sqd_date          ON public.source_quality_daily (date DESC);
CREATE INDEX idx_sqd_class         ON public.source_quality_daily (dqs_classification, date DESC);
```

### Cálculo de `dqs_internal` en v3

```python
# v2: suma ponderada normalizada de availability/gap/range/outlier (pesos 0.75)
# v3: el daily es agregación de los quality_score por observación ya calculados en Silver
dqs_internal = round(AVG(m.quality_score for m in day_measurements), 2)
```

Los sub-scores (availability, gap, range, outlier) **se siguen calculando y persistiendo** para
explicabilidad y para el endpoint `/explain`, pero `dqs_internal` ya no es su suma ponderada:
es el promedio de la calidad por observación. Una sola fuente de verdad, coherente con lo que un
cliente descarga fila por fila.

---

## 5. CLI y scheduler

El CLI `compute-quality` pasa a `compute-source-quality` (o se mantiene el nombre con
`--source-uuid` en vez de `--station-uuid`). Los systemd timers v2 (daily 02:00, parcial AM
09:00, parcial PM 21:00, todos `Persistent=true`) **se conservan idénticos**, solo cambia el
comando que invocan.

```ini
# yvy-dqs-daily.service (sin cambios salvo el comando)
ExecStart=docker compose -f /opt/yvy/docker-compose.yml exec -T app \
    poetry run yvy-server compute-source-quality \
    --mode complete --date yesterday --all-active-sources
```

> El pipeline depende ahora de que la promoción a Silver (D1) haya corrido. Orden de timers:
> promoción Silver (cada 5min) corre siempre antes que el daily DQS (02:00). El daily lee Silver,
> no Bronze.

---

## 6. Exposición en API (D4)

```
GET /api/v1/public/quality/network?period=30d        # DQS medio de la red, anonimizado
GET /api/v1/inst/sources/{id}/quality?period=90d      # [auth] serie con breakdown por flag
GET /api/v1/inst/quality/explain                       # documento versionado del método
```

El endpoint `/explain` es citable (un cliente lo referencia en un paper). Versionado por
`computation_version` / `method_version`.

---

## 7. Lo que se conserva sin cambios de v2

- Lógica de `availability_score`, `gap_score`, `range_score`, `outlier_score` (fórmulas).
- Z-score threshold 3.0, gap threshold 5min, capture interval 1min.
- Clasificaciones (EXCELENTE 95-100, etc.).
- Runs parciales AM/PM con UPSERT, el PM pisa al AM, el daily pisa al parcial.
- `window_minutes` efectivo (no hardcode 1440).
- Variables de entorno DQS (`YVY_DQS_*`).
- Tests unit/integration (adaptar nombres station→source).
- Migración futura a Airflow: el DAG llama al mismo CLI command.

---

## 8. Criterios de cierre v3 (cuando se implemente en D1+D2)

- [ ] Promoción Silver computa `quality_flags` y `quality_score` por measurement.
- [ ] `source_quality_daily` poblada con `dqs_internal = AVG(quality_score)`.
- [ ] `quality_range_config` referencia `variable_key` canónico.
- [ ] Sub-scores persistidos para explicabilidad.
- [ ] `reference_score`/`drift_score` poblados con Open-Meteo (D2).
- [ ] Timers conservan comportamiento, invocan el comando nuevo.
- [ ] DQS de IEA-07 cae en BUENA/EXCELENTE (validación manual, igual que v2).
- [ ] Endpoint `/explain` versionado disponible.

---

**Fin · DQS v3 (alineado al modelo canónico)**

Esta actualización se implementa como parte de D1 (quality por observación) y D2 (agregado +
referencia externa) del Track DATA. No es una fase aislada.
