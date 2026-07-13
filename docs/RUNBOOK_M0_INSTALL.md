# YVY · RUNBOOK DE INSTALACIÓN — Migración M0 (Modelo Canónico)

> ## ⚠️ SUPERSEDIDO (2026-07-07)
>
> Este runbook fue reemplazado por
> **`RUNBOOK_DESPLIEGUE_M0_DQS_WEBCORE_2026-07-07.md` §E2**, que incorpora:
> - la auditoría del servidor del 07-jul (Fase 2A ya aplicada, conteos reales ~41.9k,
>   3 estaciones, pre-check de schemas canónicos manuales),
> - los fixes de código QW1-QW6 (bloqueantes descubiertos en la auditoría de código),
> - correcciones de comandos que acá están MAL: los `docker compose run --rm yvy-api
>   poetry run alembic ...` fallan (el runtime de producción NO tiene poetry — usar
>   `alembic` directo), el usuario de psql es `yvyserver` (no `yvy`), y los smoke
>   tests con `https://yvyedge.com/healthz` usan rutas/dominios incorrectos
>   (es `https://edge.yvyedge.com/api/v1/healthz`).
>
> Se conserva como referencia histórica del diseño de la ventana. **NO ejecutar
> comandos de este documento.**

**Versión:** 1.0 (histórica)
**Fecha:** 15-Jun-2026
**Rama:** `plan_fases_api` → merge a `master`
**Ejecutores:** Luis (SysAdmin) + Gonzalo (admin de proyecto)
**Duración estimada ventana:** 15–30 min (depende de build de imagen)
**Ref. arquitectura:** `RUNBOOK_CICDv3.md` §4–§8

---

## PRE-CONDICIONES (todos los ítems deben estar en verde antes de empezar)

### En CI/GitHub
- [ ] CI pasa en el branch `plan_fases_api` (lint + mypy + migration roundtrip + tests)
- [ ] PR de `plan_fases_api` → `master` aprobado

### En el servidor (Luis)
- [ ] `api.yvyedge.com` resuelve a la IP correcta del servidor
- [ ] Puerto 443 aceptando conexiones (`curl -I https://api.yvyedge.com/healthz` → 200)
- [ ] Certbot expandido para incluir `api.yvyedge.com` en el certificado
- [ ] Stack actual en pie: `docker compose ps` muestra postgres, yvy-api, nginx `(healthy)`
- [ ] `/healthz` responde OK y `/admin/healthz` responde con `db_responsive: true`

### En código (Gonzalo)
- [ ] Merge de `plan_fases_api` a `master` en GitHub completado
- [ ] `git log master --oneline -1` muestra el commit M0 (`116d94b` o posterior)

---

## PASO 0 — ANUNCIAR VENTANA DE MANTENIMIENTO

Las edges acumulan datos en su queue local (offline-first). No se pierde ningún dato
durante la ventana. Aun así, notificar a cualquier consumidor activo del API público.

```bash
# Opcional: habilitar página de mantenimiento en nginx si existe
# En el contexto del piloto IEA, simplemente notificar al equipo.
echo "Ventana M0 iniciada: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

---

## PASO 1 — ESTADO INICIAL Y CONTEOS PRE-M0

Registrar los conteos actuales para comparar después del backfill.

```bash
# Estado del stack
docker compose ps

# Conteos de las tablas que serán migradas
docker compose exec postgres psql -U yvy -d yvy -c "
SELECT
  'sensor_raw'                AS tabla,
  COUNT(*)::int               AS filas
FROM bronze.sensor_raw
UNION ALL
SELECT 'empirical_observation_raw', COUNT(*) FROM bronze.empirical_observation_raw
UNION ALL
SELECT 'empirical_event_raw',       COUNT(*) FROM bronze.empirical_event_raw;
"
```

**Guardar la salida.** Se usa para verificar el backfill en el Paso 7.

---

## PASO 2 — BACKUP PRE-M0 (VERIFICADO)

```bash
# Crear backup
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="/opt/backups/yvy_pre_m0_${TIMESTAMP}.dump"

docker compose exec -T postgres pg_dump \
  -U yvy -d yvy -Fc > "$BACKUP_FILE"

echo "$BACKUP_FILE" > /tmp/yvy_backup_pre_m0.txt
echo "Backup guardado: $BACKUP_FILE ($(du -sh $BACKUP_FILE | cut -f1))"

# VERIFICAR restaurabilidad (NO restaurar, solo listar)
pg_restore --list "$BACKUP_FILE" | tail -20
# Si pg_restore falla o muestra 0 objetos, el backup es inválido — PARAR.
```

> **PARAR si el backup no se puede verificar.** Un backup no verificado no es un backup.

---

## PASO 3 — DETENER INGESTA (mantener postgres y nginx)

```bash
# Solo detener yvy-api. postgres y nginx siguen corriendo.
docker compose stop yvy-api

