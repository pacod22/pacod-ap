# Runbook

Operating pacod-ap: the three stacks, their secrets, the first start, the gateway's
configuration, backup and restore, and what goes wrong. Every command runs from the repository
root unless it says otherwise.

## The stacks

| Stack | Compose project | Where | What |
|---|---|---|---|
| App | `pacod` | `pacod-api/docker-compose.yml` (the app file) + `docker-compose.standalone.yml` (+ their `.local.yml` / `.standalone.local.yml` overlays locally) | the app file: `ap-api`, `ap-admin-api`, `ap-frontend`, the workers, `ap-mcp`, `pacod-embeddings`, `ap-ingestion(-cpu)`, `ap-pgbouncer`, `ap-redis`, `ap-adminer`; the standalone file: `pacod-caddy`, `pacod-postgres` 18.6, `pacod-db-init` (every role and database), `pacod-qdrant`, **`pacod-id`**, `pacod-mailpit` (local) |
| Gateway | `pacod-ap-gw` | pacod-gw's app file from a checkout at `GW_DIR` (default `../pacod-gw`; + its `docker-compose.dev.yml` in development) + `deploy/pacod-gw/docker-compose.platform-id*.yml` | `pacod-gw` (data plane, management API, metrics), `gw-console`, `gw-analytics`, `gw-tester`, `gw-clickhouse`, `gw-preflight`; `gw-mock` in development |
| Monitoring | `pacod-monitoring` | `monitoring/` | `pacod-prometheus`, `pacod-alertmanager`, `pacod-grafana`, the exporters |

