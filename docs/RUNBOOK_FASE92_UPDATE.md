# YVY · RUNBOOK · Actualización Fase 9.2 + Referencia de Endpoints

**Versión:** 1.0  
**Fecha:** 08-Jun-2026  
**Aplica a:** servidor Debian 12 con Fase 9.1 ya deployada (`RUNBOOK_DEPLOY_CODE.md` aplicado).  
**Tiempo estimado:** 20–30 minutos.

---

## Qué introduce Fase 9.2

| Categoría | Cambio |
|---|---|
| **Código** | 5 endpoints nuevos, 1 refactor de use cases, 3 repos extendidos |
| **DB** | Migración `0002_edge_releases`: tablas `public.sync_state` y `public.edge_releases` |
| **Config** | Variable de entorno `YVY_RELEASES_DIR` + volumen Docker para packages |
| **nginx** | Bloque nuevo para `/api/v1/edge/` (timeouts largos, buffering off para streaming) |
| **CLI** | Subcomando `yvy-server publish-update` |

---

## 1. Preparar el servidor

### 1.1 Actualizar el código

```bash
ssh yvyadmin@<IP_LAN>
cd /opt/yvy-server/server-code

git fetch
git log HEAD..origin/main --oneline      # verificar qué entra
git pull

cd /opt/yvy-server
cat > current_version.txt << EOF
deployed_at: $(date -Iseconds)
git_commit: $(cd server-code && git rev-parse HEAD)
git_branch: $(cd server-code && git rev-parse --abbrev-ref HEAD)
deployed_by: $(whoami)
EOF
cat current_version.txt
```

### 1.2 Crear directorio de releases

Los packages `.tar.gz` del Edge se almacenan acá. El volumen debe existir antes de levantar el container.

```bash
mkdir -p /opt/yvy-server/data/releases
chmod 755 /opt/yvy-server/data/releases
```

### 1.3 Actualizar `.env`

Agregar al final de `/opt/yvy-server/.env`:

```bash
# ─── Canal Core→Edge (Fase 9.2) ─────────────────────────────────────────
# Directorio donde se almacenan los .tar.gz publicados con yvy-server publish-update
YVY_RELEASES_DIR=/app/data/releases
```

---

## 2. Actualizar nginx.conf

Agregar el bloque de `/api/v1/edge/` dentro del server block HTTPS (después del bloque de `/api/v1/admin/` y antes del `location /`).

```bash
cd /opt/yvy-server/nginx/conf
cp nginx.conf "nginx.conf.bak.$(date +%Y%m%d)"
nano nginx.conf
```

Insertar (reemplazando `# Cualquier otra ruta: 404` por lo siguiente):

```nginx
        # Canal Core→Edge: sync y descarga de updates
        # Timeouts extendidos para descarga de packages (40+ MB en conectividad IEA)
        location /api/v1/edge/ {
            proxy_pass http://yvy-api:8000;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;
            proxy_connect_timeout 5s;
            proxy_send_timeout 300s;
            proxy_read_timeout 300s;
            # Sin buffering: streaming directo del binario al Edge
            proxy_buffering off;
            proxy_request_buffering off;
        }

        # Cualquier otra ruta: 404
        location / {
            return 404 "Not Found\n";
        }
```

Verificar sintaxis:

```bash
docker compose exec nginx nginx -t
# Esperado: syntax is ok
```

---

## 3. Actualizar docker-compose.yml

En el servicio `yvy-api`, agregar el volumen de releases y la variable de entorno:

```bash
nano /opt/yvy-server/docker-compose.yml
```

En la sección `environment` de `yvy-api`:

```yaml
      YVY_RELEASES_DIR: ${YVY_RELEASES_DIR:-/app/data/releases}
```

En la sección `volumes` de `yvy-api` (junto a `./logs/api:/app/logs`):

```yaml
      - ./data/releases:/app/data/releases
```

Verificar que el archivo sea válido:

```bash
docker compose -f /opt/yvy-server/docker-compose.yml config | grep -A 10 "yvy-api"
```

---

## 4. Backup previo + redeploy

