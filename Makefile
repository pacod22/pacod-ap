# ===========================================================================
# pacod — Multi-Profile AI Agent Platform
#
# Umbrella task runner for the whole platform (backend + frontend). It delegates
# to the per-service Makefiles and drives the combined docker-compose stack
# defined in pacod-api/docker-compose.yml (the app file: ap-api, ap-frontend, …) and
# pacod-api/docker-compose.standalone.yml (its copies of the shared components). Every
# stack joins the one external network pacod-net (pacod-demo/docs/NETWORK.md).
#
#   pacod-api/Makefile   — backend tasks and the compose stack
#   pacod-ui/Makefile    — frontend checks (unit tests, svelte-check, lint)
# ===========================================================================

API_DIR := $(CURDIR)/pacod-api
UI_DIR  := $(CURDIR)/pacod-ui
MON_DIR := $(CURDIR)/monitoring

# --- The gateway (pacod-gw), run from its own checkout with the platform's overrides ---
# Only pacod-gw's app file (pacod-gw, gw-console, gw-analytics, gw-tester, gw-clickhouse,
# gw-preflight), never its standalone file: the shared components it uses (pacod-postgres,
# pacod-id, pacod-caddy, pacod-prometheus) are this platform's, on pacod-net.
# GW_ENV=dev (default): plus pacod-gw's development file (its images built from the checkout,
# the mock provider gw-mock, development values) and the local addresses.
# GW_ENV=live: the app file alone; every secret from deploy/secrets/gw.env.
# Its own compose project (its relative paths resolve in GW_DIR/deploy) and volumes, so it
# never touches a standalone pacod-gw stack's data; its ports are the same as pacod-gw's
# development stack, so not both at once.
GW_DIR ?= $(abspath $(CURDIR)/../pacod-gw)
GW_ENV ?= dev
GW_PROJECT := pacod-ap-gw
GW_PROFILES ?=
GW_FILES_dev := -f $(GW_DIR)/deploy/docker-compose.yml -f $(GW_DIR)/deploy/docker-compose.dev.yml
GW_FILES_live := -f $(GW_DIR)/deploy/docker-compose.yml
GW_OVERRIDES := -f $(CURDIR)/deploy/pacod-gw/docker-compose.platform-id.yml \
                $(if $(filter dev,$(GW_ENV)),-f $(CURDIR)/deploy/pacod-gw/docker-compose.platform-id.dev.yml)
# --env-file replaces the gateway's own deploy/.env, which is never read here.
# pacod-gw's database fragment, run by this platform's pacod-db-init (`make up-all`).
GW_DB_FRAGMENT := $(GW_DIR)/platform/postgres/gw-db.sh
# The app's stack hands it to pacod-db-init whenever the checkout has it (server and laptop), so
# the gateway's database exists before `make gw-up`.
AP_GW_DB = GW_DIR=$(GW_DIR) $(if $(wildcard $(GW_DB_FRAGMENT)),AP_EXTRA_FILES="-f $(CURDIR)/deploy/pacod-gw/docker-compose.gw-db.yml")
GW_COMPOSE = docker compose -p $(GW_PROJECT) --project-directory $(GW_DIR)/deploy \
             --env-file $(CURDIR)/deploy/secrets/gw.env $(GW_FILES_$(GW_ENV)) $(GW_OVERRIDES)

.PHONY: help update up down up-api down-api up-ui down-ui logs ps bootstrap test rebuild upgrade upgrade-ui clean monitor-up monitor-down monitor-logs backup restore \
        up-local down-local bootstrap-local smoke-local clean-local secret-set secrets-init id-image \
        ensure-pacod-net gw-up gw-down gw-ps gw-logs gw-config gw-bootstrap gw-smoke gw-turn-smoke \
        up-all down-all smoke-all

