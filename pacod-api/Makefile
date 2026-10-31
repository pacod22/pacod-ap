# ===========================================================================
# pacod-api — backend task runner
#
# Drives the combined docker-compose stack: this directory's docker-compose.yml (the app file,
# pacod-ap's own services) with docker-compose.standalone.yml (its copies of the shared
# components: pacod-caddy, pacod-postgres, pacod-db-init, pacod-qdrant, pacod-id), all on the
# external network pacod-net (pacod-demo/docs/NETWORK.md).
# API / backend tasks only — frontend and full-stack orchestration live in the
# repo-root Makefile.
# ===========================================================================

.PHONY: help upgrade-cpu upgrade-ui up-cpu up-gpu up-ui down-cpu down-ui restart rebuild-cpu \
        start-cpu start-local rebuild-embeddings rebuild-embeddings-local \
        ensure-hf-cache clean-models \
        ensure-gateway-env recreate-gateway-clients recreate-gateway-clients-local gw-matrix-local \
        bootstrap seed-profile seed-example seed-example-local reset-prompt-overrides \
        indexnow-ping import-agents adopt-agents \
        ingest ingest-clean reingest ingest-cpu ingest-clean-cpu reingest-cpu \
        dev-install lint format test-unit test-integration eval \
        logs ps clean \
        up-local down-local rebuild-local restart-local bootstrap-local \
        logs-local ps-local clean-local smoke-local ensure-env ensure-env-local ensure-pacod-net \
        ensure-secrets ensure-id-image id-image secret-set

# --- Local test stack ------------------------------------------------------
# The committed compose targets the server (TLS on real hostnames, /data/**
# bind mounts). docker-compose.local.yml (the app file's services) and
# docker-compose.standalone.local.yml (the shared components') overlay the
# laptop-safe equivalents; see their headers and docker/caddy/Caddyfile.local.
#
# Two things this variable must get right:
#   * Passing -f DISABLES the auto-loaded docker-compose.override.yml. That is
#     intentional — the overlay supersedes it — but it means every local target
#     must go through $(COMPOSE_LOCAL), never a bare `docker compose`.
#   * AP_EXTRA_FILES adds compose files (here and to the server's $(COMPOSE)): the repo
#     root's up targets pass ../deploy/pacod-gw/docker-compose.gw-db.yml when a pacod-gw
#     checkout is there, which hands pacod-gw's database fragment to pacod-db-init.
#   * --env-file feeds ${VAR} INTERPOLATION (the LOCAL_* knobs), which is a
#     different channel from the services' own `env_file:`. Both are needed,
#     and the later file wins in both.
AP_EXTRA_FILES ?=
COMPOSE_LOCAL = docker compose -f docker-compose.yml -f docker-compose.local.yml \
                   -f docker-compose.standalone.yml -f docker-compose.standalone.local.yml \
                   $(AP_EXTRA_FILES) \
                   --env-file .env --env-file .env.local --env-file .env.gateway
# The server stack. The gateway keys (.env.gateway, written by `make gw-bootstrap` in the repo
# root) reach each service through ${GATEWAY_KEY_*} INTERPOLATION in docker-compose.yml, one
# key per service; the file is never an env_file of any service. With an explicit --env-file
# Compose no longer reads .env by itself, hence both. With an explicit -f it no longer reads a
# server's docker-compose.override.yml either, hence the third file when there is one.
COMPOSE_FILES := -f docker-compose.yml -f docker-compose.standalone.yml \
                 $(if $(wildcard docker-compose.override.yml),-f docker-compose.override.yml)
COMPOSE = docker compose $(COMPOSE_FILES) $(AP_EXTRA_FILES) --env-file .env --env-file .env.gateway
LOCAL_PROFILES := --profile cpu --profile tools --profile ui

