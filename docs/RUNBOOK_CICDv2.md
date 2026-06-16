# YVY · RUNBOOK · Proceso CI/CD

**Versión:** 1.1  
**Fecha:** 14-Jun-2026 (rev. 14-Jun-2026)  
**Aplica a:** servidor Debian 12 con stack YVY deployado (`RUNBOOK_DEPLOY_CODE.md` aplicado).  
**Audiencia:** Luis (SysAdmin/DevOps), Gonzalo (admin del proyecto) y futuros commiters.

---

## 0. Contexto y objetivos

Este runbook define el proceso de CI/CD adoptado para YVY a partir de Fase 9.2.
El incidente de Fase 9.2 (puerto 443 cerrado en firewall, detectado recién en smoke tests
en producción) expuso que el flujo anterior carecía de:

- Validación automatizada antes de tocar producción.
- Un paso de pre-deploy que verificara el estado del servidor.
- Rollback documentado y ejecutable en menos de 5 minutos.
- Backup garantizado *justo antes* de cada deploy, no solo nocturno.

El proceso definido acá es **deliberadamente manual-primero**: cada paso es un comando
que Luis ejecuta conscientemente. La automatización se puede sumar después, pero la
lógica y las salvaguardas ya están incorporadas desde el inicio.

### Qué NO abarca este documento

- Configuración inicial del servidor (ver `RUNBOOK_SERVER_BOOTSTRAP.md`).
- Primer deploy (ver `RUNBOOK_DEPLOY_CODE.md`).
- Actualizaciones de infraestructura (nginx config, docker-compose.yml): esas
  tienen su propio runbook por fase.

---

## 1. Política de ramas en GitHub

Antes de ejecutar cualquier deploy, el código debe llegar desde una rama validada,
no directamente desde `master`. La política mínima es:

```
master  ←  rama de feature/fix  (Pull Request con revisión)
```

### 1.1 Configurar protección de rama en GitHub

En el repositorio de GitHub → Settings → Branches → Add branch protection rule:

- Branch name pattern: `master`
- Marcar: **Require a pull request before merging**
  - Required number of approvals: 1 (puede ser el mismo Luis revisando código de Gonzalo y viceversa)
- Marcar: **Require status checks to pass before merging** (cuando CI esté configurado)
- Marcar: **Do not allow bypassing the above settings**

### 1.2 Convención de nombres de ramas

| Tipo | Patrón | Ejemplo |
|---|---|---|
| Feature nueva | `feat/<descripcion>` | `feat/edge-sync-channel` |
| Fix de bug | `fix/<descripcion>` | `fix/puerto-443-firewall` |
| Actualización operativa | `ops/<descripcion>` | `ops/nginx-edge-block` |
| Hotfix urgente a prod | `hotfix/<descripcion>` | `hotfix/sync-version-null` |

### 1.3 Flujo de un cambio tipo

```
1. Gonzalo crea rama feat/nueva-funcionalidad desde master
2. Desarrolla y hace commits en esa rama
3. Abre Pull Request hacia master
4. Luis (o Gonzalo) revisa el PR
5. CI corre los checks automáticos (§2)
6. Si todo pasa: merge a master
7. Luis ejecuta el deploy (§3)
```

---

## 2. CI — Checks automáticos con GitHub Actions

El workflow de CI corre automáticamente en cada Pull Request y en cada push a `master`.
**Nunca se hace deploy de un commit que no pasó CI.**

### 2.1 Crear el workflow

