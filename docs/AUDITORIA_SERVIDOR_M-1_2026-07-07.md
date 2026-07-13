# YVY Core · Auditoría de servidor (M-1) — Runbook reproducible

**Fecha de creación:** 2026-07-07
**Rol:** Release Engineer + SRE
**Objetivo:** fotografía exacta y reproducible del servidor de producción, verificada
comando por comando. **Cero modificaciones.** Solo lectura, salvo las pruebas marcadas
`[ESCRIBE]` o `[INTRUSIVA]` en Fase 8 (esas requieren decisión explícita y ventana).
**Complementa:** auditoría de código del 07-jul-2026 (sesión Claude Code, rama
`plan_fases_api`). Este runbook verifica el **estado del server**; aquella verificó el
**estado del repo**.

---

## 0. Contexto, alcance y reglas de seguridad

### 0.1 Qué se espera encontrar (hipótesis a confirmar o refutar)

Según `RECUENTO_SESION_OTA_HMAC_24jun.md`, `CONSOLIDACION_MASTER_core-prod.md` y
`CLAUDE.md`, el estado esperado del server al 07-jul-2026 es:

| # | Hipótesis (a verificar, NO asumir) | Fase que la verifica |
|---|---|---|
| H1 | Producción corre el código de `core-prod-actual`/`master` local **pre-M0** (sin `canonical.*`, sin `raw_record`) | F1, F4, F5 |
| H2 | El git de prod está **divergente**: 1 commit local (`736e707`) que origin no tenía + commits de origin sin traer (pendiente P1 del 24-jun) | F1 |
| H3 | El `Dockerfile` de prod está **parcheado a mano y sin commitear** (poetry export + pip) | F1, F9 |
| H4 | Postgres es `postgres:16-alpine` **sin pgvector ni PostGIS** | F2, F4 |
| H5 | `alembic current` = `0002_edge_releases`; tablas `bronze.sensor_raw` / `empirical_observation_raw` / `empirical_event_raw` existen; NO existen views `v_network_status`/`v_station_summary` ni schemas canónicos | F4, F5 |
| H6 | `bronze.sensor_raw` recibe datos en tiempo real de IEA-07; `empirical_observation_raw` estancada desde el 08-jun (DIAG-002) | F4 |
| H7 | Servicios compose: `yvy-api`, `postgres`, `nginx` desde `/opt/yvy-server`; repo git en `/opt/yvy-server/server-code`; solo nginx expone 80/443 (5432 NO expuesto) | F2, F7 |
| H8 | nginx de prod **sin rate limiting** en `/api/v1/ingest/*` | F7, F9 |
| H9 | `edge_releases`: dummy `1.5.1` con `is_active=false`; `sync_version` ≥ 2 | F4 |
| H10 | Endpoints `/api/v1/public/*` **no existen** en prod (C10/C11 solo en rama) | F3 |
| H11 | Backup nocturno cron 03:00 activo y generando archivos | F6, F8 |

> **Importante:** los hallazgos críticos C1-C3 de la auditoría de código (UPSERT
> `ON CONFLICT` inválido, idempotencia rota, `create-station` sin `canonical.source`)
> viven en la rama `plan_fases_api` y **no son observables en el server actual**.
> Esta auditoría establece la línea base sobre la que aterrizaría ese deploy.

### 0.2 Reglas duras durante toda la auditoría

- SSH **solo** a `yvyedge.com` o `153.67.184.39` (usuario `keter`). **Nunca**
  `edge.yvyedge.com` (es nginx, no enruta el 22).
- Mantener **una segunda sesión SSH abierta** todo el tiempo (red de seguridad).
- Todo comando `docker compose` se corre **desde `/opt/yvy-server`**.
- **Prohibido:** `docker compose down -v`, borrar `postgres_data`, `git pull` /
  `git reset` / `git checkout`, `chmod -R`, editar archivos. Esta fase es de lectura.
- Los outputs con secretos (`.env`, `docker inspect` con env vars, credenciales) se
  registran como "verificado OK/FAIL", **nunca se pegan** en documentos ni chats.
- Registrar cada output en un archivo local de evidencia:
  `auditoria_M-1_$(date +%F).log` (en tu PC, no en el server).

### 0.3 Cómo registrar evidencia (patrón para toda la auditoría)

```bash
# En tu PC (PowerShell o Git Bash): capturar la sesión completa
ssh keter@yvyedge.com | tee auditoria_M-1_2026-07-07.log
# Alternativa por comando: anteponer `echo "### <comando>"` antes de cada uno
```

---

## FASE 1 — Repositorio

Todo desde la sesión SSH. Primero **descubrir** dónde vive el git (no asumir).

### 1.0 Descubrimiento de layout

```bash
ls -la /opt/yvy-server/
```
- **Esperado:** `data/ logs/ nginx/ scripts/ secrets/ server-code/ .env docker-compose.yml current_version.txt` (layout del RUNBOOK_DEPLOY_CODE §5.4).
- **Interpretación:** `server-code/` es el clon git; el compose y nginx.conf de
  producción viven FUERA del repo (drift estructural conocido, ver F9).
- **Desvíos:** si `server-code/` no existe y el repo está directo en `/opt/yvy-server`,
  anotar — cambia las rutas de todos los comandos siguientes.

```bash
cd /opt/yvy-server/server-code && pwd && ls
```
- **Esperado:** `src/ pyproject.toml Dockerfile docker-entrypoint.sh alembic/ docs/ ...`

### 1.1 Identidad del repo

