# Plan de aplicación al servidor — YVY Core

Guía operativa para llevar los cambios al **servidor central de producción**, en
dos fases con riesgo escalonado:

- **FASE 1** — Deploy de `master` consolidado. **Sin cambios de datos.** Riesgo bajo.
- **FASE 2** — Landing de `plan_fases_api` (**M0 → C10 → C11**), con **migración de
  bronze** y **cambio de imagen de postgres**. Alto cuidado.

> **Regla de oro:** cada fase tiene **backup antes** y **verificación después**.
> No avanzar a la siguiente fase sin verificar la actual.
>
> Detalle técnico complementario: `RUNBOOK_M0_INSTALL.md`, `RUNBOOK_CICDv3.md`,
> `RUNBOOK_DEPLOY_CODE.md`.

---

## Contexto de arranque

- Producción hoy corre el código de `core-prod-actual` con **`postgres:16-alpine`**
  (plano, sin extensiones). Estable.
- `master` ya está **consolidado** = ese mismo código + fix `edge.py` (500 en
  `/edge/updates`) + Dockerfile Opción A (poetry solo exporta; runtime sin poetry).
- `master` **no cambia la base de datos** respecto a prod. Es un deploy de código.

---

# FASE 1 — Deploy de `master` consolidado

**Objetivo:** que producción corra `master` (con el fix del 500), **sin tocar
postgres ni los datos**.

> ⚠️ El `docker-compose.yml` de `master` define `postgres` con `build: ./postgres`
> (imagen PostGIS+pgvector). En Fase 1 **NO** queremos recrear postgres. Por eso se
> despliega **solo el servicio `app`** con `--no-deps`.

### 1.1 Backup liviano (por las dudas)
```bash
# Anotar la imagen/estado actual para rollback
docker compose ps
docker compose images
# Backup lógico rápido de la DB (no cambia nada, pero conviene)
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" \
  | gzip > backup_pre_fase1_$(date +%F).sql.gz
```

### 1.2 Traer el código y construir SOLO app
```bash
cd /ruta/al/repo         # server
git fetch origin
git checkout master
git pull --ff-only origin master

# Rebuild + recreate SOLO app (no toca postgres ni el volumen)
docker compose up -d --no-deps --build app
```

### 1.3 Verificación
```bash
# La app arrancó y migró (no hay migraciones nuevas en esta fase → no-op)
docker compose logs --tail=40 app

# Healthz (no toca DB)
curl -fsS http://localhost:8000/api/v1/healthz

# Versión desplegada
curl -fsS http://localhost:8000/api/v1/serverinfo

# El fix del 500: descargar un release debe responder 200/404, NO 500
#   (probar contra un filename real de edge_releases)
```
- [ ] `app` levantó sin errores
- [ ] `/healthz` OK
- [ ] `/serverinfo` muestra la versión esperada (no `unknown`)
- [ ] `GET /api/v1/edge/updates/<filename>` **ya no da 500**
- [ ] `postgres` **NO** fue recreado (mismo container ID / uptime que antes)

### 1.4 Rollback Fase 1
```bash
git checkout core-prod-actual      # o el commit previo
docker compose up -d --no-deps --build app
```
La DB no se tocó, así que el rollback es solo de la imagen `app`.

---

# FASE 2 — Landing de `plan_fases_api` (M0 → C10 → C11)

**Precondición:** Fase 1 aplicada y verificada (`master` == prod). `plan_fases_api`
**rebasado/mergeado sobre el `master` consolidado**, con estos fixes ya resueltos
en la rama antes de deployar:
- [ ] re-aplicado el fix `edge.py` (la rama traía el bug `filename`)
- [ ] `ci.yml` reconciliado (base master + fixes ruff/mypy) y **CI en verde**
- [ ] Dockerfile con Opción A (sin `poetry install`)
- [ ] errores **B904** de ruff corregidos

> La migración de M0 (`003_m0_canonical_model.py`) **es aditiva** (crea `canonical.*`
> y `bronze.raw_record`, backfillea `bronze.*_raw → raw_record` con verificación de
> conteos, deja el **contrato de ingest intacto**). **Hard-requiere `pgvector`**;
> PostGIS es graceful (latente).

---

## FASE 2A — Postgres con pgvector (data-preserving)

**Objetivo:** cambiar la imagen de postgres de `postgres:16-alpine` a una **con
pgvector** (`postgis/postgis:16-3.4` + pgvector, vía `./postgres/Dockerfile` de
master), **preservando el volumen de datos**. Mismo major (16) → el `postgres_data`
existente es compatible.

### 2A.1 Backup COMPLETO (obligatorio)
```bash
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" \
  > backup_pre_M0_$(date +%F_%H%M).dump
# Verificar que el archivo tiene tamaño razonable
ls -lh backup_pre_M0_*.dump
```

