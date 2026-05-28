# YVY · RUNBOOK · Deploy del código al server central

**Versión:** 2.0
**Fecha:** 28-May-2026
**Aplicable a:** server Debian 12 ya bootstrapped (ver `RUNBOOK_SERVER_BOOTSTRAP.md`).
**Tiempo estimado:** 2-3 horas para primera instalación. 15-30 minutos para redeploys posteriores.
**Audiencia:** admin del proyecto YVY (Gonzalo) y/o admin del server (referente técnico tecnicatura).

---

## 0. Antes de empezar

### 0.1 Prerrequisitos

Verificá que TODOS estos items están cumplidos antes de continuar:

- [ ] `RUNBOOK_SERVER_BOOTSTRAP.md` aplicado completamente.
- [ ] Server accesible por SSH con clave.
- [ ] Docker + Docker Compose funcionando (`docker compose version` responde).
- [ ] Firewall UFW con 22, 80, 443 abiertos.
- [ ] Dominio DNS apuntando a la IP pública (verificación en §3.2).
- [ ] Código YVY del server disponible (repo Git, archivo zip, o tarball).
- [ ] Acceso de admin al server (usuario `yvyadmin` con `sudo`).

### 0.2 Decisiones operativas previas

Antes de tipear comandos, definir:

| Variable | Valor para tu caso |
|---|---|
| Dominio público | `yvy-edge.<dominio-tecnicatura>.edu.ar` |
| Email para Let's Encrypt | el del admin del proyecto |
| Password PostgreSQL `yvyserver` | (generar 32 chars aleatorio, ver abajo) |
| Password PostgreSQL `yvyreader` | (generar 32 chars, solo lectura para queries admin) |
| Admin Bearer Token YVY | (generar 48 chars, para endpoints `/admin/*`) |
| Versión del código a deployar | tag git o commit hash específico |
| Directorio del proyecto | `/opt/yvy-server` (creado en bootstrap) |

**Generación de passwords seguros:**

```bash
# Password (base64, 32 bytes = 44 chars)
openssl rand -base64 32

# Token admin YVY (urlsafe, 48 bytes = 64 chars — requerido >= 32 chars por validación del código)
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

**Guardalos en `secrets/credentials.txt`** apenas los generes. Los vas a usar varias veces y NO se pueden recuperar después.

### 0.3 Estructura del runbook

```
1.  Verificación de prerrequisitos
2.  Preparar el código en el server
3.  DNS y certificado HTTPS
4.  Generar credentials
5.  docker-compose.yml + nginx config (producción)
6.  Levantar el stack
7.  Verificar PostgreSQL y migraciones
8.  Crear primera estación (IEA 7)
9.  Smoke test desde Internet
10. Configurar backup nocturno
11. Configurar logs
12. Boot automático
13. Ciclo de redeploy
14. Rollback en caso de problema
15. Troubleshooting
16. Cheatsheet operativo
17. Validación final
```

---

## 1. Verificación de prerrequisitos

### 1.1 Conexión y privilegios

```bash
ssh -i ~/.ssh/yvy_server yvyadmin@<IP_LAN>
whoami
sudo -n true && echo "sudo OK" || echo "sudo requiere password"
```

### 1.2 Docker funcional

```bash
docker compose version
docker run --rm hello-world | head -1
# Esperado: "Hello from Docker!"
```

Si falla, revisar `RUNBOOK_SERVER_BOOTSTRAP.md` §9.

### 1.3 Espacio en disco

```bash
df -h /opt
df -h /var
```

Necesitás al menos **20 GB libres** en `/var` (para PostgreSQL data + logs Docker) y **5 GB libres** en `/opt`. Si no, parar acá y resolver antes de continuar.

### 1.4 Memoria libre

```bash
free -h
```

Necesitás al menos 2 GB libres.

### 1.5 [VERIFICAR]

- [ ] SSH funciona con clave
- [ ] Docker responde
- [ ] >20 GB libres en /var
- [ ] >2 GB RAM libres

---

## 2. Preparar el código en el server

### 2.1 Opciones para llevar el código al server

**Opción A — Clone de Git (recomendado):**

```bash
cd /opt/yvy-server
git clone https://github.com/<tu-usuario>/yvy-server.git server-code
cd server-code
git log -1
```

Verificar que el commit visible coincide con el que querés deployar.

Si el repo es privado, configurar antes una deploy key:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/yvy_deploy_key -C "yvy-server-deploy" -N ""
cat ~/.ssh/yvy_deploy_key.pub
# Copiar el output y agregarlo en GitHub/GitLab como "Deploy key" (read-only)
cat >> ~/.ssh/config << 'EOF'
Host github.com
  IdentityFile ~/.ssh/yvy_deploy_key
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
```

**Opción B — Transfer manual con scp:**

```bash
# Desde tu máquina local:
cd /path/to/yvy-server
tar czf /tmp/yvy-server-code.tar.gz \
    --exclude='__pycache__' \
    --exclude='.venv' \
    --exclude='.git' \
    src/ pyproject.toml Dockerfile docker-entrypoint.sh \
    alembic/ alembic.ini scripts/ README.md
scp /tmp/yvy-server-code.tar.gz yvyadmin@<IP_LAN>:/tmp/

# En el server:
mkdir -p /opt/yvy-server/server-code
cd /opt/yvy-server/server-code
tar xzf /tmp/yvy-server-code.tar.gz
```

### 2.2 Versionado explícito

```bash
cd /opt/yvy-server
cat > current_version.txt << EOF
deployed_at: $(date -Iseconds)
git_commit: $(cd server-code && git rev-parse HEAD)
git_branch: $(cd server-code && git rev-parse --abbrev-ref HEAD)
deployed_by: $(whoami)
EOF
cat current_version.txt
```

### 2.3 [VERIFICAR]

```bash
ls /opt/yvy-server/server-code/
# Esperado: src/  pyproject.toml  Dockerfile  docker-entrypoint.sh  alembic/  ...

cat /opt/yvy-server/current_version.txt
# Esperado: timestamp + commit hash
```

---

## 3. DNS y certificado HTTPS

### 3.1 Confirmar dominio apunta a la IP pública

**Desde tu máquina local** (no desde el server):

```bash
dig +short yvy-edge.<dominio>.edu.ar
```

Debe devolver la IP pública que te dio el técnico. Si devuelve vacío o una IP distinta:
- Verificar con IT que el record DNS A está creado.
- Esperar propagación (puede tardar hasta 24h, normalmente <1h).

