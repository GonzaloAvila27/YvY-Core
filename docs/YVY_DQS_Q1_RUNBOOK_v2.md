# YVY Core — DQS Fase Q1 · Runbook v2
## Infraestructura base + métricas internas

**Versión:** 2.0 (corrige DQS Plan v0.1)
**Fecha:** 11-Jun-2026
**Estado:** APROBADO ARQUITECTÓNICAMENTE — listo para implementar
**Repositorio:** `yvy_server`
**Depende de:** Fases 9.1, 9.2, 9.3 cerradas ✅

---

## Cambios respecto al plan v0.1

| Punto | v0.1 | v2.0 |
|---|---|---|
| Scheduler | `asyncio` background task en lifespan FastAPI | `systemd timer` × 3 con `Persistent=true` |
| CLI command | No existía | `yvy-server compute-quality` (entry point del timer) |
| Runs parciales | 1 run/hora (sobrecarga) | 2 parciales/día: 09:00 y 21:00 UTC-3 |
| `outlier_score` parcial | Sin aclaración | Computable en ambos parciales (540+ obs a las 21:00) |
| `raw_response JSONB` | Incluido en `reference_weather` | Eliminado; no existe en Q1 |
| SMN | En scope Q2 | Eliminado del plan completo |

---

## Decisiones de diseño confirmadas (D1–D6)

| # | Decisión | Confirmado |
|---|---|---|
| D1 | Módulo DQS dentro de `yvy_server` (hexagonal) | ✅ |
| D2 | Scheduler: systemd timer (no asyncio, no proceso externo distinto al app) | ✅ corregido |
| D3 | Granularidad diaria por estación en v1 | ✅ |
| D4 | Referencias externas en batch diario (Open-Meteo únicamente) | ✅ |
| D5 | Estaciones `active=false` no se procesan | ✅ |
| D6 | `drift_score = NULL` hasta 30 días, infraestructura creada desde Q1 | ✅ |

---

## Scheduler — systemd timers

### Diseño

Tres timers. Los tres con `Persistent=true`: si el servidor estaba apagado a la hora objetivo, corre el job inmediatamente al levantar.

| Timer unit | Hora local (UTC-3) | UTC | Tipo | `notes` en DB |
|---|---|---|---|---|
| `yvy-dqs-daily.timer` | 02:00 | 05:00 | Completo día anterior | — (NULL) |
| `yvy-dqs-partial-am.timer` | 09:00 | 12:00 | Parcial día actual | `partial` |
| `yvy-dqs-partial-pm.timer` | 21:00 | 00:00 | Parcial día actual | `partial` |

El run diario hace UPSERT sobre la fila del día anterior → pisa cualquier parcial residual.
Los parciales hacen UPSERT sobre la fila del día actual → el PM pisa al AM.

### Archivos systemd

**`/etc/systemd/system/yvy-dqs-daily.service`**
```ini
[Unit]
Description=YVY DQS — cálculo completo día anterior
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
User=yvy
ExecStart=docker compose -f /opt/yvy/docker-compose.yml \
    exec -T app \
    poetry run yvy-server compute-quality \
    --mode complete \
    --date yesterday \
    --all-active-stations
Environment=YVY_DQS_PIPELINE_VERSION=1.0
```

**`/etc/systemd/system/yvy-dqs-daily.timer`**
```ini
[Unit]
Description=YVY DQS daily timer

[Timer]
OnCalendar=*-*-* 05:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

**`/etc/systemd/system/yvy-dqs-partial-am.service`**
```ini
[Unit]
Description=YVY DQS — cálculo parcial AM (09:00 UTC-3)

[Service]
Type=oneshot
User=yvy
ExecStart=docker compose -f /opt/yvy/docker-compose.yml \
    exec -T app \
    poetry run yvy-server compute-quality \
    --mode partial \
    --date today \
    --all-active-stations
```

**`/etc/systemd/system/yvy-dqs-partial-am.timer`**
```ini
[Timer]
OnCalendar=*-*-* 12:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

