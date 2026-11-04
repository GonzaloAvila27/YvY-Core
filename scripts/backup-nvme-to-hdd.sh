#!/bin/bash
# /opt/yvy-server/scripts/backup-nvme-to-hdd.sh
#
# Backup completo (en caliente) de nvme0n1 hacia el HDD de 1TB montado en /backup
# (/dev/sda5). Usa snapshots LVM de las LVs root/var para copiar un estado
# consistente sin detener servicios, y rsync --link-dest para que cada corrida
# incremental solo consuma espacio por los cambios (estilo rsnapshot).
#
# Pensado para correr sin supervisión vía yvy-backup-disk.timer. Nunca podamos
# llegar a borrar backups viejos si la corrida actual falla (set -e + trap
# garantizan que la retención solo se aplica al final de una corrida exitosa).
set -euo pipefail

VG_NAME="yvy-server-vg"
BACKUP_ROOT="/backup"
SNAP_DIR="${BACKUP_ROOT}/snapshots"
LOG_DIR="${BACKUP_ROOT}/logs"
LOG_FILE="${LOG_DIR}/backup-disk.log"
LOCK_FILE="/run/lock/yvy-backup-disk.lock"
EXPECTED_BACKUP_DEV="/dev/sda5"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
RETENTION_COUNT=14
MIN_FREE_GB=50
ROOT_SNAP_SIZE="20G"
VAR_SNAP_SIZE="25G"

DRY_RUN=false
FORCE=false

CLEANUP_MOUNTS=()
CLEANUP_LVS=()

log() {
    local msg="[$(date -Iseconds)] $*"
    if [[ -d "$LOG_DIR" ]]; then
        echo "$msg" | tee -a "$LOG_FILE"
    else
        echo "$msg"
    fi
}

die() {
    log "ERROR: $*"
    exit 1
}

cleanup() {
    local rc=$?
    local m lv
    for m in "${CLEANUP_MOUNTS[@]:-}"; do
        [[ -n "$m" ]] || continue
        mountpoint -q "$m" 2>/dev/null && umount "$m" 2>/dev/null
        rmdir "$m" 2>/dev/null || true
    done
    for lv in "${CLEANUP_LVS[@]:-}"; do
        [[ -n "$lv" ]] || continue
        lvremove -f "/dev/${VG_NAME}/${lv}" >/dev/null 2>&1 200>&- || true
    done
    if (( rc != 0 )); then
        log "Backup ABORTADO (exit $rc). No se tocó la retención de backups previos."
    fi
    exit "$rc"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        --force) FORCE=true; shift ;;
        *) die "Argumento desconocido: $1" ;;
    esac
done

[[ "$EUID" -eq 0 ]] || die "Este script debe ejecutarse como root"

trap cleanup EXIT

exec 200>"$LOCK_FILE"
flock -n 200 || die "Ya hay una corrida de backup en curso (lock: $LOCK_FILE)"

# --- Pre-flight ---
mountpoint -q "$BACKUP_ROOT" || die "$BACKUP_ROOT no está montado"

ACTUAL_SRC="$(readlink -f "$(findmnt -no SOURCE "$BACKUP_ROOT")")"
EXPECTED_SRC="$(readlink -f "$EXPECTED_BACKUP_DEV")"
[[ "$ACTUAL_SRC" == "$EXPECTED_SRC" ]] || \
    die "$BACKUP_ROOT no apunta a $EXPECTED_BACKUP_DEV (apunta a $ACTUAL_SRC)"

AVAIL_KB="$(df --output=avail "$BACKUP_ROOT" | tail -1 | tr -d ' ')"
AVAIL_GB=$(( AVAIL_KB / 1024 / 1024 ))
(( AVAIL_GB >= MIN_FREE_GB )) || \
    die "Espacio libre insuficiente en $BACKUP_ROOT: ${AVAIL_GB}GB (mínimo ${MIN_FREE_GB}GB)"