**Desde el server:**

```bash
nslookup yvy-edge.<dominio>.edu.ar
```

### 3.2 Verificar accesibilidad desde Internet

**Crítico antes de seguir.** Desde una conexión externa (datos móviles, casa del admin):

```bash
nc -zv yvy-edge.<dominio>.edu.ar 443
```

Resultado esperado en este punto: **"Connection refused"**. Esto es CORRECTO: la red llega al server pero no hay servicio escuchando todavía.

Si da **"timeout"** en vez de "refused", hay un problema de red (firewall, port forward, CGNAT). Parar acá y resolver con el técnico.

### 3.3 Sacar certificado HTTPS con Let's Encrypt

Vamos a usar **certbot en modo standalone**: levanta un mini-servidor temporal en puerto 80 para responder el challenge.

**Paso 1: Instalar certbot:**

```bash
sudo apt update
sudo apt install -y certbot
```

**Paso 2: Verificar puerto 80 libre (nginx todavía no levanta):**

```bash
sudo ss -tulpn | grep :80
# Debe estar vacío
```

**Paso 3: Obtener certificado:**

```bash
sudo certbot certonly --standalone \
    -d yvy-edge.<dominio>.edu.ar \
    --email <email-admin>@example.com \
    --agree-tos \
    --non-interactive
```

Output esperado al final:
```
Successfully received certificate.
Certificate is saved at: /etc/letsencrypt/live/yvy-edge.<dominio>.edu.ar/fullchain.pem
Key is saved at:         /etc/letsencrypt/live/yvy-edge.<dominio>.edu.ar/privkey.pem
This certificate expires on 2026-08-XX.
```

**Paso 4: Permisos para que nginx en Docker pueda leer los certs:**

```bash
sudo chmod -R o+rx /etc/letsencrypt/live /etc/letsencrypt/archive
```

**Paso 5: Verificar el cert:**

```bash
sudo openssl x509 \
    -in /etc/letsencrypt/live/yvy-edge.<dominio>.edu.ar/fullchain.pem \
    -noout -subject -issuer -dates
```

Esperado: subject CN coincide con tu dominio, issuer Let's Encrypt, validity de 90 días.

### 3.4 Configurar renovación automática

Let's Encrypt da certs por 90 días. Hay que renovar automáticamente.

**Verificar timer de certbot:**

```bash
sudo systemctl list-timers | grep certbot
```

Si aparece `certbot.timer` con `enabled`, ya está. Si no:

```bash
sudo systemctl enable --now certbot.timer
```

**Hooks para detener/arrancar nginx durante renovación:**

El certbot usa modo standalone para renovar (mismo que la emisión inicial). Necesita el puerto 80 libre, así que debe detener nginx primero.

```bash
# Hook PRE: detener nginx antes de renovar
sudo tee /etc/letsencrypt/renewal-hooks/pre/10-stop-nginx.sh << 'EOF'
#!/bin/bash
cd /opt/yvy-server
docker compose stop nginx 2>/dev/null || true
EOF
sudo chmod +x /etc/letsencrypt/renewal-hooks/pre/10-stop-nginx.sh

# Hook POST: arrancar nginx después de renovar
sudo tee /etc/letsencrypt/renewal-hooks/post/10-start-nginx.sh << 'EOF'
#!/bin/bash
cd /opt/yvy-server
docker compose start nginx 2>/dev/null || true
EOF
sudo chmod +x /etc/letsencrypt/renewal-hooks/post/10-start-nginx.sh
```

**Test del renewal (dry run, no toca nada):**

```bash
sudo certbot renew --dry-run
```

### 3.5 [VERIFICAR]

- [ ] DNS resuelve al server desde Internet (no solo LAN)
- [ ] `nc -zv ... 443` desde afuera da "refused" (no timeout)
- [ ] Certificado HTTPS válido en `/etc/letsencrypt/live/...`
- [ ] `certbot renew --dry-run` corre sin errores
- [ ] `certbot.timer` enabled
- [ ] Hooks pre/post creados y ejecutables

---

## 4. Generar credentials

### 4.1 Generar passwords y tokens

```bash
cd /opt/yvy-server/secrets

# Password PostgreSQL usuario principal (yvyserver)
openssl rand -base64 32 > pg_password_yvyserver.txt

# Password PostgreSQL usuario read-only (yvyreader)
openssl rand -base64 32 > pg_password_yvyreader.txt

# Admin bearer token YVY (>= 32 chars, sin "CHANGE_ME" — validado por el código en env=production)
python3 -c "import secrets; print(secrets.token_urlsafe(48))" > yvy_admin_token.txt

chmod 600 *.txt
```

### 4.2 Crear archivo .env

```bash
cd /opt/yvy-server
nano .env
```

Pegar (reemplazando dominio y leyendo passwords con `cat secrets/pg_password_yvyserver.txt`):

```bash
# /opt/yvy-server/.env · YVY Server — producción
# DO NOT COMMIT TO GIT

# ─── Dominio (usado por nginx.conf) ─────────────────────────────────────
YVY_DOMAIN=yvy-edge.<dominio>.edu.ar

# ─── PostgreSQL (leídos por el servicio postgres del compose) ───────────
POSTGRES_DB=yvy
POSTGRES_USER=yvyserver
POSTGRES_PASSWORD=<contenido-de-pg_password_yvyserver.txt>

# ─── Admin API (prefijo YVY_ requerido por el código) ───────────────────
# Debe ser >= 32 chars y no contener "CHANGE_ME" en env=production
YVY_ADMIN_TOKEN=<contenido-de-yvy_admin_token.txt>

# ─── Log level ──────────────────────────────────────────────────────────
YVY_LOG_LEVEL=INFO
```

```bash
chmod 600 .env
```

> **Nota importante**: `YVY_ENV=production` y `YVY_DB_DSN` NO van en el `.env` — están
> hardcodeados en el `docker-compose.yml` de producción (§5.1) para evitar accidentes.

### 4.3 Actualizar credentials.txt

Agregar al archivo `secrets/credentials.txt`:

```
=== Servicios YVY ===
PostgreSQL DB: yvy
PostgreSQL user yvyserver: <password>
PostgreSQL user yvyreader: <password>
YVY Admin Bearer Token: <token>
Dominio público: yvy-edge.<dominio>.edu.ar
HTTPS cert: Let's Encrypt, renueva automático
Cert expires: 2026-08-XX
Deployed version: <commit hash de current_version.txt>
First deployed: <fecha>
```