help:
	@echo "pacod — full platform (api + ui)"
	@echo ""
	@echo "  make up / down     Start / stop the server stack (up-api + up-ui)"
	@echo "  make up-api        Start the full stack (api + frontend + services)"
	@echo "  make down-api      Stop the full stack (preserves volumes)"
	@echo "  make up-ui         Start the frontend"
	@echo "  make down-ui       Stop the frontend"
	@echo "  make logs          Tail logs from all services"
	@echo "  make ps            List service status"
	@echo "  make bootstrap     init_db + starter agent facts + corpus (first run)"
	@echo "  make test          Run backend unit tests + frontend unit tests and svelte-check"
	@echo "  make secret-set NAME=X [FILE=pacod-api/.env]  Set one secret from stdin (never echoed)"
	@echo "  make secrets-init  Generate pacod-id's and the gateway's secrets (deploy/secrets/, names only)"
	@echo "  make id-image      Build the pacod-id image from the pinned tree (PACOD_ID_SRC)"
	@echo "  make update        git pull the latest code (no rebuild)"
	@echo "  make rebuild       Rebuild + restart both api and frontend"
	@echo "  make upgrade       update + rebuild (pull, then rebuild both)"
	@echo "  make upgrade-ui    update + rebuild the UI only (pull, then rebuild both)"
	@echo "  make clean         Stop the stack and DELETE all volumes"
	@echo ""
	@echo "  --- Local test stack (laptop; plain HTTP on ports, no TLS/certs) ---"
	@echo "  make up-local        Start the whole stack locally (:8080 app)"
	@echo "  make bootstrap-local init_db + starter facts + dev users + corpus (first local run)"
	@echo "  make smoke-local     Health + one SSE chat turn against localhost"
	@echo "  make down-local      Stop the local stack (preserves volumes)"
	@echo "  make clean-local     Stop the local stack and DELETE ALL volumes (incl. the database)"
	@echo ""
	@echo "  --- The gateway (pacod-gw at GW_DIR=$(GW_DIR); GW_ENV=dev|live) ---"
	@echo "  make gw-up           Start it, signed in through this platform's pacod-id"
	@echo "  make gw-bootstrap    Import the platform configuration; write keys to pacod-api/.env.gateway"
	@echo "  make gw-smoke        pacod-gw's smoke test against the mock provider (leaves smoke-* objects)"
	@echo "  make gw-down / gw-ps / gw-logs"
	@echo "  make up-all          pacod-net, app + pacod-id (+ pacod-gw's database), gateway, then the URLs"
	@echo "  make gw-turn-smoke   One app chat turn through the gateway's mock, checked on both sides"
	@echo "  make smoke-all       smoke-local + gw-turn-smoke + gw-smoke"
	@echo "  make down-all        Stop the app and the gateway (volumes kept)"
	@echo ""
	@echo "  make monitor-up    Start the Grafana/Prometheus monitoring module"
	@echo "  make monitor-down  Stop the monitoring module"
	@echo "  make monitor-logs  Tail monitoring-module logs"
	@echo "  make backup        Back up DB + Qdrant + config + volumes (stack must be up)"
	@echo "  make restore ARCHIVE=path/to/backup.tar.gz   Restore a backup"

update:
	git pull

# The server stack: the API side, then the frontend.
up: up-api up-ui

down: down-ui down-api

up-api: ensure-pacod-net
	$(AP_GW_DB) $(MAKE) -C $(API_DIR) up-cpu

down-api:
	$(MAKE) -C $(API_DIR) down-cpu

up-ui: ensure-pacod-net
	$(MAKE) -C $(API_DIR) up-ui

down-ui:
	$(MAKE) -C $(API_DIR) down-ui

# --- Local test stack (a laptop, not a deployment) ---
# Plain HTTP on high ports, named volumes instead of the server's /data/**
# bind mounts. See pacod-api/docker-compose.local.yml and CLAUDE.md.
up-local: ensure-pacod-net
	$(AP_GW_DB) $(MAKE) -C $(API_DIR) up-local

down-local:
	$(MAKE) -C $(API_DIR) down-local

