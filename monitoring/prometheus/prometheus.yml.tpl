# Prometheus config TEMPLATE. Rendered at container start by entrypoint.sh, which
# substitutes the __*_TARGET__ tokens from environment variables (see .env).
# Do not edit the rendered file — edit this template.
#
# This file holds the platform's jobs: pacod-ap's monitoring module is the shared monitoring
# of pacod-ap's own stacks (pacod-demo/docs/NETWORK.md). pacod-ap's own jobs (api,
# admin-api, mcp-server, embeddings, ingestion, frontend, pgbouncer, redis) are the fragment
# platform/prometheus/ap.yml, which the full stack loads as well; entrypoint.sh copies it
# under /tmp/scrape.d with the *_TARGET overrides applied.

global:
  scrape_interval: 15s
  evaluation_interval: 15s
  external_labels:
    monitor: pacod

rule_files:
  - /etc/prometheus/alerts/*.rules.yml

alerting:
  alertmanagers:
    - static_configs:
        - targets: ["pacod-alertmanager:9093"]

# Every product's jobs (platform/prometheus/*.yml, rendered by entrypoint.sh).
scrape_config_files:
  - /tmp/scrape.d/*.yml

scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]

  # ---- Edge & web layer ----
  # Caddy's built-in Prometheus metrics (all edge traffic, every vhost) on its
  # internal :9180 listener; enabled via `servers { metrics }` in the Caddyfile.
  - job_name: caddy
    static_configs:
      - targets: ["__CADDY_TARGET__"]

  # ---- Native /metrics ----
  - job_name: qdrant
    static_configs:
      - targets: ["__QDRANT_TARGET__"]

  # ---- The LLM gateway (pacod-gw, on pacod-net) ----
  # Every LLM call's tokens, cost, errors and latency are the gateway's series
  # (gw_*), as its own Prometheus scrapes them: "instance" is the core's
  # PGW_INSTANCE_ID ("core"), which the gateway's rules and dashboards group by.
  - job_name: pacod-gw-core
    honor_labels: true
    static_configs:
      - targets: ["__GW_CORE_TARGET__"]
        labels:
          instance: core

  # The platform's identity provider (pacod-id, on pacod-net).
  - job_name: pacod-id
    static_configs:
      - targets: ["__PACOD_ID_TARGET__"]

  # ---- Infrastructure exporters ----
  - job_name: postgres
    static_configs:
      - targets: ["__POSTGRES_EXPORTER_TARGET__"]

  - job_name: node
    static_configs:
      - targets: ["__NODE_EXPORTER_TARGET__"]

  - job_name: cadvisor
    static_configs:
      - targets: ["__CADVISOR_TARGET__"]