En el repositorio, crear el archivo `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [master]
  pull_request:
    branches: [master]

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: yvyserver
          POSTGRES_PASSWORD: test_password
          POSTGRES_DB: yvy_test
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
        ports:
          - 5432:5432

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.12
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install Poetry
        run: pip install poetry

      - name: Install dependencies
        run: poetry install --with dev

      - name: Lint (ruff)
        run: poetry run ruff check .

      - name: Type check (mypy)
        run: poetry run mypy .

      - name: Run migrations on test DB
        env:
          YVY_DB_DSN: postgresql+asyncpg://yvyserver:test_password@localhost:5432/yvy_test
          YVY_ENV: test
          YVY_ADMIN_TOKEN: test_token_for_ci_only_not_used_in_prod_xxxxxxxxxx
        run: poetry run alembic upgrade head

      - name: Run tests
        env:
          YVY_DB_DSN: postgresql+asyncpg://yvyserver:test_password@localhost:5432/yvy_test
          YVY_ENV: test
          YVY_ADMIN_TOKEN: test_token_for_ci_only_not_used_in_prod_xxxxxxxxxx
        run: poetry run pytest -x -v
```

> **Nota sobre `YVY_ADMIN_TOKEN` en CI:** el valor usado en CI es ficticio y solo existe
> para satisfacer la validación de startup. Nunca usar tokens de producción en CI.
> Guardar secretos reales en GitHub → Settings → Secrets and variables → Actions.

### 2.2 Agregar badge al README

```markdown
[![CI](https://github.com/GonzaloAvila27/YvY-Core/actions/workflows/ci.yml/badge.svg)](https://github.com/GonzaloAvila27/YvY-Core/actions/workflows/ci.yml)
```

### 2.3 Verificar que CI corre

Después de crear el archivo, hacer push y confirmar en la pestaña Actions de GitHub
que el workflow aparece y corre. El primer run puede tardar ~3 minutos.

---

## 3. Deploy a producción — Proceso paso a paso

El deploy se ejecuta **solo desde commits en `master` que pasaron CI**.
Luis es responsable de ejecutarlo.

### Pre-condición

```bash
# En GitHub, confirmar que el commit a deployar tiene ✓ verde en CI
# URL: https://github.com/GonzaloAvila27/YvY-Core/commits/master
```

### Paso 0 — Conectarse al servidor

```bash
ssh -i ~/.ssh/yvy_server yvyadmin@<IP_LAN>
cd /opt/yvy-server
```

### Paso 1 — Verificar estado del servidor antes de tocar nada

```bash
# Stack corriendo y sano
docker compose ps
# Esperado: nginx, yvy-api, postgres con estado "Up" (no "Restarting" ni "Exit")

# Health check desde adentro
docker compose exec nginx wget -qO- http://yvy-api:8000/api/v1/healthz
# Esperado: JSON con status ok

# Espacio en disco
df -h /opt /var
# Mínimo: 5 GB libres en /opt, 20 GB en /var

# Firewall
sudo ufw status
# Debe mostrar 22, 80 y 443 como ALLOW — si 443 no aparece, PARAR AQUÍ
```

> **Lección de Fase 9.2:** la verificación del firewall es obligatoria antes de
> cada deploy. Un puerto cerrado no es detectable hasta los smoke tests finales,
> cuando el daño ya está hecho.

Si alguna verificación falla, no continuar. Resolver primero.

**[VERIFICAR]**
- [ ] `docker compose ps` → todos Up y sin errores
- [ ] `healthz` responde OK
- [ ] Disco OK
- [ ] Firewall: 443 ALLOW

### Paso 2 — Backup previo al deploy

```bash
# Backup manual justo antes de modificar cualquier cosa
# Prerequisito: scripts/backup_postgres.sh y scripts/restore_postgres.sh deben existir
# en /opt/yvy-server/scripts/ con permisos de ejecución (chmod +x).
./scripts/backup_postgres.sh

# Confirmar que el backup existe y tiene tamaño razonable
ls -lh /opt/yvy-server/backups/ | tail -3
```

Anotar el nombre exacto del backup generado (lo vas a necesitar si hay que hacer rollback de DB).

```bash
BACKUP_ANTES_DEPLOY=$(ls -t /opt/yvy-server/backups/*.sql.gz | head -1)
echo "Backup pre-deploy: $BACKUP_ANTES_DEPLOY"

# Persistir en disco por si se cierra la terminal durante el deploy
echo "$BACKUP_ANTES_DEPLOY" > /tmp/yvy_backup_pre_deploy.txt
```

