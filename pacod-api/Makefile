# ===========================================================================
# pacod-api — backend task runner
#
# Drives the combined docker-compose stack (this directory's docker-compose.yml).
# API / backend tasks only — frontend and full-stack orchestration live in the
# repo-root Makefile.
# ===========================================================================

.PHONY: help upgrade-cpu upgrade-ui up-cpu up-gpu up-ui down-cpu down-ui restart rebuild-cpu \
        ensure-gateway-env recreate-gateway-clients recreate-gateway-clients-local gw-matrix-local \
        bootstrap seed-profile seed-example seed-example-local reset-prompt-overrides \
        indexnow-ping import-agents adopt-agents \
        ingest ingest-clean reingest ingest-cpu ingest-clean-cpu reingest-cpu \
        webui-up webui-down dev-install lint format test-unit test-integration eval \
        logs ps clean \
        up-local down-local rebuild-local restart-local bootstrap-local \
        logs-local ps-local clean-local smoke-local ensure-env ensure-env-local ensure-agent-net \
        ensure-pacod-link ensure-secrets ensure-id-image id-image secret-set

# --- Local test stack ------------------------------------------------------
# The committed compose targets the server (TLS on real hostnames, /data/**
# bind mounts). docker-compose.local.yml overlays the laptop-safe equivalents;
# see its header and docker/caddy/Caddyfile.local.
#
# Two things this variable must get right:
#   * Passing -f DISABLES the auto-loaded docker-compose.override.yml. That is
#     intentional — the overlay supersedes it — but it means every local target
#     must go through $(COMPOSE_LOCAL), never a bare `docker compose`.
#   * --env-file feeds ${VAR} INTERPOLATION (the LOCAL_* knobs), which is a
#     different channel from the services' own `env_file:`. Both are needed,
#     and the later file wins in both.
COMPOSE_LOCAL := docker compose -f docker-compose.yml -f docker-compose.local.yml \
                   --env-file .env --env-file .env.local --env-file .env.gateway
# The server stack. The gateway keys (.env.gateway, written by `make gw-bootstrap` in the repo
# root) reach each service through ${GATEWAY_KEY_*} INTERPOLATION in docker-compose.yml, one
# key per service; the file is never an env_file of any service. With an explicit --env-file
# Compose no longer reads .env by itself, hence both.
COMPOSE := docker compose --env-file .env --env-file .env.gateway
LOCAL_PROFILES := --profile cpu --profile tools --profile ui

help:
	@echo "pacod-api — backend tasks"
	@echo ""
	@echo "  make up-ui              Start the UI only (frontend, no backend)"
	@echo "  make up-cpu             Start the stack (CPU-only, no GPU devices)"
	@echo "  make up-gpu             Start the stack with AMD GPU support"
	@echo "  make down-ui            Stop the UI only (frontend, no backend)"
	@echo "  make down-cpu           Stop the stack (preserves volumes)"
	@echo "  make restart            Restart the api container only"
	@echo "  make rebuild-cpu        Rebuild + restart the api container only"
	@echo "  make recreate-gateway-clients  Recreate the services that call the gateway (new keys)"
	@echo "  make bootstrap          init_db + the starter agent's reference facts + corpus"
	@echo "  make seed-profile PROFILE=<id>    Provision one agent from scripts/profiles/<id>.py"
	@echo "  make seed-example PROFILE=taxes    Install an example agent + ingest its knowledge base"
	@echo "  make reset-prompt-overrides [ARGS=--yes]  Drop stale loop-prompt overrides (previews by default)"
	@echo "  make import-agents [ARGS=\"--force <id>\"]  Import agents into the visual agent editor"
	@echo "  make adopt-agents       Hand every unmanaged agent to the agent editor (locks owned legacy settings)"
	@echo "  make indexnow-ping [ARGS=--dry-run]  Submit the site's sitemap to IndexNow"
	@echo "  make ingest [PROFILE=<id>]   Ingest a profile's data/profiles/<id>/ sources (GPU pipeline)"
	@echo "  make ingest-clean [PROFILE=<id>]   Drop a profile's KB collection + source rows"
	@echo "  make reingest [PROFILE=<id>]  Drop then re-ingest (GPU pipeline)"
	@echo "  make ingest-cpu [PROFILE=<id>]   Same, CPU pipeline"
	@echo "  make ingest-clean-cpu [PROFILE=<id>]   Drop via the CPU container"
	@echo "  make reingest-cpu [PROFILE=<id>]  Drop then re-ingest (CPU pipeline)"
	@echo "  make webui-up           Start Open-WebUI (http://localhost:3000)"
	@echo "  make webui-down         Stop Open-WebUI"
	@echo "  make dev-install        Install test + lint deps into local .venv"
	@echo "  make lint               ruff check + ruff format --check (via uvx)"
	@echo "  make format             ruff format + safe autofixes"
	@echo "  make test-unit          Run unit tests inside a temporary api container"
	@echo "  make secret-set NAME=X [FILE=.env]  Set one secret from stdin (never echoed)"
	@echo "  make test-integration   Run integration tests (stack must be up)"
	@echo "  make eval               Run golden-set evaluation"
	@echo "  make logs               Tail logs from all services"
	@echo "  make ps                 List service status"
	@echo "  make clean              Stop stack and DELETE all volumes"
	@echo ""
	@echo "  --- Local test stack (laptop; plain HTTP on ports, no TLS/certs) ---"
	@echo "  make up-local           Start the whole stack locally (api + both UIs + tools)"
	@echo "  make down-local         Stop the local stack (preserves volumes)"
	@echo "  make restart-local      Restart the local api container only"
	@echo "  make rebuild-local      Rebuild + restart the local api container only"
	@echo "  make bootstrap-local    init_db + starter facts + dev users + corpus (CPU pipeline)"
	@echo "  make seed-example-local PROFILE=taxes  Install an example agent locally"
	@echo "  make smoke-local        Check health + an SSE chat turn against localhost"
	@echo "  make logs-local         Tail local stack logs"
	@echo "  make ps-local           List local service status"
	@echo "  make clean-local        Stop the local stack and DELETE ALL volumes (incl. the database)"