| Comando | Esperado | Interpretación / desvíos |
|---|---|---|
| `git remote -v` | `origin  https://github.com/GonzaloAvila27/YvY-Core...` (o SSH deploy key) | Si hay más de un remote o URL distinta → anotar. |
| `git branch -vv` | `* master <hash> [origin/master: ahead N, behind M]` o similar | **Este es el dato central de H2.** `ahead`/`behind` cuantifican la divergencia P1. |
| `git rev-parse HEAD` y `git rev-parse --short HEAD` | Un hash concreto | Compararlo después contra `serverinfo` (F3) y `current_version.txt`. |
| `git log --oneline -10` | Debe incluir `736e707 fix(edge): filename → package_filename` (cherry-pick del 24-jun) | Si no aparece → prod NO tiene el fix del 500; H2 falsa en esa mitad. |
| `git describe --tags --always` | Probablemente solo el hash (no hay tags conocidos) | Si hay tags → documentarlos. |
| `git tag -l` | Vacío o lista | — |

### 1.2 Divergencia con origin (SIN traer nada)

```bash
git fetch origin --dry-run       # ver qué traería, sin traerlo
git fetch origin                  # fetch real es seguro: NO toca el working tree
git status
git log --oneline origin/master..HEAD    # commits locales que origin NO tiene
git log --oneline HEAD..origin/master    # commits de origin que prod NO tiene
```
- **Esperado (H2):** `origin/master..HEAD` ≥ 1 commit (el cherry-pick), **salvo** que
  la consolidación de `CONSOLIDACION_MASTER_core-prod.md` (que re-aplicó ese fix como
  `aabee2b` y se pushó) haga que el mismo cambio exista en ambos lados con hash
  distinto. `HEAD..origin/master` debería listar los commits de la consolidación
  (`aabee2b`, `68fbe0e`, `9613720`, y todo lo pusheado después).
- **Interpretación:** si ambos lados tienen "el mismo fix con distinto hash", la
  reconciliación de Fase 1 del deploy es un `git reset --hard origin/master` seguro
  (previa verificación de H3). Si prod tiene commits locales adicionales NO
  documentados → **PARAR y revisarlos uno por uno** antes de cualquier deploy.

### 1.3 Modificaciones locales y stash (H3)

```bash
git status --porcelain
git diff --stat
git diff Dockerfile            # el hotfix documentado del 24-jun
git stash list
git worktree list
git submodule status
```
- **Esperado (H3):** `Dockerfile` modificado sin commitear (poetry export → pip), o
  bien limpio si alguien ya lo consolidó. `git stash list` puede tener el stash del
  24-jun. Sin submódulos, un solo worktree.
- **Interpretación:** cualquier archivo modificado que NO sea el Dockerfile documentado
  es **drift no registrado** → va directo a la tabla de riesgos (F10).
- **Desvíos:** si `git diff Dockerfile` difiere del Dockerfile "Opción A" ya commiteado
  en `origin/master`, comparar línea a línea: define si el reset de Fase 1 pierde algo.

```bash
cat /opt/yvy-server/current_version.txt
```
- **Esperado:** timestamp + hash del último deploy (Runbook §13.1 paso 3).
- **Interpretación:** debe coincidir con `git rev-parse HEAD`. Si no coincide → el
  último deploy no siguió el runbook.

---

## FASE 2 — Docker

Todo desde `/opt/yvy-server`.

### 2.1 Estado de servicios

```bash
docker compose ps
```
- **Esperado (H7):** 3 servicios: `yvy-api` (Up, healthy), `postgres` (Up, healthy),
  `nginx` (Up). Nombres de contenedor `yvy-api`, `yvy-postgres`, `yvy-nginx`.
- **Desvíos:** servicio llamado `app` en vez de `yvy-api` → el compose de prod fue
  reemplazado por el del repo; anotar (afecta TODOS los runbooks).

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
```
- **Esperado:** solo nginx publica `0.0.0.0:80->80` y `0.0.0.0:443->443`.
  `yvy-api` y `postgres` **sin** puertos publicados al host.
- **Interpretación (H7):** si `5432` aparece publicado → riesgo de seguridad ALTO
  (el compose del repo lo expone; el de prod no debería).

### 2.2 Imágenes: cuál, cuándo, de qué

```bash
docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.CreatedAt}}\t{{.Size}}'
docker inspect --format '{{.Config.Image}} {{.Image}}' yvy-postgres
docker image inspect $(docker inspect --format '{{.Image}}' yvy-postgres) \
  --format 'Created={{.Created}} Digest={{index .RepoDigests 0}}'
```
- **Esperado (H4):** postgres = `postgres:16-alpine` (o similar plano). **NO**
  `postgis/postgis` ni build local con pgvector.
- **Interpretación:** confirma que Fase 2A del plan de deploy (swap de imagen) sigue
  pendiente. Si ya es una imagen con extensiones → alguien avanzó sin registrar.

```bash
docker inspect yvy-api --format 'Image={{.Config.Image}} Created={{.Created}}'
docker inspect yvy-api --format '{{json .Config.Labels}}'
```
- **Interpretación:** la fecha `Created` de la imagen debe ser ≈ 24/25-jun (deploy del
  fix del 500). Muy anterior → el fix no está corriendo; muy posterior → hubo deploys
  no documentados.

### 2.3 Configuración efectiva de cada contenedor

```bash
# Restart policy + healthcheck + mounts (por contenedor)
for c in yvy-api yvy-postgres yvy-nginx; do
  echo "== $c =="
  docker inspect $c --format 'Restart={{.HostConfig.RestartPolicy.Name}}'
  docker inspect $c --format 'Health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
  docker inspect $c --format '{{range .Mounts}}{{.Type}} {{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'
