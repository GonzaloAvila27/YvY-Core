# YVY · Plan Web + Core — Actualización a v3 (modelo canónico)

**Versión:** 3.0
**Fecha:** 15-Jun-2026
**Reemplaza:** `YVY_Plan_Fases_Web_Core_v2.md` (las fases siguen vigentes; este documento
registra solo los cambios que introduce M0).
**Depende de:** `RUNBOOK_CICDv3.md` (M0) aplicado.

---

## 0. Tesis de esta actualización

**El refactor canónico (M0) es invisible para la Web.** El contrato de datos
(`DATA-CONTRACT.md`, Apéndice A del plan v2) NO cambia: cada endpoint público devuelve el mismo
JSON, byte a byte. Lo único que cambia es la **fuente interna** de las views y endpoints: dejan
de leer `bronze.sensor_raw` (que ya no existe tras M0) y pasan a leer el modelo canónico
(`bronze.raw_record` + `canonical.source` + Silver).

Esto significa: las fases Web (W0-W6) **no se tocan**. Siguen consumiendo `api.yvyedge.com` con
el mismo contrato. La Web V1 no se entera del refactor.

---

## 1. Cambios por fase Core

| Fase | v2 | v3 |
|---|---|---|
| C10 (✅ cerrada) | Views `v_network_status`, `v_station_summary` sobre `bronze.sensor_raw` | **Re-apuntadas** a `canonical.source` + `bronze.raw_record`. Contrato idéntico. Va dentro de M0. |
| C11 (✅ cerrada) | Endpoints `/public/network`, `/public/stations` | Sin cambios. Leen las views re-apuntadas. |
| C12 | Views/funciones de métricas sobre Bronze | Leen `silver_proj.meteorology` (tras D1). Mientras D1 no esté, pueden seguir sobre `raw_record`. |
| C13 | VPD/dewpoint como funciones SQL on-the-fly | Las derivadas ya están persistidas en Silver (`is_derived=true`). C13 lee Silver. Las funciones SQL quedan como fallback. |
| C14-C15 | Novedades/Insights (tablas propias) | Sin cambios — son contenido editorial, no datos de sensor. |
| C16 | Cache in-memory para `/public/*` | Sin cambios para público. Para `data.yvyedge.com` (Track DATA): cache de nginx por API key, NO cachetools. |

---

## 2. C10 re-apuntada — detalle

Las dos views se reescriben para leer del modelo canónico. El esquema fue introducido en
`RUNBOOK_CICDv3.md` §6. Resumen del cambio de FROM:

### `v_station_summary`

```sql
-- v2: FROM public.station + último payload de bronze.sensor_raw
-- v3: FROM canonical.source (source_type='weather_station') + último raw_record del source
--     code/locality/coords salen de source.metadata
--     lastValues sale del payload del último raw_record (record_kind='sensor_reading')
```

### `v_network_status`

```sql
-- v2: COUNT sobre public.station + bronze.sensor_raw/empirical_observation_raw
-- v3: COUNT sobre canonical.source (source_type='weather_station') + bronze.raw_record
--     total_records cuenta raw_record por source_type apropiado
--     uptime_30d: buckets de 5min con al menos 1 raw_record de un source weather_station
```

### Garantía de no-regresión

Antes de M0, tomar snapshot del JSON de los endpoints:

```bash
curl -s https://api.yvyedge.com/api/v1/public/network  > /tmp/snap_network_pre_m0.json
curl -s https://api.yvyedge.com/api/v1/public/stations > /tmp/snap_stations_pre_m0.json
```

Tras M0, comparar:

```bash
curl -s https://api.yvyedge.com/api/v1/public/network  | diff - /tmp/snap_network_pre_m0.json
curl -s https://api.yvyedge.com/api/v1/public/stations | diff - /tmp/snap_stations_pre_m0.json
# Esperado: sin diferencias (salvo timestamps generatedAt / lastSyncSeconds que avanzan)
```

---

## 3. Quick win habilitado por M0+D1

Una vez Silver (D1) poblada, los endpoints `/public/dashboard` y `/public/metrics` (C12)
migran a leer `silver_proj.meteorology`:

- Latencia ~10x menor (Silver tipada y particionada vs parseo JSONB de Bronze).
- VPD/dewpoint ya persistidos, no recalculados por query.
- Reverse-compatible: el contrato no cambia.

Esto se hace en la implementación de C12 sobre Silver, no requiere cambio de la Web.

---

## 4. Decisiones cerradas nuevas (añadir a la tabla del plan v2)

| # | Decisión | Valor | Fecha |
|---|---|---|---|
| C17 | Fuente interna de views públicas | Modelo canónico (`canonical.source` + `bronze.raw_record` + Silver), contrato externo intacto | 15-06 |
| C18 | Derivadas (VPD/dewpoint) | Persistidas en Silver durante promoción (`is_derived`), no recalculadas por query | 15-06 |
| C19 | Cache para Track DATA institucional | nginx por API key (NO cachetools); cachetools queda solo para `/public/*` sin auth | 15-06 |
| C20 | Subdominio Track DATA | `data.yvyedge.com` separado de `api.yvyedge.com` | 15-06 |

---

## 5. Lo que NO cambia (tranquilidad)

- Todas las fases Web W0-W6 (mocks, mapa, dashboard, insights, launch).
- `DATA-CONTRACT.md` — los shapes de respuesta.
- Endpoints `/public/*` — rutas, parámetros, cache headers públicos.
- CORS, certificados, nginx server block de `api.yvyedge.com`.
- La secuencia de implementación Web↔Core.
- `edge.yvyedge.com` y el flujo de ingesta HMAC.

---

## 6. Orden de aplicación

1. M0 (`RUNBOOK_CICDv3.md`) incluye el re-apuntado de C10. Se aplica como un solo deploy.
2. Snapshot pre-M0 + diff post-M0 confirman no-regresión del contrato.
3. C12-C13 sobre Silver se implementan tras D1 (Track DATA), como mejora de latencia
   transparente.
4. La Web sigue su roadmap sin cambios.

---

**Fin · Web + Core v3 (alineado al modelo canónico)**

El cambio es deliberadamente mínimo y aislado: re-apuntar la fuente interna sin tocar el
contrato. La Web V1 procede sin modificaciones.
