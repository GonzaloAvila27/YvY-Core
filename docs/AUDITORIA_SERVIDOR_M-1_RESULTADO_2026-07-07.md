# YVY Core · Auditoría de servidor (M-1) — RESULTADO

**Fecha de ejecución:** 2026-07-07 (13:00–14:15 -03 aprox., sesión SSH `keter@yvy-server`)
**Evidencia:** `docs/log_test_auditoria_servidor_07072026.txt` (1487 líneas)
**Runbook aplicado:** `AUDITORIA_SERVIDOR_M-1_2026-07-07.md`
**Analista:** Claude Code (CORE) sobre evidencia recolectada por Gonzalo.

> **Veredicto en una línea:** el servidor está **mejor que lo documentado** — backups
> restaurables verificados, rate limiting activo, recuperación automática tras corte de
> luz real, y la Fase 2A del plan de deploy (postgres con pgvector+PostGIS) **ya está
> hecha**. Los bloqueantes del deploy M0 siguen siendo los del **código de la rama**
> (QW1-QW6), no el servidor. Aparecieron 2 sorpresas que exigen pre-checks nuevos antes
> de la migración 003.

---

## 1. Veredicto de hipótesis (H1-H11)

| H | Hipótesis | Veredicto | Evidencia (línea del log) |
|---|---|---|---|
| H1 | Prod corre master local pre-M0 | ✅ **CONFIRMADA** (con sorpresa, ver §2.2) | HEAD `736e707` (l.37); openapi sin `/public/*` (l.254-265); alembic `0002` (l.571-582) |
| H2 | Git divergente (P1) | ✅ **CONFIRMADA y AMPLIADA** | `[delante 1, detrás 4]` (l.34) — pero el fetch del server **falló/quedó viejo** (l.59-61: keter no tiene la deploy key). Contra el `origin/master` real (`3f336b6`, verificado desde el repo local el 07-jul): **delante 1, detrás 13**. El commit local `736e707` tiene equivalente exacto en origin (`aabee2b`) |
| H3 | Dockerfile hotfixeado sin commitear | ❌ **REFUTADA** | Working tree **limpio** (l.81-84). El hotfix fue commiteado a la rama local `core-prod-actual` (`5badbeb`, l.33) y ya está consolidado en el origin/master real |
| H4 | Postgres alpine sin extensiones | ❌ **REFUTADA** | Corre imagen custom `yvy-server-postgres` (build 22-jun, 1.11GB, l.117) basada en PostGIS. **`vector 0.8.3` y `postgis 3.4.3` ya instaladas y creadas en la DB `yvy`** (l.316-322). PG 16.14 Debian (l.288) |
| H5 | DB pre-M0 (alembic 0002, tablas viejas, sin views) | ✅ **CONFIRMADA** (con sorpresa, §2.1) | `alembic_version=0002` (l.574); `bronze.sensor_raw/empirical_*` intactas (l.358-367); sin views C10; `station` SIN columnas C10 (l.402-413). **PERO los schemas `canonical/silver/gold/knowledge/auth/exports/catalog` ya existen** (l.331-344) |
| H6 | sensor vivo / obs estancada desde 08-jun | ⚠️ **PARCIAL** | sensor_raw vivo: 41.872 filas, MAX = ahora, ~50-59/hora (l.459-497). **empirical_obs NO está estancada en jun-08: tiene 6 filas, última 01-jul** (l.468) → DIAG-002 quedó viejo; el canal anda, con volumen bajísimo |
| H7 | Servicios yvy-api/postgres/nginx; solo 80/443 al host | ✅ **CONFIRMADA** | compose ps (l.99-103); 5432 NO publicado; ss muestra solo 22/80/443 (l.894-901) |
| H8 | nginx sin rate limiting | ❌ **REFUTADA** (¡a favor!) | El nginx de **prod** SÍ tiene `limit_req 60r/m burst=30` en `/ingest/` + security headers + X-Real-IP (l.909-916, 1302-1359). El desactualizado es el **del repo** — el drift es inverso al asumido |
| H9 | edge_releases: 1.5.1 inactivo, sync ≥ 2 | ✅ **CONFIRMADA y SUPERADA** | El OTA avanzó: `1.5.4` activo (27-jun), `sync_version=5` consistente entre log de startup y DB (l.247-250, 539-546). ⚠️ `1.5.3` sigue `is_active=t` junto con 1.5.4 |
| H10 | Sin `/public/*` en prod | ✅ **CONFIRMADA** | openapi (l.254-265); preflight OPTIONS → 405 sin headers CORS (l.268-284) |
| H11 | Backup nocturno activo | ✅ **CONFIRMADA y VERIFICADA** | cron 03:00 (l.672-673); backups diarios presentes y crecientes (l.626-636); **restore real a `yvy_restore_test` OK con 41.893 filas** (l.1010-1044) |

