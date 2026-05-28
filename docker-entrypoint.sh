#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# YVY Server · Docker entrypoint
#
# Comportamiento:
#   1. Espera a que la DB esté disponible (depends_on healthcheck ya
#      asegura esto, pero defensa adicional contra race conditions).
#   2. Corre `alembic upgrade head` para aplicar migraciones pendientes.
#   3. Ejecuta el CMD del Dockerfile (uvicorn por default).
#
# El exec final pasa señales SIGTERM/SIGINT al proceso uvicorn, lo
# que permite graceful shutdown en docker compose down.
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

# ─── Helpers de log ───────────────────────────────────────────────────
log() {
    echo "[entrypoint] $*" >&2
}

# ─── 1. Esperar a la DB ──────────────────────────────────────────────
# Aunque depends_on con healthcheck en docker-compose ya cubre esto,
# este loop defensivo nos protege contra setups que no usen compose
# (k8s, deploy manual, etc).

wait_for_db() {
    log "Waiting for database to accept connections..."

    # Parsea el DSN de forma simple para extraer host y port.
    # Formato esperado: postgresql+asyncpg://user:pass@host:port/db
    local dsn="${YVY_DB_DSN:-}"
    if [[ -z "$dsn" ]]; then
        log "ERROR: YVY_DB_DSN is not set"
        exit 1
    fi

    # Extraer host:port con awk (simple, evita instalar parse libs)
    local host_port
    host_port=$(echo "$dsn" | awk -F'@' '{print $2}' | awk -F'/' '{print $1}')
    local host="${host_port%:*}"
    local port="${host_port#*:}"

    if [[ -z "$host" ]] || [[ -z "$port" ]]; then
        log "ERROR: could not parse host:port from YVY_DB_DSN"
        exit 1
    fi

    local max_attempts=30
    local attempt=0

    while ! (echo > "/dev/tcp/${host}/${port}") 2>/dev/null; do
        attempt=$((attempt + 1))
        if [[ $attempt -ge $max_attempts ]]; then
            log "ERROR: database not reachable at ${host}:${port} after ${max_attempts}s"
            exit 1
        fi
        sleep 1
    done

    log "Database is up at ${host}:${port} (after ${attempt}s)."
}

# ─── 2. Aplicar migraciones ──────────────────────────────────────────
run_migrations() {
    log "Running alembic upgrade head..."
    if ! alembic upgrade head; then
        log "ERROR: alembic upgrade failed"
        exit 1
    fi
    log "Migrations applied."
}

# ─── 3. Main ─────────────────────────────────────────────────────────
main() {
    wait_for_db
    run_migrations

    log "Starting application: $*"
    exec "$@"
}

main "$@"