upgrade-cpu: down-cpu up-cpu

upgrade-ui: down-ui up-ui

up-ui:
	# Naming the service explicitly rather than relying on the profile keeps this
	# target's blast radius obvious.
	$(COMPOSE) --profile ui up -d --build frontend
	@echo ""
	@echo "Waiting for services to be healthy..."
	@sleep 5
	$(COMPOSE) ps

up-cpu: ensure-env ensure-agent-net ensure-pacod-link ensure-secrets ensure-id-image
	$(COMPOSE) --profile tools --profile cpu up -d --build
	@echo ""
	@echo "Waiting for services to be healthy..."
	@sleep 5
	$(COMPOSE) ps

up-gpu: ensure-env ensure-agent-net ensure-pacod-link ensure-secrets ensure-id-image
	$(COMPOSE) -f docker-compose.yml -f docker-compose.gpu.yml --profile tools --profile gpu up -d --build
	@echo ""
	@echo "Waiting for services to be healthy..."
	@sleep 5
	$(COMPOSE) ps

down-ui:
	$(COMPOSE) --profile ui down

down-cpu:
	$(COMPOSE) --profile tools --profile cpu down

restart:
	$(COMPOSE) restart api

rebuild-cpu:
	$(COMPOSE) --profile tools --profile cpu up -d --build api

# The services that call the gateway read their key when the container is CREATED: after
# `make gw-bootstrap` issued new keys, recreate them (a plain restart keeps the old key).
GATEWAY_CLIENTS := api admin-api improvement-loop-worker newsletter-worker
recreate-gateway-clients: ensure-env
	$(COMPOSE) up -d --no-build --no-deps $(GATEWAY_CLIENTS)

recreate-gateway-clients-local: ensure-env-local
	$(COMPOSE_LOCAL) up -d --no-build --no-deps $(GATEWAY_CLIENTS)

recreate-webui: ensure-env
	$(COMPOSE) up -d --no-build --no-deps open-webui

recreate-webui-local: ensure-env-local
	$(COMPOSE_LOCAL) up -d --no-build --no-deps open-webui

# Every model the gateway grants × thinking × prompt cache × native search, through the real
# client: lists what the gateway refuses (scripts/gw_request_matrix.py). Free on the mock.
gw-matrix-local: ensure-env-local
	$(COMPOSE_LOCAL) exec -T api python -m scripts.gw_request_matrix


# First run: the schema, then the default (starter) agent's reference facts and
# knowledge base. The api seeds the starter profile row itself at boot.
# The ingestion container that runs: `ingestion` (GPU pipeline, make up-gpu) when it is up,
# else `ingestion-cpu` (make up-cpu). Both answer as http://ingestion:8000; only `exec`
# needs the service's own name.
INGESTION_SVC ?= $(shell docker inspect -f '{{.State.Running}}' pacod-ingestion 2>/dev/null | grep -q true && echo ingestion || echo ingestion-cpu)

