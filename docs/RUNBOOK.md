# Runbook

Operating pacod-ap: the three stacks, their secrets, the first start, the gateway's
configuration, backup and restore, and what goes wrong. Every command runs from the repository
root unless it says otherwise.

## The stacks

| Stack | Compose project | Where | What |
|---|---|---|---|
| App | `pacod` | `pacod-api/docker-compose.yml` (+ `docker-compose.local.yml` locally) | api, admin-api, frontend, workers, mcp-server, embeddings, ingestion, PostgreSQL 18.6 + PgBouncer, Redis, Qdrant, Caddy, Open-WebUI, **pacod-id** + `pacod-id-db` (creates its role and database), mailpit (local) |
| Gateway | `pacod-ap-gw` | a pacod-gw checkout at `GW_DIR` (default `../pacod-gw`) + `deploy/pacod-gw/*.yml` | gw core (data plane, management API, metrics), console, tester, its PostgreSQL/ClickHouse, its Prometheus; `gw-mock` in development |
| Monitoring | `pacod-monitoring` | `monitoring/` | Prometheus, Alertmanager, Grafana, exporters |

Two external networks join them: `agent-net` (the app and monitoring) and `pacod-link` (the app's
services, pacod-id, Caddy and Prometheus meet the gateway's `gw-core`, `gw-console`, `gw-tester`
and `gw-prometheus` there). `make ensure-pacod-link` makes it. The platform overrides switch the
gateway's own identity provider, Caddy and Alertmanager off and point it at the platform's
pacod-id (`PGW_AUTH=oidc`, `PGW_ID_INTERNAL_URL=http://pacod-id:8071`).