**`/etc/systemd/system/yvy-dqs-partial-pm.service`**
```ini
[Unit]
Description=YVY DQS — cálculo parcial PM (21:00 UTC-3)

[Service]
Type=oneshot
User=yvy
ExecStart=docker compose -f /opt/yvy/docker-compose.yml \
    exec -T app \
    poetry run yvy-server compute-quality \
    --mode partial \
    --date today \
    --all-active-stations
```

**`/etc/systemd/system/yvy-dqs-partial-pm.timer`**
```ini
[Timer]
OnCalendar=*-*-* 00:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

### Activación

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now yvy-dqs-daily.timer
sudo systemctl enable --now yvy-dqs-partial-am.timer
sudo systemctl enable --now yvy-dqs-partial-pm.timer

# Verificar
systemctl list-timers | grep yvy-dqs
```

### Migración futura a Airflow

Cuando llegue Airflow, el DAG llama al mismo CLI command o importa el mismo use case.
Los `.timer` se deshabilitan; el pipeline DQS no cambia nada.

---

## Modelo de datos Q1

### Tabla `public.station_quality_daily`

```sql
CREATE TABLE public.station_quality_daily (
    id                      BIGSERIAL PRIMARY KEY,
    station_uuid            UUID NOT NULL REFERENCES station(station_uuid),
    date                    DATE NOT NULL,

    -- ventana efectiva
    window_start            TIMESTAMPTZ NOT NULL,
    window_end              TIMESTAMPTZ NOT NULL,
    window_minutes          NUMERIC(8,2) NOT NULL,  -- efectivo, no siempre 1440

    -- métricas internas (Q1)
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

    -- métricas externas (Q3, NULL hasta entonces)
    reference_score         NUMERIC(5,2),
    drift_score             NUMERIC(5,2),

    -- score compuesto
    dqs_internal            NUMERIC(5,2),   -- 0-100, disponible desde Q1
    dqs_total               NUMERIC(5,2),   -- 0-100, disponible desde Q3
    dqs_classification      TEXT CHECK (dqs_classification IN (
                                'EXCELENTE','BUENA','ACEPTABLE',
                                'SOSPECHOSA','CRITICA')),

    -- trazabilidad
    computed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    computation_version     TEXT NOT NULL,
    notes                   TEXT,           -- NULL = completo, 'partial' = parcial

    UNIQUE (station_uuid, date)
);

CREATE INDEX idx_sqd_station_date     ON public.station_quality_daily (station_uuid, date DESC);
CREATE INDEX idx_sqd_date             ON public.station_quality_daily (date DESC);
CREATE INDEX idx_sqd_classification   ON public.station_quality_daily (dqs_classification, date DESC);
```

**Nota sobre `window_*`:** campos explícitos en lugar de asumir 1440 min. Para días parciales y para el primer día de operación de una estación, la ventana efectiva es menor. Todos los cálculos usan `window_minutes` de esta fila, no un hardcode.

### Tabla `public.quality_range_config`

```sql
CREATE TABLE public.quality_range_config (
    id          SERIAL PRIMARY KEY,
    variable    TEXT NOT NULL UNIQUE,
    range_min   NUMERIC NOT NULL,
    range_max   NUMERIC NOT NULL,
    warn_min    NUMERIC,
    warn_max    NUMERIC,
    unit        TEXT NOT NULL,
    notes       TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Seed inicial (Misiones):**

```sql
INSERT INTO public.quality_range_config
    (variable, range_min, range_max, unit, notes) VALUES
    ('temp_outdoor',  -5,   50,   '°C',      NULL),
    ('temp_indoor',    0,   60,   '°C',      NULL),
    ('hum_outdoor',    0,  100,   '%',       NULL),
    ('hum_indoor',     0,  100,   '%',       NULL),
    ('pressure_abs', 980, 1040,   'hPa',     'ajustable por altitud IEA'),
    ('wind_ave',       0,   60,   'm/s',     NULL),
    ('wind_gust',      0,   80,   'm/s',     NULL),
    ('wind_dir',       0,   15,   'índice',  '0-15 WH1081');