### 4.4 [VERIFICAR]

```bash
ls -la /opt/yvy-server/secrets/
# Todos los archivos deben tener permisos -rw------- (600)

ls -la /opt/yvy-server/.env
# -rw------- 600
```

---

## 5. docker-compose.yml + nginx config (producción)

> **Importante**: el repositorio del código trae su propio `server-code/docker-compose.yml`
> (para desarrollo local). Ese archivo expone puertos de Postgres y API al host, y NO
> se usa en producción. El archivo que creamos acá en `/opt/yvy-server/docker-compose.yml`
> es el de producción: sin puertos expuestos para Postgres ni API, nginx delante con TLS.

### 5.1 Crear docker-compose.yml de producción

```bash
cd /opt/yvy-server
nano docker-compose.yml
```

Pegar:

```yaml
# /opt/yvy-server/docker-compose.yml
# YVY Server — stack de producción: PostgreSQL + FastAPI + nginx
# NO usar el docker-compose.yml del directorio server-code/ (es para desarrollo).

services:
  postgres:
    image: postgres:16-alpine
    container_name: yvy-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-yvy}
      POSTGRES_USER: ${POSTGRES_USER:-yvyserver}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD no está en .env}
      POSTGRES_INITDB_ARGS: "--encoding=UTF-8 --lc-collate=C --lc-ctype=C"
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
    networks:
      - yvy-internal
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-yvyserver} -d ${POSTGRES_DB:-yvy}"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
    # SIN ports: PostgreSQL no expuesto al host. Solo accesible desde yvy-internal.

  yvy-api:
    build:
      context: ./server-code
      dockerfile: Dockerfile
      args:
        # Inyectar info de versión en la imagen para que /api/v1/serverinfo la muestre.
        # Estas variables se pasan en el comando docker compose build (ver §6.1).
        GIT_COMMIT: ${GIT_COMMIT:-unknown}
        GIT_COMMIT_SHORT: ${GIT_COMMIT_SHORT:-unknown}
        BUILD_DATE: ${BUILD_DATE:-unknown}
    container_name: yvy-api
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      # YVY_ENV=production activa validaciones estrictas del código:
      # - admin token debe ser >= 32 chars y sin "CHANGE_ME"
      # - logs más detallados de errores
      YVY_ENV: production
      # DSN construido desde las vars POSTGRES_* para no repetir el password
      YVY_DB_DSN: postgresql+asyncpg://${POSTGRES_USER:-yvyserver}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB:-yvy}
      YVY_ADMIN_TOKEN: ${YVY_ADMIN_TOKEN:?YVY_ADMIN_TOKEN no está en .env}
      YVY_LOG_LEVEL: ${YVY_LOG_LEVEL:-INFO}
      YVY_SUPPORTED_SCHEMAS: "1.0"
      YVY_CLOCK_SKEW_MAX_SECONDS: "1800"
      YVY_CLOCK_SKEW_WARN_SECONDS: "300"
    networks:
      - yvy-internal
    volumes:
      - ./logs/api:/app/logs
    # SIN ports: FastAPI no expuesto al host. Solo accesible desde nginx via yvy-internal.

  nginx:
    image: nginx:1.27-alpine
    container_name: yvy-nginx
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/conf/nginx.conf:/etc/nginx/nginx.conf:ro
      - /etc/letsencrypt:/etc/letsencrypt:ro
      - ./logs/nginx:/var/log/nginx
    depends_on:
      yvy-api:
        condition: service_healthy
    networks:
      - yvy-internal

networks:
  yvy-internal:
    driver: bridge
    name: yvy-internal
```

### 5.2 Crear nginx.conf

```bash
mkdir -p /opt/yvy-server/nginx/conf
nano /opt/yvy-server/nginx/conf/nginx.conf
```

Pegar (reemplazando `yvy-edge.<dominio>.edu.ar` con tu dominio en los 4 lugares donde aparece):

```nginx
# /opt/yvy-server/nginx/conf/nginx.conf
# YVY Server — reverse proxy de producción

user nginx;
worker_processes auto;
worker_rlimit_nofile 65536;

error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
    use epoll;
    multi_accept on;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    log_format main '$remote_addr - [$time_iso8601] '
                    '"$request" $status $body_bytes_sent '
                    'rt=$request_time urt=$upstream_response_time';
    access_log /var/log/nginx/access.log main;

    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    server_tokens off;

    # Cap defensivo: requests YVY son pequeños (< 50KB)
    client_max_body_size 1m;
    client_body_timeout 10s;
    client_header_timeout 10s;

    # Rate limiting: 60 requests/min sostenido, burst 30
    limit_req_zone $binary_remote_addr zone=yvy_ingest:10m rate=60r/m;

    # ─── HTTPS principal ─────────────────────────────────────────────────
    server {
        listen 443 ssl http2;
        server_name yvy-edge.<dominio>.edu.ar;

        ssl_certificate /etc/letsencrypt/live/yvy-edge.<dominio>.edu.ar/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/yvy-edge.<dominio>.edu.ar/privkey.pem;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
        ssl_prefer_server_ciphers off;
        ssl_session_timeout 1d;
        ssl_session_cache shared:YVY_SSL:10m;
        ssl_session_tickets off;
        ssl_stapling on;
        ssl_stapling_verify on;

        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-Frame-Options "DENY" always;
        add_header Referrer-Policy "no-referrer" always;

        # Healthcheck público (sin auth, sin rate limit)
        location = /api/v1/healthz {
            proxy_pass http://yvy-api:8000;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;
            proxy_connect_timeout 5s;
            proxy_read_timeout 5s;
        }

        # Server info público
        location = /api/v1/serverinfo {
            proxy_pass http://yvy-api:8000;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;
            proxy_connect_timeout 5s;
            proxy_read_timeout 5s;
        }

        # Ingest endpoints (con rate limit)
        location /api/v1/ingest/ {
            limit_req zone=yvy_ingest burst=30 nodelay;
            proxy_pass http://yvy-api:8000;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;
            proxy_connect_timeout 5s;
            proxy_send_timeout 15s;
            proxy_read_timeout 15s;
        }

        # Admin endpoints (requieren bearer token — lo valida la API, no nginx)
        location /api/v1/admin/ {
            proxy_pass http://yvy-api:8000;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;
            proxy_connect_timeout 5s;
            proxy_read_timeout 30s;
        }

        # Cualquier otra ruta: 404
        location / {
            return 404 "Not Found\n";
        }
    }

    # ─── HTTP → HTTPS redirect ───────────────────────────────────────────
    server {
        listen 80;
        server_name yvy-edge.<dominio>.edu.ar;
        return 301 https://$server_name$request_uri;
    }

    # ─── Healthcheck interno para Docker (puerto 80 default server) ──────
    # Este bloque responde al healthcheck de docker compose.
    # No es accesible desde Internet (solo desde dentro de la red de containers).
    server {
        listen 80 default_server;
        server_name _;
        location = /healthz {
            access_log off;
            return 200 "ok\n";
        }
        location / {
            return 444;
        }
    }
}
```