---

## 2. Hallazgos nuevos (no anticipados por el runbook)

### 2.1 🔴 Schemas canónicos ya existen en la DB — creados por fuera de Alembic

`\dn` lista `canonical, silver, gold, knowledge, auth, exports, catalog` (l.331-344) con
`alembic_version=0002` y las tablas bronze viejas intactas. **Alguien los creó a mano**
(presumiblemente durante las pruebas de la imagen postgres del 22-26 jun; el dir
`/opt/yvy-server/postgres/` es del 26-jun, l.15).

**Impacto sobre M0:** la migración `003_m0_canonical_model` hace `CREATE SCHEMA IF NOT
EXISTS` (inofensivo) pero después `CREATE TABLE canonical.unit ...` **sin** `IF NOT
EXISTS`. Si alguno de esos schemas contiene tablas, la 003 aborta a mitad de camino
(transaccional, pero aborta).

**Pre-check nuevo obligatorio antes de la 2B** (agregar al runbook de deploy):
```sql
\dt canonical.*  \dt silver.*  \dt gold.*  \dt knowledge.*
\dt auth.*       \dt exports.* \dt catalog.*
-- Esperado: "Did not find any relation" en TODOS.
-- Si hay tablas → inventariarlas y decidir (drop manual documentado) ANTES de migrar.
```

> **✅ Actualización 07-jul (misma jornada):** verificado por Gonzalo — los 7 schemas
> están **vacíos**. R2 cerrado para el estado actual; re-correr el check igualmente el
> día de la ventana (es gratis y el estado puede cambiar).

### 2.2 🟢 La Fase 2A del plan de deploy YA ESTÁ HECHA

Postgres corre la imagen con PostGIS+pgvector desde el ~22-jun, con los datos
preservados (41.9k filas) y **las extensiones ya creadas en la base** (l.316-322).
Coincide con el merge de PR #16 `feat/m0` ("update docker files to include pg
extentions") que el server sí tiene en su origin viejo. El `postgres:16-alpine` quedó
como imagen huérfana sin uso (l.119).

**Impacto:** `PLAN_APLICACION_SERVIDOR.md` Fase 2A pasa de "ejecutar" a "verificar"
(la verificación ya está hecha en esta auditoría). La ventana M0 se acorta.

### 2.3 🟠 El fetch del server está vencido — la divergencia real es mayor y más simple

`keter` no puede fetchear (deploy key es de `yvyadmin`, l.59-61), así que los refs
`origin/*` del server son de ~fines de junio. Contra el origin real:

- **Server:** `master @ 736e707`, working tree limpio, sin stash relevante.
- **Origin real:** `master @ 3f336b6` = consolidación completa (fix edge.py como
  `aabee2b`, Dockerfile Opción A, CI verde) + fixes de CI.
- El **único** commit local del server (`736e707`) está contenido semánticamente en
  origin (`aabee2b`, mismo diff).

**Impacto:** la Fase 1 del deploy queda **más segura que lo previsto**: como yvyadmin,
`git fetch origin && git reset --hard origin/master` no pierde nada (verificado: tree
limpio + único commit local duplicado en origin). No hay conflicto que resolver.

### 2.4 🟠 Identidad del build corriendo: `unknown`

`serverinfo` devuelve `version=0.9.1-dev+unknown, git_commit_short=unknown` (l.209) —
la imagen del 24-jun se buildeó **sin build-args** (deuda F91 §5 confirmada en prod).
Además `current_version.txt` dice `deployed_at 2026-06-17 @ 46dcee9` (l.90-95) pero la
imagen corriendo es del 24-jun → **el registro de deploy quedó 1 deploy atrás**.
No podemos afirmar por evidencia directa qué commit corre; lo inferimos por fecha de
imagen + git local. En Fase 1: rebuild **con** build-args y actualizar
`current_version.txt` (paso 3 del Runbook §13.1).

### 2.5 🟡 Dos releases OTA activos a la vez

`1.5.3` y `1.5.4` ambos `is_active=t` (l.539-546). `get_latest_active` ordena por
`released_at DESC` así que ofrece 1.5.4 — sin bug funcional hoy, pero si algún día se
"despublica" 1.5.4, el fallback silencioso sería 1.5.3. Higiene: desactivar 1.5.3
(y recordar el pendiente P3: despublicar por DB no bumpea sync_version).

### 2.6 🟡 ufw no gobierna los puertos publicados por Docker

ufw permite solo 22 y 443 (l.881-892), pero el puerto **80 está escuchando y accesible**
(l.894-901): Docker inserta sus propias reglas iptables **antes** que ufw. Hoy es
inofensivo (80 solo redirige a HTTPS y sirve el webroot de certbot), pero significa que
**el firewall del host no protege nada que Docker publique**. Documentarlo como modelo
mental: la exposición se controla en `docker-compose.yml` (`ports:`), no en ufw.

