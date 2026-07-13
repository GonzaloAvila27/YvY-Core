# YVY Core · Runbook maestro — Despliegue M0 → DQS → Web/Core

**Fecha de creación:** 2026-07-07
**Estado:** VIGENTE — reemplaza operativamente a `PLAN_APLICACION_SERVIDOR.md` (que
queda como referencia de diseño). Incorpora los hallazgos de la auditoría de código
(07-jul) y de servidor (`AUDITORIA_SERVIDOR_M-1_RESULTADO_2026-07-07.md`).
**Ejecutores:** Gonzalo (código + admin) · Luis (SysAdmin, ventanas en server).

---

## 0. Mapa general — qué es deploy y qué es implementación

| Etapa | Qué | Tipo | Estado al 07-jul | Bloqueada por |
|---|---|---|---|---|
| **E0** | Fixes críticos en `plan_fases_api` (QW1-QW6) | Código | 🔴 Pendiente | decisión D-1 (abajo) |
| **E1** | Deploy `master` consolidado (Fase 1) | Deploy | 🟢 Lista | nada — independiente de E0 |
| **E2** | Deploy `plan_fases_api`: M0 → C10 → C11 (Fase 2) | Deploy | 🔴 Bloqueada | E0 + E1 |
| **E3** | DQS v3 = D1 (Silver + quality/obs) + D2 (daily + Open-Meteo) | **Implementación** + deploy | ⚪ No existe código aún | E2 |
| **E4** | Web/Core: C12-C16 (Core) + W0-W6 (repo web) | **Implementación** + deploy | ⚪ Pendiente | C12+: E2 · W0-W1: nada |

> **Aclaración de alcance:** "desplegar el DQS y el plan web_core" hoy significa
> **construirlos primero** — el DQS v3 y C12-C16 existen como diseño
> (`YVY_DQS_RUNBOOK_v3.md`, `YVY_Plan_Fases_Web_Core_v2/v3.md`), no como código.
> Este runbook define el orden, los gates y los pasos de deploy de cada etapa.

### Estado del servidor (auditado 07-jul — no re-verificar salvo que pase tiempo)

- ✅ Postgres con pgvector 0.8.3 + PostGIS 3.4.3, datos preservados (≈41.9k filas) → **Fase 2A ya hecha**.
- ✅ Schemas `canonical/silver/gold/knowledge/auth/exports/catalog` existen **vacíos**.
- ✅ Backup nocturno + restore verificado. ✅ `yvy-stack.service` recupera tras corte.
- ✅ DNS `api.yvyedge.com` → 153.67.184.39.
- ❌ Cert: **ningún cert cubre `api.yvyedge.com`** (expand pendiente, ver E2-C).
- ⚠ Git server: `master@736e707`, tree limpio, *delante 1 / detrás 13* del origin real
  (`3f336b6`); el commit local ya está en origin como `aabee2b`.
- ⚠ Build corriendo sin identidad (`serverinfo=unknown`) — corregir en E1.

---

## D-1 · Decisión previa requerida (bloquea QW1)

**¿Cómo se garantiza la idempotencia por `event_id` en `bronze.raw_record`?**

| Opción | Cómo | Pros | Contras |
|---|---|---|---|
| **A — Despartir (RECOMENDADA)** | `raw_record` tabla normal, `PRIMARY KEY (record_id)`; se edita la migración 003 (nunca aplicada en ningún entorno → editable sin `004`) | `ON CONFLICT (record_id)` vuelve a ser válido; dedupe idéntico al actual; cero complejidad | Se pierde el particionado (hoy cosmético: solo existía la partición DEFAULT; a 42k filas/mes no rinde). Re-particionar en el futuro = migración planificada cuando haya volumen |
| B — Mantener partición + dedupe explícito | PK compuesta se queda; antes del INSERT, `SELECT 1 FROM raw_record WHERE record_id=:id` | Conserva el particionado | Dos statements por ingesta; dedupe no atómico (aceptable con un solo writer); particiones mensuales siguen sin existir (haría falta job) |

**Recomendación:** A. El particionado se justifica recién con varios millones de filas
(≥6 IEAs × años); reintroducirlo entonces será una migración normal con datos conocidos.

> ☑ **Decisión tomada: A (despartir)** — 2026-07-07, Gonzalo. `raw_record` queda como
> tabla normal con `PRIMARY KEY (record_id)`; el particionado se reintroduce como
> migración planificada cuando el volumen lo justifique (≥6 IEAs activas).

---