bootstrap-local:
	$(MAKE) -C $(API_DIR) bootstrap-local

smoke-local:
	$(MAKE) -C $(API_DIR) smoke-local

clean-local:
	$(MAKE) -C $(API_DIR) clean-local

logs:
	$(MAKE) -C $(API_DIR) logs

ps:
	$(MAKE) -C $(API_DIR) ps

bootstrap:
	$(MAKE) -C $(API_DIR) bootstrap

test:
	$(MAKE) -C $(API_DIR) test-unit
	$(MAKE) -C $(UI_DIR) test

# pacod-id's and the gateway's secrets: deploy/secrets/{id,gw,providers}.env (0600,
# gitignored). Fills only what is missing; prints names, never values.
secrets-init:
	@$(CURDIR)/scripts/secrets-init.sh

# The pacod-id image, from the pinned pacod-id tree (default: the copy pacod-gw vendors).
id-image:
	$(MAKE) -C $(API_DIR) id-image $(if $(PACOD_ID_SRC),PACOD_ID_SRC=$(PACOD_ID_SRC))

# The shared network every pacod stack joins (pacod-demo/docs/NETWORK.md); idempotent.
ensure-pacod-net:
	@docker network inspect pacod-net >/dev/null 2>&1 || docker network create pacod-net >/dev/null

# --- The gateway ---
gw-up: ensure-pacod-net secrets-init
	@test -f "$(GW_DIR)/deploy/docker-compose.yml" || { echo "GW_DIR=$(GW_DIR) is not a pacod-gw checkout"; exit 1; }
	$(GW_COMPOSE) $(GW_PROFILES) up -d --wait
	@echo ""
	@echo "Gateway console:  $(if $(filter dev,$(GW_ENV)),http://127.0.0.1:13000,https://console.<domain>)  (signs in at pacod-id)"

gw-down:
	$(GW_COMPOSE) $(GW_PROFILES) down

gw-ps:
	$(GW_COMPOSE) ps

gw-logs:
	$(GW_COMPOSE) logs -f --tail=100

# Checks the merged compose files without printing them (they would show the secrets).
gw-config:
	$(GW_COMPOSE) config --quiet && echo "gw compose files: ok"

# GW_BOOTSTRAP_ARGS passes options through, e.g. on a server (GW_ENV=live):
#   GW_BOOTSTRAP_ARGS="--admin-url http://<core>:8081 --data-url http://<core>:8080"
# Imports deploy/pacod-gw/platform-config.$(GW_ENV).json, provider keys from
# deploy/secrets/providers.env, and writes the services' gateway keys and tokens to
# pacod-api/.env.gateway (0600). Idempotent. Prints names, never values.
# The app's services read their key when their container is created: when the file changed
# and the app is running, they are recreated (a restart would keep the old keys).
gw-bootstrap:
	@before="$$(shasum $(API_DIR)/.env.gateway 2>/dev/null)"; \
	python3 -I $(CURDIR)/scripts/gw_bootstrap.py --env $(GW_ENV) $(GW_BOOTSTRAP_ARGS) || exit 1; \
	if [ "$$before" != "$$(shasum $(API_DIR)/.env.gateway 2>/dev/null)" ] && docker inspect ap-api >/dev/null 2>&1; then \
		echo "gw-bootstrap: the gateway credentials changed; recreating the services that use them"; \
		$(MAKE) --no-print-directory -C $(API_DIR) $(if $(filter dev,$(GW_ENV)),recreate-gateway-clients-local,recreate-gateway-clients); \
	fi

# pacod-gw's own smoke test, against the mock provider, signed in through pacod-id as the
# bootstrap admin (the development client gw-smoke; GW_ENV=dev only). It builds and runs in
# GW_DIR and leaves its smoke-* organisation, providers and models behind.
gw-smoke:
	@set -a; . $(CURDIR)/deploy/secrets/id.env; set +a; \
	SMOKE_AUTH=oidc SMOKE_ID_ISSUER=http://127.0.0.1:8070 \
	PGW_ADMIN_EMAIL="$$PID_BOOTSTRAP_ADMIN_EMAIL" PGW_ADMIN_PASSWORD="$$PID_BOOTSTRAP_ADMIN_PASSWORD" \
	$(MAKE) --no-print-directory -C $(GW_DIR) smoke-mock

