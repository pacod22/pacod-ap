# Prometheus config TEMPLATE. Rendered at container start by entrypoint.sh, which
# substitutes the __*_TARGET__ tokens from environment variables (see .env).
# Do not edit the rendered file — edit this template.

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
        - targets: ["alertmanager:9093"]

scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]

  # ---- Application services (FastAPI /metrics) ----
  # The app emits its own `service` label on every series, so we don't add a
  # static one here (that would only clutter series with `exported_service`).
  - job_name: api
    static_configs:
      - targets: ["__API_TARGET__"]

  - job_name: admin-api
    static_configs:
      - targets: ["__ADMIN_API_TARGET__"]

  - job_name: mcp-server
    static_configs:
      - targets: ["__MCP_TARGET__"]

  - job_name: embeddings
    static_configs:
      - targets: ["__EMBEDDINGS_TARGET__"]

  - job_name: ingestion
    static_configs:
      - targets: ["__INGESTION_TARGET__"]

  # ---- Edge & web layer ----
  # Caddy's built-in Prometheus metrics (all edge traffic, every vhost) on its
  # internal :9180 listener; enabled via `servers { metrics }` in the Caddyfile.
  - job_name: caddy
    static_configs:
      - targets: ["__CADDY_TARGET__"]

  # SvelteKit frontend — the registered-vs-anonymous request split + active-sessions
  # gauge (Caddy can't derive user type). Scraped at /internal/metrics.
  - job_name: frontend
    metrics_path: /internal/metrics
    static_configs:
      - targets: ["__FRONTEND_TARGET__"]

  # ---- Native /metrics ----
  - job_name: qdrant
    static_configs:
      - targets: ["__QDRANT_TARGET__"]

  # ---- The LLM gateway (pacod-gw, over pacod-link) ----
  # Every LLM call's tokens, cost, errors and latency are the gateway's series
  # (gw_*), as its own Prometheus scrapes them: "instance" is the core's
  # PGW_INSTANCE_ID ("core"), which the gateway's rules and dashboards group by.
  - job_name: pacod-gw-core
    honor_labels: true
    static_configs:
      - targets: ["__GW_CORE_TARGET__"]
        labels:
          instance: core

  # The platform's identity provider (pacod-id, on agent-net).
  - job_name: pacod-id
    static_configs:
      - targets: ["__PACOD_ID_TARGET__"]

  # ---- Infrastructure exporters ----
  - job_name: postgres
    static_configs:
      - targets: ["__POSTGRES_EXPORTER_TARGET__"]

  - job_name: pgbouncer
    static_configs:
      - targets: ["__PGBOUNCER_EXPORTER_TARGET__"]

  - job_name: redis
    static_configs:
      - targets: ["__REDIS_EXPORTER_TARGET__"]

  - job_name: node
    static_configs:
      - targets: ["__NODE_EXPORTER_TARGET__"]

  - job_name: cadvisor
    static_configs:
      - targets: ["__CADVISOR_TARGET__"]
