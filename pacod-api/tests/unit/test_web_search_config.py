"""Tests for per-turn web-search config override and live-key resolution.

Covers provider resolution, the disabled short-circuit, and that availability is
driven by `effective_search_key` (the same source the request uses) — without
hitting the network. With APP_SECRETS_KEY unset (unit default), effective_search_key
falls back to settings, so monkeypatching settings keys exercises the path.
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.services.web_search import WebSearch, _resolve_provider_from


def _keys_only(monkeypatch, *, tavily: bool, google: bool):
    s = get_settings()
    monkeypatch.setattr(s, "tavily_api_key", "tv-key" if tavily else "")
    monkeypatch.setattr(s, "google_search_api_key", "g-key" if google else "")
    monkeypatch.setattr(s, "google_search_cx", "cx" if google else "")


class TestResolveProviderFrom:
    def test_disabled_is_none(self):
        assert _resolve_provider_from(False, "auto", True, True) == "none"

    def test_auto_prefers_tavily(self):
        assert _resolve_provider_from(True, "auto", True, True) == "tavily"
        assert _resolve_provider_from(True, "auto", False, True) == "google"
        assert _resolve_provider_from(True, "auto", False, False) == "none"

    def test_explicit_provider_requires_its_key(self):
        assert _resolve_provider_from(True, "google", True, False) == "none"
        assert _resolve_provider_from(True, "google", True, True) == "google"
        assert _resolve_provider_from(True, "tavily", False, True) == "none"


@pytest.mark.asyncio
class TestWebSearchOverride:
    async def test_override_disabled_returns_empty(self, monkeypatch):
        _keys_only(monkeypatch, tavily=True, google=False)
        ws = WebSearch()
        out = await ws.search(
            "q", config={"enabled": False, "provider": "auto", "allowed_domains": []}
        )
        assert out == []

    async def test_no_keys_resolves_to_none(self, monkeypatch):
        _keys_only(monkeypatch, tavily=False, google=False)
        ws = WebSearch()
        out = await ws.search(
            "q", config={"enabled": True, "provider": "auto", "allowed_domains": ["x.bg"]}
        )
        assert out == []

    async def test_availability_drives_routing_and_passes_override(self, monkeypatch):
        # Only a Google key exists → auto must route to Google, with the override
        # allowlist applied. Stub the network call to assert routing offline.
        _keys_only(monkeypatch, tavily=False, google=True)
        ws = WebSearch()
        seen = {}

        async def fake_google(query, max_results, allowed, api_key, cx):
            seen.update(query=query, allowed=allowed, api_key=api_key, cx=cx)
            return [{"url": "https://x.bg/a", "title": "t", "content": "c", "score": 1.0}]

        monkeypatch.setattr(ws, "_google_search", fake_google)
        out = await ws.search(
            "q", config={"enabled": True, "provider": "auto", "allowed_domains": ["x.bg"]}
        )
        assert out and out[0]["url"] == "https://x.bg/a"
        assert seen["allowed"] == ["x.bg"]
        assert seen["api_key"] == "g-key" and seen["cx"] == "cx"

    async def test_empty_override_allowlist_does_not_fall_back_to_global(self, monkeypatch):
        # A profile whose allowlist is explicitly empty must NOT inherit the
        # global env allowlist — the allowlist is per-profile, not global. The
        # empty list reaches the provider as-is (which then filters everything).
        monkeypatch.setattr(get_settings(), "web_search_allowed_domains", "global.bg")
        _keys_only(monkeypatch, tavily=False, google=True)
        ws = WebSearch()
        assert ws.allowed_domains == ["global.bg"]  # global default is present...
        seen = {}

        async def fake_google(query, max_results, allowed, api_key, cx):
            seen.update(allowed=allowed)
            return []

        monkeypatch.setattr(ws, "_google_search", fake_google)
        out = await ws.search(
            "q", config={"enabled": True, "provider": "auto", "allowed_domains": []}
        )
        assert out == []
        assert seen["allowed"] == []  # ...but the empty per-profile list wins, not global.bg

    async def test_no_config_honors_instance_enabled_flag(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "web_search_enabled", False)
        ws = WebSearch()  # captures disabled at init
        out = await ws.search("q")
        assert out == []