One external network joins them: **`pacod-net`**, which every container joins under its one name
(service key = container name = DNS name; shared components `pacod-*`, pacod-ap's own `ap-*`, the
gateway's `gw-*` — [pacod-demo/docs/NETWORK.md](../../pacod-demo/docs/NETWORK.md)). Every `make up*`
target creates it (`make ensure-pacod-net`). The gateway runs from pacod-gw's app file only: the
shared components it uses are the platform's — `pacod-postgres` (its database `pacod_gw`, made by
the platform's `pacod-db-init` from pacod-gw's `platform/postgres/gw-db.sh`, password
`GW_DB_PASSWORD` in `deploy/secrets/gw.env`), `pacod-id` (`PGW_AUTH=oidc`,
`PGW_ID_INTERNAL_URL=http://pacod-id:8071`), `pacod-caddy` (the console) and `pacod-prometheus`
(the console's Metrics page, `make monitor-up`).

**Start order matters:** pacod-id first (the gateway signs in through it), then the gateway, then
its bootstrap (which hands the app's services their gateway keys and recreates them).
`make up-all` does exactly that; `make down-all` stops the gateway
and the app, volumes kept.

The gateway stack uses pacod-gw's development ports, so it cannot run at the same time as a
standalone pacod-gw development stack (project `pacod-gw`, whose volumes it never touches).

## Ports (local)

| Port | Service |
|---|---|
| 8080 | the app (Caddy) |
| 8070 | pacod-id's public listener (Caddy → `pacod-id:8070`): sign-in, account, `/oidc/*` |
| 13000 | the gateway console (Caddy → `gw-console:3000`) |
| 127.0.0.1:3001 | admin-api direct (accepts the development `dev-token-<id>` bearer) |
| 8025 | mailpit |
| 15432 | PostgreSQL |
| 6333, 8081 | Qdrant, Adminer |
| 18080 / 18081 / 19100 | gateway data plane / management API / metrics |
| 19000 | the gateway's mock provider (its `/__mock/*` control API scripts replies) |
| 9095, 3300, 9093 | monitoring: Prometheus (not 9090), Grafana, Alertmanager |

In production Caddy is the only public edge: `www.<domain>` (the app), `id.<domain>` (pacod-id's
public listener), `console.<domain>` (the gateway console), each with an access log that leaves out
sign-in codes, tokens and API keys (see **Logs**). The gateway's management API and pacod-id's
internal listener are never routed.

## Logs

Every pacod repo logs the same way: [pacod-demo/docs/LOGGING.md](../../pacod-demo/docs/LOGGING.md).

- **stdout, one event per line**, read with `docker logs <container>` / `make logs`. Every compose
  service (third-party ones included) rotates its container log: `json-file`, 10 MB × 5
  (`x-logging` in each compose file).
- **The event:** `timestamp` (UTC, milliseconds, `Z`), `level` (`debug|info|warn|error`), `service`
  (the compose service name, `SERVICE_NAME`), `logger`, `message`, then the fields flat;
  `error` + `stack_trace` with an exception. JSON, or one text line:

  ```text
  {"timestamp":"2026-10-23T06:41:07.695Z","level":"info","service":"ap-api","logger":"uvicorn.access","message":"GET /api/v1/quota 200","method":"GET","path":"/api/v1/quota","status":200,"client_ip":"172.18.0.9"}
  2026-10-23T06:41:07.695Z INFO  ap-api uvicorn.access: GET /api/v1/quota 200 method=GET path=/api/v1/quota status=200 client_ip=172.18.0.9
  ```

- **Level and format.** Python services: `APP_LOG_LEVEL` and `APP_LOG_FORMAT` (`json|text`; unset:
  text while `APP_ENV=development`, else json) in `.env` (pacod-embeddings, which has no env_file,
  by compose interpolation of the same names). The frontend: `FRONTEND_LOG_LEVEL` (default `info`)
  and `FRONTEND_LOG_FORMAT` (default `json`) by interpolation, its `LOG_LEVEL` / `LOG_FORMAT`.
  Request lines (`GET /path 200`, never the query string) come from uvicorn's access log, the
  admin-api middleware and the frontend's request hook; health checks are at `debug`.
- **Files** (the Python services with a log directory: ap-api, ap-admin-api, ap-ingestion(-cpu),
  both loop services, ap-newsletter-worker): `APP_LOG_DIR` (`/srv/logs`, mounted from
  `/data/logs/<service>` on a server) holds `<service>.log` (e.g. `ap-api.log`), always JSON,
  rotated at midnight UTC and at 20 MB to `<service>.<yyyy-mm-dd>.<n>.log.gz`; rotated files are
  kept 30 days and 600 MB per directory. **The file used to be `pacod-agent.log`** (rotated to
  `pacod-agent.log.<date>.tar.gz`): point log shippers, `tail -f` habits and logrotate rules at
  the new names. The old archives are not touched by the new rotation; delete them by hand when no
  longer needed.
- **The edge:** `/data/caddy/logs/caddy.log`, JSON, every site's requests (`www`, `id`, `console`)
  with the query parameters `token`, `code`, `state`, `next`, `flow`, `id_token_hint`,
  `login_hint`, `key`, the request headers `Referer`, `X-Api-Key`, `X-Goog-Api-Key` and the response
  header `Location` removed.

## Secrets

All secret files are gitignored and mode 0600. The scripts print **names, never values**; never
`cat` these files, and check merged compose files with `docker compose … config --quiet` (a plain
`config` prints every secret).

| File | Holds | Written by |
|---|---|---|
| `pacod-api/.env` | the app's settings (the `env_file` of every backend container): database, Redis and session secrets, search keys | `make ensure-env` (from `.env.example`, generating blank secrets) |
| `pacod-api/.env.local` | local deltas (dev tokens, issuer `http://127.0.0.1:8070`, hash embeddings, …) | `make ensure-env-local` from `.env.local.example` |
| `pacod-api/.env.gateway` | compose interpolation only (no service's `env_file`): one gateway key (`pgw_…`) per service, admin-api's report and admin tokens, the platform workspace's ids, the pacod-id client secrets of the API and the frontend (copied from `id.env`) | `make gw-bootstrap`, `make secrets-init` |
| `deploy/secrets/id.env` | pacod-id: database password, `PID_KEK` (seals its signing keys), token pepper, bootstrap admin, client secrets | `make secrets-init` |
| `deploy/secrets/gw.env` | the gateway's stores, KEK, pepper, internal token, console session secret + the client secrets shared with `id.env` | `make secrets-init` |
| `deploy/secrets/providers.env` | LLM provider keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `GLM_API_KEY`) — read only by `scripts/gw_bootstrap.py` | you, with `make secret-set` |

`make secrets-init` fills only what is missing. `make secret-set NAME=<VAR> [FILE=<file>]` reads
one value from stdin without echoing it, e.g.

```bash
make secret-set FILE=deploy/secrets/providers.env NAME=ANTHROPIC_API_KEY
make secret-set FILE=deploy/secrets/id.env NAME=PID_BOOTSTRAP_ADMIN_EMAIL   # before the first start
```

The services each get only their own variables: compose interpolates the gateway keys into each
service's `environment:` from `.env.gateway` (which is no service's `env_file`), and the frontend
gets no `env_file` at all. **Compose by hand needs every env file:**
`docker compose --env-file .env --env-file .env.local --env-file .env.gateway …` in `pacod-api/`
— without `.env.gateway` the services start with blank gateway keys (every LLM call 401s).

## First start (local)

```bash
make secrets-init
make up-all          # pacod-net → up-local (app + pacod-id + pacod-gw's database) → gw-up → gw-bootstrap (→ monitor-up)
make bootstrap-local # tables, the starter agent's reference facts and corpus (CPU pipeline)
make smoke-all       # smoke-local + gw-turn-smoke + the gateway's own smoke-mock
```

- `bootstrap-local` creates the schema (`init_db` + the `_MIGRATIONS` ledger), seeds the `starter`
  profile's facts from `pacod-api/data/profiles/starter/` and ingests its corpus. On a server,
  `make bootstrap` / `make seed-example` use whichever ingestion container runs (`ap-ingestion` after
  `make up-gpu`, `ap-ingestion-cpu` after `make up-cpu`). The ingestion API refuses a named agent that
  does not exist (404 `unknown_profile`) instead of filing its sources under the default one.
- Optional example: `make -C pacod-api seed-example-local PROFILE=taxes`. Its CPU ingest of the
  Bulgarian law corpus is slow (tens of minutes) and memory-hungry on a laptop.
- `gw-turn-smoke` scripts the mock's replies, runs one chat turn and checks that the gateway's
  trace of it (trace id = the turn id) has the same cost as the turn's `done` event.
- Locally `LOCAL_EMBEDDINGS_BACKEND=hash` (`.env.local`) keeps the embeddings service from
  downloading bge-m3 (~2 GB, 7 GB on disk); retrieval quality is then meaningless.

Sign in at http://localhost:8080 with the bootstrap admin (`PID_BOOTSTRAP_ADMIN_*`), or create an
account (self sign-up is `PACOD_ID_SELF_SIGNUP`, default on); the confirmation mail is in mailpit.

## The gateway's configuration

The platform's gateway objects — providers, the platform's model ids with prices and capability
presets, organisation and workspace `pacod` (tag keys `purpose`, `site`, `profile`, `variant`),
grants, the end-user token limit `user-daily-tokens`, the workspace spend limit with alerts — are
two files imported by `scripts/gw_bootstrap.py` (`POST /system/config-import`, dry run first):

| File | Providers | Used by |
|---|---|---|
| `deploy/pacod-gw/platform-config.dev.json` | the mock, one per family (`mock-anthropic`, …) | `make gw-bootstrap` (default `GW_ENV=dev`) |
| `deploy/pacod-gw/platform-config.live.json` | the real providers | `GW_ENV=live` |

`make gw-bootstrap` is idempotent: objects keep their ids (an import updates in place and never
deletes), stored keys are probed and reissued only when they stop working, and the services that
read `.env.gateway` are recreated when it changed. Models, prices and limits may also be edited
in the console; to keep such a change, re-author the files (`python3 -I scripts/gw_bootstrap.py
--env dev --author` / `--author-live`, which converge the development gateway and write the
files) and commit them.

### Real providers

Status: the switch to real providers has been prepared but **not yet run** with real keys.

1. Put the keys into `deploy/secrets/providers.env` with `make secret-set` (one per provider in use).
2. Production layout: `GW_ENV=live` on every gateway target (`make gw-up GW_ENV=live`, …) — pacod-gw's
   production compose file with every secret from `deploy/secrets/gw.env`.
3. Create a platform-admin token in the console (Platform → Access → Admin tokens, role
   `platform_admin`) and store it: `make secret-set FILE=pacod-api/.env.gateway NAME=GW_ADMIN_TOKEN`
   (only development signs in by itself).
4. `make gw-bootstrap GW_ENV=live GW_BOOTSTRAP_ARGS="--admin-url http://<core>:8081 --data-url
   http://<core>:8080"` (live has no default addresses) imports `platform-config.live.json` and the
   provider credentials.

Every provider's timeout in the gateway must stay at least the app's `LLM_TIMEOUT_SECONDS` (180)
and `LOOP_LLM_TIMEOUT_SECONDS` (600): the configs carry 600 s.

## Identity (pacod-id)

- **The bootstrap admin** holds `id.admin`, `pacod.superuser`, `pacod.admin` and
  `gw.platform_admin`, so it administers the app, pacod-id and the gateway.
- **Admins and superusers** are role bindings at pacod-id. Grant them on the app's Users page,
  which acts at pacod-id as you, so pacod-id enforces who may grant what: an admin cannot make a
  superuser, and the last admin cannot be removed. pacod-id's own admin pages are at
  `<issuer>/admin`.
- **The catalogue.** The app's roles and permissions are `pacod-api/authz/pacod.yaml`. The api
  pushes it to pacod-id at boot. A role dropped from it is named in `RETIRED_ROLES`
  (`pacod-api/app/services/identity.py`), sent with the push as `migrate`: pacod-id removes its
  bindings rather than refusing the catalogue (409 `role_in_use`).
- **The OpenAI-compatible API** (`/v1/models`, `/v1/chat/completions` on `ap-api`, reachable on
  `pacod-net` and locally on 127.0.0.1:8000, not through the edge) takes the same tokens as the
  rest of the API: a person's pacod-id access token, a development token, or a pacod-id service
  account's `pidt_` token — the call is then the service account's own. A service account has no
  local user row, so no agent assignments: with a `pacod.admin` binding it sees every active
  agent, without one none.
- **Mail** (confirmation, password reset, invitations) is pacod-id's: `PID_SMTP_*` and
  `PID_EMAIL_FROM_*` in `deploy/secrets/id.env`. Set `PID_EMAIL_FROM_NAME` in a deployment:
  pacod-id's default sender name is `pacod-id`. Locally it always goes to mailpit. The app's own
  SMTP settings are only for the newsletter.
- **The look of the sign-in pages and mails** is the realm's `brand` in
  `pacod-api/docker/pacod-id/pacod-id.toml`. It must be one of pacod-id's built-in brands
  (`gateway`, `shop`, `bazaar`), or pacod-id refuses to start. The platform uses `gateway`: the
  base teal and gold, with the wordmark "pacod-gw · AI Gateway". pacod-id has no brand for the
  platform itself.

## Monitoring

`make monitor-up` (needs `monitoring/.env`, from `.env.example`; `make up-all` runs it when the file
exists). Prometheus scrapes the platform's jobs (`monitoring/prometheus/prometheus.yml.tpl`: the
edge, Qdrant, PostgreSQL, node, cAdvisor, pacod-id at `pacod-id:9170` and the gateway at
`pacod-gw:9100`, job `pacod-gw-core`, `instance=core`) and pacod-ap's own
(`platform/prometheus/ap.yml`, loaded through `scrape_config_files` — the fragment the full stack
loads too), all by name on `pacod-net`. The gateway console's Metrics page asks this Prometheus
(`PROMETHEUS_URL=http://pacod-prometheus:9090`).
Grafana (http://127.0.0.1:3300/monitoring, embedded in the admin console's Metrics page in
production) has the app's dashboards in the folder **pacod** and the gateway's in **pacod-gw**.
Alertmanager is the platform's only one; the gateway's rules are `monitoring/prometheus/alerts/gw.rules.yml`.
Caddy routes `/monitoring` to `pacod-grafana` behind the admin check, locally too; Grafana's
`GF_SERVER_ROOT_URL` (monitoring/.env) must name the origin the browser uses
(`http://localhost:8080/monitoring/` locally), or open Grafana directly.

