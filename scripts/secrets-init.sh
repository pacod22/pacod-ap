#!/usr/bin/env bash
# Generate the platform's service secrets: pacod-id's and the gateway's.
#
#   scripts/secrets-init.sh            (make secrets-init)
#
# Writes, mode 0600 in a 0700 directory (all gitignored):
#
#   deploy/secrets/id.env         pacod-id: KEK, token pepper, database password, the client
#                                 secrets, the bootstrap admin
#   deploy/secrets/gw.env         pacod-gw (the --env-file of its compose): its database role's
#                                 password (GW_DB_PASSWORD, also read by pacod-ap's
#                                 pacod-db-init, which makes the role in `make up-all`), its
#                                 own PostgreSQL's and ClickHouse's passwords, KEK, pepper,
#                                 internal token, console session secret, and the client
#                                 secrets it shares with pacod-id
#   deploy/secrets/providers.env  LLM provider API keys, read only by scripts/gw_bootstrap.py;
#                                 created empty — fill it with `make secret-set FILE=… NAME=…`
#
# Idempotent: a name that already has a value keeps it; only missing (or empty) names are
# generated. The client secrets that both sides need are generated once, in id.env, and copied
# into gw.env, so the two never disagree. Prints names only, never a value.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$ROOT/deploy/secrets"
ID="$DIR/id.env"
GW="$DIR/gw.env"
PROVIDERS="$DIR/providers.env"

command -v openssl >/dev/null || { echo "secrets-init: openssl is needed" >&2; exit 1; }

umask 077
mkdir -p "$DIR"
chmod 700 "$DIR"
for f in "$ID" "$GW" "$PROVIDERS"; do
	touch "$f"
	chmod 600 "$f"
done

hex() { openssl rand -hex "$1"; }
b64() { openssl rand -base64 "$1" | tr -d '\n'; }
# A password of letters and digits only: safe in URLs and on any command line.
alnum() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"; }

has() { grep -q "^$2=." "$1"; }
get() { grep "^$2=" "$1" | tail -1 | cut -d= -f2-; }
put() { # file name value: replace or append the line
	local tmp
	tmp=$(mktemp "$1.XXXXXX")
	grep -v "^$2=" "$1" >"$tmp" || true
	printf '%s=%s\n' "$2" "$3" >>"$tmp"
	mv "$tmp" "$1"
	chmod 600 "$1"
}
added=()
ensure() { # file name generator...
	local file=$1 name=$2
	shift 2
	if ! has "$file" "$name"; then
		put "$file" "$name" "$("$@")"
		added+=("$(basename "$file"):$name")
	fi
}

# --- pacod-id -----------------------------------------------------------------------------
ensure "$ID" PID_DB_PASSWORD hex 24
# Derived, kept in step with the password: compose cannot build it from id.env (interpolation
# reads only --env-file). The host is the platform's PostgreSQL (pacod-postgres), never
# PgBouncer; an older file's address (host "postgres") is rewritten here.
url="postgres://pacod_id:$(get "$ID" PID_DB_PASSWORD)@pacod-postgres:5432/pacod_id"
if [ "$(get "$ID" PID_DATABASE_URL)" != "$url" ]; then
	put "$ID" PID_DATABASE_URL "$url"
	added+=("id.env:PID_DATABASE_URL")
fi
ensure "$ID" PID_KEK b64 32
ensure "$ID" PID_KEK_ID echo k1
ensure "$ID" PID_TOKEN_PEPPER hex 32
# The first admin, created on pacod-id's first start (an empty database) and never again. Set
# your own address before that start: make secret-set FILE=deploy/secrets/id.env NAME=PID_BOOTSTRAP_ADMIN_EMAIL
ensure "$ID" PID_BOOTSTRAP_ADMIN_EMAIL echo admin@example.com
ensure "$ID" PID_BOOTSTRAP_ADMIN_PASSWORD alnum 24
ensure "$ID" PACOD_WEB_CLIENT_SECRET hex 32
ensure "$ID" PACOD_API_CLIENT_SECRET hex 32
# Shared with the gateway (copied into gw.env below).
SHARED=(PGW_ID_CLIENT_SECRET PGW_UI_CLIENT_SECRET TESTER_ID_CLIENT_SECRET)
for name in "${SHARED[@]}"; do
	ensure "$ID" "$name" hex 32