help:
	@echo "pacod-api — backend tasks"
	@echo ""
	@echo "  make up-ui              Start the UI only (frontend, no backend)"
	@echo "  make up-cpu             Start the stack (CPU-only, no GPU devices)"
	@echo "  make up-gpu             Start the stack with AMD GPU support"
	@echo "  make down-ui            Stop the UI only (frontend, no backend)"
	@echo "  make down-cpu           Stop the stack (preserves volumes)"
	@echo "  make start-cpu          Start the stack from the existing images (no build)"
	@echo "  make restart [SVC=...]  Restart services (default ap-api)"
	@echo "  make rebuild-cpu [SVC=...]  Rebuild + recreate services only (default ap-api; deps untouched)"
	@echo "  make rebuild-embeddings Rebuild + recreate pacod-embeddings only"
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
	@echo "  make dev-install        Install test + lint deps into local .venv"
	@echo "  make lint               ruff check + ruff format --check (via uvx)"
	@echo "  make format             ruff format + safe autofixes"
	@echo "  make test-unit          Run unit tests inside a temporary ap-api container"
	@echo "  make secret-set NAME=X [FILE=.env]  Set one secret from stdin (never echoed)"
	@echo "  make test-integration   Run integration tests (stack must be up)"
	@echo "  make eval               Run golden-set evaluation"
	@echo "  make logs               Tail logs from all services"
	@echo "  make ps                 List service status"
	@echo "  make clean [MODELS=1]   Stop stack and DELETE all volumes (the models only with MODELS=1)"
	@echo ""
	@echo "  --- Local test stack (laptop; plain HTTP on ports, no TLS/certs) ---"
	@echo "  make up-local           Start the whole stack locally (api + both UIs + tools)"
	@echo "  make down-local         Stop the local stack (preserves volumes)"
	@echo "  make start-local        Start the local stack from the existing images (no build)"
	@echo "  make restart-local [SVC=...]  Restart local services (default ap-api)"
	@echo "  make rebuild-local [SVC=...]  Rebuild + recreate local services only (default ap-api; deps untouched)"
	@echo "  make rebuild-embeddings-local  Rebuild + recreate the local pacod-embeddings only"
	@echo "  make bootstrap-local    init_db + starter facts + dev users + corpus (CPU pipeline)"
	@echo "  make seed-example-local PROFILE=taxes  Install an example agent locally"
	@echo "  make smoke-local        Check health + an SSE chat turn against localhost"
	@echo "  make logs-local         Tail local stack logs"
	@echo "  make ps-local           List local service status"
	@echo "  make clean-local [MODELS=1]  Stop the local stack and DELETE ALL volumes (incl. the database; the models only with MODELS=1)"

upgrade-cpu: down-cpu up-cpu

upgrade-ui: down-ui up-ui

up-ui: ensure-pacod-net
	# Naming the service explicitly rather than relying on the profile keeps this
	# target's blast radius obvious.
	$(COMPOSE) --profile ui up -d --build ap-frontend
	@echo ""
	@echo "Waiting for services to be healthy..."
	@sleep 5
	$(COMPOSE) ps

up-cpu: ensure-env ensure-pacod-net ensure-secrets ensure-id-image
	$(COMPOSE) --profile tools --profile cpu up -d --build
	@echo ""
	@echo "Waiting for services to be healthy..."
	@sleep 5
	$(COMPOSE) ps

up-gpu: ensure-env ensure-pacod-net ensure-secrets ensure-id-image
	$(COMPOSE) -f docker-compose.gpu.yml --profile tools --profile gpu up -d --build
	@echo ""
	@echo "Waiting for services to be healthy..."
	@sleep 5
	$(COMPOSE) ps

down-ui:
	$(COMPOSE) --profile ui down

down-cpu:
	$(COMPOSE) --profile tools --profile cpu down

# --- Fast re-deploys. SVC names the services (default ap-api; several: SVC="ap-api ap-admin-api").
# --no-deps: compose would otherwise build and recreate what they depend on too, pacod-embeddings
# among them, whose image also changes with app/core/{metrics,log_schema}.py; a recreated
# embeddings container reloads its models and every service waiting on it waits too.
# start-* bring the whole stack up from the images there are, building nothing.
SVC ?= ap-api

restart:
	$(COMPOSE) restart $(SVC)

rebuild-cpu: ensure-pacod-net
	$(COMPOSE) --profile tools --profile cpu up -d --no-deps --build $(SVC)