if [[ ! -d "$SNAP_DIR" ]]; then
    mapfile -t existing_entries < <(find "$BACKUP_ROOT" -maxdepth 1 -mindepth 1 \
        -not -name 'lost+found' -not -name 'logs' -not -name 'snapshots')
    if [[ ${#existing_entries[@]} -gt 0 && "$FORCE" != true ]]; then
        die "$BACKUP_ROOT tiene contenido inesperado y no parece inicializado por" \
            " este script: ${existing_entries[*]}. Revisar manualmente o usar --force."
    fi
fi

mkdir -p "$LOG_DIR" "$SNAP_DIR"

log "=== Backup disco iniciado (timestamp=${TIMESTAMP} dry_run=${DRY_RUN}) ==="

# --- Helpers de sincronización ---

run_rsync() {
    local src="$1" dest_base="$2"; shift 2
    local excludes=("$@")
    local dest="${dest_base}/${TIMESTAMP}"
    local link_dest_arg=()

    mkdir -p "$dest_base"
    [[ -L "${dest_base}/latest" ]] && link_dest_arg=(--link-dest="${dest_base}/latest")

    local rsync_args=(-aAX --delete)
    "$DRY_RUN" && rsync_args+=(--dry-run)

    rsync "${rsync_args[@]}" "${link_dest_arg[@]}" "${excludes[@]}" "${src}/" "${dest}/"

    if ! "$DRY_RUN"; then
        ln -sfn "$(basename "$dest")" "${dest_base}/latest.tmp"
        mv -T "${dest_base}/latest.tmp" "${dest_base}/latest"
    fi
}

sync_lvm_source() {
    local name="$1" lv="$2" size="$3"; shift 3
    local excludes=("$@")
    local snap_name="${lv}_backup_snap"
    local snap_dev="/dev/${VG_NAME}/${snap_name}"

    log "Sincronizando ${name} (LV ${lv}, snapshot ${size})"

    if lvs "$snap_dev" 200>&- &>/dev/null; then
        log "WARN: snapshot residual ${snap_name} encontrado (corrida anterior falló), eliminando"
        lvremove -f "$snap_dev" >/dev/null 200>&-
    fi

    lvcreate --size "$size" --snapshot --name "$snap_name" "/dev/${VG_NAME}/${lv}" >/dev/null 200>&-
    CLEANUP_LVS+=("$snap_name")

    local mnt
    mnt="$(mktemp -d "/mnt/yvy-backup-${name}.XXXXXX")"
    mount -o ro "$snap_dev" "$mnt"
    CLEANUP_MOUNTS+=("$mnt")

    run_rsync "$mnt" "${SNAP_DIR}/${name}" "${excludes[@]}"

    umount "$mnt"
    rmdir "$mnt"
    lvremove -f "$snap_dev" >/dev/null 200>&-
    CLEANUP_MOUNTS=("${CLEANUP_MOUNTS[@]/$mnt}")
    CLEANUP_LVS=("${CLEANUP_LVS[@]/$snap_name}")

    log "OK ${name}"
}

sync_plain_source() {
    local name="$1" src="$2"; shift 2
    local excludes=("$@")

    log "Sincronizando ${name} (${src})"
    run_rsync "$src" "${SNAP_DIR}/${name}" "${excludes[@]}"
    log "OK ${name}"
}

prune_old() {
    local dest_base="$1" keep="$2"
    [[ -d "$dest_base" ]] || return 0

    mapfile -t snaps < <(find "$dest_base" -maxdepth 1 -mindepth 1 -type d -name '20*' | sort)
    local total=${#snaps[@]}
    if (( total > keep )); then
        local remove_count=$(( total - keep ))
        local i
        for ((i = 0; i < remove_count; i++)); do
            log "Eliminando snapshot antiguo: ${snaps[$i]}"
            rm -rf "${snaps[$i]}"
        done
    fi
}

# --- Fuentes a respaldar ---

sync_lvm_source root root "$ROOT_SNAP_SIZE" \
    --exclude=/swapfile --exclude=/lost+found/

sync_lvm_source var var "$VAR_SNAP_SIZE" \
    --exclude=/lib/docker/ --exclude=/cache/apt/archives/ --exclude=/tmp/ --exclude=/lost+found/

sync_plain_source boot /boot
sync_plain_source boot-efi /boot/efi

# --- Retención (solo si todo lo anterior salió bien) ---

if ! "$DRY_RUN"; then
    for name in root var boot boot-efi; do
        prune_old "${SNAP_DIR}/${name}" "$RETENTION_COUNT"
    done
else
    log "dry-run: se omite la poda de retención"
fi

log "=== Backup disco finalizado OK (timestamp=${TIMESTAMP}) ==="