```bash
cd /opt/yvy-server

# Backup antes de migrar
./scripts/backup_postgres.sh

# Rebuild
GIT_COMMIT=$(cd server-code && git rev-parse HEAD)
GIT_COMMIT_SHORT=$(cd server-code && git rev-parse --short HEAD)
BUILD_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

docker compose build yvy-api \
    --build-arg GIT_COMMIT="$GIT_COMMIT" \
    --build-arg GIT_COMMIT_SHORT="$GIT_COMMIT_SHORT" \
    --build-arg BUILD_DATE="$BUILD_DATE"

# Reiniciar solo yvy-api (postgres y nginx quedan en pie)
docker compose up -d --no-deps yvy-api

# Verificar que la migración corrió
docker compose logs yvy-api | grep -E "(alembic|migrat|entrypoint|startup)"
# Debe mostrar: "[entrypoint] Migrations applied." + "startup complete"

# Recarga nginx (nueva config)
docker compose exec nginx nginx -s reload
```

---

## 5. Verificar tablas nuevas

```bash
docker compose exec postgres psql -U yvyserver -d yvy -c "
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('sync_state', 'edge_releases')
ORDER BY table_name;
"
```

Esperado: 2 filas. Si aparecen, la migración `0002_edge_releases` aplicó correctamente.

```bash
# Verificar fila inicial en sync_state
docker compose exec postgres psql -U yvyserver -d yvy -c \
    "SELECT id, sync_version FROM public.sync_state;"
# Esperado: id=1, sync_version=0
```

---

## 6. Smoke tests Fase 9.2

### 6.1 Nuevos endpoints de ingest

```bash
ADMIN_TOKEN=$(cat /opt/yvy-server/secrets/yvy_admin_token.txt)
BASE="https://yvy-edge.<dominio>.edu.ar"

# Admin stats
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
    "$BASE/api/v1/admin/stats" | python3 -m json.tool
# Esperado: JSON con total_attempts, total_successes, etc.

# Admin failures (DLQ vacío en instalación limpia)
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
    "$BASE/api/v1/admin/failures" | python3 -m json.tool
# Esperado: {"total": 0, "items": []}
```

### 6.2 Edge sync sin auth (debe dar 401)

```bash
curl -s -i "$BASE/api/v1/edge/sync"
# Esperado: HTTP 401, body con "error_class":"AUTH"
```

### 6.3 Header X-Server-Sync-Version en ingest

```bash
curl -s -i -X POST "$BASE/api/v1/ingest/observation" \
    -H "Content-Type: application/json" \
    -d '{"test": true}' | grep -i "x-server-sync-version"
# Esperado: x-server-sync-version: 0
# (0 porque aún no se publicó ningún release)
```

### 6.4 [VERIFICAR]

- [ ] Tablas `sync_state` y `edge_releases` existen
- [ ] `GET /admin/stats` responde con conteos correctos
- [ ] `GET /admin/failures` responde `{"total":0,"items":[]}`
- [ ] `GET /edge/sync` sin auth devuelve 401
- [ ] Header `X-Server-Sync-Version: 0` presente en respuestas de ingest

---

## 7. Publicar el primer release Edge (cuando corresponda)

Cuando el código del Edge esté listo para distribuir:

```bash
# En tu máquina local — copiar el package al server
scp yvy-edge-0.9.2.tar.gz yvyadmin@<IP_LAN>:/tmp/

# En el server
docker compose exec yvy-api yvy-server publish-update \
    --file /tmp/yvy-edge-0.9.2.tar.gz \
    --version "0.9.2" \
    --min-edge-version "0.9.0" \
    --notes "Primera release publicada desde Core. Mejoras de estabilidad USB."

# Verificar
docker compose exec postgres psql -U yvyserver -d yvy -c \
    "SELECT edge_version, package_sha256, package_size_bytes, released_at, sync_version
     FROM public.edge_releases ORDER BY released_at DESC LIMIT 3;"

# Reiniciar yvy-api para que el nuevo sync_version se cachee en app.state
docker compose up -d --no-deps yvy-api
```

> **Nota:** El servidor cachea `sync_version` al arrancar. Después de publicar un release,
> reiniciar `yvy-api` para que los headers `X-Server-Sync-Version` reflejen el valor nuevo.
> Los Edges que ya están corriendo lo detectarán en su próxima respuesta de ingest.

---

## Referencia completa de endpoints

### Dominio `edge.yvyedge.com`

**Ingest (Edge → Core) · auth: HMAC-SHA256 sobre body**