# Confirmar que postgres sigue healthy
docker compose exec postgres pg_isready -U yvy -d yvy
# Esperado: /var/run/postgresql:5432 - accepting connections
```

---

## PASO 4 — PULL DEL CÓDIGO M0 (master)

```bash
cd /opt/yvy-server

# Guardar el commit actual para rollback
git rev-parse HEAD > /tmp/yvy_commit_pre_m0.txt
echo "Commit pre-M0: $(cat /tmp/yvy_commit_pre_m0.txt)"

# Traer master con M0
git fetch origin master
git reset --hard origin/master

# Verificar que el archivo de migración M0 está presente
ls alembic/versions/003_m0_canonical_model.py
# También debe existir postgres/Dockerfile
ls postgres/Dockerfile
```

---

## PASO 5 — REBUILD IMAGEN POSTGRES (PostGIS + pgvector)

Esta es la única vez que se hace este rebuild. La imagen base cambia de
`postgres:16-alpine` a `postgis/postgis:16-3.4` + pgvector.

```bash
# Build de la nueva imagen (requiere acceso a internet para apt)
docker compose build postgres

# Recrear el contenedor postgres con la nueva imagen
# IMPORTANTE: --no-deps para no tocar yvy-api todavía
docker compose up -d --no-deps postgres

# Esperar a que postgres esté healthy (puede tardar ~30s tras rebuild)
for i in $(seq 1 20); do
  docker compose exec postgres pg_isready -U yvy -d yvy && break
  echo "Esperando postgres... ($i/20)"
  sleep 3
done
```

---

## PASO 6 — VERIFICAR EXTENSIONES

La migración M0 instala las extensiones vía `CREATE EXTENSION IF NOT EXISTS`, pero
verificamos manualmente que la imagen las tiene disponibles:

```bash
docker compose exec postgres psql -U yvy -d yvy -c "
SELECT extname, extversion
FROM pg_extension
WHERE extname IN ('vector', 'postgis');
"
```

**Si `vector` no aparece tras correr la migración:** la imagen no tiene pgvector instalado.
Verificar el Dockerfile y rebuild.

> PostGIS puede no estar presente en este punto (aún no se creó la extensión). La migración
> la instala; si falla, el bloque graceful del M0 lo registra como NOTICE sin abortar.

---

## PASO 7 — CORRER LA MIGRACIÓN M0

```bash
cd /opt/yvy-server

# Verificar que alembic está en la revisión correcta (002_edge_releases)
docker compose run --rm yvy-api poetry run alembic current
# Esperado: 0002_edge_releases (head)

# Correr M0
docker compose run --rm yvy-api poetry run alembic upgrade head
# Esperado: Running upgrade 0002_edge_releases -> 0003_m0_canonical_model
# La migración tarda ~1-3 min con 10k filas (backfill incluido)

# Verificar revisión final
docker compose run --rm yvy-api poetry run alembic current
# Esperado: 0003_m0_canonical_model (head)
```

---

## PASO 8 — VERIFICAR BACKFILL (CRÍTICO)

Comparar con los conteos del Paso 1. Deben coincidir exactamente.

```bash
docker compose exec postgres psql -U yvy -d yvy -c "
SELECT
  (SELECT COUNT(*) FROM bronze.raw_record WHERE record_kind='sensor_reading')::int
    AS new_sensor,
  (SELECT COUNT(*) FROM bronze.raw_record WHERE record_kind='empirical_observation')::int
    AS new_obs,
  (SELECT COUNT(*) FROM bronze.raw_record WHERE record_kind='empirical_event')::int
    AS new_ev;
"
```

**Si algún valor no coincide con los conteos del Paso 1 → PARAR e ir al rollback (Paso 11).**
No continuar con yvy-api nuevo si el backfill falló.

Verificar también que las tablas viejas fueron dropeadas:

```bash
docker compose exec postgres psql -U yvy -d yvy -c "
SELECT tablename FROM pg_tables
WHERE schemaname='bronze'
ORDER BY tablename;
"
# Esperado: ingest_failures, ingest_log, raw_record, raw_record_default
# NO deben aparecer: sensor_raw, empirical_observation_raw, empirical_event_raw
```

Verificar extensiones instaladas:

```bash
docker compose exec postgres psql -U yvy -d yvy -c "
SELECT extname, extversion FROM pg_extension
WHERE extname IN ('vector','postgis');
"
# vector debe aparecer; postgis puede aparecer o no según el graceful block
```

---

## PASO 9 — BUILD Y ARRANQUE DE yvy-api CON CÓDIGO M0

```bash
# Build de la nueva imagen yvy-api (incluye BronzeRepository nuevo, RawRecordORM, etc.)
docker compose build yvy-api