### 5.3 Crear directorios de datos y logs

```bash
mkdir -p /opt/yvy-server/data/postgres
mkdir -p /opt/yvy-server/logs/{nginx,api}
chmod 755 /opt/yvy-server/data /opt/yvy-server/logs
```

### 5.4 [VERIFICAR]

```bash
ls /opt/yvy-server/
# Debe mostrar: data/ logs/ nginx/ scripts/ secrets/ server-code/ .env docker-compose.yml current_version.txt

docker compose -f /opt/yvy-server/docker-compose.yml config
# Valida sintaxis YAML y resuelve variables del .env.
# Si hay error de variable no definida, revisar el .env.
```

---

## 6. Levantar el stack

### 6.1 Build de la imagen yvy-api

El Dockerfile usa un build multi-stage que incrusta la versión git en la imagen. Pasar los args ahora para que `/api/v1/serverinfo` muestre el commit correcto:

```bash
cd /opt/yvy-server

GIT_COMMIT=$(cd server-code && git rev-parse HEAD)
GIT_COMMIT_SHORT=$(cd server-code && git rev-parse --short HEAD)
BUILD_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

docker compose build yvy-api \
    --build-arg GIT_COMMIT="$GIT_COMMIT" \
    --build-arg GIT_COMMIT_SHORT="$GIT_COMMIT_SHORT" \
    --build-arg BUILD_DATE="$BUILD_DATE"
```

Primera vez puede tardar 5-10 minutos (descarga imagen base Python, instala dependencias).

**[TROUBLESHOOT]** Si `docker compose build yvy-api` falla:
- "no such file Dockerfile": verificar `/opt/yvy-server/server-code/Dockerfile` existe.
- "pip install failed": probable problema de red. Reintentar.
- "poetry install failed": verificar que `pyproject.toml` está en `server-code/`.

### 6.2 Pull de imágenes (postgres y nginx)

```bash
docker compose pull postgres nginx
```

### 6.3 Levantar el stack en orden controlado

**Paso 1: PostgreSQL primero:**

```bash
docker compose up -d postgres

# Esperar hasta que esté healthy
until docker compose ps postgres | grep -q healthy; do
    sleep 3
    echo "esperando postgres..."
done
echo "postgres listo"
```

**Paso 2: yvy-api:**

```bash
docker compose up -d yvy-api
```

El entrypoint (`docker-entrypoint.sh`) hace automáticamente:
1. Espera a que la DB acepte conexiones.
2. Corre `alembic upgrade head` (aplica migraciones).
3. Arranca `uvicorn`.

Ver los logs para confirmar startup completo:

```bash
docker compose logs -f yvy-api
# Esperar a ver estas líneas:
# [entrypoint] Migrations applied.
# [entrypoint] Starting application: uvicorn ...
# INFO: Uvicorn running on http://0.0.0.0:8000
# Ctrl+C para salir del follow
```

**Paso 3: nginx:**

```bash
docker compose up -d nginx
docker compose ps
# Los 3 deben estar "Up"
```

### 6.4 Verificar conectividad interna

```bash
docker compose exec nginx wget -qO- http://yvy-api:8000/api/v1/healthz
# Esperado: {"status":"ok"}
```

Si falla, problema en la red interna de Docker o yvy-api crasheó.

### 6.5 [VERIFICAR]

```bash
docker compose ps
# 3 containers running

docker compose logs --tail=20 yvy-api
# Sin errores. Debe mostrar "startup complete; ready to serve requests"
```

---

## 7. Verificar PostgreSQL y migraciones

### 7.1 Confirmar que las migraciones se aplicaron

Las migraciones las corre automáticamente el entrypoint al iniciar `yvy-api`. Verificar que las 7 tablas existen:

```bash
docker compose exec postgres psql -U yvyserver -d yvy -c "
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema IN ('public', 'bronze')
ORDER BY table_schema, table_name;
"
```

Esperado — **7 tablas en total**:

| Schema | Tabla |
|--------|-------|
| `bronze` | `empirical_event_raw` |
| `bronze` | `empirical_observation_raw` |
| `bronze` | `ingest_failures` |
| `bronze` | `ingest_log` |
| `bronze` | `sensor_raw` |
| `public` | `station` |
| `public` | `station_credentials` |

Si alguna falta, correr migraciones manualmente:

```bash
docker compose exec yvy-api alembic upgrade head
```

### 7.2 Crear usuario read-only para queries admin

```bash
PG_READER_PASS=$(cat /opt/yvy-server/secrets/pg_password_yvyreader.txt)

docker compose exec -T postgres psql -U yvyserver -d yvy << EOF
CREATE USER yvyreader WITH PASSWORD '${PG_READER_PASS}';
GRANT CONNECT ON DATABASE yvy TO yvyreader;
GRANT USAGE ON SCHEMA public, bronze TO yvyreader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO yvyreader;
GRANT SELECT ON ALL TABLES IN SCHEMA bronze TO yvyreader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO yvyreader;
ALTER DEFAULT PRIVILEGES IN SCHEMA bronze GRANT SELECT ON TABLES TO yvyreader;
EOF
```

### 7.3 [VERIFICAR]

```bash
docker compose exec postgres psql -U yvyreader -d yvy -c "SELECT current_user, current_database();"
# Esperado: yvyreader | yvy

docker compose exec postgres psql -U yvyserver -d yvy -c "SELECT COUNT(*) FROM bronze.sensor_raw;"
# Esperado: 0 (DB recién inicializada)
```

---

## 8. Crear primera estación (IEA 7)

### 8.1 Comando create-station

