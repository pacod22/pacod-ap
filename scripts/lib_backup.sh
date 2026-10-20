#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# lib_backup.sh — shared helpers sourced by backup.sh and restore.sh.
#
# Not meant to be run directly. Provides logging, config loading (env vars +
# fallback to the app's pacod-api/.env), Compose wrappers, and small utilities.
# ---------------------------------------------------------------------------

# --- logging -----------------------------------------------------------------
_c_reset=$'\033[0m'; _c_blue=$'\033[34m'; _c_yellow=$'\033[33m'; _c_red=$'\033[31m'; _c_green=$'\033[32m'
# Disable colour when not writing to a terminal.
if [[ ! -t 2 ]]; then _c_reset=; _c_blue=; _c_yellow=; _c_red=; _c_green=; fi

# Case-insensitive truthiness (bash 3.2-safe — no ${var,,}).
is_true() { case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in true|1|yes|on) return 0 ;; *) return 1 ;; esac; }

log()  { printf '%s[backup]%s %s\n'  "$_c_blue"   "$_c_reset" "$*" >&2; }
ok()   { printf '%s[ok]%s %s\n'      "$_c_green"  "$_c_reset" "$*" >&2; }
warn() { printf '%s[warn]%s %s\n'    "$_c_yellow" "$_c_reset" "$*" >&2; }
die()  { printf '%s[error]%s %s\n'   "$_c_red"    "$_c_reset" "$*" >&2; exit 1; }

# --- repo root ---------------------------------------------------------------
# This file lives in <repo>/scripts/. Resolve the repo root from it.
_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$_lib_dir/.." && pwd)"

# --- read a single KEY=value from an env file (no arbitrary code execution) ---
# Strips surrounding quotes and trailing inline comments. Returns empty if absent.
read_env_var() {
  local key="$1" file="$2" line val
  [[ -f "$file" ]] || return 0
  # Last matching assignment wins; ignore commented lines.
  line="$(grep -E "^[[:space:]]*${key}=" "$file" | grep -vE '^[[:space:]]*#' | tail -n1)" || true
  [[ -n "$line" ]] || return 0
  val="${line#*=}"
  # Strip a trailing inline comment only when the value is not quoted.
  if [[ "$val" != \"* && "$val" != \'* ]]; then
    val="${val%%#*}"
  fi
  # Trim surrounding whitespace.
  val="${val#"${val%%[![:space:]]*}"}"
  val="${val%"${val##*[![:space:]]}"}"
  # Strip a single pair of surrounding quotes.
  if [[ "$val" == \"*\" ]]; then val="${val#\"}"; val="${val%\"}"; fi
  if [[ "$val" == \'*\' ]]; then val="${val#\'}"; val="${val%\'}"; fi
  printf '%s' "$val"
}

# --- load configuration ------------------------------------------------------
# Order of precedence: real environment > --env-file (BACKUP_ENV_FILE) > app .env
# defaults > hardcoded defaults.
load_config() {
  # Optional dedicated backup env file (KEY=value); default scripts/backup.env.
  BACKUP_ENV_FILE="${BACKUP_ENV_FILE:-$REPO_ROOT/scripts/backup.env}"
  if [[ -f "$BACKUP_ENV_FILE" ]]; then
    log "Loading backup config from $BACKUP_ENV_FILE"
    set -a; # shellcheck disable=SC1090
    source "$BACKUP_ENV_FILE"; set +a
  fi

  # The app env file we mine DB creds from (never sourced — parsed key by key).
  ENV_FILE="${ENV_FILE:-$REPO_ROOT/pacod-api/.env}"

  COMPOSE_FILE="${COMPOSE_FILE:-$REPO_ROOT/pacod-api/docker-compose.yml}"
  # The shared components (pacod-postgres, pacod-qdrant, pacod-id, …) are in the standalone
  # file next to the app file; a server's docker-compose.override.yml is loaded when present.
  COMPOSE_STANDALONE_FILE="${COMPOSE_STANDALONE_FILE:-$(dirname "$COMPOSE_FILE")/docker-compose.standalone.yml}"
  COMPOSE_OVERRIDE_FILE="${COMPOSE_OVERRIDE_FILE:-$(dirname "$COMPOSE_FILE")/docker-compose.override.yml}"
  COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-pacod}"
  export COMPOSE_PROJECT_NAME

  BACKUP_DIR="${BACKUP_DIR:-$REPO_ROOT/backups}"

  # DB creds: env wins, else pulled from the app .env, else conventional defaults.
  POSTGRES_USER="${POSTGRES_USER:-$(read_env_var POSTGRES_USER "$ENV_FILE")}"
  POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(read_env_var POSTGRES_PASSWORD "$ENV_FILE")}"
  POSTGRES_DB="${POSTGRES_DB:-$(read_env_var POSTGRES_DB "$ENV_FILE")}"
  POSTGRES_USER="${POSTGRES_USER:-pacod}"
  POSTGRES_DB="${POSTGRES_DB:-pacod}"
  # pacod-id's database: owned by its own role, created by the pacod-db-init service.
  POSTGRES_ID_DB="${POSTGRES_ID_DB:-pacod_id}"

  QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
  QDRANT_URL="${QDRANT_URL%/}" # drop any trailing slash

  BACKUP_INCLUDE_REDIS="${BACKUP_INCLUDE_REDIS:-false}"
  BACKUP_INCLUDE_DOCUMENTS="${BACKUP_INCLUDE_DOCUMENTS:-true}"
  BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-0}"

  # Compose service names (rarely need overriding).
  SVC_POSTGRES="${SVC_POSTGRES:-pacod-postgres}"
  SVC_REDIS="${SVC_REDIS:-ap-redis}"
  SVC_DOCUMENTS="${SVC_DOCUMENTS:-ap-api}"       # container that mounts ap-documents-data
  DOCUMENTS_MOUNT="${DOCUMENTS_MOUNT:-/srv/documents}"
  REDIS_MOUNT="${REDIS_MOUNT:-/data}"
}

# --- docker compose wrapper --------------------------------------------------
compose() {
  local files=(-f "$COMPOSE_FILE")
  [[ -f "$COMPOSE_STANDALONE_FILE" ]] && files+=(-f "$COMPOSE_STANDALONE_FILE")
  [[ -f "$COMPOSE_OVERRIDE_FILE" ]] && files+=(-f "$COMPOSE_OVERRIDE_FILE")
  docker compose "${files[@]}" "$@"
}

# Run a command in a running service container (no TTY, so it is pipe-safe).
compose_exec() { compose exec -T "$@"; }

# True if the named compose service has a running container.
service_running() {
  local svc="$1" cid
  cid="$(compose ps -q "$svc" 2>/dev/null)" || return 1
  [[ -n "$cid" ]] || return 1
  [[ "$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null)" == "true" ]]
}

# --- shared preflight --------------------------------------------------------
require_docker() {
  command -v docker >/dev/null 2>&1 || die "docker not found on PATH"
  docker compose version >/dev/null 2>&1 || die "'docker compose' not available"
  [[ -f "$COMPOSE_FILE" ]] || die "compose file not found: $COMPOSE_FILE"
  docker info >/dev/null 2>&1 || die "cannot talk to the Docker daemon (is it running?)"
}

# Parse Qdrant collection names from GET /collections without needing jq.
qdrant_collections() {
  curl -fsS "$QDRANT_URL/collections" \
    | tr ',' '\n' \
    | grep -oE '"name"[[:space:]]*:[[:space:]]*"[^"]+"' \
    | sed -E 's/.*"name"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/'
}