# Levantar yvy-api con el código M0
docker compose up -d --no-deps yvy-api

# Esperar a que healthz responda (carga source_cache al startup)
for i in $(seq 1 15); do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/healthz)
  [ "$STATUS" = "200" ] && echo "yvy-api UP" && break
  echo "Esperando yvy-api... ($i/15)"
  sleep 3
done
```

En los logs del startup debe aparecer:

```
{"message": "startup complete; ready to serve requests", "initial_sync_version": N}
```

---

## PASO 10 — SMOKE TESTS POST-M0

```bash
# Healthz público
curl -sf https://yvyedge.com/healthz | python3 -m json.tool
# Esperado: {"status": "ok"}

# Healthz admin (con tu token)
curl -sf -H "Authorization: Bearer $YVY_ADMIN_TOKEN" \
  https://api.yvyedge.com/admin/healthz | python3 -m json.tool
# Esperado: db_responsive=true, counts con bronze.raw_record (no sensor_raw)

# Endpoints públicos (contrato intacto)
curl -sf https://yvyedge.com/public/network | python3 -m json.tool | head -30
curl -sf https://yvyedge.com/public/stations | python3 -m json.tool | head -30
# Verificar que devuelven datos (misma estructura que antes de M0)

# Alembic no tiene drift
docker compose run --rm yvy-api poetry run alembic check
# Esperado: No new upgrade operations detected.
```

---

## PASO 11 — VERIFICAR INGESTA DESDE EDGE

Si hay una edge activa (IEA-07 Garupá), verificar que drenan su queue acumulada:

```bash
# Esperar ~5 min y consultar raw_record para el source de IEA-07
docker compose exec postgres psql -U yvy -d yvy -c "
SELECT
  c.name                                        AS station,
  COUNT(r.record_id)                            AS registros_nuevos,
  MAX(r.received_at)                            AS ultimo_recibido
FROM bronze.raw_record r
JOIN canonical.source c ON c.source_id = r.source_id
WHERE r.received_at > NOW() - INTERVAL '10 minutes'
GROUP BY c.name;
"
# Si aparece IEA-07 con registros recientes, la ingesta está funcionando sobre raw_record.
```

---

## PASO 12 — CIERRE DE VENTANA

```bash
# Registrar el resultado
echo "M0 completado: $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a /opt/yvy-server/deploy.log
git log --oneline -1 >> /opt/yvy-server/deploy.log

docker compose ps
```

Notificar a los usuarios que el mantenimiento terminó.

---

## ROLLBACK (si algún paso falla entre 7 y 10)

**Si el backfill falló o yvy-api no arranca:**

```bash
# 1. Detener yvy-api roto
docker compose stop yvy-api

# 2. Restaurar backup pre-M0
BACKUP_FILE=$(cat /tmp/yvy_backup_pre_m0.txt)

# Detener postgres, limpiar volumen, restaurar
docker compose stop postgres
docker compose run --rm -v postgres_data:/var/lib/postgresql/data postgres \
  sh -c "rm -rf /var/lib/postgresql/data/*"
docker compose up -d --no-deps postgres
sleep 10

pg_restore -h localhost -p 5432 -U yvy -d yvy "$BACKUP_FILE"

# 3. Volver al código pre-M0
COMMIT_PRE=$(cat /tmp/yvy_commit_pre_m0.txt)
git reset --hard "$COMMIT_PRE"

# 4. Rebuild yvy-api y levantar
docker compose build yvy-api
docker compose up -d --no-deps yvy-api

# 5. Smoke tests con el estado anterior
curl -sf https://yvyedge.com/healthz
```

> Las edges mantienen su queue local intacta. Al volver a la versión anterior,
> drenan normalmente a las tablas viejas (que fueron restauradas desde el backup).

---

## CHECKLIST FINAL

```
[ ] CI verde en plan_fases_api antes del merge
[ ] 443 / api.yvyedge.com resuelto (Luis)
[ ] Backup tomado y verificado con pg_restore --list
[ ] Migración 003_m0_canonical_model aplicada (alembic current = head)
[ ] Backfill verificado: conteos old_X == new_X para las 3 tablas
[ ] Tablas viejas dropeadas, raw_record presente
[ ] Extension vector presente en pg_extension
[ ] yvy-api arranca sin errores, source_cache cargado
[ ] /healthz y /admin/healthz OK
[ ] /public/network y /public/stations devuelven JSON correcto
[ ] Ingesta desde edge llega a raw_record con source_id correcto
[ ] alembic check sin drift
[ ] deploy.log actualizado
```

---

**Tiempo esperado:** 15 min (sin problemas de red en apt). 30 min si hay reintento.
**Bloqueante de:** Track DATA (D1+), Web/Core v3 (C12+), DQS v3.