```bash
cd /opt/yvy-server

docker compose exec yvy-api yvy-server create-station \
    --name "IEA 7 - Posadas" \
    --description "Escuela Agrotécnica N° 7, Misiones" \
    --location-name "Posadas, Misiones, AR" \
    --location-lat -27.366 \
    --location-lon -55.896 \
    | tee secrets/iea7_credentials.json
```

El comando imprime el JSON de credenciales a **stdout** (que `tee` captura al archivo) y los logs de audit van a **stderr** (visibles en la terminal, no van al archivo).

**Output esperado en la terminal (stdout):**

```json
{
  "station_uuid": "xxxxxxxx-xxxx-4xxx-xxxx-xxxxxxxxxxxx",
  "key_version": 1,
  "key_hmac_hex": "64 caracteres hexadecimales...",
  "name": "IEA 7 - Posadas",
  "created_at_utc": "2026-05-28T..."
}
```

```bash
chmod 600 secrets/iea7_credentials.json
```

> **IMPORTANTE**: `key_hmac_hex` es la clave HMAC-SHA256 con la que el edge firma todos
> sus requests. Es secreta. **No tiene forma de recuperarse** — si se pierde, hay que
> crear una nueva estación o rotar la clave (Fase 9.2). Guardala en el pendrive.

### 8.2 Guardar para el pendrive IEA 7

El archivo `secrets/iea7_credentials.json` es lo que hay que llevar a la IEA en un pendrive.
El edge necesita `station_uuid` y `key_hmac_hex` para firmar sus envelopes.

Además: guardar el `server_url` que el edge va a usar:

```bash
cat >> secrets/iea7_credentials.json.info << EOF
# Este archivo va al pendrive junto con iea7_credentials.json
server_url: https://yvy-edge.<dominio>.edu.ar
schema_version: "1.0"
EOF
```

### 8.3 [VERIFICAR]

```bash
docker compose exec postgres psql -U yvyserver -d yvy -c \
    "SELECT station_uuid, name, location_name FROM station;"
# Debe mostrar IEA 7

docker compose exec postgres psql -U yvyserver -d yvy -c \
    "SELECT station_uuid, key_version, LENGTH(key_hmac_hex) AS key_chars
     FROM station_credentials;"
# Debe mostrar: key_version=1, key_chars=64
```

---

## 9. Smoke test desde Internet

### 9.1 Healthcheck público

**Desde una computadora externa a la red de la tecnicatura** (datos móviles, casa, etc):

```bash
curl -v https://yvy-edge.<dominio>.edu.ar/api/v1/healthz
```

Esperado:
```
< HTTP/2 200
< x-server-time: 2026-05-28T18:00:00.123Z
< x-schema-versions-supported: 1.0
< x-server-version: 0.9.1-dev+...
{"status":"ok"}
```

Si recibís esto, **HTTPS funciona end-to-end desde Internet**.

### 9.2 Verificar server info

```bash
curl https://yvy-edge.<dominio>.edu.ar/api/v1/serverinfo
```

Esperado: JSON con `version`, `git_commit_short`, `build_date`, `schema_versions_supported`.

### 9.3 Verificar admin endpoint

```bash
# Con auth — debe funcionar:
ADMIN_TOKEN=$(cat /opt/yvy-server/secrets/yvy_admin_token.txt)
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
    https://yvy-edge.<dominio>.edu.ar/api/v1/admin/healthz

# Esperado: {"status":"ok", "db_responsive":true, "counts":{...}}

# Sin auth — debe dar 401:
curl -i https://yvy-edge.<dominio>.edu.ar/api/v1/admin/healthz
# Esperado: HTTP 401, body con "error_class":"AUTH"
```

### 9.4 Smoke test de ingest (firma HMAC manual)

Este test verifica el flujo completo: request firmado → autenticación → persistencia en bronze.

Crear el script de test (en el server o en tu máquina con acceso a Internet):

```bash
cat > /tmp/test_ingest.py << 'PYEOF'
#!/usr/bin/env python3
"""Smoke test: ingest firmado HMAC contra el server YVY."""
import hashlib
import hmac
import json
import secrets
import sys
from datetime import datetime, timezone

# pip install requests  (si no está)
import requests

# ── Config: completar con los valores de iea7_credentials.json ──────────
SERVER_URL = "https://yvy-edge.<dominio>.edu.ar"
STATION_UUID = "<station_uuid de iea7_credentials.json>"
KEY_VERSION = 1
KEY_HEX = "<key_hmac_hex de iea7_credentials.json>"
# ────────────────────────────────────────────────────────────────────────

EVENT_ID = "550e8400-e29b-41d4-a716-446655440000"


def canonicalize(obj: dict) -> bytes:
    """Canonical JSON v1.0 — debe ser byte-a-byte idéntico al del edge."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def main():
    now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    payload = {
        "record_id": EVENT_ID,
        "station_uuid": STATION_UUID,
        "observed_at_utc": now_utc,
        "observed_at_local": now_utc,  # simplificado para el smoke test
        "rain_mm": 5.0,
        "temp_c": 18.5,
        "wind_beaufort": 2,
        "wind_direction": "N",
        "humidity_percept": "media",
        "sky_coverage_pct": 50,
        "cloud_type": "cumulos",
        "soil_state": "humedo",
        "visibility": "buena",
        "precipitation_now": "no",
        "phenology_notes": "smoke test deploy",
        "is_valid": True,
        "created_by_user_id": 1,
        "created_at_utc": now_utc,
    }

    envelope_unsigned = {
        "schema_version": "1.0",
        "event_id": EVENT_ID,
        "station_uuid": STATION_UUID,
        "key_version": KEY_VERSION,
        "sent_at_utc": now_utc,
        "nonce": secrets.token_hex(16),
        "payload": payload,
    }

    # Firmar con HMAC-SHA256 sobre canonical JSON
    canonical = canonicalize(envelope_unsigned)
    key_bytes = bytes.fromhex(KEY_HEX)
    signature = hmac.new(key_bytes, canonical, hashlib.sha256).hexdigest()
    envelope = {**envelope_unsigned, "signature": signature}

    print(f"POST {SERVER_URL}/api/v1/ingest/observation")
    response = requests.post(
        f"{SERVER_URL}/api/v1/ingest/observation",
        json=envelope,
        timeout=20,
    )

    print(f"Status : {response.status_code}")
    print(f"Headers: x-server-time={response.headers.get('x-server-time')}")
    print(f"Body   : {json.dumps(response.json(), indent=2)}")

    if response.status_code != 200:
        print("\nFAILED")
        sys.exit(1)

    result = response.json().get("ingest_result")
    print(f"\ningest_result: {result}")
    if result not in ("inserted", "duplicate"):
        print("FAILED: resultado inesperado")
        sys.exit(1)

    print("SMOKE TEST OK")


if __name__ == "__main__":
    main()
PYEOF
```