## Backup and restore

```bash
make backup                                    # stack up; → ./backups/pacod-backup-<UTC>.tar.gz (0600)
make restore ARCHIVE=backups/pacod-backup-<UTC>.tar.gz [ARGS='--yes --overwrite-config']
```

The archive holds PostgreSQL globals and dumps of `pacod` and `pacod_id`; Qdrant snapshots; the
config and secrets (`.env`, `.env.gateway`, `deploy/secrets/`, the pacod-id and gateway
configuration, compose files, Caddyfiles, monitoring config, `data/`); the documents volume; with `REMOTE_HOST` set in `scripts/backup.env` the archive is also copied
there with scp. **The secrets must travel with the dumps**: `APP_SECRETS_KEY` decrypts the
admin-managed search keys and `PID_KEK` unseals pacod-id's signing keys. The archive is a secret.
Restore only previews config unless `--overwrite-config`; afterwards recreate the services that
read the restored env files (the script lists them). An older archive's Open-WebUI database dump
and data directory are skipped with a note. The gateway's own stores are backed up with
pacod-gw's tooling, not here.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Admin pages answer 503 `identity_unavailable` | The copy of pacod-id's feed is older than 60 s (`ID_MAX_STALENESS`): pacod-id is down or unreachable. Chat keeps working on the last copy. |
| Every LLM call 401s | The service's gateway key is blank or revoked: compose was run without `--env-file .env.gateway`, or run `make gw-bootstrap`. |
| The gateway's management API answers 503 | Its copy of pacod-id's feed is stale (`identity_unavailable`); it recovers by itself. Only 401/403 mean a refused credential. |
| A local chat answers "echo: …" or is off-topic | The mock echoes. Script the classifier/planner replies first (`POST 127.0.0.1:19000/__mock/script`, see `scripts/smoke_gateway_turn.sh`). A repeated question is served by the response cache. |
| Admin LLM figures are 0 with a "gateway did not answer" note | admin-api could not read the gateway's reports (`GATEWAY_REPORT_TOKEN`, the workspace ids in `.env.gateway`, or the gateway is down). |
| Docker builds fail, PostgreSQL crash-loops with "No space left" | The Docker VM's disk is full (the CPU ingestion image alone is ~10 GB; a rebuild needs much more transiently). `docker builder prune -f`, build one image at a time, check with `docker run --rm alpine df -h /`. PostgreSQL and pacod-id recover once space is free. |
| A person exists at pacod-id but not on the Users page | The page lists the app's shadow rows; a row appears with the person's first request to the API. |
| `pacod` containers collide with another checkout | The original pacod uses the same compose project name and container names: do not run both on one Docker host. |
| "container name `/pacod-postgres` is already in use" | Another pacod stack (pacod-id, pacod-gw, pacod-cms, pacod-kb standalone, or pacod-demo) runs its own copy of a shared component: only one of each `pacod-*` may run on a host. Stop the other stack. |