bootstrap:
	$(COMPOSE) exec api python -m scripts.init_db
	$(COMPOSE) exec api python -m scripts.seed_reference_facts --profile starter
	$(COMPOSE) exec $(INGESTION_SVC) python -m scripts.ingest_corpus --profile starter
	@echo ""
	@echo "Bootstrap complete. Optional example agent: make seed-example PROFILE=taxes"

# Provision one profile (agent) from scripts/profiles/<id>.py: profile row,
# collections, prompt + settings overrides, reference facts.
seed-profile:
	@test -n "$(PROFILE)" || { echo "usage: make seed-profile PROFILE=<id>   (e.g. taxes)"; exit 1; }
	$(COMPOSE) exec api python -m scripts.seed_profile $(PROFILE)

# An example agent end to end: its profile spec, then its knowledge base
# (data/profiles/<id>/{corpus,links.md,documents}).
seed-example: seed-profile
	$(COMPOSE) exec $(INGESTION_SVC) python -m scripts.ingest_corpus --profile $(PROFILE)

# Stale per-profile prompt overrides hide a changed code default; previews unless
# ARGS=--yes. Runs in `api` (the loop image ships no scripts/).
reset-prompt-overrides:
	$(COMPOSE) exec api python -m scripts.reset_prompt_overrides $(ARGS)

# Visual agent editor (app/agent_editor): import = draft + version 1 per profile
# (insert-only, also done at every boot); adopt = publish the current config as
# a release so the editor owns it (legacy config pages then lock owned keys).
import-agents:
	$(COMPOSE) exec api python -m scripts.agent_editor import $(ARGS)

adopt-agents:
	$(COMPOSE) exec api python -m scripts.agent_editor adopt

# Post-deploy: tell Bing/Yandex/Seznam/Naver the public pages changed. Runs on
# the HOST (stdlib only) and reads SITE_URL + INDEXNOW_KEY from each running
# frontend container. A site without a key is skipped. ARGS="--dry-run".
indexnow-ping:
	python3 scripts/indexnow_ping.py $(ARGS)

# Corpus ingestion is per profile: PROFILE=<id> (blank = the default profile)
# reads data/profiles/<id>/{corpus,links.md,documents} into that profile's
# collections. The profile row must exist (make seed-profile / admin UI).
PROFILE_ARG = $(if $(PROFILE),--profile $(PROFILE),)

ingest:
	$(COMPOSE) exec ingestion python -m scripts.ingest_corpus $(PROFILE_ARG)

ingest-clean:
	$(COMPOSE) exec ingestion python -m scripts.drop_collection $(PROFILE_ARG)

reingest: ingest-clean ingest

ingest-cpu:
	$(COMPOSE) exec ingestion-cpu python -m scripts.ingest_corpus $(PROFILE_ARG)

ingest-clean-cpu:
	$(COMPOSE) exec ingestion-cpu python -m scripts.drop_collection $(PROFILE_ARG)

reingest-cpu: ingest-clean-cpu ingest-cpu

webui-up:
	$(COMPOSE) --profile tools up -d open-webui
	@echo "Open-WebUI available at http://localhost:3000"

webui-down:
	$(COMPOSE) --profile tools stop open-webui
	docker rm pacod-openwebui || true

# A local test venv: the runtime deps (requirements-base.txt), the test + lint deps, and the
# vendored pacod_id wheel. The images run Python 3.13; use the same python3.
dev-install:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements-base.txt -r requirements-dev.txt
	.venv/bin/pip install --no-deps --no-index vendor/wheels/pacod_id-*.whl

# Lint + format gate (ruff owns both). Run via uv so it works whether or not the
# local .venv has ruff installed; the version is pinned in requirements-dev.txt.
lint:
	uvx ruff@0.7.1 check .
	uvx ruff@0.7.1 format --check .

format:
	uvx ruff@0.7.1 format .
	uvx ruff@0.7.1 check --fix .

# Unit tests need no services. `--no-deps` stops Compose from STARTING them, but
# when the stack is already up the container still joins agent-net and reaches the
# live Postgres/Redis, so a test that assumes "no DB" picks up rows other tests
# left. Point the service hosts at an unroutable port so every DB/Redis touch
# fails fast and the suite is hermetic regardless of stack state.
test-unit: ensure-env ensure-agent-net
	$(COMPOSE) run --rm --no-deps \
	  -e POSTGRES_HOST=127.0.0.1 -e POSTGRES_PORT=1 \
	  -e REDIS_URL=redis://127.0.0.1:1/0 \
	  api python -m pytest tests/unit -v