### 2A.2 Swap de imagen (sin borrar el volumen)
```bash
# El compose de master ya define postgres con build: ./postgres (PostGIS+pgvector)
docker compose build postgres
docker compose up -d postgres        # recrea el container, REUSA postgres_data

docker compose logs --tail=30 postgres   # debe arrancar OK con los datos existentes
```
> ⚠️ **NUNCA** correr `docker compose down -v` ni borrar el volumen `postgres_data`
> (perdería todos los datos). Solo se recrea el **container**, no el **volumen**.

### 2A.3 Verificar que pgvector está disponible
```bash
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "CREATE EXTENSION IF NOT EXISTS vector; SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector','postgis');"
```
- [ ] postgres levantó con los datos previos (contar filas de una tabla conocida)
- [ ] `vector` disponible (extversion listada)
- [ ] backup `.dump` guardado y verificado

---

## FASE 2B — Migración M0 (`003`)

> El `docker-entrypoint.sh` corre `alembic upgrade head` **automáticamente** al
> arrancar `app`. Para una migración grande conviene correrla **manual y controlada
> primero** (contenedor one-off), verificar, y recién después arrancar la app.

### 2B.1 Backup (otra vez, justo antes de migrar)
```bash
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" \
  > backup_pre_003_$(date +%F_%H%M).dump
```

### 2B.2 Correr la migración de forma controlada
```bash
# Build de la imagen app con el código de plan_fases_api ya consolidado
docker compose build app

# Migración en un contenedor one-off (no arranca uvicorn)
docker compose run --rm app alembic upgrade head
```
La `003` va a: crear `canonical.*` (+ seeds), crear `bronze.raw_record`
(particionado), **backfillear** `bronze.*_raw → raw_record` con verificación de
conteos, `CREATE EXTENSION vector` (y postgis graceful), agregar `station.code/active`.

### 2B.3 Verificar el backfill
```bash
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
  SELECT 'raw_record' t, count(*) FROM bronze.raw_record
  UNION ALL SELECT 'sensor_raw', count(*) FROM bronze.sensor_raw
  UNION ALL SELECT 'emp_obs_raw', count(*) FROM bronze.empirical_observation_raw
  UNION ALL SELECT 'emp_evt_raw', count(*) FROM bronze.empirical_observation_raw;"
# raw_record debe ≈ suma de las *_raw (verificar contra los conteos que loguea la migración)

docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT count(*) FROM canonical.source;"   # debe estar poblado desde station
```
- [ ] `alembic upgrade head` terminó sin error
- [ ] conteos `raw_record` vs `*_raw` coinciden (migración verifica y aborta si no)
- [ ] `canonical.source` poblado
- [ ] contrato de ingest intacto: un nodo Edge sigue enviando y responde 200

### 2B.4 Rollback 2B
```bash
docker compose run --rm app alembic downgrade -1     # la 003 tiene downgrade
# Si el downgrade no deja la DB sana → restaurar el dump:
#   docker compose exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean backup_pre_003_*.dump
```

---

## FASE 2C — App C10 / C11

**Objetivo:** exponer la capa pública (agregación + API), última porque es la cara externa.

### 2C.1 Arrancar la app (ya migrada)
```bash
docker compose up -d --no-deps app      # entrypoint: alembic upgrade head (no-op, ya migrado) + uvicorn
docker compose logs --tail=40 app
```

### 2C.2 C10 — verificar agregación (views)
```bash
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT * FROM v_station_summary LIMIT 3; SELECT * FROM v_network_status LIMIT 3;"
```

### 2C.3 C11 — endpoints públicos + nginx + CORS
```bash
# Recargar nginx con el server block api.yvyedge.com (viene en plan_fases_api)
docker compose up -d --no-deps nginx
curl -fsS https://api.yvyedge.com/api/v1/public/... # (endpoint público real)
```
- [ ] views C10 devuelven datos
- [ ] `/api/v1/public/*` responde
- [ ] `api.yvyedge.com` resuelve (nginx + DNS + TLS)
- [ ] CORS OK desde el front

### 2C.4 Rollback 2C
Redeploy de la imagen `app`/`nginx` anterior. (La DB migrada queda; C10/C11 son
solo lectura/expuestos — se pueden "apagar" volviendo la imagen sin datos perdidos.)

---

## Resumen de orden y puntos de no-retorno

| Fase | Cambia datos | Reversible | Punto crítico |
|---|---|---|---|
| 1 — master (app) | No | Sí (redeploy) | No recrear postgres (`--no-deps`) |
| 2A — postgres+pgvector | No (preserva volumen) | Sí | **No borrar `postgres_data`** |
| 2B — migración 003 | **Sí** (crea/backfill) | downgrade + dump | Verificar conteos |
| 2C — C10/C11 app | No | Sí (redeploy) | DNS/TLS/CORS de `api.yvyedge.com` |

> Después de cada fase, actualizar los runbooks que correspondan
> (`RUNBOOK_CICDv3` §3 pasa a "aplicado", `RUNBOOK_M0_INSTALL` baseline real,
> `YVY_DQS_*` prerequisito pgvector satisfecho, `YVY_Plan_Fases_Web_Core_v3`).
