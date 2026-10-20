#!/usr/bin/env bash
# ===========================================================================
# backup.sh — full, configurable backup of the pacod platform.
#
# Captures every irreplaceable piece of state into ONE timestamped, mode-0600
# tar.gz archive:
#
#   postgres/   pg_dumpall globals + custom-format dumps of the app (pacod) and
#               pacod_id (identity) databases (via `docker compose exec
#               pacod-postgres`, NOT pgbouncer, NOT the host pg_dump).
#   qdrant/     A REST snapshot of every Qdrant collection (real names from
#               GET /collections, so any fine-tune alias is resolved), pulled
#               over the REST download endpoint.
#   config/     Secrets + configuration — .env, .env.gateway, deploy/secrets/
#               (pacod-id's and the gateway's), the pacod-id and gateway config,
#               compose files, Caddyfiles, monitoring config, seed data.
#               (.env carries APP_SECRETS_KEY, which decrypts provider_secrets DB
#                rows, and deploy/secrets/id.env carries PID_KEK, which seals
#                pacod-id's signing keys — both MUST live in the same archive as
#                the DB dumps.)
#
# Not here: the gateway's own stores (its PostgreSQL and ClickHouse) — pacod-gw's
# `make backup` (in GW_DIR) backs those up.
#   volumes/    documents_data (volume ap-documents-data), tarred from inside the
#               container, so the compose volume prefix / declared-vs-bind
#               discrepancies never matter.
#   redis/      (optional, off by default) BGSAVE + /data.
#
# ONLINE backup: the stack must be running. Restore with restore.sh.
#
# Configuration is entirely via env vars (see scripts/backup.env.example and
# `backup.sh --help`). Zero config works out of the box on a standard checkout.
# ===========================================================================
set -euo pipefail
umask 077   # every file we create is owner-only (the archive holds all secrets)

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib_backup.sh"

usage() {
  cat <<'EOF'
Usage: scripts/backup.sh [options]

Creates a single timestamped, owner-only tar.gz under $BACKUP_DIR.

Options:
  --env-file <path>   Load configuration from this file (KEY=value).
                      Default: scripts/backup.env if it exists.
  -h, --help          Show this help.

Key environment variables (see scripts/backup.env.example for the full list):
  BACKUP_DIR                 Destination root        (default: ./backups)
  COMPOSE_FILE               Stack compose file      (default: pacod-api/docker-compose.yml)
  COMPOSE_PROJECT_NAME       Compose project name    (default: pacod)
  ENV_FILE                   App env mined for creds (default: pacod-api/.env)
  POSTGRES_USER/_PASSWORD    DB credentials          (default: from ENV_FILE)
  POSTGRES_DB/_ID_DB         DB names                (default: pacod/pacod_id)
  QDRANT_URL                 Qdrant REST endpoint    (default: http://localhost:6333)
  BACKUP_INCLUDE_DOCUMENTS   Back up doc volumes     (default: true)
  BACKUP_INCLUDE_REDIS       Back up Redis           (default: false)
  BACKUP_RETENTION_DAYS      Prune archives older    (default: 0 = never)
EOF
}

# --- args --------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) BACKUP_ENV_FILE="$2"; shift 2 ;;
    --env-file=*) BACKUP_ENV_FILE="${1#*=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
done

load_config

# --- preflight ---------------------------------------------------------------
require_docker
service_running "$SVC_POSTGRES" || die "the '$SVC_POSTGRES' container is not running — start the stack first (make up). This is an online backup."
curl -fsS "$QDRANT_URL/collections" >/dev/null 2>&1 || die "cannot reach Qdrant at $QDRANT_URL — is the stack up and the port published?"

# --- staging -----------------------------------------------------------------
TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"
NAME="pacod-backup-$TIMESTAMP"
STAGE="$BACKUP_DIR/$NAME"
ARCHIVE="$BACKUP_DIR/$NAME.tar.gz"
mkdir -p "$STAGE"/{postgres,qdrant,config,volumes,redis}
# Clean up the staging dir on any exit (the archive is what we keep).
cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

log "Backing up pacod → $ARCHIVE"

# --- 1. PostgreSQL -----------------------------------------------------------
log "PostgreSQL: dumping globals + databases ($POSTGRES_DB, $POSTGRES_ID_DB)"
pg() { compose_exec -e PGPASSWORD="$POSTGRES_PASSWORD" "$SVC_POSTGRES" "$@"; }

pg pg_dumpall -U "$POSTGRES_USER" --globals-only > "$STAGE/postgres/globals.sql" \
  || die "pg_dumpall --globals-only failed"