test-integration:
	$(COMPOSE) exec \
	  -e INTEGRATION_API_BASE=http://localhost:8000/api/v1 \
	  -e INTEGRATION_HEALTH=http://localhost:8000/health \
	  -e INTEGRATION_MCP_BASE=http://mcp-server:8000 \
	  api python -m pytest tests/integration -v -m integration

# Golden-set eval of one profile (PROFILE=<id>, blank = the default profile):
# data/profiles/<id>/golden_qa.yaml, asked of that profile. ARGS for the rest,
# e.g. ARGS="--source both".
eval:
	$(COMPOSE) exec api python -m scripts.run_eval $(PROFILE_ARG) $(ARGS)

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

clean:
	$(COMPOSE) down -v

# ===========================================================================
# Local test stack
#
# Everything below drives docker-compose.yml + docker-compose.local.yml. It is
# for running the platform on a development machine to try changes end to end;
# it terminates no TLS and publishes on high ports, so it is not a deployment.
#
#   http://localhost:8080  main shell        http://localhost:3000  Open-WebUI
#   http://localhost:3001  admin-api         http://localhost:8081  Adminer
#   http://localhost:6333  Qdrant
# ===========================================================================

# Every service reads `.env` through `env_file:`, and the local targets also pass
# it as --env-file, so Compose refuses to start without it. Seed it from the
# example once; never overwrite it, and never print it (it holds secrets).
ensure-env:
	@test -f .env || { \
		cp .env.example .env; chmod 600 .env; \
		echo "Created .env from .env.example — set real secrets with: make secret-set NAME=<VAR>"; \
	}
	@../scripts/env-init.sh .env
	@$(MAKE) --no-print-directory ensure-gateway-env

# The gateway keys (pacod-api/.env.gateway, 0600, gitignored). Compose reads it as --env-file,
# which must exist, so an empty one stands in until `make gw-bootstrap` fills it; the services
# then start with blank keys and their LLM calls answer 401.
ensure-gateway-env:
	@test -f .env.gateway || { (umask 077; : > .env.gateway); echo "Created an empty .env.gateway — fill it with: make gw-bootstrap (repo root)"; }

# Write one secret into an env file from stdin (typed hidden, or piped), so the
# value never lands in a terminal, the shell history or a process list.
#   make secret-set NAME=ANTHROPIC_API_KEY            -> .env
#   make secret-set NAME=X FILE=.env.local
secret-set:
	@test -n "$(NAME)" || { echo "usage: make secret-set NAME=<VAR> [FILE=.env]"; exit 1; }
	@../scripts/secret-set.sh $(or $(FILE),.env) $(NAME)

# `.env.local` carries the local-only overrides (see .env.local.example). It is
# gitignored and machine-specific, so seed it rather than requiring a manual
# step — and never overwrite an existing one. --env-file also *requires* the
# file to exist, so this cannot be left to the developer to remember.
ensure-env-local: ensure-env
	@test -f .env.local || { \
		cp .env.local.example .env.local; chmod 600 .env.local; \
		echo "Created .env.local from .env.local.example — review it before going further."; \
	}

# agent-net is declared `external: true` (the monitoring compose joins the same
# network), so Compose will not create it. On a fresh checkout it does not exist.
ensure-agent-net:
	@docker network inspect agent-net >/dev/null 2>&1 || docker network create agent-net

# pacod-link joins this stack to the gateway's (deploy/pacod-gw/docker-compose.link.yml):
# pacod-id, Caddy and, from Phase 4 on, the services that call the gateway. External for the
# same reason as agent-net.
ensure-pacod-link:
	@docker network inspect pacod-link >/dev/null 2>&1 || docker network create pacod-link

# pacod-id's and the gateway's secrets (deploy/secrets/*.env, gitignored, 0600). Idempotent:
# fills only what is missing, prints names only.
ensure-secrets:
	@../scripts/secrets-init.sh >/dev/null

# --- pacod-id image ----------------------------------------------------------
# Built from a pinned pacod-id tree: by default the copy pacod-gw vendors, the same tree the
# libraries in vendor/ came from (scripts/vendor-pacod-id.sh records its VERSION). The tag is
# the commit, so compose (PACOD_ID_IMAGE, default in docker-compose.yml) and the vendored
# libraries cannot drift apart unnoticed (tests/unit/test_pacod_id_config.py).
PACOD_ID_SRC ?= $(abspath ../../pacod-gw/vendor/pacod-id)
PACOD_ID_COMMIT := $(shell awk '/^commit/ {print substr($$2, 1, 7)}' vendor/PACOD_ID_VERSION 2>/dev/null)
export PACOD_ID_IMAGE ?= pacod-id:$(PACOD_ID_COMMIT)