**[VERIFICAR]**
- [ ] Backup existe con timestamp de ahora mismo y tamaño > 0

### Paso 3 — Actualizar el código

```bash
cd /opt/yvy-server/server-code

# Ver qué commits entran
git fetch
git log HEAD..origin/master --oneline
# Revisar que son los commits esperados, no hay sorpresas

# Confirmar el commit exacto que se va a deployar
git rev-parse origin/master
# Anotar este hash — es el identificador del deploy

# Actualizar
git pull
git log -1 --oneline
# Confirmar que HEAD es el commit esperado
```

Si `git pull` falla (conflictos u otro error), **no continuar**. Resolver y volver al Paso 1.

### Paso 4 — Actualizar `current_version.txt`

```bash
cd /opt/yvy-server
cat > current_version.txt << EOF
deployed_at: $(date -Iseconds)
git_commit: $(cd server-code && git rev-parse HEAD)
git_branch: $(cd server-code && git rev-parse --abbrev-ref HEAD)
deployed_by: $(whoami)
backup_pre_deploy: $BACKUP_ANTES_DEPLOY
EOF
cat current_version.txt
```

### Paso 5 — Si el deploy incluye cambios de infraestructura

Si el PR incluye cambios en `nginx.conf` o `docker-compose.yml` (como en Fase 9.2),
aplicarlos en este paso **antes** del rebuild:

```bash
# Siempre hacer backup de config antes de editar
cp nginx/conf/nginx.conf "nginx/conf/nginx.conf.bak.$(date +%Y%m%d_%H%M)"

# Editar según lo que indica el runbook de la fase correspondiente
# ...

# Validar nginx ANTES de recargar
docker compose exec nginx nginx -t
# Debe decir: syntax is ok
```

Si `nginx -t` falla, revertir y no continuar:

```bash
# Restaurar el backup más reciente (no usar glob dentro de comillas — no expande)
cp $(ls -t nginx/conf/nginx.conf.bak.* | head -1) nginx/conf/nginx.conf
```

### Paso 6 — Rebuild y redeploy

```bash
cd /opt/yvy-server

GIT_COMMIT=$(cd server-code && git rev-parse HEAD)
GIT_COMMIT_SHORT=$(cd server-code && git rev-parse --short HEAD)
BUILD_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

docker compose build yvy-api \
    --build-arg GIT_COMMIT="$GIT_COMMIT" \
    --build-arg GIT_COMMIT_SHORT="$GIT_COMMIT_SHORT" \
    --build-arg BUILD_DATE="$BUILD_DATE"

# Reiniciar solo yvy-api (postgres y nginx quedan en pie)
docker compose up -d --no-deps yvy-api

# Si también cambió nginx config:
docker compose exec nginx nginx -s reload
```

### Paso 7 — Verificar migraciones (si las hay)

```bash
docker compose logs yvy-api | grep -E "(alembic|migrat|entrypoint|startup|ERROR)"
# Esperado: "[entrypoint] Migrations applied." y "startup complete"
# Si aparece ERROR: ver §4 (Rollback)
```

### Paso 8 — Smoke tests

```bash
ADMIN_TOKEN=$(cat /opt/yvy-server/secrets/yvy_admin_token.txt)
BASE="https://edge.yvyedge.com"
PUB="https://api.yvyedge.com"

# 1. Health público
curl -sf "$BASE/api/v1/healthz" | python3 -m json.tool
# Esperado: JSON con status ok y el nuevo git_commit_short

# 2. Health admin (requiere auth)
curl -sf -H "Authorization: Bearer $ADMIN_TOKEN" \
    "$BASE/api/v1/admin/healthz" | python3 -m json.tool
# Esperado: JSON con DB connectivity ok y row counts

# 3. serverinfo muestra el commit nuevo
curl -sf "$BASE/api/v1/serverinfo" | python3 -m json.tool | grep git_commit_short
# Debe mostrar el commit recién deployado

# 4. API pública — solo si el deploy incluye C11 o posterior
curl -sf "$PUB/api/v1/public/network" | python3 -m json.tool
# Esperado: JSON con totalNodes, activeNodes, uptime30d, generatedAt

curl -sf "$PUB/api/v1/public/stations" | python3 -m json.tool | grep '"code"' | head -5
# Esperado: lista de estaciones con code IEA-07 etc.
```