**Primera ejecución (debe decir `inserted`):**

```bash
pip install requests --quiet
python3 /tmp/test_ingest.py
# Esperado al final: "ingest_result: inserted" + "SMOKE TEST OK"
```

**Segunda ejecución del mismo script (idempotencia — debe decir `duplicate`):**

```bash
python3 /tmp/test_ingest.py
# Esperado al final: "ingest_result: duplicate" + "SMOKE TEST OK"
```

### 9.5 Verificar que el dato llegó a bronze

```bash
ssh yvyadmin@<IP_LAN>

docker compose exec postgres psql -U yvyserver -d yvy -c \
    "SELECT event_id, station_uuid, received_at
     FROM bronze.empirical_observation_raw
     ORDER BY received_at DESC LIMIT 3;"
```

Debe mostrar la fila con el `event_id` `550e8400-...`.

### 9.6 [VERIFICAR]

- [ ] `/api/v1/healthz` responde 200 desde Internet
- [ ] HTTPS válido (sin warning de cert en el browser)
- [ ] `/api/v1/admin/healthz` requiere auth y funciona con token
- [ ] Smoke test de ingest devuelve `inserted` la primera vez
- [ ] Smoke test de ingest devuelve `duplicate` la segunda vez (UPSERT funcional)
- [ ] La fila aparece en `bronze.empirical_observation_raw`

**Si los 6 items están ✓, el server está LISTO para producción.**

---

## 10. Backup nocturno de PostgreSQL

### 10.1 Script de backup

```bash
mkdir -p /opt/yvy-server/scripts
cat > /opt/yvy-server/scripts/backup_postgres.sh << 'EOF'
#!/bin/bash
# /opt/yvy-server/scripts/backup_postgres.sh
set -euo pipefail

BACKUP_DIR="/opt/yvy-server/backups"
RETENTION_DAYS=30
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/yvy_${TIMESTAMP}.sql.gz"
LOG_FILE="${BACKUP_DIR}/backup.log"

mkdir -p "$BACKUP_DIR"
echo "[$(date -Iseconds)] Backup iniciado: $BACKUP_FILE" | tee -a "$LOG_FILE"

cd /opt/yvy-server
if docker compose exec -T postgres pg_dump -U yvyserver -d yvy --no-owner --no-acl \
    | gzip > "$BACKUP_FILE"; then
    SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    echo "[$(date -Iseconds)] Backup OK: $BACKUP_FILE ($SIZE)" | tee -a "$LOG_FILE"
else
    echo "[$(date -Iseconds)] ERROR: backup falló" | tee -a "$LOG_FILE"
    rm -f "$BACKUP_FILE"
    exit 1
fi

# Verificar integridad
if gunzip -t "$BACKUP_FILE" 2>/dev/null; then
    echo "[$(date -Iseconds)] Integridad OK" | tee -a "$LOG_FILE"
else
    echo "[$(date -Iseconds)] ERROR: backup corrupto" | tee -a "$LOG_FILE"
    rm -f "$BACKUP_FILE"
    exit 2
fi

# Limpiar backups viejos (> RETENTION_DAYS días)
DELETED=$(find "$BACKUP_DIR" -name "yvy_*.sql.gz" -mtime +${RETENTION_DAYS} -delete -print | wc -l)
[ "$DELETED" -gt 0 ] && echo "[$(date -Iseconds)] Eliminados $DELETED backups antiguos" | tee -a "$LOG_FILE"
EOF
chmod +x /opt/yvy-server/scripts/backup_postgres.sh
```

### 10.2 Script de restore

```bash
cat > /opt/yvy-server/scripts/restore_postgres.sh << 'EOF'
#!/bin/bash
# Uso: ./restore_postgres.sh /opt/yvy-server/backups/yvy_20260528_030000.sql.gz
set -euo pipefail

BACKUP_FILE="${1:?Uso: $0 <backup-file.sql.gz>}"
[ -f "$BACKUP_FILE" ] || { echo "ERROR: $BACKUP_FILE no existe"; exit 1; }

echo "ATENCIÓN: esto SOBRESCRIBE la base de datos actual."
read -p "Continuar? (escribe 'yes'): " CONFIRM
[ "$CONFIRM" = "yes" ] || { echo "Abortado."; exit 0; }

cd /opt/yvy-server

docker compose stop yvy-api

docker compose exec -T postgres psql -U postgres -d postgres << SQLEOF
DROP DATABASE IF EXISTS yvy;
CREATE DATABASE yvy OWNER yvyserver;
SQLEOF

gunzip -c "$BACKUP_FILE" | docker compose exec -T postgres psql -U yvyserver -d yvy

docker compose start yvy-api
echo "Restore completado."
EOF
chmod +x /opt/yvy-server/scripts/restore_postgres.sh
```

### 10.3 Programar cron diario

```bash
sudo crontab -e
```

Agregar al final:

```
# YVY backup nightly @ 03:00 AM
0 3 * * * /opt/yvy-server/scripts/backup_postgres.sh >> /opt/yvy-server/backups/cron.log 2>&1
```

### 10.4 Test manual

```bash
/opt/yvy-server/scripts/backup_postgres.sh
ls -lh /opt/yvy-server/backups/
# Debe aparecer el archivo .sql.gz con tamaño > 0
```

### 10.5 [VERIFICAR]

- [ ] `backup_postgres.sh` ejecutado manualmente sin error
- [ ] Archivo `.sql.gz` creado en `/opt/yvy-server/backups/`
- [ ] Crontab con la entrada de las 03:00 AM

---

## 11. Configurar logs

### 11.1 Verificar rotación de logs Docker

```bash
docker info | grep -A 3 "Logging Driver"
# Esperado: json-file, max-size 10m, max-file 3 (configurado en bootstrap)
```

### 11.2 Rotación de logs de nginx y API

```bash
sudo logrotate -d /etc/logrotate.d/yvy
# Debug mode: muestra qué haría sin hacerlo. Verificar que apunte a logs/nginx/*.log
```

### 11.3 Acceder a logs