**Start order matters:** pacod-id first (the gateway signs in through it), then the gateway, then
its bootstrap (which hands the app's services their gateway keys and recreates them), then
Open-WebUI's service account. `make up-all` does exactly that; `make down-all` stops the gateway
and the app, volumes kept.

The gateway stack uses pacod-gw's development ports, so it cannot run at the same time as a
standalone pacod-gw development stack (project `pacod-gw`, whose volumes it never touches).

## Ports (local)

| Port | Service |
|---|---|
| 8080 | the app (Caddy) |
| 8070 | pacod-id's public listener (Caddy → `pacod-id:8070`): sign-in, account, `/oidc/*` |
| 127.0.0.1:8071 | pacod-id's internal listener — published on loopback for `make id-bootstrap` only; never routed |
| 13000 | the gateway console (Caddy → `gw-console:3000`) |
| 3000 | Open-WebUI |
| 127.0.0.1:3001 | admin-api direct (accepts the development `dev-token-<id>` bearer) |
| 8025 | mailpit |
| 15432 | PostgreSQL |
| 6333, 8081 | Qdrant, Adminer |
| 18080 / 18081 / 19100 | gateway data plane / management API / metrics |
| 19000 | the gateway's mock provider (its `/__mock/*` control API scripts replies) |
| 9095, 3300, 9093 | monitoring: Prometheus (not 9090), Grafana, Alertmanager |

In production Caddy is the only public edge: `www.<domain>` (the app), `id.<domain>` (pacod-id's
public listener, with a log filter that drops `code`, `state` and `token`), `console.<domain>`
(the gateway console), `ai.<domain>` (Open-WebUI). The gateway's management API and pacod-id's
internal listener are never routed.

## Secrets

All secret files are gitignored and mode 0600. The scripts print **names, never values**; never
`cat` these files, and check merged compose files with `docker compose … config --quiet` (a plain
`config` prints every secret).

| File | Holds | Written by |
|---|---|---|
| `pacod-api/.env` | the app's settings (the `env_file` of every backend container): database, Redis, Open-WebUI and session secrets, search keys | `make ensure-env` (from `.env.example`, generating blank secrets) |
| `pacod-api/.env.local` | local deltas (dev tokens, issuer `http://127.0.0.1:8070`, hash embeddings, …) | `make ensure-env-local` from `.env.local.example` |
| `pacod-api/.env.gateway` | compose interpolation only (no service's `env_file`): one gateway key (`pgw_…`) per service, admin-api's report and admin tokens, the platform workspace's ids, Open-WebUI's service token, the pacod-id client secrets of the API and the frontend (copied from `id.env`) | `make gw-bootstrap`, `make id-bootstrap`, `make secrets-init` |
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
make up-all          # ensure networks → up-local (app + pacod-id) → gw-up → gw-bootstrap → id-bootstrap
make bootstrap-local # tables, the starter agent's reference facts and corpus (CPU pipeline)
make smoke-all       # smoke-local + gw-turn-smoke + the gateway's own smoke-mock
```

- `bootstrap-local` creates the schema (`init_db` + the `_MIGRATIONS` ledger), seeds the `starter`
  profile's facts from `pacod-api/data/profiles/starter/` and ingests its corpus. On a server,
  `make bootstrap` / `make seed-example` use whichever ingestion container runs (`ingestion` after
  `make up-gpu`, `ingestion-cpu` after `make up-cpu`). The ingestion API refuses a named agent that
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
5. Open-WebUI's service account: `make id-bootstrap ID_BOOTSTRAP_ARGS="--public-url … --issuer
   https://id.<domain> --internal-url … --app-url https://www.<domain>"`, run from a host that
   reaches pacod-id's public and internal listeners (the internal one is never routed).

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
  pushes it to pacod-id at boot.
- **Mail** (confirmation, password reset, invitations) is pacod-id's: `PID_SMTP_*` and
  `PID_EMAIL_FROM_*` in `deploy/secrets/id.env`. Set `PID_EMAIL_FROM_NAME` in a deployment:
  pacod-id's default sender name is `pacod-id`. Locally it always goes to mailpit. The app's own
  SMTP settings are only for the newsletter.
- **The look of the sign-in pages and mails** is the realm's `brand` in
  `pacod-api/docker/pacod-id/pacod-id.toml`. It must be one of pacod-id's built-in brands
  (`gateway`, `shop`, `bazaar`), or pacod-id refuses to start. The platform uses `gateway`: the
  base teal and gold, with the wordmark "pacod-gw · AI Gateway". pacod-id has no brand for the
  platform itself.
- **Open-WebUI** calls the API as the service account `pacod-webui` (role
  `pacod.webui_service`), with its `pidt_` token from `make id-bootstrap`, and forwards the person
  in `X-OpenWebUI-User-*` headers. The API trusts those headers only from that account.
  - `make id-bootstrap` is idempotent and recreates Open-WebUI when the token changed.
  - Compose makes Open-WebUI's environment its configuration (`ENABLE_PERSISTENT_CONFIG=False`).
    Settings changed in its admin panel last until the next restart.

## Monitoring

`make monitor-up` (needs `monitoring/.env`, from `.env.example`). Prometheus scrapes the app,
pacod-id (`pacod-id:9170`) and the gateway's core (`gw-core:9100` over `pacod-link`, `instance=core`).
Grafana (http://127.0.0.1:3300/monitoring, embedded in the admin console's Metrics page in
production) has the app's dashboards in the folder **pacod** and the gateway's in **pacod-gw**.
Alertmanager is the platform's only one; the gateway's rules are `monitoring/prometheus/alerts/gw.rules.yml`.
Locally the app's Caddy does not route `/monitoring`, so the Metrics page's frames 404 — open
Grafana directly.

## Backup and restore

```bash
make backup                                    # stack up; → ./backups/pacod-backup-<UTC>.tar.gz (0600)
make restore ARCHIVE=backups/pacod-backup-<UTC>.tar.gz [ARGS='--yes --overwrite-config']
```

The archive holds PostgreSQL globals and dumps of `pacod`, `openwebui` and `pacod_id`; Qdrant
snapshots; the config and secrets (`.env`, `.env.gateway`, `deploy/secrets/`, the pacod-id and
gateway configuration, compose files, Caddyfiles, monitoring config, `data/`); the documents and
Open-WebUI volumes; with `REMOTE_HOST` set in `scripts/backup.env` the archive is also copied
there with scp. **The secrets must travel with the dumps**: `APP_SECRETS_KEY` decrypts the
admin-managed search keys and `PID_KEK` unseals pacod-id's signing keys. The archive is a secret.
Restore only previews config unless `--overwrite-config`; afterwards recreate the services that
read the restored env files (the script lists them). The gateway's own stores are backed up with
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
| Open-WebUI users see no model / "Model not found" | Open-WebUI's per-model grants; compose sets `BYPASS_MODEL_ACCESS_CONTROL=True` because the API authorizes each completion as the forwarded person. |
| A person exists at pacod-id but not on the Users page | The page lists the app's shadow rows; a row appears with the person's first request to the API. |
| `pacod` containers collide with another checkout | The original pacod uses the same compose project name and container names: do not run both on one Docker host. |