| Método | Path | Estado | Descripción |
|---|---|---|---|
| `GET` | `/api/v1/healthz` | ✅ 9.1 | Liveness check público |
| `GET` | `/api/v1/serverinfo` | ✅ 9.1 | Versión + schemas soportados |
| `POST` | `/api/v1/ingest/observation` | ✅ 9.1 | Observación empírica del formulario |
| `POST` | `/api/v1/ingest/sensor` | ✅ 9.2 | Lectura USB del sensor pywws |
| `POST` | `/api/v1/ingest/event` | ✅ 9.2 | Evento puntual (helada, granizo, viento) |
| `POST` | `/api/v1/ingest/comment` | 🚧 stub 501 | Comentarios (Fase 9.3+) |

**Admin (operador) · auth: Bearer token**

| Método | Path | Estado | Descripción |
|---|---|---|---|
| `GET` | `/api/v1/admin/healthz` | ✅ 9.1 | DB connectivity + row counts |
| `GET` | `/api/v1/admin/stats` | ✅ 9.2 | Estadísticas de ingest (totales, por endpoint, por error class) |
| `GET` | `/api/v1/admin/failures?limit=N&offset=N` | ✅ 9.2 | DLQ paginado (failures post-HMAC sin resolver) |
| `POST` | `/api/v1/admin/failures/{id}/resolve` | ✅ 9.2 | Resolver entrada del DLQ |

**Canal Core→Edge · auth: HMAC-SHA256 sobre headers**

| Método | Path | Estado | Descripción |
|---|---|---|---|
| `GET` | `/api/v1/edge/sync` | ✅ 9.2 | sync_version actual + metadata de update si hay versión nueva |
| `GET` | `/api/v1/edge/updates/{filename}` | ✅ 9.2 | Streaming download del .tar.gz del Edge |

**Headers de respuesta en todos los endpoints de ingest:**

```
X-Server-Sync-Version: <int>    ← piggyback para trigger del UpdateCheckerDaemon
X-Server-Time: <ISO8601>
X-Schema-Versions-Supported: 1.0
X-Server-Version: 0.9.2
```

---

### Dominio `api.yvyedge.com` (planeados · Track Core C10–C16)

**Lectura pública · sin auth · `Cache-Control: public, max-age=30`**

| Fase | Método | Path | Descripción |
|---|---|---|---|
| C11 | `GET` | `/api/v1/public/network` | KPIs agregados de la red (nodos, sincronizaciones, uptime 30d) |
| C11 | `GET` | `/api/v1/public/stations` | Lista de estaciones con último valor T/H/P |
| C11 | `GET` | `/api/v1/public/stations/{id}` | Detalle de una estación |
| C12 | `GET` | `/api/v1/public/metrics?period=24h\|7d\|30d` | Métricas provinciales agregadas para el período |
| C12 | `GET` | `/api/v1/public/dashboard?station=network&variable=temp&period=7d` | Serie temporal provincial (`station` siempre forzado a `network` en V1) |
| C13 | — | (extiende C12) | Añade variables derivadas: `variable=vpd`, `variable=dewpoint` |
| C14 | `GET` | `/api/v1/public/news?limit=N&tag=X` | Novedades editoriales publicadas |
| C14 | `GET` | `/api/v1/public/news/{slug}` | Detalle con body markdown |
| C15 | `GET` | `/api/v1/public/insights?limit=N&tag=X` | Insights (artículos con gráfico + attachment opcionales) |
| C15 | `GET` | `/api/v1/public/insights/{slug}` | Detalle con body, `chartConfig` JSON y `attachmentUrl` |

**Dependencias del Plan Web:**

```
C10 → C11 → W2 (mapa en vivo)
C12 → C13 → W3 (dashboard provincial)
C14 → C15 → W4 (novedades + insights)
C16 → cache layer (recomendado antes del launch)
```

---

## CLI completo (estado Fase 9.2)

```bash
# Dentro del container o con docker compose exec yvy-api ...

yvy-server version
yvy-server create-station --help
yvy-server publish-update --help
```

| Subcomando | Disponible | Qué hace |
|---|---|---|
| `version` | ✅ 9.1 | Imprime la versión del server |
| `create-station` | ✅ 9.1 | Registra una IEA nueva (genera UUID + HMAC key) |
| `publish-update` | ✅ 9.2 | Registra un release del Edge (copia .tar.gz, calcula SHA256, inserta en DB, incrementa sync_version) |
| `publish-news` | 🔲 C14 | Publica una novedad editorial |
| `list-news` | 🔲 C14 | Lista novedades |
| `publish-insight` | 🔲 C15 | Publica un insight con gráfico y/o attachment |
| `list-insights` | 🔲 C15 | Lista insights |
| `cache-clear` | 🔲 C16 | Invalida cache HTTP de endpoints públicos |

