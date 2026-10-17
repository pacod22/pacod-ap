#!/bin/sh
# Render prometheus.yml from the template by substituting __*_TARGET__ tokens
# from the environment, then exec Prometheus. Runs inside the prom/prometheus
# image (busybox sh + sed), so no host envsubst/gettext is required — the
# scrape targets stay fully parameterised (same-instance defaults = Docker DNS;
# separate-instance = override the *_TARGET env vars, see .env).
set -eu

# Defaults mirror .env.example (same-instance Docker DNS).
: "${API_TARGET:=api:8000}"
: "${ADMIN_API_TARGET:=admin-api:8000}"
: "${MCP_TARGET:=mcp-server:8000}"
: "${EMBEDDINGS_TARGET:=embeddings:8080}"
: "${INGESTION_TARGET:=ingestion:8000}"
: "${CADDY_TARGET:=caddy:9180}"
: "${FRONTEND_TARGET:=frontend:3000}"
: "${QDRANT_TARGET:=qdrant:6333}"
: "${GW_CORE_TARGET:=gw-core:9100}"
: "${PACOD_ID_TARGET:=pacod-id:9170}"
: "${POSTGRES_EXPORTER_TARGET:=postgres-exporter:9187}"
: "${PGBOUNCER_EXPORTER_TARGET:=pgbouncer-exporter:9127}"
: "${REDIS_EXPORTER_TARGET:=redis-exporter:9121}"
: "${NODE_EXPORTER_TARGET:=node-exporter:9100}"
: "${CADVISOR_TARGET:=cadvisor:8080}"

TPL=/etc/prometheus/prometheus.yml.tpl
OUT=/tmp/prometheus.yml

sed \
  -e "s|__API_TARGET__|${API_TARGET}|g" \
  -e "s|__ADMIN_API_TARGET__|${ADMIN_API_TARGET}|g" \
  -e "s|__MCP_TARGET__|${MCP_TARGET}|g" \
  -e "s|__EMBEDDINGS_TARGET__|${EMBEDDINGS_TARGET}|g" \
  -e "s|__INGESTION_TARGET__|${INGESTION_TARGET}|g" \
  -e "s|__CADDY_TARGET__|${CADDY_TARGET}|g" \
  -e "s|__FRONTEND_TARGET__|${FRONTEND_TARGET}|g" \
  -e "s|__QDRANT_TARGET__|${QDRANT_TARGET}|g" \
  -e "s|__GW_CORE_TARGET__|${GW_CORE_TARGET}|g" \
  -e "s|__PACOD_ID_TARGET__|${PACOD_ID_TARGET}|g" \
  -e "s|__POSTGRES_EXPORTER_TARGET__|${POSTGRES_EXPORTER_TARGET}|g" \
  -e "s|__PGBOUNCER_EXPORTER_TARGET__|${PGBOUNCER_EXPORTER_TARGET}|g" \
  -e "s|__REDIS_EXPORTER_TARGET__|${REDIS_EXPORTER_TARGET}|g" \
  -e "s|__NODE_EXPORTER_TARGET__|${NODE_EXPORTER_TARGET}|g" \
  -e "s|__CADVISOR_TARGET__|${CADVISOR_TARGET}|g" \
  "$TPL" > "$OUT"

exec /bin/prometheus \
  --config.file="$OUT" \
  --storage.tsdb.path=/prometheus \
  --web.console.libraries=/usr/share/prometheus/console_libraries \
  --web.console.templates=/usr/share/prometheus/consoles \
  --web.enable-lifecycle
