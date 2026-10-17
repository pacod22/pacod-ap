#!/usr/bin/env bash
# Set one NAME=value line in an env file without the value ever reaching the
# terminal, the shell history or a process list.
#
#   scripts/secret-set.sh <env-file> <NAME>
#
# The value is read from stdin: typed (hidden) when stdin is a terminal, or piped
# (`some-command | scripts/secret-set.sh …`). An existing line for NAME is
# replaced; every other line is kept. The file is created, and kept, mode 0600.
# Only the name is printed.
set -euo pipefail

file=${1:?usage: secret-set.sh <env-file> <NAME>}
name=${2:?usage: secret-set.sh <env-file> <NAME>}

if [[ ! $name =~ ^[A-Z][A-Z0-9_]*$ ]]; then
	echo "secret-set: '$name' is not a valid variable name" >&2
	exit 2
fi

if [ -t 0 ]; then
	read -r -s -p "$name: " value
	echo >&2
else
	IFS= read -r value || true
fi

if [ -z "$value" ]; then
	echo "secret-set: empty value for $name, nothing written" >&2
	exit 1
fi

umask 077
mkdir -p "$(dirname "$file")"
touch "$file"
chmod 600 "$file"

tmp=$(mktemp "${file}.XXXXXX")
grep -v "^${name}=" "$file" >"$tmp" || true
printf '%s=%s\n' "$name" "$value" >>"$tmp"
mv "$tmp" "$file"
chmod 600 "$file"

echo "$name set in $file"