---

## Cheatsheet Fase 9.2

```bash
cd /opt/yvy-server
ADMIN="Authorization: Bearer $(cat secrets/yvy_admin_token.txt)"
BASE="https://yvy-edge.<dominio>.edu.ar"

# ── Stats de ingest ───────────────────────────────────────────────────────
curl -s -H "$ADMIN" "$BASE/api/v1/admin/stats" | python3 -m json.tool

# ── DLQ (failures post-HMAC) ──────────────────────────────────────────────
curl -s -H "$ADMIN" "$BASE/api/v1/admin/failures?limit=10" | python3 -m json.tool

# ── Resolver un failure del DLQ ───────────────────────────────────────────
curl -s -X POST -H "$ADMIN" -H "Content-Type: application/json" \
    -d '{"action":"discarded","notes":"payload con campo inválido, origen conocido"}' \
    "$BASE/api/v1/admin/failures/1/resolve" | python3 -m json.tool

# ── Publicar un release del Edge ──────────────────────────────────────────
docker compose exec yvy-api yvy-server publish-update \
    --file /data/releases/yvy-edge-0.9.2.tar.gz \
    --version "0.9.2" \
    --min-edge-version "0.9.0" \
    --notes "..."

# ── Verificar releases publicados ─────────────────────────────────────────
docker compose exec postgres psql -U yvyserver -d yvy -c \
    "SELECT edge_version, package_size_bytes, released_at, sync_version, is_active
     FROM public.edge_releases ORDER BY released_at DESC;"

# ── sync_version actual ───────────────────────────────────────────────────
docker compose exec postgres psql -U yvyserver -d yvy -c \
    "SELECT sync_version FROM public.sync_state WHERE id=1;"

# ── Verificar que el header piggyback llega ───────────────────────────────
curl -si -X POST "$BASE/api/v1/ingest/sensor" \
    -H "Content-Type: application/json" -d '{}' \
    | grep -i "x-server-sync-version"

# ── Row counts completo ───────────────────────────────────────────────────
docker compose exec postgres psql -U yvyserver -d yvy -c "
SELECT 'station'             AS t, COUNT(*) FROM public.station
UNION ALL SELECT 'sync_state',   COUNT(*) FROM public.sync_state
UNION ALL SELECT 'edge_releases',COUNT(*) FROM public.edge_releases
UNION ALL SELECT 'obs_raw',      COUNT(*) FROM bronze.empirical_observation_raw
UNION ALL SELECT 'sensor_raw',   COUNT(*) FROM bronze.sensor_raw
UNION ALL SELECT 'event_raw',    COUNT(*) FROM bronze.empirical_event_raw
UNION ALL SELECT 'ingest_log',   COUNT(*) FROM bronze.ingest_log
UNION ALL SELECT 'failures_dlq', COUNT(*) FROM bronze.ingest_failures;"
```

---

## Validación final Fase 9.2

- [ ] Migración `0002_edge_releases` aplicada (tablas `sync_state` y `edge_releases` existen)
- [ ] `GET /admin/stats` responde 200 con conteos
- [ ] `GET /admin/failures` responde 200 con `{"total":0,...}` (o entradas reales)
- [ ] `POST /admin/failures/{id}/resolve` devuelve `{"resolved":true,...}`
- [ ] `GET /edge/sync` sin auth → 401
- [ ] `GET /edge/updates/archivo.tar.gz` sin auth → 401
- [ ] Header `X-Server-Sync-Version` presente en respuestas de ingest
- [ ] `POST /api/v1/ingest/sensor` acepta envelope HMAC válido
- [ ] `POST /api/v1/ingest/event` acepta envelope HMAC válido
- [ ] `yvy-server publish-update --help` muestra la ayuda del comando
- [ ] nginx recargado con el bloque `/api/v1/edge/` (verificar con `docker compose exec nginx nginx -T | grep edge`)

---

*Documento v1.0 · 08-Jun-2026 · Continúa en Fase 9.3 (nginx multi-subdominio + smoke E2E) y C10 (capa SQL de agregación pública).*