```

---

## Arquitectura hexagonal — ubicación de módulos

```
src/yvy_server/
├── domain/
│   └── quality/
│       ├── __init__.py
│       ├── metrics.py          # funciones puras: availability, gaps, range, outliers
│       ├── models.py           # VOs: DailyQualityResult, RangeConfig, QualityWindow
│       └── exceptions.py       # InsufficientDataError, NoObservationsError
│
├── application/
│   └── use_cases/
│       └── quality/
│           ├── __init__.py
│           └── compute_daily_quality.py   # orquesta domain + infra
│
├── infrastructure/
│   └── quality/
│       ├── __init__.py
│       ├── repositories.py         # QualityRepository: upsert station_quality_daily
│       └── range_config_repo.py    # RangeConfigRepository: load desde DB
│
├── presentation/
│   └── api/
│       └── routers/
│           └── quality.py          # vacío en Q1, se implementa en Q4
│
└── cli/
    └── quality.py                  # `yvy-server compute-quality` — entry point del timer
```

### CLI command `compute-quality`

```python
# cli/quality.py (typer, sync — igual patrón que create-station)

@app.command()
def compute_quality(
    mode: str = typer.Option("complete", help="complete | partial"),
    date: str = typer.Option("yesterday", help="YYYY-MM-DD | yesterday | today"),
    station_uuid: Optional[str] = typer.Option(None),
    all_active_stations: bool = typer.Option(False),
    pipeline_version: str = typer.Option("1.0"),
) -> None:
    """Calcula DQS para una o todas las estaciones activas."""
    ...
```

El command resuelve `date` a un objeto `datetime.date` antes de pasar al use case.
`--all-active-stations` y `--station-uuid` son mutuamente excluyentes (validación en CLI).

---

## Lógica de cálculo (domain puro)

### `QualityWindow` — VO de entrada

```python
@dataclass(frozen=True)
class QualityWindow:
    station_uuid: UUID
    date: date
    mode: Literal["complete", "partial"]  # determina notes en DB
    window_start: datetime
    window_end: datetime
    window_minutes: Decimal
    observations: list[SensorObservation]  # cargadas por el use case desde bronze
    range_configs: list[RangeConfig]       # cargadas desde quality_range_config
```

### `availability_score`

```python
expected = window_minutes / capture_interval_minutes  # capture_interval = 1
received = len(observations)
score = round((received / expected) * 100, 2) if expected > 0 else 0.0
```

`capture_interval_minutes = 1` hardcodeado en Q1. Config por estación en Q2+.

### `gap_score`

```python
GAP_THRESHOLD_MIN = 5  # configurable vía env YVY_DQS_GAP_THRESHOLD_MIN

timestamps = sorted(obs.captured_at for obs in observations)
gaps = [
    (timestamps[i+1] - timestamps[i]).total_seconds() / 60
    for i in range(len(timestamps) - 1)
    if (timestamps[i+1] - timestamps[i]).total_seconds() / 60 > GAP_THRESHOLD_MIN
]
gap_total_minutes = sum(gaps)
penalty = min(gap_total_minutes / float(window_minutes) * 100, 100)
score = round(100 - penalty, 2)
```

Usa `window_minutes` del `QualityWindow`, no 1440 hardcodeado.

### `range_score`

```python
violations = sum(
    1 for obs in observations
    if any(
        not (cfg.range_min <= getattr(obs, cfg.variable) <= cfg.range_max)
        for cfg in range_configs
        if getattr(obs, cfg.variable, None) is not None
    )
)
score = round((1 - violations / received) * 100, 2) if received > 0 else 0.0
```

### `outlier_score`

Z-score por variable. Si `std = 0` → sensor congelado → se registra en `notes`.

```python
ZSCORE_THRESHOLD = 3.0  # configurable vía env YVY_DQS_OUTLIER_ZSCORE_THRESHOLD