```bash
cd /opt/yvy-server

# Logs en vivo
docker compose logs -f yvy-api
docker compose logs -f nginx

# Logs históricos
docker compose logs --tail=100 yvy-api

# Archivos de log persistentes
tail -f logs/nginx/access.log
tail -f logs/nginx/error.log
```

### 11.4 [VERIFICAR]

```bash
docker compose logs --tail=10 nginx
# Sin errores recientes

ls logs/nginx/
# access.log y error.log presentes
```

---

## 12. Boot automático

### 12.1 Verificar que Docker arranca al boot

```bash
systemctl is-enabled docker
# Esperado: enabled
```

### 12.2 Crear servicio systemd para el stack YVY

```bash
sudo tee /etc/systemd/system/yvy-stack.service << 'EOF'
[Unit]
Description=YVY Server Stack (Docker Compose)
Requires=docker.service
After=docker.service network-online.target
StartLimitIntervalSec=0

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/yvy-server
User=yvyadmin
Group=yvyadmin
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable yvy-stack
sudo systemctl status yvy-stack
```

### 12.3 Test de boot completo

```bash
sudo reboot
```

Esperar 2-3 minutos y reconectar:

```bash
ssh yvyadmin@<IP_LAN>
cd /opt/yvy-server
docker compose ps
# Los 3 containers deben estar "Up"
```

Desde Internet:

```bash
curl https://yvy-edge.<dominio>.edu.ar/api/v1/healthz
# Debe responder 200
```

### 12.4 [VERIFICAR]

- [ ] Reboot completo sin intervención humana
- [ ] Los 3 containers levantan en orden (postgres → api → nginx)
- [ ] Healthcheck público responde desde Internet post-reboot

---

## 13. Ciclo de redeploy

Esta sección se usa cuando llegue Fase 10, 11, etc., con cambios al código.

### 13.1 Workflow estándar de redeploy

```bash
ssh yvyadmin@<IP_LAN>
cd /opt/yvy-server

# Paso 1: backup manual ANTES de cualquier cambio
./scripts/backup_postgres.sh

# Paso 2: pull del código nuevo
cd server-code
git fetch
git log HEAD..origin/main --oneline   # ver qué cambia
git pull

# Paso 3: actualizar current_version.txt
cd /opt/yvy-server
cat > current_version.txt << EOF
deployed_at: $(date -Iseconds)
git_commit: $(cd server-code && git rev-parse HEAD)
git_branch: $(cd server-code && git rev-parse --abbrev-ref HEAD)
deployed_by: $(whoami)
EOF

# Paso 4: rebuild con info de versión
GIT_COMMIT=$(cd server-code && git rev-parse HEAD)
GIT_COMMIT_SHORT=$(cd server-code && git rev-parse --short HEAD)
BUILD_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

docker compose build yvy-api \
    --build-arg GIT_COMMIT="$GIT_COMMIT" \
    --build-arg GIT_COMMIT_SHORT="$GIT_COMMIT_SHORT" \
    --build-arg BUILD_DATE="$BUILD_DATE"

# Paso 5: reciclar solo yvy-api (postgres y nginx se quedan)
docker compose up -d --no-deps yvy-api

# Paso 6: verificar
docker compose logs --tail=30 yvy-api
curl https://yvy-edge.<dominio>.edu.ar/api/v1/healthz
```

### 13.2 Si la actualización incluye migraciones de DB

El entrypoint corre `alembic upgrade head` automáticamente al arrancar `yvy-api`. No hace falta correrlas manualmente. Verificar que corrieron en los logs:

```bash
docker compose logs yvy-api | grep -E "(alembic|migrat|entrypoint)"
# Debe mostrar: "[entrypoint] Migrations applied."
```

### 13.3 Verificación post-redeploy

```bash
docker compose ps
# Todos healthy

curl https://yvy-edge.<dominio>.edu.ar/api/v1/serverinfo
# Debe mostrar el nuevo git_commit_short
```

---

## 14. Rollback en caso de problema

### 14.1 Rollback rápido del código

```bash
cd /opt/yvy-server/server-code
git log --oneline -10        # ver últimos commits
git checkout <commit-anterior>
cd /opt/yvy-server

GIT_COMMIT=$(cd server-code && git rev-parse HEAD)
GIT_COMMIT_SHORT=$(cd server-code && git rev-parse --short HEAD)
BUILD_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

docker compose build yvy-api \
    --build-arg GIT_COMMIT="$GIT_COMMIT" \
    --build-arg GIT_COMMIT_SHORT="$GIT_COMMIT_SHORT" \
    --build-arg BUILD_DATE="$BUILD_DATE"

docker compose up -d --no-deps yvy-api
```

### 14.2 Rollback de DB

Solo si una migración corrompió datos. El backup nocturno te cubre hasta el día anterior.

```bash
ls -lt /opt/yvy-server/backups/
./scripts/restore_postgres.sh /opt/yvy-server/backups/yvy_<fecha>.sql.gz
```

### 14.3 Rollback de config nginx

```bash
cd /opt/yvy-server/nginx/conf
# (esto requiere que hayas hecho una copia antes del cambio)
cp nginx.conf.bak nginx.conf
docker compose restart nginx
```

Buena práctica: antes de editar `nginx.conf`, siempre hacer:

```bash
cp nginx.conf "nginx.conf.bak.$(date +%Y%m%d)"
```

---

## 15. Troubleshooting

### 15.1 nginx no levanta — error de cert

```
nginx: [emerg] cannot load certificate "...fullchain.pem"
```

```bash
sudo ls -l /etc/letsencrypt/live/yvy-edge.<dominio>.edu.ar/
sudo openssl x509 \
    -in /etc/letsencrypt/live/yvy-edge.<dominio>.edu.ar/fullchain.pem \
    -noout -subject
```

Si el directorio no existe, repetir §3.3.

### 15.2 yvy-api crashea en startup

```bash
docker compose logs yvy-api
```

Causas comunes:
- `YVY_ADMIN_TOKEN` no está en el `.env` → error `YVY_ADMIN_TOKEN no está en .env` del compose.
- `YVY_ADMIN_TOKEN` tiene menos de 32 chars o contiene "CHANGE_ME" → validación falla porque `YVY_ENV=production`.
- `YVY_DB_DSN` no arranca con `postgresql+asyncpg://` → validación falla.
- Postgres no está listo → el entrypoint reintenta 30 veces, luego sale con error.