### 2.7 🟡 Datos: 4× lo documentado + estación nueva

- **41.9k filas** en sensor_raw, no "~10k" como dicen CICDv3/CLAUDE.md. El backfill M0
  sigue siendo trivial (segundos), pero actualizar el gate de conteos pre-migración.
- **Tercera estación** `IEA 1 - Bompland` creada el **29-jun** (l.519-525) — no estaba
  en ningún doc. El backfill M0 la cubre (mapea todas las stations al migrar), pero
  refuerza el QW2: la próxima alta post-M0 nacería muerta sin el fix de `create-station`.
- DLQ: **4 sin resolver** (l.511-515); 6 SCHEMA históricos, 1 en las últimas 48h
  (l.501-507). Revisables vía `/admin/failures` cuando se quiera.
- Nombre de estación dice "IEA 7 - **Posadas**" (docs dicen Garupá) — cosmético.

### 2.8 🟢 Resiliencia verificada en condiciones reales

Hubo **corte de luz el 06-jul ~20:17** y el stack volvió solo: `yvy-stack.service`
(enabled) levantó postgres→api→nginx en orden con healthchecks (l.703-720). Además en
esta sesión se ejecutaron con éxito: `restart yvy-api` (healthy en 6s, migración no-op,
l.1047-1076) y `down`/`up -d` completo (l.1080-1098). **F8.10-8.12 cubiertas.**

### 2.9 🟢 Backup + restore: precondición dura de M0 CUMPLIDA

Backup del día verificado con `gunzip -t` + **restore completo a `yvy_restore_test`**
con conteo correcto (41.893) y limpieza posterior (l.999-1044). Es exactamente el gate
"backup verificado, no solo creado" de CICDv3 §1.1. Repetir el mismo procedimiento
**el día de la ventana M0** con el dump fresco.

### 2.10 Menores / cosmético

- `logs/api/` vacío (l.622-625): la app loguea a stdout (docker logs). Verificar
  rotación de logs de Docker (`max-size`) — no quedó en el log de esta sesión.
- `.env` con permisos `640 yvyadmin:docker` (l.600-601): legible por el grupo docker.
  Aceptable; idealmente `600`.
- `secrets/` contiene `iea7_credentials.json` en disco (l.590-599) — las credenciales
  deberían vivir solo en el pendrive de onboarding; evaluar retirarlas del server.
- Dir basura `/opt/yvy-server/date/` (typo de `data`, 10-jun, l.10) y
  `data/` con owner uid 999 crudo — cosmético.
- nginx warnings: `http2` deprecado y `ssl_stapling` ignorado (l.903-905) — cosmético.
- sshd endurecido OK: sin password, sin root, `AllowUsers yvyadmin keter bina`
  (l.781-877); fail2ban activo, 0 baneos (l.931-944); cert válido hasta 01-sep
  (56 días, l.922-929).
- DNS: **`api.yvyedge.com` ya resuelve a la IP del server** (l.946-966) — prerequisito
  de Fase 2C medio hecho.
  **✅ Actualización 07-jul:** `sudo certbot certificates` completo confirma **2 certs,
  ninguno cubre `api.yvyedge.com`**: `edge.yvyedge.com` (solo edge., vence 03-sep) y
  `yvyedge.com` (apex + edge. + www., vence 01-sep — es el que usa el nginx de prod).
  → **R5 CONFIRMADO:** la Fase 2C requiere expandir el cert `yvyedge.com` agregando
  `api.yvyedge.com` (revisar antes el authenticator en `/etc/letsencrypt/renewal/
  yvyedge.com.conf` — si es standalone, la expansión necesita los hooks de
  parada/arranque de nginx). Nota de higiene: el cert `edge.yvyedge.com` es redundante
  (edge. ya está en el cert principal) — consolidar cuando se toque certbot.

---

## 3. Tabla de estado por componente

| Componente | Estado | Nota |
|---|---|---|
| Git repo / rama | ⚠ WARNING | master local limpio pero atrasado; fetch vencido; reconciliación simple (§2.3) |
| Imagen yvy-api == código | ⚠ WARNING | corre el código correcto (por inferencia), pero `serverinfo=unknown` y `current_version.txt` vencido (§2.4) |
| Postgres + extensiones | ✅ OK | PG 16.14 + vector 0.8.3 + postgis 3.4.3, datos preservados |
| Schema DB vs migraciones | ⚠ WARNING | alembic 0002 == heads == código; **pero schemas canónicos manuales** (§2.1) |
| Ingesta en vivo | ✅ OK | ~55/h de IEA-07, 99,99% success (41.901/41.907), DLQ=4 |
| Canal pull / OTA | ✅ OK | sync_version=5 consistente; 1.5.4 activo; ⚠ 1.5.3 también activo (§2.5) |
| nginx / TLS / DNS | ✅ OK | rate limit + headers + certs válidos; ⚠ confirmar cert de api. |
| Seguridad host | ✅ OK | sshd endurecido, fail2ban, ufw; ⚠ nota conceptual Docker-bypass (§2.6) |
| Backups | ✅ OK | nocturno + restore verificado hoy (§2.9) |
| Arranque tras corte | ✅ OK | verificado con corte real 06-jul + tests down/up (§2.8) |
| Drift repo↔server | ⚠ WARNING | compose y nginx de prod ≠ repo (prod es MEJOR); versionarlos (§4-R4) |

