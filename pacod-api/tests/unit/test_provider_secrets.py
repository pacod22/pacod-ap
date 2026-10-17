"""Tests for provider-secret encryption helpers (no DB needed).

The DB-backed set/get/list paths need Postgres (integration); here we cover the
crypto + status logic: the Fernet round-trip via a real key, the masking, the
"editable only when APP_SECRETS_KEY is set" gate, and the env fallback in
effective_search_key / list_secret_status when no DB row exists.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.core.config import get_settings
from app.services import provider_secrets as ps


def _set_fernet(monkeypatch, key: str | None):
    monkeypatch.setattr(get_settings(), "app_secrets_key", key or "")


class TestEditableGate:
    def test_unset_key_disables_editing(self, monkeypatch):
        _set_fernet(monkeypatch, None)
        assert ps.is_editable() is False
        assert ps._fernet() is None

    def test_valid_key_enables_editing(self, monkeypatch):
        _set_fernet(monkeypatch, Fernet.generate_key().decode())
        assert ps.is_editable() is True
        assert ps._fernet() is not None

    def test_malformed_key_treated_as_unconfigured(self, monkeypatch):
        _set_fernet(monkeypatch, "not-a-valid-fernet-key")
        assert ps._fernet() is None


class TestCryptoRoundTrip:
    def test_encrypt_decrypt_roundtrip(self, monkeypatch):
        _set_fernet(monkeypatch, Fernet.generate_key().decode())
        f = ps._fernet()
        token = f.encrypt(b"sk-secret-1234").decode()
        assert token != "sk-secret-1234"
        assert f.decrypt(token.encode()).decode() == "sk-secret-1234"

    def test_mask(self):
        assert ps._mask("sk-abcd1234") == "1234"
        assert ps._mask("ab") == "••"


class TestKnownKeys:
    def test_only_the_search_keys_are_managed_here(self):
        # LLM provider keys are the gateway's; only the web-search keys remain.
        assert {
            "TAVILY_API_KEY",
            "GOOGLE_SEARCH_API_KEY",
            "GOOGLE_SEARCH_CX",
        } == ps.SEARCH_KEY_NAMES
        assert ps.known_key("TAVILY_API_KEY")
        assert not ps.known_key("ANTHROPIC_API_KEY") and not ps.known_key("BOGUS")


@pytest.mark.asyncio
class TestEffectiveSearchKeyFallback:
    async def test_falls_back_to_env_when_no_db_or_fernet(self, monkeypatch):
        # No APP_SECRETS_KEY → get_secret returns None without a DB hit → env value.
        _set_fernet(monkeypatch, None)
        monkeypatch.setattr(get_settings(), "tavily_api_key", "env-tavily")
        assert await ps.effective_search_key("TAVILY_API_KEY") == "env-tavily"

    async def test_unknown_name_returns_none(self, monkeypatch):
        _set_fernet(monkeypatch, None)
        assert await ps.effective_search_key("NOPE") is None


@pytest.mark.asyncio
async def test_list_status_reflects_env_without_db(monkeypatch):
    # No fernet, no DB rows: a key set in env still reports configured.
    _set_fernet(monkeypatch, None)
    s = get_settings()
    monkeypatch.setattr(s, "tavily_api_key", "tvly-9999")
    monkeypatch.setattr(s, "google_search_api_key", "")
    status = {row["name"]: row for row in await ps.list_secret_status()}
    assert set(status) == ps.SEARCH_KEY_NAMES
    assert status["TAVILY_API_KEY"]["configured"] is True
    assert status["TAVILY_API_KEY"]["last4"] == "9999"
    assert status["TAVILY_API_KEY"]["target"] == "live"
    assert status["TAVILY_API_KEY"]["pending_restart"] is False
    assert status["GOOGLE_SEARCH_API_KEY"]["configured"] is False