start-cpu: ensure-env ensure-pacod-net ensure-secrets ensure-id-image
	$(COMPOSE) --profile tools --profile cpu up -d --no-build
	$(COMPOSE) ps

# The embeddings service alone, after a change to embeddings_service/ or its Dockerfile.
rebuild-embeddings: ensure-pacod-net
	$(COMPOSE) up -d --no-deps --build pacod-embeddings

# The services that call the gateway read their key when the container is CREATED: after
# `make gw-bootstrap` issued new keys, recreate them (a plain restart keeps the old key).
GATEWAY_CLIENTS := ap-api ap-admin-api ap-improvement-loop-worker ap-newsletter-worker
recreate-gateway-clients: ensure-env
	$(COMPOSE) up -d --no-build --no-deps $(GATEWAY_CLIENTS)

recreate-gateway-clients-local: ensure-env-local
	$(COMPOSE_LOCAL) up -d --no-build --no-deps $(GATEWAY_CLIENTS)

# Every model the gateway grants × thinking × prompt cache × native search, through the real
# client: lists what the gateway refuses (scripts/gw_request_matrix.py). Free on the mock.
gw-matrix-local: ensure-env-local
	$(COMPOSE_LOCAL) exec -T ap-api python -m scripts.gw_request_matrix


# First run: the schema, then the default (starter) agent's reference facts and
# knowledge base. The api seeds the starter profile row itself at boot.
# The ingestion container that runs: `ap-ingestion` (GPU pipeline, make up-gpu) when it is up,
# else `ap-ingestion-cpu` (make up-cpu). Both answer as http://ap-ingestion:8000; only `exec`
# needs the service's own name.
INGESTION_SVC ?= $(shell docker inspect -f '{{.State.Running}}' ap-ingestion 2>/dev/null | grep -q true && echo ap-ingestion || echo ap-ingestion-cpu)

bootstrap:
	$(COMPOSE) exec ap-api python -m scripts.init_db
	$(COMPOSE) exec ap-api python -m scripts.seed_reference_facts --profile starter
	$(COMPOSE) exec $(INGESTION_SVC) python -m scripts.ingest_corpus --profile starter
	@echo ""
	@echo "Bootstrap complete. Optional example agent: make seed-example PROFILE=taxes"

# Provision one profile (agent) from scripts/profiles/<id>.py: profile row,
# collections, prompt + settings overrides, reference facts.
seed-profile:
	@test -n "$(PROFILE)" || { echo "usage: make seed-profile PROFILE=<id>   (e.g. taxes)"; exit 1; }
	$(COMPOSE) exec ap-api python -m scripts.seed_profile $(PROFILE)

# An example agent end to end: its profile spec, then its knowledge base
# (data/profiles/<id>/{corpus,links.md,documents}).
seed-example: seed-profile
	$(COMPOSE) exec $(INGESTION_SVC) python -m scripts.ingest_corpus --profile $(PROFILE)

# Stale per-profile prompt overrides hide a changed code default; previews unless
# ARGS=--yes. Runs in `ap-api` (the loop image ships no scripts/).
reset-prompt-overrides:
	$(COMPOSE) exec ap-api python -m scripts.reset_prompt_overrides $(ARGS)

# Visual agent editor (app/agent_editor): import = draft + version 1 per profile
# (insert-only, also done at every boot); adopt = publish the current config as
# a release so the editor owns it (legacy config pages then lock owned keys).
import-agents:
	$(COMPOSE) exec ap-api python -m scripts.agent_editor import $(ARGS)

adopt-agents:
	$(COMPOSE) exec ap-api python -m scripts.agent_editor adopt

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
	$(COMPOSE) exec ap-ingestion python -m scripts.ingest_corpus $(PROFILE_ARG)

ingest-clean:
	$(COMPOSE) exec ap-ingestion python -m scripts.drop_collection $(PROFILE_ARG)

reingest: ingest-clean ingest

ingest-cpu:
	$(COMPOSE) exec ap-ingestion-cpu python -m scripts.ingest_corpus $(PROFILE_ARG)

ingest-clean-cpu:
	$(COMPOSE) exec ap-ingestion-cpu python -m scripts.drop_collection $(PROFILE_ARG)

