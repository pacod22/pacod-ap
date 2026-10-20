#!/usr/bin/env bash
# Fill the app's generated secrets into an env file (default pacod-api/.env).
#
#   scripts/env-init.sh [<env-file>]        (run by `make ensure-env`)
#
# The example files ship these blank, never with a value: a value in a committed example ends
# up on every installation made from it. Each one that is missing or blank here gets a fresh
# random value; one that is set keeps it. Prints names only, never a value.
#
# Not generated: anything that comes from outside (provider API keys, SMTP account, Google
# OAuth client); set those with `make secret-set NAME=…`.
set -euo pipefail

file=${1:-"$(cd "$(dirname "$0")/.." && pwd)/pacod-api/.env"}
[ -f "$file" ] || { echo "env-init: $file does not exist" >&2; exit 1; }
command -v openssl >/dev/null || { echo "env-init: openssl is needed" >&2; exit 1; }

hex() { openssl rand -hex "$1"; }
b64() { openssl rand -base64 "$1" | tr -d '\n'; }
# A Fernet key: URL-safe base64 of 32 bytes.
fernet() { openssl rand -base64 32 | tr -d '\n' | tr '+/' '-_'; }

# The value of NAME, with a trailing " # comment" and quotes removed.
value() { grep "^$1=" "$file" | tail -1 | cut -d= -f2- | sed 's/[[:space:]]#.*$//' | tr -d '"'"'"' '; }

umask 077
added=()
ensure() { # name generator...
	local name=$1 tmp
	shift
	[ -n "$(value "$name")" ] && return 0
	tmp=$(mktemp "$file.XXXXXX")
	if grep -q "^$name=" "$file"; then
		# Keep the line where it is (and its comment block above it).
		awk -v n="$name" -v v="$("$@")" 'BEGIN { p = n "=" } index($0, p) == 1 && !done { print p v; done = 1; next } { print }' "$file" >"$tmp"
	else
		cat "$file" >"$tmp"
		printf '%s=%s\n' "$name" "$("$@")" >>"$tmp"
	fi
	mv "$tmp" "$file"
	added+=("$name")
}

ensure POSTGRES_PASSWORD hex 24
ensure APP_SECRETS_KEY fernet
ensure SESSION_SECRET b64 32
ensure CRAWLER_INGEST_TOKEN b64 32
ensure MCP_SHARED_SECRET b64 32
chmod 600 "$file"

if [ ${#added[@]} -gt 0 ]; then
	echo "env-init: generated ${added[*]} in $file"
fi