for db in "$POSTGRES_DB" "$POSTGRES_ID_DB"; do
  log "  pg_dump -Fc $db"
  if ! pg pg_dump -U "$POSTGRES_USER" -Fc -d "$db" > "$STAGE/postgres/$db.dump"; then
    # pacod_id may legitimately be absent (backend-only deployment).
    if [[ "$db" != "$POSTGRES_DB" ]]; then
      warn "  pg_dump of '$db' failed — skipping (not provisioned here?)"
      rm -f "$STAGE/postgres/$db.dump"
    else
      die "pg_dump of '$db' failed"
    fi
  fi
done
ok "PostgreSQL dumped"

# --- 2. Qdrant ---------------------------------------------------------------
log "Qdrant: snapshotting all collections"
COLLECTIONS=()
while IFS= read -r _col; do [[ -n "$_col" ]] && COLLECTIONS+=("$_col"); done < <(qdrant_collections)
if [[ ${#COLLECTIONS[@]} -eq 0 ]]; then
  warn "Qdrant reported no collections — nothing to snapshot (fresh install?)"
else
  : > "$STAGE/qdrant/collections.txt"
  for col in "${COLLECTIONS[@]}"; do
    log "  snapshot $col"
    # Ask Qdrant to build a snapshot; response: {"result":{"name":"<file>.snapshot"}}
    snap_json="$(curl -fsS -X POST "$QDRANT_URL/collections/$col/snapshots")" \
      || die "failed to create snapshot for collection '$col'"
    snap_name="$(printf '%s' "$snap_json" \
      | grep -oE '"name"[[:space:]]*:[[:space:]]*"[^"]+\.snapshot"' \
      | sed -E 's/.*"([^"]+\.snapshot)".*/\1/' | head -n1)"
    [[ -n "$snap_name" ]] || die "could not parse snapshot name for '$col' from: $snap_json"
    # Download it (snapshots live in the container's /qdrant/snapshots, not the
    # /data/qdrant/storage bind — the REST endpoint is the only portable pull).
    curl -fsS "$QDRANT_URL/collections/$col/snapshots/$snap_name" \
      -o "$STAGE/qdrant/$col.snapshot" \
      || die "failed to download snapshot for '$col'"
    echo "$col" >> "$STAGE/qdrant/collections.txt"
    # Remove the server-side snapshot so /qdrant/snapshots doesn't accumulate.
    curl -fsS -X DELETE "$QDRANT_URL/collections/$col/snapshots/$snap_name" >/dev/null 2>&1 \
      || warn "  could not delete server-side snapshot $snap_name (harmless)"
  done
  ok "Qdrant snapshotted ${#COLLECTIONS[@]} collection(s)"
fi

# --- 3. Config & secrets -----------------------------------------------------
log "Config: collecting secrets + configuration files"
# Paths are relative to REPO_ROOT. Missing ones are warned about, not fatal
# (a dev box may lack monitoring/.env, the override file, etc.).
CONFIG_PATHS=(
  # --- irreplaceable, gitignored secrets (highest priority) ---
  "pacod-api/.env"
  # The services' gateway keys and admin-api's tokens (scripts/gw_bootstrap.py
  # reissues any that stop working, so this one is convenience, not survival).
  "pacod-api/.env.gateway"
  # pacod-id's KEK + pepper + client secrets, the gateway's secrets, provider keys.
  "deploy/secrets"
  "pacod-api/docker-compose.override.yml"
  "monitoring/.env"
  "monitoring/prometheus/prometheus.yml"
  # --- git-tracked config, bundled for a self-contained restore ---
  "pacod-api/.env.example"
  "pacod-api/docker/pacod-id"
  "pacod-api/authz"
  "deploy/pacod-gw"
  "platform"
  "pacod-api/docker/caddy/Caddyfile"
  "pacod-api/docker-compose.yml"
  "pacod-api/docker-compose.standalone.yml"
  "pacod-api/docker-compose.gpu.yml"
  "monitoring/docker-compose.monitoring.yml"
  "monitoring/docker-compose.exporters.yml"
  "monitoring/prometheus/alerts"
  "monitoring/alertmanager"
  "monitoring/grafana/provisioning"
  "pacod-api/data"
)
present=()
for p in "${CONFIG_PATHS[@]}"; do
  if [[ -e "$REPO_ROOT/$p" ]]; then
    present+=("$p")
  else
    warn "  config path absent, skipping: $p"
  fi
done
if [[ ${#present[@]} -gt 0 ]]; then
  # tar -C REPO_ROOT preserves the relative paths so restore can lay them back down.
  tar czf "$STAGE/config/config-files.tar.gz" -C "$REPO_ROOT" "${present[@]}"
  # Loud check on the two files a restore cannot do without.
  for must in "pacod-api/.env" "deploy/secrets/id.env"; do
    [[ -e "$REPO_ROOT/$must" ]] || warn "  NOTE: $must was not present — restore may be incomplete"
  done
  ok "Config archived (${#present[@]} paths)"
else
  warn "No config paths found — config archive is empty"
fi

# --- 4. Document / upload volumes -------------------------------------------
if is_true "$BACKUP_INCLUDE_DOCUMENTS"; then
  # Tar the mountpoint as the container sees it (sidesteps volume naming).
  tar_volume() { # <service> <mount> <out-file> <label>
    local svc="$1" mount="$2" out="$3" label="$4"
    if ! service_running "$svc"; then
      warn "  $label: service '$svc' not running — skipping"; return 0
    fi
    log "  $label: tar $svc:$mount"
    if compose_exec "$svc" tar czf - -C "$mount" . > "$out" 2>/dev/null; then
      ok "  $label archived"
    else
      warn "  $label: tar failed (image may lack tar, or mount empty) — skipping"
      rm -f "$out"
    fi
  }
  log "Volumes: documents"
  tar_volume "$SVC_DOCUMENTS" "$DOCUMENTS_MOUNT" "$STAGE/volumes/documents_data.tar.gz" "documents_data"
else
  log "Volumes: skipped (BACKUP_INCLUDE_DOCUMENTS=$BACKUP_INCLUDE_DOCUMENTS)"
fi

# --- 5. Redis (optional) -----------------------------------------------------
if is_true "$BACKUP_INCLUDE_REDIS"; then
  log "Redis: BGSAVE + copy /data"
  if service_running "$SVC_REDIS"; then
    last_save="$(compose_exec "$SVC_REDIS" redis-cli LASTSAVE 2>/dev/null | tr -d '\r' || echo 0)"
    compose_exec "$SVC_REDIS" redis-cli BGSAVE >/dev/null 2>&1 || warn "  BGSAVE returned non-zero"
    # Wait (up to ~30s) for LASTSAVE to advance, i.e. the background save to finish.
    for _ in $(seq 1 30); do
      now_save="$(compose_exec "$SVC_REDIS" redis-cli LASTSAVE 2>/dev/null | tr -d '\r' || echo 0)"
      [[ "$now_save" != "$last_save" ]] && break
      sleep 1
    done
    if compose_exec "$SVC_REDIS" tar czf - -C "$REDIS_MOUNT" . > "$STAGE/redis/redis_data.tar.gz" 2>/dev/null; then
      ok "Redis archived"
    else
      warn "Redis tar failed — skipping"; rm -f "$STAGE/redis/redis_data.tar.gz"
    fi
  else
    warn "Redis service not running — skipping"
  fi
else
  log "Redis: skipped (BACKUP_INCLUDE_REDIS=$BACKUP_INCLUDE_REDIS)"
fi

# --- 6. Manifest -------------------------------------------------------------
{
  echo "pacod backup manifest"
  echo "created_utc:      $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "host:             $(hostname 2>/dev/null || echo unknown)"
  echo "git_sha:          $(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo n/a)"
  echo "compose_project:  $COMPOSE_PROJECT_NAME"
  echo "postgres_dbs:     $POSTGRES_DB $POSTGRES_ID_DB"
  echo "qdrant_url:       $QDRANT_URL"
  echo "include_documents:$BACKUP_INCLUDE_DOCUMENTS"
  echo "include_redis:    $BACKUP_INCLUDE_REDIS"
  echo "docker:           $(docker --version 2>/dev/null || echo n/a)"
  echo
  echo "contents:"
  ( cd "$STAGE" && find . -type f | sort | while IFS= read -r f; do
      printf '  %s\t%s bytes\n' "$f" "$(wc -c < "$f" | tr -d ' ')"
    done )
} > "$STAGE/MANIFEST.txt"

# --- 7. Seal the archive -----------------------------------------------------
log "Sealing archive"
tar czf "$ARCHIVE" -C "$BACKUP_DIR" "$NAME"
chmod 600 "$ARCHIVE"

# --- 8. Retention pruning ----------------------------------------------------
if [[ "$BACKUP_RETENTION_DAYS" -gt 0 ]] 2>/dev/null; then
  log "Pruning archives older than $BACKUP_RETENTION_DAYS day(s)"
  find "$BACKUP_DIR" -maxdepth 1 -type f -name 'pacod-backup-*.tar.gz' \
    -mtime "+$((BACKUP_RETENTION_DAYS - 1))" -print -delete >&2 || true
fi

size="$(du -h "$ARCHIVE" | cut -f1)"
ok "Backup complete: $ARCHIVE ($size)"
echo "$ARCHIVE"

# --- 9. Optional off-site copy ------------------------------------------------
if [[ -n "${REMOTE_HOST:-}" ]]; then
  log "Copying the archive to ${REMOTE_USER:+$REMOTE_USER@}$REMOTE_HOST:${REMOTE_DIR:-.}"
  scp "$ARCHIVE" "${REMOTE_USER:+$REMOTE_USER@}$REMOTE_HOST:${REMOTE_DIR:-.}" >&2 \
    || die "the copy to $REMOTE_HOST failed (the local archive is complete: $ARCHIVE)"
fi