# Order: the app with pacod-id first (the gateway signs in through it, and its pacod-db-init
# makes the gateway's database: docker-compose.gw-db.yml runs GW_DIR's gw-db.sh), then the
# gateway's app file, then its bootstrap, which hands the app's services their keys (and
# recreates them). The monitoring module (the console's Metrics page) starts too when
# monitoring/.env exists.
up-all: ensure-pacod-net secrets-init
	@test -f "$(GW_DB_FRAGMENT)" || { echo "GW_DIR=$(GW_DIR) has no platform/postgres/gw-db.sh (a pacod-gw checkout on its pacod-net branch?)"; exit 1; }
	$(MAKE) up-local
	$(MAKE) gw-up
	$(MAKE) gw-bootstrap
	@echo ""
	@echo "App:              http://localhost:8080"
	@echo "Sign-in (issuer): http://127.0.0.1:8070"
	@echo "Gateway console:  http://127.0.0.1:13000"
	@echo "Mail (mailpit):   http://127.0.0.1:8025"
	@$(if $(wildcard $(MON_DIR)/.env),$(MAKE) --no-print-directory monitor-up && echo "Grafana:          http://127.0.0.1:3300",echo "Monitoring:       cp monitoring/.env.example monitoring/.env, then make monitor-up (the console's Metrics page)")
	@echo "First run? make bootstrap-local"

down-all:
	-$(MAKE) gw-down
	$(MAKE) down-local

# One chat turn of the app through the gateway and its mock, checked on both sides (the SSE
# stream and the gateway's trace of the turn). GW_ENV=dev only.
gw-turn-smoke:
	@$(CURDIR)/scripts/smoke_gateway_turn.sh

smoke-all: smoke-local gw-turn-smoke gw-smoke

# Write one secret from stdin into an env file (default: the backend's .env).
secret-set:
	@test -n "$(NAME)" || { echo "usage: make secret-set NAME=<VAR> [FILE=pacod-api/.env]"; exit 1; }
	@$(CURDIR)/scripts/secret-set.sh $(or $(FILE),$(API_DIR)/.env) $(NAME)

rebuild:
	$(MAKE) -C $(API_DIR) rebuild-cpu

# --- Upgrade = pull the latest code, then stop all instances and start them again (and rebuild). The upgrade-* family owns the
#     git pull; up / rebuild-* stay pull-free so they can be run without touching git.
upgrade: update down-ui down-api up-ui up-api

upgrade-ui: update down-ui up-ui
	docker restart pacod-caddy

clean:
	$(MAKE) -C $(API_DIR) clean

# --- Monitoring module (Grafana + Prometheus + exporters) ---
monitor-up:
	$(MAKE) -C $(MON_DIR) up

monitor-down:
	$(MAKE) -C $(MON_DIR) down

monitor-logs:
	$(MAKE) -C $(MON_DIR) logs

# --- Backup / restore (see scripts/backup.env.example for configuration) ---
# Online backup: the stack must be running. Produces one chmod-600 tar.gz.
backup:
	$(CURDIR)/scripts/backup.sh

# Restore a backup archive: make restore ARCHIVE=backups/pacod-backup-<ts>.tar.gz
# By default config/secrets are only previewed; pass ARGS='--overwrite-config' to apply.
restore:
	@[ -n "$(ARCHIVE)" ] || { echo "Usage: make restore ARCHIVE=path/to/backup.tar.gz [ARGS='--yes --overwrite-config']"; exit 1; }
	$(CURDIR)/scripts/restore.sh $(ARCHIVE) $(ARGS)
