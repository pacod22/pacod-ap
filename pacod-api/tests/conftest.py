"""Shared fixtures."""

from __future__ import annotations

import os

import pytest

# Unit tests must never reach a real Postgres/Redis — but `Settings` reads the
# repo's `.env`, so a developer's laptop (POSTGRES_HOST=pgbouncer, a set
# APP_SECRETS_KEY) made every "no DB → env default" fallback wait out a real
# connect timeout (the suite took ~3 minutes instead of ~10 seconds), and with
# the compose stack up the container run reached the LIVE database. Pin the
# service hosts to an unroutable port so those attempts fail fast, and blank the
# secrets key so the encrypted-store lookups short-circuit as the tests assume.
# `setdefault` keeps explicit env / `make test-unit -e …` overrides in charge.
# This runs before `app.core.db` builds its engine (conftest imports first).
for _k, _v in {
    "POSTGRES_HOST": "127.0.0.1",
    "POSTGRES_PORT": "1",
    "REDIS_URL": "redis://127.0.0.1:1/0",
    "APP_SECRETS_KEY": "",
}.items():
    os.environ.setdefault(_k, _v)
# The LLM gateway, unconditionally: inside the api container GATEWAY_URL and the service's
# real key are set, and a unit test must never call (or spend on) the live gateway. Tests
# that talk to "the gateway" swap its transport (tests/unit/test_gateway_client.py).
os.environ.update(
    {
        "GATEWAY_URL": "http://127.0.0.1:1",
        "GATEWAY_ADMIN_URL": "http://127.0.0.1:1",
        "GATEWAY_API_KEY": "pgw_unit_test",
        "GATEWAY_REPORT_TOKEN": "",
        "GATEWAY_ADMIN_TOKEN": "",
    }
)


# Force a clean settings cache per test session — important because pydantic
# settings are cached at module level and tests may flip env vars.
@pytest.fixture(autouse=True)
def _reset_settings_cache():
    from app.core import config

    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


@pytest.fixture
def env_dev(monkeypatch):
    """Set common dev env vars before importing settings."""
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("AGENT_LEGAL_YEAR", "2026")
    monkeypatch.setenv("AGENT_PER_USER_DAILY_TOKEN_LIMIT", "100000")
    monkeypatch.setenv("AGENT_PER_USER_PER_MINUTE_REQUESTS", "10")
    monkeypatch.setenv(
        "WEB_SEARCH_ALLOWED_DOMAINS",
        "revenue.example,nssi.bg,noi.bg,minfin.bg,lex.bg,parliament.bg",
    )
    yield
