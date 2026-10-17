"""Tests for Settings validation and derived properties."""

from __future__ import annotations

import pytest

from app.core.config import Settings


class TestSettingsValidation:
    def test_legal_year_in_range(self, monkeypatch):
        monkeypatch.setenv("AGENT_LEGAL_YEAR", "2026")
        s = Settings()
        assert s.agent_legal_year == 2026

    def test_legal_year_out_of_range_rejected(self, monkeypatch):
        monkeypatch.setenv("AGENT_LEGAL_YEAR", "1500")
        with pytest.raises(ValueError, match="agent_legal_year"):
            Settings()

    def test_postgres_dsn_built_correctly(self, monkeypatch):
        monkeypatch.setenv("POSTGRES_HOST", "myhost")
        monkeypatch.setenv("POSTGRES_PORT", "5433")
        monkeypatch.setenv("POSTGRES_USER", "u")
        monkeypatch.setenv("POSTGRES_PASSWORD", "p")
        monkeypatch.setenv("POSTGRES_DB", "d")
        s = Settings()
        assert s.postgres_dsn == "postgresql+asyncpg://u:p@myhost:5433/d"

    def test_allowed_domains_parsed(self, monkeypatch):
        monkeypatch.setenv("WEB_SEARCH_ALLOWED_DOMAINS", "a.com, b.com,c.com")
        s = Settings()
        assert s.web_search_allowed_domains_list == ["a.com", "b.com", "c.com"]

    def test_allowed_domains_empty_handled(self, monkeypatch):
        monkeypatch.setenv("WEB_SEARCH_ALLOWED_DOMAINS", "")
        s = Settings()
        assert s.web_search_allowed_domains_list == []