reingest-cpu: ingest-clean-cpu ingest-cpu

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
# when the stack is already up the container still joins pacod-net and reaches the
# live Postgres/Redis, so a test that assumes "no DB" picks up rows other tests
# left. Point the service hosts at an unroutable port so every DB/Redis touch
# fails fast and the suite is hermetic regardless of stack state.
test-unit: ensure-env ensure-pacod-net
	$(COMPOSE) run --rm --no-deps \
	  -e POSTGRES_HOST=127.0.0.1 -e POSTGRES_PORT=1 \
	  -e REDIS_URL=redis://127.0.0.1:1/0 \
	  ap-api python -m pytest tests/unit -v

test-integration:
	$(COMPOSE) exec \
	  -e INTEGRATION_API_BASE=http://localhost:8000/api/v1 \
	  -e INTEGRATION_HEALTH=http://localhost:8000/health \
	  -e INTEGRATION_MCP_BASE=http://ap-mcp:8000 \
	  ap-api python -m pytest tests/integration -v -m integration

# Golden-set eval of one profile (PROFILE=<id>, blank = the default profile):
# data/profiles/<id>/golden_qa.yaml, asked of that profile. ARGS for the rest,
# e.g. ARGS="--source both".
eval:
	$(COMPOSE) exec ap-api python -m scripts.run_eval $(PROFILE_ARG) $(ARGS)

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

clean:
	$(COMPOSE) down -v
	@$(if $(filter 1,$(MODELS)),$(MAKE) --no-print-directory clean-models,echo "clean: the models ($(HF_CACHE)) are kept; MODELS=1 deletes them")

# ===========================================================================
# Local test stack
#
# Everything below drives docker-compose.yml + docker-compose.local.yml with the
# standalone files (docker-compose.standalone.yml + .standalone.local.yml). It is
# for running the platform on a development machine to try changes end to end;
# it terminates no TLS and publishes on high ports, so it is not a deployment.
#
#   http://localhost:8080  main shell        http://localhost:3001  ap-admin-api
#   http://localhost:8081  Adminer           http://localhost:6333  Qdrant
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

# pacod-net, the one network every pacod stack joins (the gateway's, the monitoring module,
# the full stack: pacod-demo/docs/NETWORK.md), is declared `external: true`, so Compose will
# not create it. On a fresh machine it does not exist. Created once, kept by "down". So is
# pacod-admin-net, the internal network of the listeners only servers reach (the gateway's
# management API, pacod-id's internal one); compose never checks that it is internal.
ensure-pacod-net: ensure-hf-cache
	@docker network inspect pacod-net >/dev/null 2>&1 || docker network create pacod-net >/dev/null
	@docker network inspect pacod-admin-net >/dev/null 2>&1 || docker network create --internal pacod-admin-net >/dev/null
	@test "$$(docker network inspect -f '{{.Internal}}' pacod-admin-net)" = true || \
		{ echo "pacod-admin-net is not internal: remove it (docker network rm pacod-admin-net) and run make ensure-pacod-net" >&2; exit 1; }

# The external volume pacod-hf-cache (pacod-embeddings' models, shared with pacod-demo): made
# here, never by compose, so no project's `down -v` deletes it. The first time, the models of
# the project-owned volume it replaces (pacod_pacod-embeddings-hf-cache) are copied over.
HF_CACHE := pacod-hf-cache
HF_CACHE_OLD := pacod_pacod-embeddings-hf-cache
ensure-hf-cache:
	@docker volume inspect $(HF_CACHE) >/dev/null 2>&1 || { \
		docker volume create $(HF_CACHE) >/dev/null; \
		if docker volume inspect $(HF_CACHE_OLD) >/dev/null 2>&1; then \
			echo "ensure-hf-cache: copying the models from $(HF_CACHE_OLD) (remove it afterwards)"; \
			docker run --rm -v $(HF_CACHE_OLD):/from:ro -v $(HF_CACHE):/to alpine:3.20 cp -a /from/. /to/ || \
				{ docker volume rm $(HF_CACHE) >/dev/null; echo "ensure-hf-cache: the copy failed; run it again" >&2; exit 1; }; \
		fi; }