## E0 — Fixes críticos en `plan_fases_api` (bloquean E2)

Cada ítem con criterio de aceptación verificable. Todos entran como commits separados
en `plan_fases_api`; CI debe quedar verde al final.

### QW1 — UPSERT a `raw_record` funcional e idempotente 🔴
- **Archivos:** `src/yvy_server/infrastructure/persistence/bronze_repository.py:148`,
  `alembic/versions/003_m0_canonical_model.py` (§7 raw_record).
- **Cambio (opción A):** tabla sin `PARTITION BY`, `PRIMARY KEY (record_id)`;
  el `on_conflict_do_nothing(index_elements=["record_id"])` queda válido tal cual.
  Alinear `RawRecordORM` (PK simple ya declarada — verificar que coincida).
- **Aceptación:** test de integración inserta el mismo envelope 2 veces →
  `inserted` / `duplicate`; `SELECT COUNT(*)` = 1.

### QW2 — `create-station` crea el `canonical.source` 🔴
- **Archivo:** `src/yvy_server/cli/cli.py` (`_persist_station`).
- **Cambio:** en la misma transacción, `INSERT INTO canonical.source (source_id,
  source_type_key='weather_station', external_ref=station_uuid::text, name, metadata
  {code?, locality?, latitude, longitude}, active=true)`.
- **Aceptación:** test de integración: create-station → POST ingest firmado → 200
  `inserted` (sin restart del server: el cache resuelve el miss contra DB).

### QW3 — `observed_at` desde el payload 🟠
- **Archivo:** `bronze_repository.py:141`.
- **Cambio:** `observed_at = payload.get("observed_at_utc")` parseado; fallback
  `sent_at_utc` si falta. Misma semántica que el backfill de la 003.
- **Aceptación:** test verifica que `raw_record.observed_at` == `observed_at_utc`
  del payload, no el `sent_at_utc` del envelope.

### QW4 — Envelope malformado → 400 SCHEMA (no 500) 🟠
- **Archivo:** `application/auth/envelope_validator.py` (`validate`, y
  `check_clock_skew`/`_parse_iso_utc`).
- **Cambio:** envolver las coerciones (`UUID(...)`, `int(...)`, `fromisoformat`) en
  try/except → `EnvelopeShapeError`.
- **Aceptación:** unit tests: `station_uuid="banana"`, `key_version="x"`,
  `sent_at_utc="ayer"` → `IngestFailure(400, "SCHEMA")` y fila en `ingest_log`.

### QW5 — Runbooks de ventana corregidos 🟠
- `RUNBOOK_M0_INSTALL.md`: sin `poetry run` (runtime sin poetry); usuario psql
  `yvyserver`; URLs `https://edge.yvyedge.com/api/v1/...`; servicio `yvy-api`;
  conteos reales (~41.9k, 3 estaciones); pasos 2A → "verificar solamente".
- **Aceptación:** dry-read de Luis: cada comando copipasteable sin editar.

### QW6 — Test de integración mínimo en CI 🔴
- **Nuevo:** `tests/integration/test_ingest_e2e.py` — contra el service
  `pgvector/pg16` que el CI ya levanta: migra a head, crea station+credential
  (repos o CLI), arma envelope firmado real (canonicalize + HMAC), POST vía
  `httpx.AsyncClient(transport=ASGITransport(app))` → 200 inserted → duplicate →
  payload inválido → DLQ.
- **CI:** re-agregar el job al workflow (hoy solo corre roundtrip de migraciones).
- **Aceptación:** el job falla si se reintroduce C1 (probarlo revirtiendo QW1 en local).

**Gate de salida E0:** CI verde en `plan_fases_api` (ruff + mypy + unit + roundtrip +
integración) y CHANGELOG actualizado con las 4 correcciones de código.

---

## E1 — Deploy Fase 1: `master` consolidado (independiente de E0, puede ir YA)

En el server, **como `yvyadmin`** (keter no tiene la deploy key), desde
`/opt/yvy-server`:

```bash
# 1. Backup liviano + registro del estado actual
docker compose ps > /tmp/estado_pre_fase1.txt
docker compose exec -T postgres pg_dump -U yvyserver yvy | gzip \
  > backups/yvy_pre_fase1_$(date +%F_%H%M).sql.gz

# 2. Reconciliación git (auditada 07-jul: tree limpio, único commit local ya en origin)
cd server-code
sudo -u yvyadmin git fetch origin
git log --oneline origin/master -3        # DEBE mostrar 3f336b6 en el tope
git reset --hard origin/master

# 3. Rebuild de app CON identidad de build (corrige serverinfo=unknown)
cd /opt/yvy-server
GIT_COMMIT=$(git -C server-code rev-parse HEAD) \
GIT_COMMIT_SHORT=$(git -C server-code rev-parse --short HEAD) \
BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
docker compose build yvy-api

# 4. Recrear SOLO app (postgres y nginx quedan)
docker compose up -d --no-deps yvy-api

# 5. Registrar el deploy (el paso que se olvidó el 24-jun)
{ echo "deployed_at: $(date -Is)"; echo "git_commit: $(git -C server-code rev-parse HEAD)";
  echo "git_branch: master"; echo "deployed_by: $(whoami)"; } > current_version.txt
```

**Verificación (todas deben pasar):**
```bash
docker compose logs --tail=30 yvy-api          # "Migrations applied." (no-op) + startup complete
curl -s https://edge.yvyedge.com/api/v1/serverinfo | python3 -m json.tool
#   → git_commit_short = el short REAL (ya no "unknown")
docker compose exec postgres psql -U yvyserver -d yvy -tc "SELECT version_num FROM alembic_version;"
#   → 0002_edge_releases  (Fase 1 NO migra nada)
# Ingesta sigue viva (esperar 2-3 min):
docker compose exec postgres psql -U yvyserver -d yvy -tc \
  "SELECT MAX(received_at) FROM bronze.sensor_raw;"   # timestamp de hace minutos
```

**Rollback E1:** `git reset --hard 736e707 && docker compose build yvy-api && docker
compose up -d --no-deps yvy-api`. La DB no se tocó.

> ⚠️ Tras el reset, `server-code/alembic/versions/` va a contener `003_m0_canonical_model.py`
> **solo si ya se mergeó plan_fases_api a master** — en E1 NO debe estar (master
> consolidado termina en 0002). Verificar `ls server-code/alembic/versions/` = 001, 002.
> Si aparece la 003 → se adelantó un merge; PARAR y revisar antes del build.

---

## E2 — Deploy Fase 2: M0 → C10 → C11 (ventana de mantenimiento)

**Precondiciones (checklist de entrada):**
- [ ] E0 completo, CI verde, `plan_fases_api` mergeada a `master` vía PR.
- [ ] E1 aplicado y estable ≥ 2 días (soak).
- [ ] Ventana acordada con Luis (~30-45 min; las edges acumulan en queue local).
- [ ] Cert expandido (ver 2C-0 — puede hacerse ANTES de la ventana, no corta nada).

### 2A — Postgres con extensiones: **YA HECHA** (auditoría 07-jul)
Solo re-verificar el día D:
```bash
docker compose exec postgres psql -U yvyserver -d yvy -c \
  "SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector','postgis');"
# vector 0.8.3 + postgis 3.4.3
```

### 2B — Migración 003 (M0)

```bash
# 0. PRE-CHECKS (abortan la ventana si fallan)
docker compose exec postgres psql -U yvyserver -d yvy -c "\dt canonical.*" \
  # ídem silver/gold/knowledge/auth/exports/catalog → TODOS "Did not find any relation"
docker compose exec postgres psql -U yvyserver -d yvy -c "
  SELECT 'sensor' t, COUNT(*) FROM bronze.sensor_raw
  UNION ALL SELECT 'obs', COUNT(*) FROM bronze.empirical_observation_raw
  UNION ALL SELECT 'event', COUNT(*) FROM bronze.empirical_event_raw;"
# GUARDAR estos números — gate del paso 4. (Referencia 07-jul: ~41.9k / 6 / 0)

# 1. Backup verificado (procedimiento YA probado el 07-jul)
BK=backups/yvy_pre_m0_$(date +%F_%H%M).sql.gz
docker compose exec -T postgres pg_dump -U yvyserver yvy | gzip > "$BK"
gunzip -t "$BK" && echo OK
# Restore de prueba a scratch (opcional si se hizo <7 días antes; obligatorio si no):
#   createdb yvy_restore_test → zcat | psql → COUNT → dropdb

# 2. Detener ingesta (postgres y nginx quedan)
docker compose stop yvy-api

# 3. Pull del master post-merge + build + migración CONTROLADA (one-off, sin uvicorn)
cd server-code && sudo -u yvyadmin git fetch origin && git reset --hard origin/master
ls alembic/versions/    # ahora SÍ debe estar 003_m0_canonical_model.py
cd /opt/yvy-server
GIT_COMMIT=$(git -C server-code rev-parse HEAD) \
GIT_COMMIT_SHORT=$(git -C server-code rev-parse --short HEAD) \
BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ) docker compose build yvy-api
docker compose run --rm yvy-api alembic upgrade head       # SIN poetry
# La 003 verifica conteos internamente y ABORTA si el backfill no cuadra.

# 4. Gate de backfill (comparar contra paso 0)
docker compose exec postgres psql -U yvyserver -d yvy -c "
  SELECT record_kind, COUNT(*) FROM bronze.raw_record GROUP BY 1;"
# sensor_reading == sensor, empirical_observation == obs, empirical_event == event
docker compose exec postgres psql -U yvyserver -d yvy -c \
  "SELECT COUNT(*) FROM canonical.source;"     # ≥ 3 + 18 seed = 21
docker compose exec postgres psql -U yvyserver -d yvy -c \
  "\dt bronze.*"   # raw_record presente; sensor_raw/empirical_* YA NO; ingest_log/failures intactas

# 5. Arrancar app + smoke
docker compose up -d --no-deps yvy-api
docker compose logs --tail=20 yvy-api    # startup complete + initial_sync_version
```