done
```
- **Esperado:** restart `unless-stopped` en los 3; health `healthy` en api y postgres;
  mounts de yvy-api: `./logs/api:/app/logs`, `./data/releases:/app/data/releases`;
  postgres: volumen `postgres_data`; nginx: nginx.conf + letsencrypt + certbot webroot.
- **Desvíos:** cualquier bind mount no documentado → F9 (drift).

```bash
# Env vars: verificar PRESENCIA, no pegar valores (secretos)
docker inspect yvy-api --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | cut -d= -f1 | sort
```
- **Esperado:** `YVY_DB_DSN, YVY_ENV, YVY_ADMIN_TOKEN, YVY_SUPPORTED_SCHEMAS,`
  `YVY_CLOCK_SKEW_*, YVY_LOG_LEVEL, YVY_RELEASES_DIR, ...`
- **Interpretación:** `YVY_ENV` debe ser `production` (activa la validación de token
  fuerte). Verificar el valor de ESA sola: `docker compose exec yvy-api printenv YVY_ENV`.

### 2.4 Redes y volúmenes

```bash
docker network ls
docker volume ls
docker volume inspect yvy_postgres_data --format '{{.Mountpoint}} {{.CreatedAt}}' 2>/dev/null \
  || docker volume ls --format '{{.Name}}' | grep -i postgres
```
- **Interpretación:** la fecha de creación del volumen ≈ fecha del primer deploy.
  Si hay volúmenes huérfanos de postgres (de pruebas viejas) → anotar para limpieza
  futura (NO borrar ahora).

### 2.5 Logs recientes (errores silenciosos)

```bash
docker compose logs --tail=100 yvy-api | grep -iE '"level": ?"(ERROR|CRITICAL|WARNING)"' | tail -30
docker compose logs --tail=50 postgres | grep -iE 'error|fatal|panic' | tail -20
docker compose logs --tail=50 nginx  | grep -viE 'GET /api' | tail -20
```
- **Esperado:** sin ERROR/CRITICAL recurrentes. WARNINGs de envelope inválido son
  normales si algún edge está desalineado.
- **Interpretación:** un patrón repetido de `edge auth` / `401` → algún nodo sigue con
  la versión vieja del Edge (pre-fix HMAC 4 campos).

---

## FASE 3 — Aplicación

### 3.1 Identidad del build corriendo

```bash
docker compose exec yvy-api yvy-server version
docker compose exec yvy-api curl -s http://localhost:8000/api/v1/serverinfo
```
- **Esperado:** `version` = `0.9.1-dev+<algo>`; `git_commit_short` = **el mismo hash**
  que `git rev-parse --short HEAD` de F1.1; `build_date` ≈ fecha del último deploy;
  `schema_versions_supported: ["1.0"]`.
- **Interpretación:** si `git_commit_short` = `unknown` → el build se hizo sin
  `--build-arg` (deuda conocida F91 §5); si difiere del HEAD del repo → **el código
  en disco NO es el que corre** (imagen vieja) → riesgo ALTO, va a F10.

### 3.2 Liveness y headers (contrato con el Edge)

```bash
# Desde dentro (la API no expone puerto al host):
docker compose exec yvy-api curl -si http://localhost:8000/api/v1/healthz
```
- **Esperado:** `200`, body `{"status":"ok"}`, y los 3 headers del middleware:
  `X-Server-Time` (ISO 8601 con `Z`), `X-Schema-Versions-Supported: 1.0`,
  `X-Server-Version: 0.9.1-dev+...`.
- **Interpretación:** `X-Server-Time` es lo que recalibra el reloj del Edge en errores
  CLOCK — verificar que difiera < 2s de `date -u`.

```bash
# Desde tu PC, vía nginx (la vía real de los edges):
curl -si https://edge.yvyedge.com/api/v1/healthz
curl -s  https://edge.yvyedge.com/api/v1/serverinfo | python3 -m json.tool
```
- **Esperado:** 200 con los mismos headers + TLS válido.
- **Desvíos:** 502 → yvy-api caído o nginx apuntando a nombre de servicio incorrecto
  (`app` vs `yvy-api` — el bug clásico del drift de compose).

### 3.3 Sync version (canal piggyback)

```bash
docker compose logs yvy-api | grep initial_sync_version | tail -1
docker compose exec postgres psql -U yvyserver -d yvy -tc "SELECT sync_version FROM sync_state;"
```
- **Esperado (H9):** ambos valores **iguales** (≥ 2 tras el ciclo OTA de junio).
- **Interpretación:** si difieren → hubo un `publish-update` sin reiniciar `yvy-api`
  (paso 6 del flujo OTA olvidado); los edges no se enteran del release.

### 3.4 Superficie HTTP real (rutas expuestas)

```bash
docker compose exec yvy-api curl -s http://localhost:8000/openapi.json \
  | python3 -c "import json,sys; d=json.load(sys.stdin); [print(m.upper(), p) for p,ms in sorted(d['paths'].items()) for m in ms]"