### 15.3 502 Bad Gateway desde Internet

nginx anda pero no puede llegar a yvy-api.

```bash
docker compose exec nginx wget -qO- http://yvy-api:8000/api/v1/healthz
docker compose logs yvy-api | tail -20
```

### 15.4 PostgreSQL no acepta conexiones

```bash
docker compose logs postgres | tail -50
docker compose exec postgres pg_isready -U yvyserver -d yvy
```

Causas: disco lleno (`df -h /var`), password incorrecto en `.env`, volúmenes mal montados.

### 15.5 Cert HTTPS expirado

```bash
sudo certbot renew --force-renewal
docker compose restart nginx
```

### 15.6 Disco lleno

```bash
df -h
du -sh /opt/yvy-server/* | sort -h
docker system df
# Limpiar imágenes Docker viejas:
docker system prune -af
```

### 15.7 `POST /ingest/observation` devuelve 401

Mirar el campo `error` del body:
- `"station_uuid=X not registered"` → correr `create-station` (§8).
- `"no active credential for station=X key_version=N"` → verificar `key_version` en el envelope.
- `"HMAC signature mismatch"` → el edge firmó con una clave distinta a la registrada.

### 15.8 `POST /ingest/observation` devuelve 422 CLOCK

El `sent_at_utc` está demasiado lejos del reloj del server (> 30 min). El edge debe usar el header `X-Server-Time` de la respuesta para recalibrar su reloj.

---

## 16. Cheatsheet operativo

```bash
# ── Estado del stack ──────────────────────────────────────────────────────
cd /opt/yvy-server
docker compose ps
docker compose logs -f
docker compose logs --tail=50 yvy-api

# ── Restart de servicios ──────────────────────────────────────────────────
docker compose restart nginx
docker compose restart yvy-api
docker compose down && docker compose up -d    # full reload

# ── Acceso a PostgreSQL ───────────────────────────────────────────────────
docker compose exec postgres psql -U yvyserver -d yvy      # escritura
docker compose exec postgres psql -U yvyreader -d yvy      # solo lectura

# ── Queries útiles ────────────────────────────────────────────────────────
docker compose exec postgres psql -U yvyserver -d yvy -c "
SELECT 'station' AS t, COUNT(*) FROM public.station
UNION ALL SELECT 'credentials', COUNT(*) FROM public.station_credentials
UNION ALL SELECT 'obs_raw', COUNT(*) FROM bronze.empirical_observation_raw
UNION ALL SELECT 'event_raw', COUNT(*) FROM bronze.empirical_event_raw
UNION ALL SELECT 'sensor_raw', COUNT(*) FROM bronze.sensor_raw
UNION ALL SELECT 'ingest_log', COUNT(*) FROM bronze.ingest_log
UNION ALL SELECT 'ingest_failures', COUNT(*) FROM bronze.ingest_failures;"

# ── Smoke desde adentro del server ───────────────────────────────────────
docker compose exec nginx wget -qO- http://yvy-api:8000/api/v1/healthz

# ── Smoke desde Internet ──────────────────────────────────────────────────
curl https://yvy-edge.<dominio>.edu.ar/api/v1/healthz
curl -H "Authorization: Bearer $(cat secrets/yvy_admin_token.txt)" \
    https://yvy-edge.<dominio>.edu.ar/api/v1/admin/healthz

# ── CLI de la app ─────────────────────────────────────────────────────────
docker compose exec yvy-api yvy-server version
docker compose exec yvy-api yvy-server create-station --help

# ── Backups ───────────────────────────────────────────────────────────────
ls -lh /opt/yvy-server/backups/
/opt/yvy-server/scripts/backup_postgres.sh

# ── Logs ─────────────────────────────────────────────────────────────────
tail -f logs/nginx/access.log
tail -f logs/nginx/error.log
journalctl -u docker -f

# ── Versión deployada ─────────────────────────────────────────────────────
cat /opt/yvy-server/current_version.txt
curl https://yvy-edge.<dominio>.edu.ar/api/v1/serverinfo | python3 -m json.tool

# ── Certificado TLS ───────────────────────────────────────────────────────
sudo openssl x509 \
    -in /etc/letsencrypt/live/yvy-edge.<dominio>.edu.ar/fullchain.pem \
    -noout -dates
sudo certbot renew --dry-run

# ── Disco / RAM ───────────────────────────────────────────────────────────
df -h /opt /var
free -h
docker system df
```

---

## 17. Validación final

Marcá cuando esté completo:

- [ ] DNS resuelve a la IP pública desde Internet
- [ ] Cert HTTPS válido (sin warning en browser ni en curl)
- [ ] `/api/v1/healthz` responde 200 con headers `x-server-time` y `x-server-version`
- [ ] `/api/v1/admin/healthz` requiere auth y funciona con bearer token
- [ ] Smoke ingest firmado HMAC devuelve `inserted` la primera vez
- [ ] Smoke ingest repetido devuelve `duplicate` (UPSERT funcional)
- [ ] PostgreSQL tiene las 7 tablas y la estación IEA 7 registrada
- [ ] Backup nocturno configurado y probado manualmente
- [ ] `systemctl is-enabled yvy-stack docker certbot.timer` → todos `enabled`
- [ ] Reboot completo → todo levanta solo sin intervención
- [ ] Credentials documentados en `secrets/credentials.txt`
- [ ] `secrets/iea7_credentials.json` listo para llevar en pendrive

**Cuando los 12 items estén ✓, el server YVY está en producción.**

---

## 18. Próximos pasos

Después de cerrar este runbook:

1. **Instalar el edge en IEA 7.** Llevar pendrive con `iea7_credentials.json` + instalador del edge. Documentado en `RUNBOOK_INSTALL_EDGE_IEA.md` (a generar en Fase 9.2).

2. **Primera semana de monitoreo:**
   ```bash
   docker compose logs -f yvy-api
   tail -f /opt/yvy-server/logs/nginx/access.log
   ```

3. **Sumar IEAs 2 y 3** tras 1 semana estable de IEA 7.

4. **Monitorear estabilidad de la IP fija.** Ante cualquier cambio de conectividad (corte de fibra, cambio de proveedor), tener a mano el procedimiento de actualización de DNS.

---

**Fin del runbook · YVY Deploy v2.0**

Última actualización: 28-May-2026. Mantener este archivo en el repo junto con el código.
Cada cambio operativo importante: bumpear versión arriba.