## Upgrading to the pacod-net names

The services, containers and volumes were renamed to the shared convention (api → `ap-api`,
postgres → `pacod-postgres`, …); the networks `agent-net` and `pacod-link` are gone. Once:

1. Stop everything with the **old** checkout (`make down-all`, `make monitor-down`), then check out
   the new one. `docker network rm agent-net pacod-link` when nothing uses them.
2. The database volume keeps its old name (`pacod_postgres_data`, pinned in
   `docker-compose.standalone.yml`), and so does the local Qdrant volume (`pacod_qdrant_data`); the
   server's Qdrant, Caddy and log data are bind mounts under `/data` and stay (the services' log
   files are now `<service>.log`, not `pacod-agent.log`: see **Logs**).
3. The other named volumes have new names, and start empty unless copied. Copy the uploads (the
   only irreplaceable one) before the first start:

   ```bash
   docker volume create pacod_ap-documents-data
   docker run --rm -v pacod_documents_data:/from:ro -v pacod_ap-documents-data:/to alpine \
     sh -c 'cp -a /from/. /to/'
   ```

   The same works for `pacod_redis_data` → `pacod_ap-redis-data` (sessions and caches; without it
   everybody signs in again). `pacod_hf_cache` → `pacod_pacod-embeddings-hf-cache` only saves the model download. The
   monitoring volumes (`pacod-monitoring_prometheus_data`, `…_grafana_data`) become
   `pacod-monitoring_pacod-prometheus-data` / `…_pacod-grafana-data`.