**Smoke post-2B (obligatorio, con la credencial del PC de test):**
1. POST `/api/v1/ingest/sensor` firmado → `200 inserted`; repetir mismo event_id →
   `duplicate` (valida QW1 en prod).
2. `SELECT * FROM bronze.raw_record ORDER BY received_at DESC LIMIT 1` → la fila de
   prueba con `source_id` correcto y `observed_at` = payload (valida QW3).
3. Esperar ≤10 min: IEA-07 drena su queue → filas nuevas en `raw_record`.
4. `alembic current` = `0003_m0_canonical_model (head)`.

**Rollback 2B:** `docker compose run --rm yvy-api alembic downgrade -1` (probado en
CI); si la DB queda dudosa → restore del dump del paso 1 (procedimiento verificado
07-jul) + `git reset --hard` al commit pre-M0 + rebuild.

### 2C — C10/C11: capa pública + nginx + cert

**2C-0 (puede hacerse ANTES de la ventana):** expandir el cert.
```bash
# Ver cómo renueva hoy (¿standalone con hooks, o webroot?)
sudo cat /etc/letsencrypt/renewal/yvyedge.com.conf | grep -E 'authenticator|webroot'
# Expandir agregando api. al cert principal (ajustar método según lo anterior):
sudo certbot certonly --expand -d yvyedge.com -d edge.yvyedge.com -d www.yvyedge.com \
  -d api.yvyedge.com   # + flags del authenticator vigente
sudo certbot certificates | grep -A2 'yvyedge.com'   # api. debe aparecer en Identifiers
```
> Higiene opcional: el cert separado `edge.yvyedge.com` es redundante — consolidar.

**2C-1:** agregar el server block `api.yvyedge.com` **al nginx.conf de PROD**
(`/opt/yvy-server/nginx/conf/nginx.conf` — NO el del repo): HTTPS con el cert
expandido, `location /api/v1/public/ { proxy_pass http://yvy-api:8000; }` con los
mismos proxy headers, y preparar `location /assets/insights/` (C15). Respaldar antes:
`cp nginx/conf/nginx.conf nginx/conf/nginx.conf.bak_$(date +%F)`.

**2C-2:** validar y recargar:
```bash
docker compose exec nginx nginx -t && docker compose exec nginx nginx -s reload
curl -s https://api.yvyedge.com/api/v1/public/network  | python3 -m json.tool
curl -s https://api.yvyedge.com/api/v1/public/stations | python3 -m json.tool | head -40
# CORS: preflight desde la web
curl -si -X OPTIONS https://api.yvyedge.com/api/v1/public/network \
  -H "Origin: https://yvyedge.com" -H "Access-Control-Request-Method: GET" | head -8
#   → access-control-allow-origin presente
```

**Rollback 2C:** restaurar `nginx.conf.bak` + reload. (La app puede quedar — los
endpoints públicos sin nginx delante simplemente no son alcanzables.)

**Cierre E2:** actualizar `current_version.txt`, `deploy.log`, CHANGELOG, y la sección
"Estado actual" de `CLAUDE.md` (master pasa a incluir M0/C10/C11; `plan_fases_api`
muere o se recicla).

---

## E3 — DQS v3 (implementar D1 + D2, luego desplegar)

**Diseño ya cerrado en** `YVY_DQS_RUNBOOK_v3.md` — esto es el orden ejecutable.

