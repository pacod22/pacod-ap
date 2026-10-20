# Monitoring module task runner. Run from the monitoring/ directory.
# docker compose auto-loads ./.env for variable interpolation.

COMPOSE := docker compose -f docker-compose.exporters.yml -f docker-compose.monitoring.yml

.PHONY: up down restart logs ps reload check-env

## Bring up exporters + Prometheus + Alertmanager + Grafana (same instance).
up: check-env
	@docker network inspect pacod-net >/dev/null 2>&1 || docker network create pacod-net >/dev/null
	$(COMPOSE) up -d

## Stop the monitoring stack (volumes preserved).
down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart

## Follow logs.
logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

## Hot-reload Prometheus config (after editing the template/alerts + `make restart` re-renders it).
reload:
	curl -fsS -X POST http://localhost:$${PROMETHEUS_PORT:-9095}/-/reload && echo "reloaded"

check-env:
	@test -f .env || { echo "Missing .env — run: cp .env.example .env and edit"; exit 1; }
