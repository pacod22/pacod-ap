# ===========================================================================
# pacod-ui — frontend task runner
#
# The frontend is built and run by the platform's compose stack (see the
# repo-root Makefile); this file only drives the checks that run on the host.
#
#   make test      — unit tests (vitest) + type check (svelte-check)
#   make unit      — unit tests only
#   make check     — svelte-check only
#   make lint      — prettier + eslint
# ===========================================================================

SHELL := /bin/bash
.DEFAULT_GOAL := help

FRONTEND_DIR := $(CURDIR)/frontend

.PHONY: help install test unit check lint

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Install the frontend dependencies from the lock file
	cd $(FRONTEND_DIR) && npm ci

test: unit check ## Unit tests + type check

unit: ## Run the frontend unit tests
	cd $(FRONTEND_DIR) && npm run test:unit -- --run

check: ## Type-check the frontend (svelte-check)
	cd $(FRONTEND_DIR) && npm run check

lint: ## prettier --check + eslint
	cd $(FRONTEND_DIR) && npm run lint
