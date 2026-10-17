#!/usr/bin/env bash
# Vendors pacod-id's client libraries into this repository, from a pacod-id source tree:
#
#   pacod-api/vendor/wheels/pacod_id-<version>-py3-none-any.whl   Python: rules engine, token
#                                                                 checks, client, FastAPI helpers
#   pacod-ui/frontend/vendor/pacod-*-<version>.tgz                npm: authz, ui, client, sveltekit
#   pacod-api/vendor/PACOD_ID_VERSION                             the source's VERSION (its commit)
#
# The pacod-id image itself is built from the same tree by `make id-image`, so the service and
# the libraries always come from one commit.
#
#   scripts/vendor-pacod-id.sh [<pacod-id source>] [<pacod-gw checkout>]
#
# Default source: the copy pacod-gw vendors (../pacod-gw/vendor/pacod-id), pinned by its
# VERSION file. That tree is never written to: it is copied to a temporary directory first, and
# since it is not a git repository (pacod-gw's vendored copy) the copy is an rsync, not git
# archive. The wheel is installed in the images with `pip install --no-deps --no-index` from its
# file, never resolved through an index, so a package of the same name on PyPI cannot replace it.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GW="${2:-$(dirname "$ROOT")/pacod-gw}"
SRC="${1:-$GW/vendor/pacod-id}"
# The npm packages the frontend uses (the same four as the gateway console).
PACKAGES=(authz ui client sveltekit)

[ -f "$SRC/VERSION" ] || { echo "vendor-pacod-id: $SRC has no VERSION file; is it a pacod-id tree?" >&2; exit 1; }
for tool in rsync uv npm node; do
  command -v "$tool" >/dev/null || { echo "vendor-pacod-id: $tool is needed" >&2; exit 1; }
done

BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
rsync -a --exclude node_modules --exclude target --exclude .venv --exclude dist "$SRC/" "$BUILD/src/"

# Python: one pure wheel.
uv build --quiet --wheel --out-dir "$BUILD/dist" "$BUILD/src/python"
WHEELS="$ROOT/pacod-api/vendor/wheels"
mkdir -p "$WHEELS"
rm -f "$WHEELS"/pacod_id-*.whl
cp "$BUILD"/dist/pacod_id-*.whl "$WHEELS/"

# npm: pacod-gw's ui/vendor holds the four packages, packed from the same commit by its own
# vendoring script; they are reused when that commit is this one, else built and packed here.
NPM_DEST="$ROOT/pacod-ui/frontend/vendor"
mkdir -p "$NPM_DEST"
rm -f "$NPM_DEST"/pacod-*.tgz
if cmp -s "$GW/vendor/pacod-id/VERSION" "$SRC/VERSION" && [ "$(ls "$GW"/ui/vendor/pacod-*.tgz 2>/dev/null | wc -l)" -eq "${#PACKAGES[@]}" ]; then
  cp "$GW"/ui/vendor/pacod-*.tgz "$NPM_DEST/"
else
  (
    cd "$BUILD/src"
    npm ci --no-audit --no-fund >/dev/null
    for p in "${PACKAGES[@]}"; do npm run build -w "packages/$p" >/dev/null; done
  )
  for p in "${PACKAGES[@]}"; do
    (cd "$BUILD/src/packages/$p" && npm pack --pack-destination "$NPM_DEST" >/dev/null)
  done
fi
# package-lock.json records each tarball's integrity, and npm neither updates it nor notices a
# changed file: with the old value it installs the old package from its cache. Record the new ones.
(
  cd "$ROOT/pacod-ui/frontend"
  node -e '
    const fs = require("fs"), crypto = require("crypto");
    const lock = JSON.parse(fs.readFileSync("package-lock.json", "utf8"));
    for (const e of Object.values(lock.packages)) {
      if (!e.resolved?.startsWith("file:vendor/")) continue;
      const sum = crypto.createHash("sha512").update(fs.readFileSync(e.resolved.slice(5)));
      e.integrity = "sha512-" + sum.digest("base64");
    }
    fs.writeFileSync("package-lock.json", JSON.stringify(lock, null, "\t") + "\n");
  '
)

cp "$SRC/VERSION" "$ROOT/pacod-api/vendor/PACOD_ID_VERSION"

echo "vendored pacod-id $(awk '/^commit/ {print $2}' "$SRC/VERSION")"
ls "$WHEELS" "$NPM_DEST"