4. `make secrets-init` rewrites `PID_DATABASE_URL` in `deploy/secrets/id.env` to the host
   `pacod-postgres` and adds `GW_DB_PASSWORD` to `deploy/secrets/gw.env`. The gateway's data moves
   from its own PostgreSQL into the platform's (`pacod_gw`, a fresh database, made by
   `pacod-db-init` whenever `GW_DIR` holds pacod-gw's `platform/postgres/gw-db.sh` — `make up-all`,
   `make up-local`, `make up`/`up-api`). Its old PostgreSQL volume (`pacod-ap-gw_postgres-data`) is
   left behind: `make gw-bootstrap` (with `GW_ENV=live` on a server) brings back everything
   `platform-config` describes, the providers' keys and the app's keys; anything else made in the
   console (users' tokens, extra workspaces) needs a `pg_dump` of the old database restored into
   `pacod_gw` as the role `pacod_gw` before the gateway's first start.
5. Remove the old volumes with `docker volume rm` once the new stack works.

## Upgrading: Open-WebUI removed

Open-WebUI (the alternative chat UI on `ai.<domain>`, locally :3000) is gone: the app's own chat
is the chat UI. Its service, its sites and port 3000, its SSO bridge, the `pacod-webui` service
account's role and `make id-bootstrap` were removed. Nothing is deleted automatically; once the
new checkout runs:

1. A still-running `ap-open-webui` container is now an orphan of the compose project:
   `docker rm -f ap-open-webui` (or `docker compose … up -d --remove-orphans` in `pacod-api/`).
2. Its database, which `pacod-db-init` no longer creates:
   `docker exec pacod-postgres dropdb -U pacod --force openwebui`. `--force` ends the connections
   a still-running old `ap-pgbouncer` may hold to it.
3. Its data: on a server the bind mounts under `/data/openwebui` (`rm -rf /data/openwebui`);
   locally the named volumes — `docker volume ls | grep open-webui`, then `docker volume rm` each
   (e.g. `pacod_ap-open-webui-data`, `pacod_ap-open-webui-logs`; older checkouts named it
   `pacod_open_webui_data`).
4. The service account `pacod-webui` at pacod-id: the api's next catalogue push removes its
   binding of the retired role `pacod.webui_service`, so it holds no role; its `pidt_` token would
   still sign in as a service account without rights. Delete the account in pacod-id's admin
   (`<issuer>/admin`), which revokes the token, and drop `PACOD_WEBUI_SERVICE_TOKEN` from
   `pacod-api/.env.gateway`.
5. The variables `OPENWEBUI_URL`, `WEBUI_SECRET_KEY`, `WEBUI_ADMIN_EMAIL`, `WEBUI_ADMIN_PASSWORD`
   (`pacod-api/.env`) and `LOCAL_OPENWEBUI_URL` (`.env.local`) are read by nothing any more and
   may be deleted.

Analytics rows written before the removal keep the site `openwebui`; they stay in the database and
are shown and filtered under that name. New `/v1/*` calls without a trusted site header count as
`unknown`.