# Only `make clean[-local] MODELS=1` deletes the models; every other teardown keeps them.
clean-models:
	@if [ -n "$$(docker ps -aq --filter volume=$(HF_CACHE))" ]; then \
		echo "clean-models: $(HF_CACHE) is still mounted (stop pacod-embeddings first)" >&2; exit 1; fi
	@docker volume rm $(HF_CACHE) >/dev/null 2>&1 && echo "clean-models: $(HF_CACHE) removed" || true

# pacod-id's and the gateway's secrets (deploy/secrets/*.env, gitignored, 0600). Idempotent:
# fills only what is missing, prints names only.
ensure-secrets:
	@../scripts/secrets-init.sh >/dev/null

# --- pacod-id image ----------------------------------------------------------
# Built from a pinned pacod-id tree: by default the copy pacod-gw vendors, the same tree the
# libraries in vendor/ came from (scripts/vendor-pacod-id.sh records its VERSION). The tag is
# the commit, so compose (PACOD_ID_IMAGE, default in docker-compose.standalone.yml) and the vendored
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

up-local: ensure-env-local ensure-pacod-net ensure-secrets ensure-id-image
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
	$(COMPOSE_LOCAL) restart $(SVC)

rebuild-local: ensure-env-local ensure-pacod-net
	$(COMPOSE_LOCAL) $(LOCAL_PROFILES) up -d --no-deps --build $(SVC)

start-local: ensure-env-local ensure-pacod-net ensure-secrets ensure-id-image
	$(COMPOSE_LOCAL) $(LOCAL_PROFILES) up -d --no-build
	$(COMPOSE_LOCAL) ps

rebuild-embeddings-local: ensure-env-local ensure-pacod-net
	$(COMPOSE_LOCAL) up -d --no-deps --build pacod-embeddings

# Same steps as `bootstrap`, but ingestion runs in `ap-ingestion-cpu`: the GPU
# `ap-ingestion` container is behind the gpu profile and is not started here.
bootstrap-local: ensure-env-local
	$(COMPOSE_LOCAL) exec ap-api python -m scripts.init_db
	$(COMPOSE_LOCAL) exec ap-api python -m scripts.seed_reference_facts --profile starter
	$(COMPOSE_LOCAL) exec ap-api python -m scripts.seed_dev_users
	$(COMPOSE_LOCAL) exec ap-ingestion-cpu python -m scripts.ingest_corpus --profile starter
	@echo ""
	@echo "Bootstrap complete. Verify with: make smoke-local"
	@echo "Optional example agent: make seed-example-local PROFILE=taxes"

seed-example-local: ensure-env-local
	@test -n "$(PROFILE)" || { echo "usage: make seed-example-local PROFILE=<id>   (e.g. taxes)"; exit 1; }
	$(COMPOSE_LOCAL) exec ap-api python -m scripts.seed_profile $(PROFILE)
	$(COMPOSE_LOCAL) exec ap-ingestion-cpu python -m scripts.ingest_corpus --profile $(PROFILE)

# Health through the edge + one real streamed chat turn.
#
# The chat turn runs INSIDE the ap-api container, not through localhost:8080, and
# that is not a shortcut. Caddy sends /api/* to SvelteKit, whose handleApiProxy
# (hooks.server.ts) 401s any /api/ request that arrives without an app session
# cookie and otherwise OVERWRITES Authorization from the session — so a
# `Bearer dev-token-…` header sent at the edge never reaches Python. Exercising
# the dev token means talking to the API directly. The /health checks stay on
# the edge because Caddy proxies /health to ap-api:8000 without touching SvelteKit,
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
# pacod_postgres_data, ap-documents-data and pacod_qdrant_data — not just the
# log volumes the local overlay adds. Same semantics as `clean`.
clean-local: ensure-env-local
	$(COMPOSE_LOCAL) $(LOCAL_PROFILES) down -v
	@$(if $(filter 1,$(MODELS)),$(MAKE) --no-print-directory clean-models,echo "clean-local: the models ($(HF_CACHE)) are kept; MODELS=1 deletes them")
