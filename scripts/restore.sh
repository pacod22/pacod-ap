#!/usr/bin/env bash
# ===========================================================================
# restore.sh — restore a pacod backup produced by backup.sh.
#
# Reverses every component of the archive against a RUNNING stack:
#
#   PostgreSQL  restore globals, then `pg_restore --clean --if-exists` each
#               database (via `docker compose exec pacod-postgres`). The target
#               databases must already exist (they are created by the normal
#               `make up` / pacod-db-init bootstrap).
#   Qdrant      upload+recover each *.snapshot into its collection
#               (POST /collections/{c}/snapshots/upload?priority=snapshot).
#   Config      by default only *previews* where config files would go — pass
#               --overwrite-config to actually overwrite live .env / secrets.
#   Volumes     untar documents_data (volume ap-documents-data) back into the container.
#
# An archive made while Open-WebUI was part of the platform also holds its database
# (postgres/openwebui.dump) and data dir (volumes/openwebui_data.tar.gz): both are left out,
# with a note, since nothing here runs Open-WebUI any more.
#   Redis       (if present) untar /data and restart redis.
#
# Manual equivalents for every step are documented inline below, so the archive
# is restorable by hand if this script is unavailable.
#
# Destructive: pg_restore --clean drops & recreates objects; a Qdrant recover
# replaces the collection. Guarded by an interactive confirm (skip with --yes).
# ===========================================================================
set -euo pipefail
umask 077

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib_backup.sh"

usage() {
  cat <<'EOF'
Usage: scripts/restore.sh <archive.tar.gz> [options]

Options:
  --yes                 Don't prompt for confirmation (non-interactive).
  --overwrite-config    Actually overwrite live config/secrets (.env, deploy/secrets,
                        Caddyfiles, …). Default: extract them to a preview dir only.
  --skip-postgres       Don't restore PostgreSQL.
  --skip-qdrant         Don't restore Qdrant collections.
  --skip-volumes        Don't restore the document volume.
  --skip-redis          Don't restore Redis (Redis is restored only if present).
  --env-file <path>     Load configuration from this file (KEY=value).
  -h, --help            Show this help.

Configuration (DB creds, QDRANT_URL, compose file, service names) is read the
same way as backup.sh — see scripts/backup.env.example.
EOF
}

# --- args --------------------------------------------------------------------
ARCHIVE=""
ASSUME_YES=false
OVERWRITE_CONFIG=false
DO_POSTGRES=true; DO_QDRANT=true; DO_VOLUMES=true; DO_REDIS=true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y) ASSUME_YES=true; shift ;;
    --overwrite-config) OVERWRITE_CONFIG=true; shift ;;
    --skip-postgres) DO_POSTGRES=false; shift ;;
    --skip-qdrant) DO_QDRANT=false; shift ;;
    --skip-volumes) DO_VOLUMES=false; shift ;;
    --skip-redis) DO_REDIS=false; shift ;;
    --env-file) BACKUP_ENV_FILE="$2"; shift 2 ;;
    --env-file=*) BACKUP_ENV_FILE="${1#*=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) die "unknown option: $1 (see --help)" ;;
    *) [[ -z "$ARCHIVE" ]] && ARCHIVE="$1" || die "unexpected argument: $1"; shift ;;
  esac
done

[[ -n "$ARCHIVE" ]] || { usage; die "no archive given"; }
[[ -f "$ARCHIVE" ]] || die "archive not found: $ARCHIVE"

load_config
require_docker
service_running "$SVC_POSTGRES" || die "the '$SVC_POSTGRES' container is not running — start the stack first (make up)."

# --- extract -----------------------------------------------------------------
WORK="$(mktemp -d "${TMPDIR:-/tmp}/pacod-restore.XXXXXX")"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT
log "Extracting $ARCHIVE"
tar xzf "$ARCHIVE" -C "$WORK"
# The archive contains a single top-level pacod-backup-<ts>/ directory.
SRC="$(find "$WORK" -maxdepth 1 -type d -name 'pacod-backup-*' | head -n1)"
[[ -n "$SRC" ]] || die "archive layout unexpected — no pacod-backup-* directory inside"
[[ -f "$SRC/MANIFEST.txt" ]] && { log "Manifest:"; sed 's/^/    /' "$SRC/MANIFEST.txt" >&2 || true; }

# --- confirm -----------------------------------------------------------------
if [[ "$ASSUME_YES" != true ]]; then
  warn "This will OVERWRITE the running stack's databases and Qdrant collections."
  [[ "$OVERWRITE_CONFIG" == true ]] && warn "It will also OVERWRITE live config/secrets (.env, …)."
  printf 'Type "yes" to proceed: ' >&2
  read -r reply
  [[ "$reply" == "yes" ]] || die "aborted"
fi

