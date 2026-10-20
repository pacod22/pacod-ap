#!/bin/sh
# Render prometheus.yml from the template by substituting __*_TARGET__ tokens
# from the environment, render the scrape fragments (platform/prometheus/*.yml,
# mounted at /etc/prometheus/scrape.d) into /tmp/scrape.d, then exec Prometheus.
# Runs inside the prom/prometheus image (busybox sh + sed), so no host
# envsubst/gettext is required — the scrape targets stay fully parameterised
# (same-instance defaults = Docker DNS on pacod-net; separate-instance =
# override the *_TARGET env vars, see .env).
set -eu

# Defaults mirror .env.example (same-instance Docker DNS on pacod-net).
# The platform's jobs (prometheus.yml.tpl):
: "${CADDY_TARGET:=pacod-caddy:9180}"
: "${QDRANT_TARGET:=pacod-qdrant:6333}"
: "${GW_CORE_TARGET:=pacod-gw:9100}"
: "${PACOD_ID_TARGET:=pacod-id:9170}"
: "${POSTGRES_EXPORTER_TARGET:=pacod-postgres-exporter:9187}"
: "${NODE_EXPORTER_TARGET:=pacod-node-exporter:9100}"
: "${CADVISOR_TARGET:=pacod-cadvisor:8080}"
# pacod-ap's jobs (platform/prometheus/ap.yml). The fragment names the default targets
# itself, because the full stack reads it as it is; each is replaced by its variable.
: "${API_TARGET:=ap-api:8000}"
: "${ADMIN_API_TARGET:=ap-admin-api:8000}"
: "${MCP_TARGET:=ap-mcp:8000}"
: "${EMBEDDINGS_TARGET:=pacod-embeddings:8080}"
: "${INGESTION_TARGET:=ap-ingestion:8000}"
: "${FRONTEND_TARGET:=ap-frontend:3000}"
: "${PGBOUNCER_EXPORTER_TARGET:=ap-pgbouncer-exporter:9127}"
: "${REDIS_EXPORTER_TARGET:=ap-redis-exporter:9121}"

TPL=/etc/prometheus/prometheus.yml.tpl
OUT=/tmp/prometheus.yml

sed \
  -e "s|__CADDY_TARGET__|${CADDY_TARGET}|g" \
  -e "s|__QDRANT_TARGET__|${QDRANT_TARGET}|g" \
  -e "s|__GW_CORE_TARGET__|${GW_CORE_TARGET}|g" \
  -e "s|__PACOD_ID_TARGET__|${PACOD_ID_TARGET}|g" \
  -e "s|__POSTGRES_EXPORTER_TARGET__|${POSTGRES_EXPORTER_TARGET}|g" \
  -e "s|__NODE_EXPORTER_TARGET__|${NODE_EXPORTER_TARGET}|g" \
  -e "s|__CADVISOR_TARGET__|${CADVISOR_TARGET}|g" \
  "$TPL" > "$OUT"

# The fragments: a quoted default target ("ap-api:8000") becomes its variable's value.
mkdir -p /tmp/scrape.d
rm -f /tmp/scrape.d/*.yml
for f in /etc/prometheus/scrape.d/*.yml; do
  [ -e "$f" ] || continue
  sed \
    -e "s|\"ap-api:8000\"|\"${API_TARGET}\"|g" \
    -e "s|\"ap-admin-api:8000\"|\"${ADMIN_API_TARGET}\"|g" \
    -e "s|\"ap-mcp:8000\"|\"${MCP_TARGET}\"|g" \
    -e "s|\"pacod-embeddings:8080\"|\"${EMBEDDINGS_TARGET}\"|g" \
    -e "s|\"ap-ingestion:8000\"|\"${INGESTION_TARGET}\"|g" \
    -e "s|\"ap-frontend:3000\"|\"${FRONTEND_TARGET}\"|g" \
    -e "s|\"ap-pgbouncer-exporter:9127\"|\"${PGBOUNCER_EXPORTER_TARGET}\"|g" \
    -e "s|\"ap-redis-exporter:9121\"|\"${REDIS_EXPORTER_TARGET}\"|g" \
    "$f" > "/tmp/scrape.d/$(basename "$f")"
done

exec /bin/prometheus \
  --config.file="$OUT" \
  --storage.tsdb.path=/prometheus \
  --web.console.libraries=/usr/share/prometheus/console_libraries \
  --web.console.templates=/usr/share/prometheus/consoles \
  --web.enable-lifecycle