### E3-a · D1: promoción Bronze→Silver + quality por observación (código)
1. Job de promoción: lee `raw_record` (`record_kind='sensor_reading'`) no promovidos,
   explota el payload en `silver.measurement` (long format, `variable_key` canónico,
   conversión wind_dir índice→grados), computa `quality_flags` + `quality_score` por
   measurement (reglas DQS v3 §3), persiste derivadas (dewpoint, VPD) con `is_derived`.
2. CLI `yvy-server promote-silver` (mismo patrón sync+asyncio.run del CLI actual).
3. Proyección `silver_proj.meteorology` (materialized view o tabla regenerable).
4. Tests: equivalencia Bronze-vs-Silver sobre fixture real de IEA-07; golden de
   dewpoint/VPD (T=20°C, H=87% → VPD ≈ 0.30 kPa).
5. **Deploy:** migración 004 (tabla de estado de promoción + proyección — los schemas
   ya existen), deploy estándar de código, systemd timer `yvy-promote-silver.timer`
   (cada 5 min, `Persistent=true`), backfill inicial de los ~42k registros, validación.

### E3-b · D2: agregado diario + referencia externa (código)
1. `public.source_quality_daily` (migración) + CLI `compute-source-quality`
   (`dqs_internal = AVG(quality_score)` del día; sub-scores persistidos para /explain).
2. Cliente Open-Meteo → `canonical.source(source_type='external_api')` + job de polling
   → `raw_record(record_kind='api_response')` → `reference_score`/`drift_score`.
3. Timers: daily 02:00 + parciales AM/PM (spec v3 §5 — el daily corre DESPUÉS de la
   promoción de las 01:55).
4. **Gate de cierre:** DQS de IEA-07 cae en BUENA/EXCELENTE (validación manual);
   endpoint `/api/v1/inst/quality/explain` versionado (o diferir a D4 si se decide).

> **Regla contractual:** cualquier salida de alertas derivada de esto usa
> **"probable helada"**, nunca "helada confirmada".

## E4 — Web/Core (C12-C16 + W0-W6)

Orden según `YVY_Plan_Fases_Web_Core_v2.md` (vigente) + ajustes v3:

1. **W0-W1** (repo `web_yvyedge`, independiente del Core): bootstrap + DNS apex a
   Vercel + skeleton con mocks. Puede arrancar en paralelo a E2/E3.
   ⚠ El A record de `yvyedge.com` hoy apunta al server (153.67.184.39) y el nginx de
   prod sirve `server_name yvyedge.com` — al mover el apex a Vercel, **verificar que
   los edges usan `edge.yvyedge.com`** (sí, verificado) y que nada más depende del apex.
2. **W2** cuando E2 esté en prod (consume `/public/network` + `/public/stations` reales).
3. **C12-C13** (metrics/dashboard + derivadas): si E3-a ya está → leer
   `silver_proj.meteorology` (latencia 10x); si no → sobre `raw_record` (nota v3 §1)
   y migrar a Silver después sin cambio de contrato.
4. **C14-C15** (news/insights): independientes de datos de sensor — delegables.
5. **W3-W6 → C16** (cache) → Launch.

---

## Actualizaciones de documentación (al cierre de cada etapa)

| Cuándo | Documento | Cambio |
|---|---|---|
| E0 | `CHANGELOG.md` | QW1-QW4 con detalle de archivos |
| E0 | `RUNBOOK_M0_INSTALL.md` | QW5 (comandos reales) |
| E1 | `CLAUDE.md` §Estado, `current_version.txt` | master deployado, serverinfo con hash |
| E2 | `CLAUDE.md`, `PLAN_APLICACION_SERVIDOR.md` (marcar aplicado), `endpointsFactico` (+/public/*), `README.md` (estado real) | M0/C10/C11 en prod |
| E2 | `DIAGNOSTICO_ERRORES_YVY.md` | DIAG-002 actualizado (obs llegaron hasta 01-jul); cerrar DIAG-001 con QW2 |
| E3 | `YVY_DQS_RUNBOOK_v3.md` → estado IMPLEMENTADO + criterios de cierre tildados | |
| E4 | Plan Web/Core v2 (estados por fase) | |
| Siempre | `CHEATSHEET_OPERACION_CORE.md` | 3 estaciones, releases vigentes, todo comando nuevo |

---

*Creado 2026-07-07. Este runbook es el documento operativo único del ciclo
E0→E4; los planes de diseño (Plan Maestro, DQS v3, Web/Core v2/v3) no se duplican acá.*