**Ítems del checklist que quedaron sin ejecutar** (no bloquean, agendar):
`\dt canonical.*` y hermanos (§2.1 — **este sí bloquea la 2B**), `certbot certificates`
completo (dominios del cert), rotación de logs Docker, prueba 8.5 (edge sync autenticado
desde el PC de test) y 8.7 (ingest E2E firmado inserted/duplicate — conviene hacerla el
día de la ventana como smoke pre y post M0).

---

## 4. Riesgos actualizados (prioridad descendente)

1. **R1 — Código de la rama `plan_fases_api` (sin cambios):** C1 (ON CONFLICT inválido
   → ingesta 500 post-M0), C2 (idempotencia rota), C3 (create-station sin source).
   **Siguen siendo EL bloqueante del deploy M0.** El server está listo; el código no.
2. **R2 — Schemas canónicos pre-existentes** (§2.1): ✅ **CERRADO 07-jul** — verificados
   vacíos. Re-check gratuito el día de la ventana.
3. **R3 — Identidad de build "unknown"** (§2.4): sin `git_commit_short` real no se
   puede auditar qué corre. Se corrige gratis en el próximo build (build-args).
4. **R4 — Drift compose/nginx repo↔server** (invertido: prod mejor que repo): versionar
   `docker-compose.prod.yml` + `nginx/nginx.prod.conf` en el repo para que el próximo
   deploy no dependa de archivos que solo existen en el server.
5. **R5 — Cert de `api.yvyedge.com`: CONFIRMADO faltante** (07-jul). Bloquea solo 2C;
   DNS ya está. Fix: `certbot --expand` sobre el cert `yvyedge.com` (ver §2.10).
6. **R6 — Higiene OTA:** 1.5.3 activo redundante; pendientes P3 (despublicar no bumpea
   sync) y reset de versionado a 1.0.0 acordado en junio.
7. **R7 — Menores:** .env 640, credenciales IEA7 en disco, DLQ=4 sin triage,
   docs desactualizados (DIAG-002, conteos "~10k", nombre IEA-07).

---

## 5. Impacto sobre el plan de deploy (`PLAN_APLICACION_SERVIDOR.md`)

| Fase del plan | Estado tras esta auditoría |
|---|---|
| **Fase 1 (master)** | ✅ Lista para ejecutar, y MÁS SIMPLE: como `yvyadmin`: `git fetch origin` → verificar `git log origin/master -3` (debe mostrar `3f336b6...`) → `git reset --hard origin/master` (tree limpio + único commit local ya contenido como `aabee2b`) → rebuild **con build-args** → `up -d --no-deps yvy-api` → actualizar `current_version.txt`. ⚠ El compose de prod buildea `yvy-api` desde `./server-code` — NO usar el compose del repo |
| **Fase 2A (postgres+pgvector)** | ✅ **YA HECHA** (§2.2). Solo re-verificar extensiones el día D (comando del runbook §6) |
| **Fase 2B (migración 003)** | 🔴 **BLOQUEADA** por R1 (QW1-QW6 en el código) + nuevo pre-check R2 (schemas vacíos). El gate de conteos pre-M0 debe registrar los números reales (41.9k, no 10k) |
| **Fase 2C (C10/C11 + nginx)** | ⚠ DNS de api. ✓; cert **confirmado faltante** (R5) → `certbot --expand` requerido; server block de `api.yvyedge.com` a escribir **sobre el nginx.conf de prod** (no el del repo) |

**Secuencia recomendada (sin cambios de fondo respecto al plan post-auditoría de código):**

```
1. QW1-QW6 en plan_fases_api + CI verde          ← el único camino crítico real
2. Fase 1 (master) — puede ir ya, independiente  ← 30 min, riesgo bajo
3. Pre-check schemas vacíos + certbot api.        ← 10 min, en el server
4. Fase 2 (M0 → C10 → C11) en ventana            ← con smoke ingest pre/post
```

---

*Resultado registrado el 2026-07-07. Evidencia cruda en
`docs/log_test_auditoria_servidor_07072026.txt`. Próxima repetición de la auditoría:
usar el runbook M-1 actualizando los "esperados" con los valores de este documento.*
