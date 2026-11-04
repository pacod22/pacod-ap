#!/usr/bin/env bash
# Vendors pacod-broker's Python client into this repository, from a pacod-broker checkout:
#
#   pacod-api/vendor/wheels/pacod_broker_client-<version>-py3-none-any.whl
#   pacod-api/vendor/PACOD_BROKER_VERSION     the checkout's commit
#
#   scripts/vendor-pacod-broker.sh [<pacod-broker checkout>]
#
# Default source: ../pacod-broker. The checkout is never written to: its client is copied to a
# temporary directory and built there. As with pacod-id's wheel, the images install it with
# `pip install --no-deps --no-index` from its file, so a package of the same name on PyPI can
# never replace it; its dependencies (httpx, PyYAML) are already in requirements-base.txt.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
SRC="${1:-$(dirname "$ROOT")/pacod-broker}"

[ -f "$SRC/clients/python/pyproject.toml" ] || {
  echo "vendor-pacod-broker: $SRC has no clients/python; is it a pacod-broker checkout?" >&2
  exit 1
}
command -v uv >/dev/null || { echo "vendor-pacod-broker: uv is needed" >&2; exit 1; }

BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
cp -R "$SRC/clients/python" "$BUILD/src"
rm -rf "$BUILD/src/.venv" "$BUILD/src/dist"
uv build --quiet --wheel --out-dir "$BUILD/dist" "$BUILD/src"

WHEELS="$ROOT/pacod-api/vendor/wheels"
mkdir -p "$WHEELS"
rm -f "$WHEELS"/pacod_broker_client-*.whl
cp "$BUILD"/dist/pacod_broker_client-*.whl "$WHEELS/"
git -C "$SRC" rev-parse HEAD > "$ROOT/pacod-api/vendor/PACOD_BROKER_VERSION"
echo "vendor-pacod-broker: $(basename "$WHEELS"/pacod_broker_client-*.whl) from $(cat "$ROOT/pacod-api/vendor/PACOD_BROKER_VERSION")"