done

# --- pacod-gw -----------------------------------------------------------------------------
# The role pacod_gw on pacod-postgres (pacod-gw's platform/postgres/gw-db.sh, run by
# pacod-ap's pacod-db-init) and pacod-gw's DATABASE_URL. Hex: it is part of a URL.
ensure "$GW" GW_DB_PASSWORD hex 24
# The superuser of pacod-gw's own PostgreSQL, for its standalone stack and its preflight.
ensure "$GW" POSTGRES_PASSWORD hex 24
ensure "$GW" CLICKHOUSE_PASSWORD hex 24
ensure "$GW" PGW_KEK b64 32
ensure "$GW" PGW_KEK_ID echo k1
ensure "$GW" PGW_KEY_PEPPER b64 32
ensure "$GW" PGW_INTERNAL_TOKEN hex 32
ensure "$GW" PGW_UI_SESSION_SECRET hex 32
ensure "$GW" GRAFANA_ADMIN_PASSWORD alnum 24
synced=()
for name in "${SHARED[@]}"; do
	value=$(get "$ID" "$name")
	if [ "$(get "$GW" "$name" 2>/dev/null || true)" != "$value" ]; then
		put "$GW" "$name" "$value"
		synced+=("$name")
	fi
done

# --- the app's compose interpolation (pacod-api/.env.gateway) ----------------------------
# Its two pacod-id clients' secrets, the same as id.env's: the API's (pacod-api) and the
# frontend's (pacod-web). Compose hands each to its own service by interpolation; they are
# kept out of pacod-api/.env, which is the env_file of every backend container (workers,
# MCP, ingestion), none of which needs them. .env.gateway is interpolation-only.
APP_INTERP="$ROOT/pacod-api/.env.gateway"
[ -f "$APP_INTERP" ] || { : >"$APP_INTERP"; chmod 600 "$APP_INTERP"; }
for name in PACOD_API_CLIENT_SECRET PACOD_WEB_CLIENT_SECRET; do
	value=$(get "$ID" "$name")
	if [ "$(get "$APP_INTERP" "$name" 2>/dev/null || true)" != "$value" ]; then
		put "$APP_INTERP" "$name" "$value"
		synced+=("${name} (.env.gateway)")
	fi
done
# Copies an earlier version put into pacod-api/.env go.
APP_ENV="$ROOT/pacod-api/.env"
if [ -f "$APP_ENV" ]; then
	for name in PACOD_API_CLIENT_SECRET PACOD_WEB_CLIENT_SECRET; do
		if grep -q "^$name=" "$APP_ENV"; then
			tmp=$(mktemp "$APP_ENV.XXXXXX")
			grep -v "^$name=" "$APP_ENV" >"$tmp" || true
			mv "$tmp" "$APP_ENV"
			chmod 600 "$APP_ENV"
			synced+=("${name} (removed from .env)")
		fi
	done
fi

# --- providers ----------------------------------------------------------------------------
if [ ! -s "$PROVIDERS" ]; then
	cat >"$PROVIDERS" <<'EOF'
# LLM provider API keys, imported into the gateway by `make gw-bootstrap` (scripts/gw_bootstrap.py).
# Set each with: make secret-set FILE=deploy/secrets/providers.env NAME=<NAME>
# Names read: OPENAI_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY DEEPSEEK_API_KEY GLM_API_KEY
EOF
	chmod 600 "$PROVIDERS"
fi

if [ ${#added[@]} -eq 0 ] && [ ${#synced[@]} -eq 0 ]; then
	echo "secrets-init: nothing to do, every secret is set (deploy/secrets/)"
else
	[ ${#added[@]} -gt 0 ] && echo "secrets-init: generated ${added[*]}"
	[ ${#synced[@]} -gt 0 ] && echo "secrets-init: copied from id.env: ${synced[*]}"
fi
echo "secrets-init: the bootstrap admin's address and password are PID_BOOTSTRAP_ADMIN_* in deploy/secrets/id.env"