```
- **Esperado (H10, pre-M0):** `/api/v1/healthz`, `/serverinfo`, `/admin/healthz`,
  `/admin/stats`, `/admin/failures`, `/admin/failures/{id}/resolve`,
  `/ingest/{observation,sensor,event,comment}`, `/edge/sync`, `/edge/updates/{filename}`.
  **SIN** `/public/*`.
- **Interpretación:** si `/public/*` aparece → prod NO corre master pre-M0; H1 falsa,
  re-evaluar todo el plan de deploy.

```bash
# CORS: preflight simulado (pre-M0 no debería haber CORSMiddleware)
curl -si -X OPTIONS https://edge.yvyedge.com/api/v1/healthz \
  -H "Origin: https://yvyedge.com" -H "Access-Control-Request-Method: GET" | head -15
```
- **Esperado pre-M0:** sin headers `Access-Control-Allow-*` (el CORS llega con C11).

---

## FASE 4 — Base de datos

Consola: `docker compose exec postgres psql -U yvyserver -d yvy`
(si falla el usuario, probar `-U yvy` y **anotar cuál es el real** — resuelve la
contradicción D5 de la auditoría de código).

### 4.1 Identidad y versión

```sql
SELECT version();
SELECT current_user, current_database();
\du
\l
```
- **Esperado:** PostgreSQL 16.x; usuario `yvyserver`; DB `yvy`; posible usuario
  read-only `yvyreader` (Runbook §7.2).

### 4.2 Extensiones (H4)

```sql
SELECT extname, extversion FROM pg_extension ORDER BY 1;
SELECT name FROM pg_available_extensions WHERE name IN ('vector','postgis');
```
- **Esperado pre-M0:** solo `plpgsql` (quizás `pgcrypto` si gen_random_uuid se usó).
  `vector`/`postgis` **ni instaladas ni disponibles** (imagen alpine plana).
- **Interpretación:** confirma que Fase 2A (swap de imagen) es prerequisito real de M0.

### 4.3 Schemas, tablas, views (H5)

```sql
\dn
\dt public.*
\dt bronze.*
\dv
```
- **Esperado:** schemas `public` + `bronze`. Tablas: `station`, `station_credentials`,
  `sync_state`, `edge_releases`, `alembic_version`, `bronze.sensor_raw`,
  `bronze.empirical_observation_raw`, `bronze.empirical_event_raw`,
  `bronze.ingest_log`, `bronze.ingest_failures`. **Sin** views, **sin** schemas
  `canonical/silver/gold/knowledge/auth/exports/catalog`.
- **Desvíos:** si existe `canonical` o `raw_record` → alguien aplicó M0 (o parte) →
  **PARAR el plan de deploy y reconstruir la historia primero.**

### 4.4 Estructura fina (índices, constraints, FK)

```sql
\d bronze.sensor_raw
\d station
SELECT conname, conrelid::regclass, confrelid::regclass
FROM pg_constraint WHERE contype='f' ORDER BY 2;
SELECT schemaname, indexname, tablename FROM pg_indexes
WHERE schemaname IN ('public','bronze') ORDER BY tablename;
```
- **Esperado:** los índices de la migración 001/002 (`idx_sensor_raw_station_recv`,
  `idx_ingest_log_failures` parcial, `ix_edge_releases_active_released` parcial, etc.)
  y FKs `station_credentials→station`, `*_raw→station`.
- **Interpretación:** índice faltante o de más = la DB fue tocada a mano → F9.
- **Nota:** verificar si `station` tiene las columnas C10 (`code`, `locality`,
  `latitude`...). **Pre-M0 NO debe tenerlas** (llegan con la migración 003).

### 4.5 Datos: volúmenes y actividad (H6)

```sql
SELECT 'sensor_raw' t, COUNT(*), MAX(received_at) FROM bronze.sensor_raw
UNION ALL SELECT 'empirical_obs', COUNT(*), MAX(received_at) FROM bronze.empirical_observation_raw
UNION ALL SELECT 'empirical_event', COUNT(*), MAX(received_at) FROM bronze.empirical_event_raw
UNION ALL SELECT 'ingest_log', COUNT(*), MAX(received_at) FROM bronze.ingest_log
UNION ALL SELECT 'ingest_failures', COUNT(*), MAX(received_at) FROM bronze.ingest_failures;
```
- **Esperado:** `sensor_raw` con `MAX(received_at)` de **hace minutos** (IEA-07 en vivo)
  y ~10k+ filas; `empirical_observation_raw` con MAX ≈ **2026-06-08** (DIAG-002).
- **Interpretación:** ese conteo total es el input del gate M0 §2 ("~10k filas").
  Registrarlo con fecha: es el número contra el que se verificará el backfill.

```sql
-- Ritmo de ingesta reciente (¿1/min de IEA-07?)
SELECT date_trunc('hour', received_at) h, COUNT(*)
FROM bronze.sensor_raw WHERE received_at > now() - interval '24 hours'
GROUP BY 1 ORDER BY 1 DESC LIMIT 6;

-- Errores de ingesta últimas 48h por clase
SELECT failure_class, COUNT(*) FROM bronze.ingest_log
WHERE received_at > now() - interval '48 hours' AND failure_class IS NOT NULL
GROUP BY 1;

-- DLQ pendiente
SELECT COUNT(*) FROM bronze.ingest_failures WHERE resolved_at IS NULL;

-- Estaciones y credenciales activas (sin exponer keys)
SELECT station_uuid, name, created_at, deactivated_at FROM station;
SELECT station_uuid, key_version, deactivated_at FROM station_credentials;

-- Releases OTA (H9)
SELECT edge_version, min_edge_version, is_active, released_at, sync_version
FROM edge_releases ORDER BY released_at DESC;
```
- **Esperado:** ~60 filas/hora en sensor_raw; DLQ en 0 o valores bajos conocidos;
  2 estaciones (IEA-07 + PC de test); `1.5.1` con `is_active=false`, `1.3.2` inactivo.

### 4.6 Salud física de la DB

```sql
SELECT pg_size_pretty(pg_database_size('yvy'));
SELECT relname, pg_size_pretty(pg_total_relation_size(oid))
FROM pg_class WHERE relnamespace='bronze'::regnamespace AND relkind='r' ORDER BY 2 DESC;
SELECT count(*) FROM pg_stat_activity;
```
- **Interpretación:** dimensiona el backup y la ventana M0 (el backfill de 10k filas
  es segundos; si hay 10× más de lo esperado, recalcular la ventana).

---

## FASE 5 — Migraciones: repo vs base

```sql
SELECT version_num FROM alembic_version;
```
- **Esperado (H5):** `0002_edge_releases`.

```bash
# Head del código EN LA IMAGEN corriendo (no del repo en disco):
docker compose exec yvy-api alembic heads
docker compose exec yvy-api alembic current
# OJO: SIN "poetry run" — el runtime de prod no tiene poetry (Opción A).
# Si "alembic" no está en PATH del contenedor: docker compose exec yvy-api python -m alembic heads
```
- **Esperado:** `heads` = `0002_edge_releases` (código master pre-M0) y
  `current` = `0002_edge_releases (head)`. **head == current → sin migraciones
  pendientes.**
- **Interpretación de desvíos:**
  - `heads` = `0003_m0_canonical_model` → la imagen se buildeó desde `plan_fases_api`
    (¡el entrypoint migraría M0 en el próximo restart!) → **riesgo CRÍTICO**, verificar
    ya mismo qué rama buildeó la imagen.
  - `current` < `heads` → migración pendiente que se va a auto-aplicar en el próximo
    restart del contenedor (el entrypoint corre `upgrade head`). Documentar ANTES de
    cualquier restart.

```bash
# Head del repo EN DISCO (puede diferir de la imagen):
ls /opt/yvy-server/server-code/alembic/versions/
```
- **Esperado:** `001_init.py`, `002_edge_releases.py` (sin 003 si el repo está en
  master pre-consolidación… **con** `003_m0_canonical_model.py` si ya se trajo el
  master consolidado). Anotar exactamente qué hay: define el efecto del próximo build.

---

## FASE 6 — Configuración

### 6.1 `.env` y secretos (verificar sin exponer)

```bash
ls -la /opt/yvy-server/.env /opt/yvy-server/secrets/ 2>/dev/null
stat -c '%a %U %G %n' /opt/yvy-server/.env
grep -c . /opt/yvy-server/.env                      # cuántas líneas (no el contenido)
grep -oE '^[A-Z_]+' /opt/yvy-server/.env | sort     # SOLO nombres de variables
```
- **Esperado:** permisos `600`, owner correcto; variables `DOMAIN, POSTGRES_*,`
  `YVY_ADMIN_TOKEN, YVY_ENV, YVY_LOG_LEVEL, ...`
- **Interpretación:** si el `.env` es legible por otros usuarios → riesgo ALTO
  (contiene el admin token y el password de postgres).

### 6.2 Directorios operativos

```bash
ls -la /opt/yvy-server/data/releases/       # tars OTA + ownership yvyadmin
ls -la /opt/yvy-server/logs/api/ | tail -5
ls -la /opt/yvy-server/backups/ | tail -10  # o la ruta del script de backup
df -h /opt /var
free -h
```
- **Esperado:** releases con `-rw-r--r-- yvyadmin`; logs recientes; **backups de los
  últimos días presentes y con tamaño > 0** (H11); disco < 80%.

### 6.3 Automatizaciones del host

```bash
sudo crontab -l; crontab -l
systemctl list-timers --all | grep -iE 'certbot|yvy|backup'
systemctl status docker --no-pager | head -5
systemctl is-enabled docker
ls /etc/systemd/system/ | grep -i yvy
sudo systemctl status yvy* --no-pager 2>/dev/null | head -20
```
- **Esperado:** cron de backup 03:00 (H11); timer de renovación certbot activo;
  docker `enabled` (arranca al boot); unit `yvy-stack.service` si se aplicó
  Runbook §12.2.
- **Desvíos:** sin unit systemd del stack → tras un reboot el stack depende solo de
  `restart: unless-stopped` (suficiente si los contenedores estaban Up al apagar,
  pero anotar).

---

## FASE 7 — Seguridad

```bash
# Usuarios y sudo
cat /etc/passwd | grep -E 'bash|sh$' ; sudo cat /etc/sudoers.d/* 2>/dev/null; sudo grep -E '^[^#].*ALL' /etc/sudoers

# SSH endurecido
sudo sshd -T | grep -iE 'passwordauthentication|permitrootlogin|pubkeyauthentication'
cat ~/.ssh/authorized_keys | wc -l    # cuántas claves (no pegarlas)

# Firewall
sudo ufw status verbose
ss -tlnp | grep -vE '127.0.0.1|::1'   # puertos escuchando hacia afuera
```
- **Esperado:** `PasswordAuthentication no`, `PermitRootLogin no`; ufw con 22, 80, 443
  ALLOW y default deny; hacia afuera SOLO sshd + docker-proxy de 80/443.
- **Interpretación:** si `5432` o `8000` escuchan en `0.0.0.0` → exposición directa,
  riesgo ALTO (H7 falsa).

```bash
# nginx y TLS
docker compose exec nginx nginx -t
docker compose exec nginx cat /etc/nginx/nginx.conf | grep -nE 'server_name|limit_req|proxy_pass'
echo | openssl s_client -connect edge.yvyedge.com:443 -servername edge.yvyedge.com 2>/dev/null | openssl x509 -noout -dates -subject
sudo certbot certificates 2>/dev/null | grep -E 'Domains|Expiry'

# fail2ban
sudo systemctl status fail2ban --no-pager 2>/dev/null | head -3
sudo fail2ban-client status sshd 2>/dev/null
```
- **Esperado (H8):** nginx `syntax ok`; **sin `limit_req`** (confirmando el gap de
  rate limiting); cert válido > 20 días con dominios `edge.yvyedge.com` (y verificar
  si `api.yvyedge.com` ya fue expandido o sigue pendiente — precondición de Fase 2C);
  fail2ban activo o ausente (anotar — el diagnóstico del 24-jun sugería que no era
  fail2ban, confirmar si existe).
- **Interpretación clave:** si el cert **no** incluye `api.yvyedge.com` y el DNS del
  subdominio no resuelve → la Fase 2C del deploy tiene 2 prerequisitos manuales
  (DNS + certbot --expand) que hay que agendar con Luis.

```bash
# DNS de los 3 subdominios (desde tu PC)
nslookup yvyedge.com; nslookup edge.yvyedge.com; nslookup api.yvyedge.com
```
- **Esperado:** `edge.` → IP del server (`153.67.184.39`); `api.` → misma IP o **no
  resuelve todavía**; apex → según estado de W0 (ESIM o Vercel).

---

## FASE 8 — Plan de pruebas de integración (ejecutable)

> Orden: primero las **read-only** (8.1-8.6), después las que escriben datos
> (`[ESCRIBE]`), y las intrusivas (`[INTRUSIVA]`) SOLO en ventana acordada.
> Regla de rollback general: ninguna prueba read-only necesita rollback; las que
> escriben dejan filas append-only identificables por `event_id`/timestamp (no se
> borran: Bronze es inmutable — se documenta el event_id de prueba).

### 8.1 healthz (read-only)
- **Objetivo:** liveness por las 2 vías. **Comando:** F3.2.
- **Esperado:** 200 + 3 headers. **Rollback:** N/A.

### 8.2 admin API (read-only, requiere token)
```bash
TOKEN=$(grep '^YVY_ADMIN_TOKEN' /opt/yvy-server/.env | cut -d= -f2)
docker compose exec yvy-api curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/admin/healthz | python3 -m json.tool
docker compose exec yvy-api curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/admin/stats | python3 -m json.tool
docker compose exec yvy-api curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/api/v1/admin/healthz   # sin token
```
- **Esperado:** `db_responsive: true` + counts de 6 tablas pre-M0; stats coherentes con
  F4.5; sin token → `401`. **Interpretación:** counts ≠ F4.5 → algo escribe por fuera.
- **Rollback:** N/A. (Limpiar `TOKEN` de la shell: `unset TOKEN; history -d $(history 1)`).

### 8.3 public API — ausencia esperada (read-only)
- **Comando:** `curl -s -o /dev/null -w '%{http_code}\n' https://edge.yvyedge.com/api/v1/public/network`
- **Esperado pre-M0:** `404`. **Interpretación:** `200` → H10 falsa, hay código C11 en prod.

### 8.4 edge sync sin auth (read-only, negativo)
- **Comando:** `curl -si https://edge.yvyedge.com/api/v1/edge/sync | head -5`
- **Esperado:** `401` con `"error_class":"AUTH"` (headers HMAC faltantes).
- **Interpretación:** valida que la autenticación del canal pull está activa.

### 8.5 edge sync autenticado (read-only, requiere credencial del nodo de test)
- **Objetivo:** verificar el contrato de 4 campos post-fix (ERR-001 cerrado).
- **Comando:** desde el **PC de test** (que ya tiene el Edge 1.5.x con credencial):
  `YVY_CLI.bat check-updates` — o reproducir la firma con el snippet del
  RUNBOOK_DEPLOY_CODE §9.4 adaptado a headers.
- **Esperado:** `200` con `sync_version` real y `updates.available=false` (no hay
  release activo mayor). **Rollback:** N/A.

### 8.6 nginx / TLS / postgres / docker (read-only)
- Ya cubiertos en F7 (nginx -t, cert), F4 (psql), F2 (ps/health). Marcar en checklist.

### 8.7 ingest sensor/observation/event `[ESCRIBE]`
- **Objetivo:** pipeline completo HMAC→UPSERT→ingest_log con una fila de prueba.
- **Comando:** script Python del RUNBOOK_DEPLOY_CODE §9.4 con las credenciales del
  **PC de test** (NO las de IEA-07), apuntando a `/api/v1/ingest/observation`,
  luego `/sensor` y `/event`. Ejecutarlo **dos veces** con el mismo `event_id`.
- **Esperado:** 1ª vez `ingest_result: inserted`; 2ª vez `duplicate` (idempotencia
  pre-M0 funcionando — este es el comportamiento que M0 debe preservar).
  Header `X-Server-Sync-Version` presente con el valor de F3.3.
- **Interpretación:** el par inserted/duplicate es la **prueba de contrato de
  idempotencia**; guardar el `event_id` usado en la evidencia.
- **Rollback:** no se borra (append-only). Registrar el event_id como dato de prueba.

### 8.8 OTA end-to-end `[ESCRIBE]` — diferido
- **Estado:** bloqueado a propósito hasta construir el dummy `1.5.2` (plan de la
  sesión del 24-jun §5). No ejecutar en esta auditoría; queda como pendiente P5.

### 8.9 backup + restore `[ESCRIBE en /backups, no toca la DB viva]`
```bash
sudo /opt/yvy-server/scripts/backup_postgres.sh           # o el path real (F6.2)
ls -lh /opt/yvy-server/backups/ | tail -3
# Verificar integridad SIN restaurar sobre la DB real:
gunzip -t /opt/yvy-server/backups/<ultimo>.sql.gz && echo "GZIP OK"
# Restore a una DB scratch (prueba de restaurabilidad REAL):
docker compose exec postgres createdb -U yvyserver yvy_restore_test
zcat /opt/yvy-server/backups/<ultimo>.sql.gz | docker compose exec -T postgres psql -U yvyserver -d yvy_restore_test -q
docker compose exec postgres psql -U yvyserver -d yvy_restore_test -c "SELECT COUNT(*) FROM bronze.sensor_raw;"
docker compose exec postgres dropdb -U yvyserver yvy_restore_test
```
- **Esperado:** restore sin errores y conteo == F4.5.
- **Interpretación:** esta prueba es **precondición dura de M0** ("backup verificado,
  no solo creado"). **Rollback:** `dropdb yvy_restore_test` (incluido).

### 8.10 restart de servicio `[INTRUSIVA — ~10s de downtime de API]`
```bash
docker compose restart yvy-api
# inmediatamente después:
docker compose ps; docker compose logs --tail=20 yvy-api
docker compose exec yvy-api curl -s http://localhost:8000/api/v1/healthz
```
- **Esperado:** vuelve `healthy` < 30s; log muestra `Migrations applied.` (no-op) +
  `startup complete` + `initial_sync_version=N`.
- **Riesgo controlado:** los edges reintentan (offline-first); no se pierde dato.
- **Rollback:** N/A (es un restart).
- **⚠️ Precondición:** F5 verificada — si hubiera una migración pendiente en la
  imagen, este restart la aplicaría. NO correr antes de F5.

### 8.11 reinicio completo del stack `[INTRUSIVA — ventana]`
```bash
docker compose down     # SIN -v. Jamás -v.
docker compose up -d
docker compose ps
```
- **Esperado:** los 3 servicios vuelven healthy; datos intactos (F4.5 re-ejecutado da
  los mismos conteos + lo ingresado en el intervalo).

### 8.12 recuperación tras reboot `[INTRUSIVA — ventana, con Luis]`
```bash
sudo reboot
# tras ~2 min, reconectar:
uptime; docker compose ps; curl -si https://edge.yvyedge.com/api/v1/healthz | head -3
```
- **Esperado:** stack arriba solo (docker enabled + restart policy / unit systemd).
- **Interpretación:** si no levanta solo → falta la unit del Runbook §12.2 → riesgo
  ALTO para cortes de luz (el server está en una institución educativa).

---

## FASE 9 — Integridad: drift entre repo / server / Docker / DB / docs

| Verificación | Comando | Qué detecta |
|---|---|---|
| Working tree sucio | `git -C server-code status --porcelain` | Hotfixes sin commitear (H3: Dockerfile) |
| Compose de prod vs repo | `diff /opt/yvy-server/docker-compose.yml /opt/yvy-server/server-code/docker-compose.yml` | Drift estructural conocido (servicios `app` vs `yvy-api`, puerto 5432). **Esperado: MUY distinto.** El objetivo es inventariar exactamente en qué |
| nginx de prod vs repo | `diff /opt/yvy-server/nginx/conf/nginx.conf /opt/yvy-server/server-code/nginx/nginx.conf` (rutas según F1.0) | Drift de proxy: nombres de upstream, rate limiting, server blocks de api. |
| Imagen vs disco | `serverinfo.git_commit_short` (F3.1) vs `git rev-parse --short HEAD` (F1.1) | Imagen buildeada de otro commit |
| DB vs migraciones | F5 completa | Schema tocado a mano |
| Scripts no versionados | `ls /opt/yvy-server/scripts/` vs `ls server-code/scripts/` | Scripts operativos que viven solo en el server |
| Secretos fuera del repo | `ls /opt/yvy-server/secrets/ /opt/yvy-server/*.json 2>/dev/null` | Credenciales de estaciones olvidadas en disco (deberían estar solo en el pendrive) |
| Objetos DB huérfanos | `\dt *.*` filtrado vs modelos ORM | Tablas creadas a mano (p.ej. pruebas del DQS) |
| Docs vs realidad | Este documento completo | H1-H11 |

**Interpretación global:** cada `diff` con contenido es un ítem de la Fase
"M0 Consolidación" (abajo). La meta final es que `/opt/yvy-server` completo sea
reproducible desde el repo + `.env` + certificados.

---

## FASE 10 — Resultado

### 10.1 Checklist de ejecución

```
FASE 1 — Repositorio
[ ] 1.0 Layout /opt/yvy-server descubierto
[ ] 1.1 remote / branch / HEAD / log (¿736e707 presente?)
[ ] 1.2 fetch + ahead/behind origin/master cuantificado
[ ] 1.3 status/diff/stash/worktree/submodules + current_version.txt
FASE 2 — Docker
[ ] 2.1 compose ps (nombres reales de servicios) + puertos publicados
[ ] 2.2 imágenes: postgres alpine? fecha de build de yvy-api
[ ] 2.3 restart policy / health / mounts / env (nombres)
[ ] 2.4 networks + volúmenes
[ ] 2.5 logs recientes sin ERROR/CRITICAL
FASE 3 — Aplicación
[ ] 3.1 version + git_commit_short == HEAD del repo
[ ] 3.2 healthz interno y vía nginx + 3 headers
[ ] 3.3 sync_version log == DB
[ ] 3.4 openapi: rutas esperadas, SIN /public/*
FASE 4 — Base de datos
[ ] 4.1 versión / usuario / DBs
[ ] 4.2 extensiones (sin vector/postgis)
[ ] 4.3 schemas y tablas pre-M0, sin views
[ ] 4.4 índices / FK / columnas de station SIN campos C10
[ ] 4.5 conteos + actividad (sensor vivo, obs estancada) → REGISTRAR números
[ ] 4.6 tamaños
FASE 5 — Migraciones
[ ] alembic_version == 0002 == heads de la imagen == versiones en disco
FASE 6 — Configuración
[ ] .env 600 + variables presentes; releases/ logs/ backups/ ok; cron+timers
FASE 7 — Seguridad
[ ] sshd endurecido / ufw / puertos / nginx -t / cert (¿incluye api.?) / fail2ban
[ ] DNS de los 3 subdominios
FASE 8 — Integración
[ ] 8.1-8.6 read-only OK
[ ] 8.7 ingest x3 + duplicate [ESCRIBE] (event_id registrado: ______)
[ ] 8.9 backup + restore a scratch DB verificado
[ ] 8.10-8.12 intrusivas (solo en ventana): restart / down-up / reboot
FASE 9 — Integridad
[ ] diffs compose / nginx / scripts / secretos inventariados
```

### 10.2 Estado por componente (completar con evidencia)

| Componente | Estado | Evidencia (fase) | Nota |
|---|---|---|---|
| Git repo / rama / divergencia | ⬜ OK / ⚠ / ✖ | F1 | |
| Imagen yvy-api == código | ⬜ | F3.1 | |
| Postgres + datos | ⬜ | F4 | |
| Migraciones sin pendientes | ⬜ | F5 | |
| Ingesta en vivo (IEA-07) | ⬜ | F4.5 | |
| Canal pull / OTA | ⬜ | F8.4/8.5 | |
| nginx / TLS / DNS api. | ⬜ | F7 | |
| Backups restaurables | ⬜ | F8.9 | |
| Arranque tras reboot | ⬜ | F8.12 | |
| Drift repo↔server | ⬜ | F9 | |

### 10.3 Riesgos (pre-cargados; confirmar/ajustar con la evidencia)

1. **Migración pendiente latente en imagen** (si F5 detecta heads>current): un restart
   cualquiera aplicaría M0 sin ventana. Verificar ANTES de cualquier restart.
2. **Divergencia git sin reconciliar (P1)**: bloquea la Fase 1 del deploy; el
   `reset --hard origin/master` es seguro SOLO si F1.3 confirma que el único cambio
   local es el Dockerfile ya consolidado en origin.
3. **Backup nunca restaurado de verdad** (si F8.9 nunca se corrió antes): precondición
   dura de M0.
4. **Cert/DNS de `api.yvyedge.com` pendientes** (si F7 lo confirma): bloquea Fase 2C.
5. **Sin rate limiting** en ingesta pública (F7): pre-launch web es obligatorio.
6. **Sin unit systemd del stack** (si F6.3 lo confirma): corte de luz = stack caído
   hasta intervención manual.
7. **Drift compose/nginx repo↔server**: cada deploy futuro se hace "de memoria".

### 10.4 Plan de trabajo resultante

- **M-1 Auditoría** (este documento): ejecutar F1-F7 + F8 read-only en una sesión
  (~60-90 min); F8.9 en cualquier momento; F8.10-8.12 en la próxima ventana.
  Completar 10.2 y 10.3 con evidencia y commitear el resultado como
  `AUDITORIA_SERVIDOR_M-1_RESULTADO_<fecha>.md`.
- **M0 Consolidación**: reconciliar git de prod (P1) → Fase 1 del
  `PLAN_APLICACION_SERVIDOR.md` (deploy master) → versionar compose/nginx de prod en
  el repo (`docker-compose.prod.yml`) → recién entonces Fase 2 (M0 canónico) **con los
  fixes QW1-QW6 de la auditoría de código aplicados y CI verde**.
- **M1 Hardening**: rate limiting nginx, unit systemd del stack, `.env` 600,
  fail2ban, nonce replay, 401 uniforme en edge auth, mypy bloqueante.
- **M2 Testing**: suite de integración contra pgvector en CI (ingest E2E firmado,
  duplicado, DLQ, views, OTA); job de migración con fixture de datos.
- **M3 Observabilidad**: /metrics Prometheus, alerta "estación muda > 30 min",
  verificación mensual automática de restore.
- **M4 Automatización**: script de deploy único (build args de versión incluidos),
  restore drill programado, publish-update sin restart manual (releer sync_version).
- **M5 Performance**: C16 cache, revisar uptime_30d, pool_pre_ping (diferible — la
  carga actual no lo exige).
- **M6 Nuevas funcionalidades**: retomar el roadmap de producto (D1 Silver → DQS →
  D8a/D4) según el plan maestro, ya sobre base consolidada.

---

*Runbook generado el 2026-07-07 como fase M-1 del plan post-auditoría de código.
Repetible en cualquier momento; los valores "esperados" reflejan el estado documentado
al 07-jul-2026 y deben actualizarse tras cada deploy.*