Si cualquier smoke test falla: ver §4 (Rollback).

**[VERIFICAR]**
- [ ] `healthz` responde 200 con commit correcto
- [ ] `admin/healthz` responde 200 con DB ok
- [ ] `serverinfo` muestra el nuevo commit
- [ ] `api.yvyedge.com/public/network` responde 200 (si aplica)

### Paso 9 — Registrar el deploy

```bash
# Confirmar en GitHub que el commit tiene el check verde de CI
# Agregar un comentario en el PR mergeado o issue correspondiente:
# "Deployado a producción — $(date) — commit $(cd server-code && git rev-parse --short HEAD)"
```

---

## 4. Rollback

El rollback tiene dos variantes según qué falló. Decidir cuál usar antes de ejecutar.

### 4.1 Rollback de código (sin cambios de DB)

Usar cuando los smoke tests fallan pero la migración de DB no corrió o no hubo migración.

```bash
cd /opt/yvy-server/server-code

# Ver commits disponibles
git log --oneline -5

# Volver al commit anterior (reset mantiene la rama activa; checkout dejaría HEAD detacheado)
git reset --hard <commit-anterior>

cd /opt/yvy-server
GIT_COMMIT=$(cd server-code && git rev-parse HEAD)
GIT_COMMIT_SHORT=$(cd server-code && git rev-parse --short HEAD)
BUILD_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

docker compose build yvy-api \
    --build-arg GIT_COMMIT="$GIT_COMMIT" \
    --build-arg GIT_COMMIT_SHORT="$GIT_COMMIT_SHORT" \
    --build-arg BUILD_DATE="$BUILD_DATE"

docker compose up -d --no-deps yvy-api

# Verificar
sleep 5
docker compose logs --tail=20 yvy-api
curl -sf "https://edge.yvyedge.com/api/v1/healthz"
```

### 4.2 Rollback de DB (migración problemática)

Usar cuando Alembic corrió una migración que falló o corrompió datos.

```bash
# Usar el backup tomado en el Paso 2
# Si la variable no existe en esta sesión, recuperar del archivo persistido
BACKUP_ANTES_DEPLOY=${BACKUP_ANTES_DEPLOY:-$(cat /tmp/yvy_backup_pre_deploy.txt 2>/dev/null)}
echo "Backup a restaurar: $BACKUP_ANTES_DEPLOY"
ls -lh $BACKUP_ANTES_DEPLOY   # confirmar que existe

# Restaurar
./scripts/restore_postgres.sh $BACKUP_ANTES_DEPLOY

# Luego hacer rollback de código (4.1) con el commit anterior
```

> **Importante:** restaurar el backup borra todos los datos escritos en producción
> desde que se tomó el backup. En un servidor con pocas escrituras esto es aceptable;
> evaluar caso a caso si hubo ingesta de datos entre el backup y el fallo.

### 4.3 Rollback de nginx config

```bash
# Identificar el backup hecho en Paso 5
ls nginx/conf/nginx.conf.bak.*

# Restaurar
cp nginx/conf/nginx.conf.bak.<fecha> nginx/conf/nginx.conf

# Recargar
docker compose exec nginx nginx -t && docker compose exec nginx nginx -s reload
```

### 4.4 Checklist de rollback