# --- 1. PostgreSQL -----------------------------------------------------------
# Manual: cat globals.sql | docker compose exec -T pacod-postgres psql -U <user> -d postgres
#         docker compose exec -T pacod-postgres pg_restore --clean --if-exists -U <user> -d <db> < <db>.dump
if [[ "$DO_POSTGRES" == true ]]; then
  pg() { compose_exec -e PGPASSWORD="$POSTGRES_PASSWORD" "$SVC_POSTGRES" "$@"; }
  if [[ -f "$SRC/postgres/globals.sql" ]]; then
    log "PostgreSQL: restoring globals (roles)"
    # ON_ERROR_STOP off — roles like the bootstrap user already exist; that's fine.
    pg psql -U "$POSTGRES_USER" -d postgres < "$SRC/postgres/globals.sql" >/dev/null 2>&1 \
      || warn "  some global statements failed (pre-existing roles are expected)"
  fi
  for db in "$POSTGRES_DB" "$POSTGRES_ID_DB"; do
    dump="$SRC/postgres/$db.dump"
    [[ -f "$dump" ]] || { warn "  no dump for '$db' in archive — skipping"; continue; }
    # pacod-id's tables must stay owned by its own role (it is no superuser): restore as it.
    # The database itself is made by the pacod-db-init service at every start of the stack.
    role=()
    [[ "$db" == "$POSTGRES_ID_DB" ]] && role=(--role=pacod_id)
    log "PostgreSQL: pg_restore --clean $db"
    if pg pg_restore --clean --if-exists --no-owner ${role[@]+"${role[@]}"} -U "$POSTGRES_USER" -d "$db" < "$dump"; then
      ok "  restored $db"
    else
      warn "  pg_restore of '$db' reported errors (often harmless --clean drop-if-not-exists noise)"
    fi
  done
  # Dumps of databases this platform no longer has (Open-WebUI's, in an older archive).
  for dump in "$SRC"/postgres/*.dump; do
    db="$(basename "$dump" .dump)"
    [[ -f "$dump" && "$db" != "$POSTGRES_DB" && "$db" != "$POSTGRES_ID_DB" ]] || continue
    log "  $db.dump: not a database of this platform (Open-WebUI was retired) — not restored"
  done
else
  log "PostgreSQL: skipped (--skip-postgres)"
fi

# --- 2. Qdrant ---------------------------------------------------------------
# Manual: curl -X POST "$QDRANT_URL/collections/<c>/snapshots/upload?priority=snapshot" \
#              -F "snapshot=@<c>.snapshot"
if [[ "$DO_QDRANT" == true ]]; then
  if curl -fsS "$QDRANT_URL/collections" >/dev/null 2>&1; then
    shopt -s nullglob
    snaps=("$SRC"/qdrant/*.snapshot)
    if [[ ${#snaps[@]} -eq 0 ]]; then
      warn "Qdrant: no snapshots in archive — skipping"
    else
      for snap in "${snaps[@]}"; do
        col="$(basename "$snap" .snapshot)"
        log "Qdrant: recovering collection '$col'"
        # upload+recover in one call; ?priority=snapshot makes the snapshot authoritative.
        curl -fsS -X POST "$QDRANT_URL/collections/$col/snapshots/upload?priority=snapshot" \
          -H 'Content-Type: multipart/form-data' \
          -F "snapshot=@$snap" >/dev/null \
          && ok "  recovered $col" \
          || warn "  failed to recover '$col'"
      done
    fi
    shopt -u nullglob
  else
    warn "Qdrant: not reachable at $QDRANT_URL — skipping"
  fi
else
  log "Qdrant: skipped (--skip-qdrant)"
fi

# --- 3. Config ---------------------------------------------------------------
CFG_TAR="$SRC/config/config-files.tar.gz"
if [[ -f "$CFG_TAR" ]]; then
  if [[ "$OVERWRITE_CONFIG" == true ]]; then
    warn "Config: overwriting live files under $REPO_ROOT"
    tar xzf "$CFG_TAR" -C "$REPO_ROOT"
    ok "  config restored in place"
  else
    dest="${ARCHIVE%.tar.gz}.config"
    mkdir -p "$dest"
    tar xzf "$CFG_TAR" -C "$dest"
    log "Config: extracted to $dest (NOT applied)."
    log "        Review, then copy what you need — or re-run with --overwrite-config."
  fi
else
  warn "Config: no config archive found"
fi

# --- 4. Volumes --------------------------------------------------------------
# Manual: cat documents_data.tar.gz | docker compose exec -T ap-api tar xzf - -C /srv/documents
if [[ "$DO_VOLUMES" == true ]]; then
  restore_volume() { # <service> <mount> <tar> <label>
    local svc="$1" mount="$2" tar="$3" label="$4"
    [[ -f "$tar" ]] || { log "  $label: not in archive — skipping"; return 0; }
    if ! service_running "$svc"; then warn "  $label: service '$svc' not running — skipping"; return 0; fi
    log "  $label → $svc:$mount"
    if compose_exec "$svc" tar xzf - -C "$mount" < "$tar"; then ok "  $label restored"
    else warn "  $label restore failed"; fi
  }
  log "Volumes: restoring documents"
  restore_volume "$SVC_DOCUMENTS" "$DOCUMENTS_MOUNT" "$SRC/volumes/documents_data.tar.gz" "documents_data"
  if [[ -f "$SRC/volumes/openwebui_data.tar.gz" ]]; then
    log "  openwebui_data: Open-WebUI was retired — not restored (the file stays in the archive)"
  fi
else
  log "Volumes: skipped (--skip-volumes)"
fi

# --- 5. Redis (only if present) ---------------------------------------------
if [[ "$DO_REDIS" == true && -f "$SRC/redis/redis_data.tar.gz" ]]; then
  if service_running "$SVC_REDIS"; then
    log "Redis: restoring /data + restarting"
    if compose_exec "$SVC_REDIS" tar xzf - -C "$REDIS_MOUNT" < "$SRC/redis/redis_data.tar.gz"; then
      compose restart "$SVC_REDIS" >/dev/null 2>&1 || warn "  redis restart failed — restart it manually"
      ok "  Redis restored"
    else
      warn "  Redis restore failed"
    fi
  else
    warn "Redis: service not running — skipping"
  fi
fi

ok "Restore complete."
log "Reminder: after restoring .env.gateway or deploy/secrets, recreate the services that read them:"
log "  make -C pacod-api recreate-gateway-clients   (the local stack: recreate-gateway-clients-local)"
log "  docker restart pacod-id   (it reads deploy/secrets/id.env and its configuration at start)"
log "The gateway's own data (its PostgreSQL/ClickHouse) is restored by pacod-gw's restore, in GW_DIR."
