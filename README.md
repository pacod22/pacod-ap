# pacod-ap — an agent-building platform

pacod-ap hosts any number of self-contained AI agents (**profiles**) on one deployment. Each
agent has its own knowledge base, reference facts, prompts, directives, runtime configuration,
conversations and reviews, behind one sign-in and one admin console. It started as a fork of
*pacod* (a Bulgarian tax assistant) and keeps its structure and names; the tax assistant ships as
an advanced example.

| | |
|---|---|
| **Default agent** | `starter` — a generic template: a small fictional help desk ("Nimbus Notes") that exercises the knowledge base and the reference-facts tools |
| **Example agent** | `taxes` — Bulgarian tax & accounting, with its own prompts, facts, law corpus and web-search allow-list (`make seed-example PROFILE=taxes`) |
| **LLM calls** | through **pacod-gw**, the platform's LLM gateway: models, prices, provider keys, token limits, usage, cost and LLM metrics are the gateway's |
| **Sign-in** | through **pacod-id**, the platform's identity provider: accounts, passwords, sessions, roles; the app and the gateway console share one account |
| **Languages** | Bulgarian and English for the agents (configurable: `PLATFORM_LANGUAGES`, per-agent `languages`); the UI in en/bg/de/ru |

## The pieces

```
pacod-ap/
├── pacod-api/      FastAPI + LangGraph: the chat API, admin API, MCP tools, ingestion, embeddings,
│                   the improvement loop and newsletter workers, the visual agent editor
├── pacod-ui/       SvelteKit: the chat shell and the admin console (a BFF on pacod-id)
├── monitoring/     Prometheus, Alertmanager, Grafana (incl. the gateway's dashboards and alerts)
├── platform/       pacod-ap's shares of the shared configuration: Caddy sites, database, scrape jobs
├── deploy/         the gateway's platform overrides and configuration; secrets (gitignored)
├── scripts/        bootstrap (gateway, identity), secrets, backup and restore
└── docs/           RUNBOOK, LLM_GATEWAY, BUILD_YOUR_OWN_AGENT
```

Three compose stacks, started in this order (`make up-all` does it):

1. **The app** (project `pacod`): `pacod-api/docker-compose.yml`, the app file with pacod-ap's own
   services (`ap-api`, `ap-frontend`, the workers, `ap-redis`, `ap-pgbouncer`, and the shared
   `pacod-embeddings`), plus `pacod-api/docker-compose.standalone.yml`, its copies of the shared
   components: `pacod-caddy`, `pacod-postgres` 18.6 with `pacod-db-init`, `pacod-qdrant`,
   **`pacod-id`** (and `pacod-mailpit` locally).
2. **The gateway** (project `pacod-ap-gw`): pacod-gw's app file only, from a pacod-gw checkout at
   `GW_DIR` (default `../pacod-gw`), with `deploy/pacod-gw/*.yml`. It signs in through the
   platform's pacod-id, keeps its data in the platform's `pacod-postgres`, and is served by its Caddy.
3. **Monitoring** (`monitoring/`): `pacod-prometheus`, `pacod-alertmanager`, `pacod-grafana` and
   the exporters; optional, and started by `make up-all` when `monitoring/.env` exists.

## Shared network

Every container joins one external Docker network, **`pacod-net`**, and has one name on it: the
compose service key, the container name and the DNS name are the same. Shared components (used by
more than one product) are named `pacod-*`, everything of pacod-ap's own `ap-*`; volumes follow the
same rule. Each repository's compose is split in two — the app file (its own services) and the
standalone file (its copies of the shared components) — so the full stack in **pacod-demo** can
load the app file and run every shared component once. pacod-ap's shares of the shared
configuration are under `platform/`: the Caddy sites (`platform/caddy/ap.caddy`), the database
role and databases (`platform/postgres/ap-db.sh`) and the scrape jobs
(`platform/prometheus/ap.yml`). The convention is
[pacod-demo/docs/NETWORK.md](../pacod-demo/docs/NETWORK.md). Every `make up*` target creates the
network when it is missing (`make ensure-pacod-net`).

The listeners that only servers reach are on a second external network, **`pacod-admin-net`**, which
is internal (no route out): the gateway's management API, bound to `pacod-gw-admin:8081`, and
pacod-id's internal listener, bound to `pacod-id-internal:8071`, each an alias its service has there
alone. Their callers join it too (`ap-frontend`, `ap-api`, `ap-admin-api`; the gateway's `pacod-gw`,
`gw-console`, `gw-analytics`, `gw-tester`) and use those aliases; the rest of `pacod-net` cannot
reach either. `make ensure-pacod-net` creates it as well.

## Requirements

pacod-ap runs next to two sibling projects, both Apache-2.0:

- **[pacod-gw](https://github.com/<github-owner>/pacod-gw)** — the LLM gateway. Check it out
  next to this repository (`GW_DIR`, default `../pacod-gw`); `make gw-up` runs it from there.
  Its `vendor/pacod-id` tree is what `make id-image` builds the pacod-id image from.
- **[pacod-id](https://github.com/<github-owner>/pacod-id)** — the identity provider. Its client
  libraries are vendored here (`pacod-api/vendor/wheels/`, `pacod-ui/frontend/vendor/`) at the
  commit named in `pacod-api/vendor/PACOD_ID_VERSION`; `scripts/vendor-pacod-id.sh` refreshes them.

```
<parent>/
├── pacod-ap/    this repository
└── pacod-gw/    the gateway (includes vendor/pacod-id)
```

On the host: Docker with Compose v2, GNU make, Python 3, `openssl`, `curl` and `jq`; Node 22 for
the frontend tests; a Rust toolchain for `make gw-smoke` (it builds the gateway's smoke client);
`rsync` and `uv` only to re-vendor pacod-id. Docker needs about 8 GB of memory and 20 GB of disk
for the full stack (the CPU ingestion image alone is ~10 GB; `LOCAL_EMBEDDINGS_BACKEND=hash` in
`pacod-api/.env.local` skips the 2 GB embedding model).

## Quick start (laptop)

```bash
make secrets-init          # deploy/secrets/{id,gw,providers}.env — prints names, never values
make up-all                # app + pacod-id, the gateway (mock providers), its bootstrap
make bootstrap-local       # schema, the starter agent's facts and corpus
make smoke-all             # health, a chat turn through the gateway, the gateway's own smoke test
```

Afterwards, without rebuilding everything:

```bash
make start-local                         # start again from the existing images (no build)
make rebuild-local SVC="ap-api ap-admin-api"   # rebuild + recreate only these (default ap-api)
make restart-local SVC=ap-api            # restart as is
make clean-local                         # delete the data; the downloaded models are kept (MODELS=1: those too)
```

The embedding models are downloaded once per host into the shared volume `pacod-hf-cache`; see
[docs/RUNBOOK.md, Re-deploying and restarting](docs/RUNBOOK.md#re-deploying-and-restarting) for the
server targets and the model cache.

| URL | |
|---|---|
| http://localhost:8080 | the app (sign in or create an account) |
| http://127.0.0.1:8070 | pacod-id (sign-in pages, account) |
| http://127.0.0.1:13000 | the gateway console (same account) |
| http://127.0.0.1:8025 | mailpit — every mail pacod-id sends locally |

The bootstrap admin is `PID_BOOTSTRAP_ADMIN_EMAIL` in `deploy/secrets/id.env`. Locally the gateway
answers with its **mock** providers (scripted replies, no cost); real providers, their keys and the
production layout are in [docs/RUNBOOK.md](docs/RUNBOOK.md#real-providers).

## Read next

- [docs/RUNBOOK.md](docs/RUNBOOK.md) — operating the three stacks: start order, secrets, bootstrap,
  configuration import, backup and restore, ports, troubleshooting.
- [docs/LLM_GATEWAY.md](docs/LLM_GATEWAY.md) — how the app uses pacod-gw: attribution, retries,
  quotas, usage reports, monitoring.
- [docs/BUILD_YOUR_OWN_AGENT.md](docs/BUILD_YOUR_OWN_AGENT.md) — a new agent: spec, reference
  facts, corpus, golden set, prompts.
- [pacod-api/CLAUDE.md](pacod-api/CLAUDE.md) and [pacod-ui/CLAUDE.md](pacod-ui/CLAUDE.md) — the
  developer references.

## Before you deploy

- **You are the operator.** The legal pages (terms, privacy, cookies, about, the footer imprint)
  name the operator from `pacod-ui/frontend/src/lib/legal/company.ts`, which ships with
  placeholders ("Example Operator Ltd", `legal@example.com`). Put your own legal entity there; the
  texts are written for an operator registered in Bulgaria, so adapt them if you are elsewhere.
- **Domains.** The production defaults in the compose files and Caddyfile are `www.pacod.io`,
  `id.pacod.io` and `console.pacod.io`; set `PACOD_SITE_URL`, `PACOD_ID_ISSUER` and
  the other `PACOD_*` values (see `pacod-api/.env.example`) and your own Caddy vhosts.
- **Account mail and sign-in pages** are pacod-id's: set its SMTP server and sender
  (`PID_SMTP_*`, `PID_EMAIL_FROM_*`) in `deploy/secrets/id.env`. The sign-in pages and mails use
  pacod-id's built-in `gateway` brand ([docs/RUNBOOK.md](docs/RUNBOOK.md#identity-pacod-id)).
- **Secrets** are generated, never committed: `make secrets-init` and `make ensure-env` write them
  to gitignored files. Report vulnerabilities as described in [SECURITY.md](SECURITY.md).

## Example data

The `taxes` example bundles public sources as its corpus (`pacod-api/data/profiles/taxes/`):
Bulgarian legal acts, official guidance and EU documents, in their published form. They are there
to show ingestion and retrieval on a real body of law. They are not kept up to date, and nothing
the example agent says is legal or tax advice.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