outlier_obs = set()
for variable in MONITORED_VARIABLES:
    values = [getattr(obs, variable) for obs in observations
              if getattr(obs, variable) is not None]
    if len(values) < 2:
        continue
    mean = statistics.mean(values)
    std = statistics.stdev(values)
    if std == 0:
        # sensor congelado — no es outlier estadístico, es anomalía de tipo distinto
        # se registra en notes, no penaliza outlier_score
        continue
    for i, obs in enumerate(observations):
        v = getattr(obs, variable)
        if v is not None and abs(v - mean) / std > ZSCORE_THRESHOLD:
            outlier_obs.add(i)

outlier_count = len(outlier_obs)
score = round((1 - outlier_count / received) * 100, 2) if received > 0 else 0.0
```

**Comportamiento en parciales:** con 540 observaciones al run de 21:00, la std es estable.
Con 180 obs al run AM de 09:00, la std es aceptable para detección burda.
El `outlier_score` se computa en ambos parciales sin restricción adicional.

### `dqs_internal` — score parcial normalizado

```python
WEIGHTS_INTERNAL = {
    "availability": 0.25,
    "gap":          0.15,
    "range":        0.15,
    "outlier":      0.20,
}
# suma = 0.75

raw = (
    availability_score * WEIGHTS_INTERNAL["availability"] +
    gap_score          * WEIGHTS_INTERNAL["gap"] +
    range_score        * WEIGHTS_INTERNAL["range"] +
    outlier_score      * WEIGHTS_INTERNAL["outlier"]
)
dqs_internal = round(raw / 0.75, 2)  # normalizado a 0-100
```

### `dqs_classification`

Aplicada sobre `dqs_internal` en Q1, sobre `dqs_total` en Q3+.

| Score | Clasificación |
|---|---|
| 95–100 | EXCELENTE |
| 85–94 | BUENA |
| 70–84 | ACEPTABLE |
| 50–69 | SOSPECHOSA |
| 0–49 | CRITICA |

---

## Tests obligatorios Q1

### Unit (`tests/unit/quality/`)

| Test | Cubre |
|---|---|
| `test_availability_score` | día completo, parcial, 0 observaciones |
| `test_gap_score` | sin gaps, gap único, múltiples, gap total > ventana |
| `test_gap_score_uses_window_minutes` | verifica que usa `QualityWindow.window_minutes`, no 1440 |
| `test_range_score` | todas OK, 1 violación, todas violadas, variable NULL ignorada |
| `test_outlier_score_normal` | distribución normal sin outliers → 100 |
| `test_outlier_score_clear_outlier` | 1 outlier 10σ detectado |
| `test_outlier_score_frozen_sensor` | std=0 → no penaliza score, registra en notes |
| `test_outlier_score_insufficient_data` | < 2 obs → no computa, no rompe |
| `test_dqs_internal_normalization` | max → 100, cero → 0 |
| `test_dqs_internal_weights_sum` | suma de pesos = 0.75 exacto |
| `test_classification_boundaries` | 95/85/70/50 → boundary exacto |
| `test_partial_mode_sets_notes` | mode=partial → `notes='partial'` en result |

### Integration (`tests/integration/quality/`)

| Test | Cubre |
|---|---|
| `test_upsert_idempotency` | pipeline 2 veces mismo día → 1 fila, no duplicados |
| `test_partial_then_complete` | parcial AM → parcial PM → completo nocturno → fila final sin `notes` |
| `test_station_inactive_skipped` | `active=false` no genera fila |
| `test_migration_q1_applies_clean` | `alembic upgrade head` desde 001 no rompe |
| `test_range_config_seed_present` | 8 variables en `quality_range_config` post-migración |
| `test_pipeline_real_iea7_data` | corre sobre datos reales IEA7, no lanza excepción, score en rango BUENA/EXCELENTE |

---

## Variables de entorno nuevas

```bash
# .env — additions DQS Q1
YVY_DQS_ENABLED=true
YVY_DQS_PIPELINE_VERSION=1.0
YVY_DQS_CAPTURE_INTERVAL_MIN=1
YVY_DQS_GAP_THRESHOLD_MIN=5
YVY_DQS_OUTLIER_ZSCORE_THRESHOLD=3.0
YVY_DQS_DRIFT_MIN_DAYS=30
```

No hay variables de cron — el scheduler vive en systemd, no en la app.

---

## Migración Alembic

Archivo: `alembic/versions/002_dqs_q1.py`

Crear las dos tablas + seed de `quality_range_config` + índices.
No tocar ninguna tabla existente del schema 001.

```bash
# Generar esqueleto
poetry run alembic revision --autogenerate -m "dqs_q1"

