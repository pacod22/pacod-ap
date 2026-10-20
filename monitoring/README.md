# pacod monitoring module

Self-contained observability for the pacod stack: **Prometheus** (scrape + alert
rules), **Alertmanager** (routing), **Grafana** (provisioned dashboards), and a
set of **exporters** (Postgres, PgBouncer, Redis, host node, cAdvisor). The app's
FastAPI services expose `/metrics` directly (see `app/core/metrics.py`); **Caddy**
exposes its built-in metrics on an internal listener, and the **SvelteKit frontend**
exposes a request counter split by registered-vs-anonymous user — together these
cover the full web-request path (see *Web-traffic metrics* below).

## Design: what runs where

Exporters read *into* Redis/Postgres and off the host, so they must live on the
**app host**, on the shared `pacod-net` network. Prometheus + Alertmanager +
Grafana are the **relocatable monitoring server**:

| Component | Same instance | Separate instance (future) |
|---|---|---|
| Exporters + app `/metrics` | app host, `pacod-net` | app host, `pacod-net`, ports published to a reachable interface |
| Prometheus / Alertmanager / Grafana | app host, `pacod-net`, scrape by Docker DNS | its own host, scrape `app-host-ip:published-port` |

Every container here joins the one **external** Docker network of every pacod stack, `pacod-net`
(the app's services, Caddy, pacod-id and the gateway's `pacod-gw` are on it too), under its shared
name: `pacod-prometheus`, `pacod-alertmanager`, `pacod-grafana` and the platform exporters
`pacod-postgres-exporter`, `pacod-node-exporter`, `pacod-cadvisor` (one of each per host,
[pacod-demo/docs/NETWORK.md](../../pacod-demo/docs/NETWORK.md)); pacod-ap's own
`ap-pgbouncer-exporter` and `ap-redis-exporter`. Compose does not create external networks; `make
up` creates it when missing. This module is the monitoring of pacod-ap's own stacks (`make up-all`
too: the gateway console's Metrics page asks `pacod-prometheus`).

**Scrape configuration.** `prometheus/prometheus.yml.tpl` holds the platform's jobs (`prometheus`,
`caddy`, `qdrant`, `pacod-gw-core`, `pacod-id`, `postgres`, `node`, `cadvisor`); pacod-ap's own jobs
(`api`, `admin-api`, `mcp-server`, `embeddings`, `ingestion`, `frontend`, `pgbouncer`, `redis`) are
the fragment `../platform/prometheus/ap.yml`, mounted at `/etc/prometheus/scrape.d` and loaded
through `scrape_config_files` — the same file the full stack (pacod-demo) loads into its shared
Prometheus. The job names never change: dashboards, alerts and the gateway console select on them.

Every scrape target is parameterised via `*_TARGET` env vars (defaults = Docker
service DNS), so relocating the server is a config change, not a rewrite: `prometheus/entrypoint.sh`
substitutes them into the template, and into a copy of each fragment under `/tmp/scrape.d` (the
fragment names its default targets literally, e.g. `"ap-api:8000"`, because the full stack reads it
as it is).

## Quick start — same instance (default)

```bash
cd monitoring
cp .env.example .env          # then edit: set GF_SECURITY_ADMIN_PASSWORD and POSTGRES_PASSWORD
make up                        # creates pacod-net if missing, then starts everything
```

Also runnable from the repo root: `make monitor-up` / `make monitor-down` / `make monitor-logs`.

Then (UIs bind to 127.0.0.1 — SSH-tunnel if remote; ports from `.env`):

- Grafana:      http://127.0.0.1:3300/monitoring/  (`GRAFANA_PORT`; Grafana serves from the
  `/monitoring` sub-path; login from `.env`; dashboards under folders **pacod** and **pacod-gw**)
- Prometheus:   http://127.0.0.1:9095  (`PROMETHEUS_PORT`; `/targets`, `/alerts`. Not 9090 on the host,
  which is often taken by another service; inside the container it is still 9090)
- Alertmanager: http://127.0.0.1:9093  (`ALERTMANAGER_PORT`)

`make reload` hot-reloads Prometheus on `PROMETHEUS_PORT` (default 9095).

### Access to Grafana: embedded in the admin console + `www.pacod.io/monitoring`

Grafana is exposed through the app's Caddy edge at `https://www.pacod.io/monitoring` and **embedded in
the admin console** at `/metrics` (Monitoring → Metrics in the sidebar) as a same-origin iframe — so
admins reach dashboards without an SSH tunnel or a separate login. Three pieces make this work:

1. **Grafana sub-path + embedding config** (`docker-compose.monitoring.yml`): `GF_SERVER_ROOT_URL`
   (default `https://www.pacod.io/monitoring/`) + `GF_SERVER_SERVE_FROM_SUB_PATH=true` so Grafana emits
   its asset/redirect URLs under `/monitoring`; plus `GF_SECURITY_ALLOW_EMBEDDING=true` (removes the
   anti-framing headers) and `GF_AUTH_ANONYMOUS_ENABLED=true` / `GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer`
   (dashboards render in the iframe with no Grafana login). The `GF_SECURITY_ADMIN_*` login still works
   for editing.

   > **⚠️ Recreate Grafana after changing any `GF_SERVER_*` (or other) env var — `restart` is not
   > enough.** Container environment is fixed at **creation** time; `docker compose restart pacod-grafana`
   > reuses the old env. Apply env changes with
   > `docker compose -f docker-compose.monitoring.yml up -d --force-recreate pacod-grafana` (the
   > `pacod-grafana-data` volume persists, so no dashboards/settings are lost). Symptom of stale env: hitting
   > `/monitoring` (past the forward_auth gate) `302`-redirects to a **`/login`** with **no
   > `/monitoring` prefix** — Grafana thinks it lives at `/` — and the browser, following that into
   > the SvelteKit app (whose legacy `/login` sends it through pacod-id's sign-in), ends up on
   > `/chat`. Confirm the running env with
   > `docker exec pacod-grafana env | grep GF_SERVER`; once fixed, the redirect is `/monitoring/login`.
2. **Caddy route** (`platform/caddy/ap.caddy`, pacod-ap's site, `handle /monitoring*`): reverse-proxies to
   `pacod-grafana:3000` **without stripping** the prefix. Because Grafana serves anonymously, this
   route is the **only** access control: `forward_auth` asks SvelteKit's `/internal/monitoring-auth`
   whether the browser's session (the pacod-id session behind the `app_session` cookie) belongs to an
   **admin** (200 → proxy, 403 → reject). This replaced the earlier HTTP `basic_auth`, which popped an
   un-fillable dialog inside the iframe. Grafana's static assets (`/monitoring/public/*`) are excluded
   from the gate; the data endpoints (`/monitoring/api/*`) stay protected. Grafana joins the shared
   external `pacod-net`, so Caddy reaches it by container DNS.
3. **In-app page** (`pacod-ui`): admin route `(app)/metrics` (Monitoring → Metrics) renders the
   `pacod-app`, `pacod-infra`, `pacod-web`, `pacod-crawlers` and `pacod-usage` dashboards and the
   gateway's `pacod-gw-overview` and `pacod-gw-providers` as tabs of `?kiosk` iframes; the forward-auth
   endpoint is `src/routes/internal/monitoring-auth/+server.ts`. The in-app path is `/metrics`,
   **not** `/monitoring` (which Caddy routes to Grafana).

> **Locally** the same route serves `localhost:8080/monitoring/*` (the local edge imports the same
> site fragment). Set `GF_SERVER_ROOT_URL=http://localhost:8080/monitoring/` in `.env` for the
> in-app frames, or open Grafana directly at **http://127.0.0.1:3300/monitoring/**.

> **⚠️ Reload Caddy after any Caddyfile edit.** The Caddyfile and the site fragments are bind-mounted
> read-only (`./docker/caddy/Caddyfile:/etc/caddy/Caddyfile:ro`, `../platform/caddy:/etc/caddy/sites:ro`);
> Caddy reads them **once at startup** and does
> **not** hot-reload on disk changes. After editing the Caddyfile (or pulling a change to it), apply
> it from `pacod-api/` with `docker exec pacod-caddy caddy reload --config /etc/caddy/Caddyfile`
> (or `docker compose restart pacod-caddy`). Symptom of a stale config: `/monitoring` returns a plain
> **404** — because the request never matches `handle /monitoring*` and falls through to the SvelteKit
> catch-all. (A **502** instead means the route is live but Grafana is unreachable — the monitoring
> stack isn't up or hasn't joined `pacod-net`. A **403** means forward_auth rejected the caller —
> not logged in, or not an admin.)

Access is admin-only via the app session (no separate monitoring password to rotate). Grant access by
giving the user the `admin` role in the app; revoke by removing it. Prometheus and Alertmanager are
**not** exposed publicly — tunnel to them as above.

`POSTGRES_USER/PASSWORD/DB` in `.env` **must match** `pacod-api/.env` (the exporter
connects to Postgres directly, not via PgBouncer).

## What gets scraped

- **App services** (`/metrics` on their own port): `ap-api`, `ap-admin-api`, `ap-mcp`,
  `pacod-embeddings`, `ap-ingestion`. Metrics: `http_requests_total`,
  `http_request_duration_seconds`, `http_requests_in_progress`, `guard_rejections_total`,
  plus the usage series below (from `ap-api` / `ap-admin-api`).
- **Usage / LLM series** (`ap-api` + `ap-admin-api`, see `app/core/metrics.py`) — the app's own view of
  its LLM calls, including what the gateway cannot count (client timeouts, an unreachable gateway):
  - `llm_requests_total{service,site,profile,model,purpose,outcome}` — logical LLM calls by final
    outcome `ok|error|timeout` (one sample per call, *after* retries).
  - `llm_retries_total{service,model,purpose}` — transient failures that were retried (no site/profile).
  - `agent_events_total{service,site,profile,event_type}` — audit events: `request_completed` (chat
    turn), `request_accepted`, `rejected_*`, …
  - `site` = `pacod` (the app), `internal` (workers), `unknown` (a direct call, e.g. an OpenAI client on `/v1/*`).
    `profile` = the agent's profile id (`starter`, `taxes`, …) or `none`.
  - Tokens and cost are **not** app series any more: they are the gateway's (next item).
- **The LLM gateway** (job `pacod-gw-core`, `pacod-gw:9100` on `pacod-net`): the
  gateway's own `gw_*` series — requests, errors, tokens, cost (`gw_cost_micros_total`), latency,
  limits, prices, plugins, the audit spool. `instance="core"` (the core's `PGW_INSTANCE_ID`), as its own
  Prometheus has it. Its dashboards are vendored unchanged into `grafana/dashboards-gw/` (Grafana
  folder **pacod-gw**: Overview, Providers and models, Workspaces, Gateway internals) and its alert
  rules into `prometheus/alerts/gw.rules.yml` — evaluated here and routed by this Alertmanager, the
  platform's only one (the gateway stack's own is disabled by the platform override). To refresh
  them, copy `pacod-gw/deploy/grafana/dashboards/*.json` and
  `pacod-gw/deploy/prometheus/rules/pacod-gw.rules.yml` again (keep the header of `gw.rules.yml`).
  Per-user and per-turn figures are in the admin console (Usage & costs, Analytics) and the
  gateway console, which read the gateway's reports.
- **Identity** (`pacod-id`, `pacod-id:9170`): the platform's identity provider.
- **Edge** (`pacod-caddy`, `pacod-caddy:9180/metrics`): Caddy's built-in Prometheus metrics
  (`caddy_http_requests_total{server,handler}`, `caddy_http_request_duration_seconds{server,handler,code,method}`,
  request/response sizes) — the authoritative **all-web-requests** view across every vhost, incl.
  anonymous marketing/static traffic that never reaches the API. Enabled via `servers { metrics }` in
  the Caddyfile; the `:9180` listener is internal-only (not host-published).
- **Web** (`ap-frontend`, `ap-frontend:3000/internal/metrics`; target `FRONTEND_TARGET`): the one SvelteKit
  container, serving www.pacod.io — site `pacod`. Series:
  `pacod_frontend_http_requests_total{method,route,user_type,status}` (`user_type` =
  `registered|anonymous|bot` — the split Caddy can't derive from an opaque session cookie),
  `pacod_frontend_active_sessions` gauge (live server-side sign-in sessions in Redis),
  `pacod_frontend_crawler_requests_total{bot,category,verified,status}`, + default Node process
  metrics. Every `pacod_frontend_*` series carries `site="pacod"` (a prom-client default label).
- **Native**: `pacod-qdrant` (`:6333/metrics`).
- **Exporters**: postgres (`pacod-postgres-exporter`), pgbouncer (`ap-pgbouncer-exporter`), redis
  (`ap-redis-exporter`), node (host, `pacod-node-exporter`), cadvisor (containers, `pacod-cadvisor`).

### Notes / known gotchas

- The `/metrics` endpoints sit on each service's own port (8000/8080), reachable
  only inside `pacod-net` — Caddy does not route them publicly, so no separate
  metrics port is used.
- **PgBouncer exporter** needs a PgBouncer stats/admin user (`docker/postgres/pgbouncer.ini`
  + `userlist.txt`). Until configured its target shows DOWN — harmless.
- **Non-HTTP workers** (`ap-improvement-loop-worker`, `ap-newsletter-worker`) have no HTTP
  server and are not scraped yet; instrumenting them needs a Pushgateway or an
  embedded metrics server (future). Their LLM usage is therefore missing from the
  `llm_*` series — use the admin Analytics page for it.

## Dashboards

Provisioned into the **pacod** folder from `grafana/dashboards/`: `pacod-app` (API services),
`pacod-infra` (hosts, DBs, containers), `pacod-web` (Caddy edge + the frontend), `pacod-crawlers`
(search/AI crawlers), and `pacod-usage` — **Usage (sites, agents, LLM)**: frontend and agent request
rates by site, agent events, 24h chat turns / rejected requests / LLM calls, and LLM calls / error
ratio / failures / retries. LLM **tokens and cost** are not on it any more: they are the gateway's,
in the **pacod-gw** folder (from `grafana/dashboards-gw/`: Overview, Providers and models,
Workspaces, Gateway internals). `$site` / `$profile` template variables filter every panel whose
series carry those labels.

## Alerting

Starter rules in `prometheus/alerts/` (service down, 5xx rate, p95 latency, guard
spikes, Postgres/Redis down, pool/disk/memory pressure, container restart loops).
`web.rules.yml` adds the edge/web layer: `FrontendDown`, `CaddyEdgeDown`,
`HighEdge5xxRate`, `EdgeTrafficDropToZero` (an app-wide outage signal); `FrontendDown` watches
`up{job="frontend"}`, and the crawler rules aggregate `by (site)` (the Googlebot/Bingbot
*absence* arm stays global so a site that isn't crawled can't page forever). `gw.rules.yml` holds
the gateway's own rules (see *What gets scraped*). `app.rules.yml` group
`pacod-llm`: `LlmErrorRateHigh` (>10% error/timeout per site+model over 15m, min 10 calls),
`LlmTimeoutsSpike` (>5 timeouts/15m), `LlmAllCallsFailing` (no successful call in 15m while >5 attempted).
`alertmanager/alertmanager.yml` has a **receiver stub** — add a Slack webhook or
SMTP block to actually deliver notifications.

### Web-traffic metrics — gotchas

- **`caddy_http_requests_total` has only `{server, handler}` labels — no status code.** The status
  code lives on the duration histogram (`caddy_http_request_duration_seconds{...,code,method}`), so
  5xx-rate queries use its `_count` series, not the counter. See `grafana/dashboards/pacod-web.json` /
  `prometheus/alerts/web.rules.yml`.
- **Exclude `handler="metrics"`** in total/drop-to-zero queries: Prometheus scraping `pacod-caddy:9180`
  itself increments the counter, so an unfiltered "traffic == 0" alert could never fire.
- **"Anonymous" is request volume, not a headcount.** A distinct anonymous count would need a
  first-party cookie (gated by the frontend's `hasAnalyticsConsent()`); `pacod_frontend_active_sessions`
  is the registered side: live sign-in sessions (one lasts up to 30 days with a refresh token, so read
  it as signed-in sessions, not people online this minute). These are aggregate counters with no per-user labels → no
  analytics consent required. Distinct DAU/MAU stays a DB/`audit_log` query.
- The frontend endpoint is `ap-frontend:3000/internal/metrics`; the public Caddy edge 404s `/internal/*`,
  so it (like the FastAPI `/metrics`) is reachable only inside `pacod-net`.

## Separate-instance deployment (capability built in, not yet hardened)

1. **App host** — run only the exporters and rebind their ports to an interface the
   monitoring host can reach (edit the `127.0.0.1:` prefixes in
   `docker-compose.exporters.yml`), plus ensure the app `/metrics` ports are reachable:
   ```bash
   docker compose -f docker-compose.exporters.yml up -d
   ```
2. **Monitoring host** — copy this directory, set every `*_TARGET` in `.env` to
   `app-host-ip:published-port` (`GW_CORE_TARGET` too: the gateway's core is otherwise reached over
   `pacod-net`), remove the `pacod-net` entries from the services' `networks` (and the top-level
   external network) in `docker-compose.monitoring.yml`, then:
   ```bash
   docker compose -f docker-compose.monitoring.yml up -d
   ```

> **Not hardened yet.** This assumes a trusted/VPN network between the two hosts —
> there is no scrape auth/TLS. Add basic-auth or mTLS (e.g. a metrics reverse proxy)
> before exposing exporters across an untrusted network.