- [ ] Identificar qué componente falló (código / DB / nginx)
- [ ] Ejecutar el rollback correspondiente
- [ ] Smoke tests pasan con la versión anterior
- [ ] Documentar qué pasó en el issue/PR de GitHub

---

## 5. Hotfix — cuando hay que arreglar producción sin esperar el flujo normal

Un hotfix es para situaciones donde producción está rota y no se puede esperar
al ciclo habitual de PR + review.

```
1. Crear rama hotfix/<descripcion> desde master
2. Hacer el fix mínimo
3. Push — CI corre en la rama
4. Si CI pasa: merge a master sin esperar review formal (documentar en el PR)
5. Deploy inmediato siguiendo §3 completo (incluyendo backup y smoke tests)
```

> Un hotfix NO saltea el backup previo ni los smoke tests. Solo saltea el tiempo
> de espera de revisión.

---

## 6. Referencia rápida de comandos

```bash
# ── Pre-deploy ────────────────────────────────────────────────────────────
docker compose ps                                    # estado del stack
sudo ufw status                                      # firewall (443 debe estar ALLOW)
./scripts/backup_postgres.sh                         # backup manual

# ── Deploy ───────────────────────────────────────────────────────────────
cd /opt/yvy-server/server-code && git pull          # actualizar código
docker compose build yvy-api [--build-arg ...]      # rebuild
docker compose up -d --no-deps yvy-api              # reemplazar container

# ── Smoke tests ──────────────────────────────────────────────────────────
curl -sf https://edge.yvyedge.com/api/v1/healthz
curl -sf -H "Authorization: Bearer $(cat secrets/yvy_admin_token.txt)" \
    https://edge.yvyedge.com/api/v1/admin/healthz

# ── Rollback código ──────────────────────────────────────────────────────
cd server-code && git reset --hard <commit-anterior>
docker compose build yvy-api && docker compose up -d --no-deps yvy-api

# ── Rollback DB ──────────────────────────────────────────────────────────
./scripts/restore_postgres.sh /opt/yvy-server/backups/yvy_<fecha>.sql.gz

# ── Ver versión deployada ────────────────────────────────────────────────
cat /opt/yvy-server/current_version.txt
curl -sf https://edge.yvyedge.com/api/v1/serverinfo | python3 -m json.tool
```

---

## 7. Pendientes — próximos pasos de madurez

Estos ítems quedan fuera del alcance actual pero son los siguientes naturales
una vez que el flujo manual esté estabilizado:

- **Script `deploy.sh`:** encapsular los pasos 0–9 de §3 en un script
  que corra desde la máquina local de Luis y haga todo via SSH.
- **Notificación de deploy:** mensaje automático en Slack/Discord/Telegram cuando
  un deploy termina (o falla).
- **Automatización del deploy en push a `master`:** GitHub Actions dispara el deploy
  vía SSH cuando CI pasa. Requiere runner self-hosted o SSH action + IP pública.
- **Staging environment:** un segundo stack en el mismo servidor (puerto diferente,
  DB separada) para validar cambios antes de tocar producción.

---

## 8. Situación actual — Fase 9.2 en producción rota

Al momento de redactar este runbook, producción está rota por el incidente de Fase 9.2
(puerto 443 cerrado en firewall). El fix es:

```bash
ssh -i ~/.ssh/yvy_server yvyadmin@<IP_LAN>

# Verificar estado actual del firewall
sudo ufw status verbose

# Si 443 no aparece como ALLOW:
sudo ufw allow 443/tcp
sudo ufw reload

# Verificar
sudo ufw status | grep 443
curl -sf https://edge.yvyedge.com/api/v1/healthz
```

Después de confirmar que el 443 está abierto y los smoke tests pasan, el servidor
vuelve al estado correcto de Fase 9.2 sin necesidad de redeploy.

---

**Fin del runbook · YVY CI/CD v1.0**

Mantener este archivo en el repo junto al código.
Ante cualquier cambio de proceso, actualizar versión y fecha arriba.