# Build it when missing (a first build compiles Rust and the hosted UI: several minutes).
ensure-id-image:
	@docker image inspect $(PACOD_ID_IMAGE) >/dev/null 2>&1 || $(MAKE) --no-print-directory id-image

id-image:
	@test -f "$(PACOD_ID_SRC)/VERSION" || { echo "PACOD_ID_SRC=$(PACOD_ID_SRC) is not a pacod-id tree"; exit 1; }
	@cmp -s "$(PACOD_ID_SRC)/VERSION" vendor/PACOD_ID_VERSION || { \
		echo "$(PACOD_ID_SRC) is not the commit vendor/PACOD_ID_VERSION names:"; \
		echo "re-vendor the libraries first (../scripts/vendor-pacod-id.sh $(PACOD_ID_SRC))"; exit 1; }
	docker build -f "$(PACOD_ID_SRC)/deploy/docker/pacod-id.Dockerfile" --target pacod-id \
		-t $(PACOD_ID_IMAGE) "$(PACOD_ID_SRC)"

up-local: ensure-env-local ensure-agent-net ensure-pacod-link ensure-secrets ensure-id-image
	$(COMPOSE_LOCAL) $(LOCAL_PROFILES) up -d --build
	@echo ""
	@echo "Waiting for services to be healthy..."
	@sleep 5
	$(COMPOSE_LOCAL) ps
	@echo ""
	@echo "Main shell:     http://localhost:8080"
	@echo "First run? Populate the database next:  make bootstrap-local"

# Every target below needs .env.local to EXIST, not just up-local: `--env-file`
# is a hard requirement, so without it even `make ps-local` dies with
# "couldn't find env file". Hence ensure-env-local on all of them.
down-local: ensure-env-local
	$(COMPOSE_LOCAL) $(LOCAL_PROFILES) down

restart-local: ensure-env-local
	$(COMPOSE_LOCAL) restart api

rebuild-local: ensure-env-local
	$(COMPOSE_LOCAL) $(LOCAL_PROFILES) up -d --build api

# Same steps as `bootstrap`, but ingestion runs in `ingestion-cpu`: the GPU
# `ingestion` container is behind the gpu profile and is not started here.
bootstrap-local: ensure-env-local
	$(COMPOSE_LOCAL) exec api python -m scripts.init_db
	$(COMPOSE_LOCAL) exec api python -m scripts.seed_reference_facts --profile starter
	$(COMPOSE_LOCAL) exec api python -m scripts.seed_dev_users
	$(COMPOSE_LOCAL) exec ingestion-cpu python -m scripts.ingest_corpus --profile starter
	@echo ""
	@echo "Bootstrap complete. Verify with: make smoke-local"
	@echo "Optional example agent: make seed-example-local PROFILE=taxes"

seed-example-local: ensure-env-local
	@test -n "$(PROFILE)" || { echo "usage: make seed-example-local PROFILE=<id>   (e.g. taxes)"; exit 1; }
	$(COMPOSE_LOCAL) exec api python -m scripts.seed_profile $(PROFILE)
	$(COMPOSE_LOCAL) exec ingestion-cpu python -m scripts.ingest_corpus --profile $(PROFILE)

# Health through the edge + one real streamed chat turn.
#
# The chat turn runs INSIDE the api container, not through localhost:8080, and
# that is not a shortcut. Caddy sends /api/* to SvelteKit, whose handleApiProxy
# (hooks.server.ts) 401s any /api/ request that arrives without an app session
# cookie and otherwise OVERWRITES Authorization from the session — so a
# `Bearer dev-token-…` header sent at the edge never reaches Python. Exercising
# the dev token means talking to the API directly. The /health checks stay on
# the edge because Caddy proxies /health to api:8000 without touching SvelteKit,
# which makes them a genuine end-to-end test of the local edge.
# The edge's /health and one real chat turn of the starter agent (../scripts/smoke_local.sh:
# scripts the gateway mock's replies when the mock is the provider; fails unless the stream
# reaches `event: done`).
smoke-local: ensure-env-local
	@../scripts/smoke_local.sh

logs-local: ensure-env-local
	$(COMPOSE_LOCAL) logs -f --tail=100

ps-local: ensure-env-local
	$(COMPOSE_LOCAL) ps

# DESTRUCTIVE: -v deletes every volume in the project, which includes
# postgres_data, documents_data and qdrant_data — not just the log volumes
# the local overlay adds. Same semantics as `clean`.
clean-local: ensure-env-local
	$(COMPOSE_LOCAL) $(LOCAL_PROFILES) down -v