# Revisar el archivo generado antes de aplicar
# Aplicar
poetry run alembic upgrade head

# Verificar sin drift
poetry run alembic check
```

---

## Comandos de validación

### Post-migración

```bash
# Tablas creadas
docker compose exec postgres psql -U yvy -d yvy -c \
  "\dt public.station_quality_daily public.quality_range_config"

# Seed presente
docker compose exec postgres psql -U yvy -d yvy -c \
  "SELECT variable, range_min, range_max FROM quality_range_config ORDER BY variable;"
```

### Pipeline manual sobre IEA7

```bash
# Run completo día anterior
poetry run yvy-server compute-quality \
  --mode complete \
  --date yesterday \
  --station-uuid <iea7-uuid>

# Run completo fecha específica (backfill manual)
poetry run yvy-server compute-quality \
  --mode complete \
  --date 2026-05-24 \
  --station-uuid <iea7-uuid>
```

### Verificar resultado

```bash
docker compose exec postgres psql -U yvy -d yvy -c "
SELECT
    date,
    window_minutes,
    observations_received,
    availability_score,
    gap_score,
    range_score,
    outlier_score,
    dqs_internal,
    dqs_classification,
    notes
FROM station_quality_daily
WHERE station_uuid = '<iea7-uuid>'
ORDER BY date DESC
LIMIT 10;
"
```

### Tests

```bash
poetry run pytest tests/unit/quality/ -v
poetry run pytest tests/integration/quality/ -v
```

### Verificar timers

```bash
# Estado de los timers
systemctl list-timers | grep yvy-dqs

# Log de ejecuciones
journalctl -u yvy-dqs-daily.service -n 20
journalctl -u yvy-dqs-partial-am.service -n 5
journalctl -u yvy-dqs-partial-pm.service -n 5
```

---

## Criterios de cierre Q1

- [ ] `alembic upgrade head` aplica sin errores desde schema 001
- [ ] `alembic check` sin drift
- [ ] 8 variables en `quality_range_config`
- [ ] Pipeline corre sobre datos reales de IEA7 (24-May en adelante) sin excepción
- [ ] `SELECT * FROM station_quality_daily WHERE station_uuid = '<iea7>'` → 1 fila por día desde 24-May
- [ ] `dqs_internal` de IEA7 cae en BUENA o EXCELENTE (validación manual)
- [ ] Test `test_upsert_idempotency` verde
- [ ] Test `test_partial_then_complete` verde
- [ ] Todos los tests unit e integration verdes
- [ ] Tres timers activos (`systemctl list-timers` los muestra)
- [ ] Run manual de los tres modos (complete, partial AM, partial PM) sin error

---

## Orden de implementación recomendado

```
Día 1  — migración 002 + seed + alembic check
Día 2  — domain: QualityWindow VO + métricas puras + tests unit
Día 3  — infrastructure: repositories (upsert) + range_config_repo
Día 4  — use case compute_daily_quality + CLI command compute-quality
Día 5  — tests integration + validación manual sobre IEA7
Día 6  — systemd timers + activación + primer run automático monitoreado
```

---

## Lo que NO entra en Q1

- Endpoints REST → Q4
- Open-Meteo client → Q2
- `reference_score`, `drift_score`, `dqs_total` → Q3
- Score por observación individual → deuda técnica registrada
- SMN → eliminado del plan
- `raw_response JSONB` → eliminado del plan
