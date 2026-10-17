"""Tests for native (model-integrated) web search support.

Covers the capability map, the request builder, the citation parser, and the
graph-level backend resolver. No external services — pure functions over the
runtime config and the ``provider_specific_fields`` shape. The gateway keeps that
shape (tests/fixtures/gw/ carry it for thinking blocks); the web_search_results below were
observed through the former proxy, and the gateway's native-search answer is still to be
recorded against a real provider (docs/LLM_GATEWAY.md).
"""

from __future__ import annotations

from dataclasses import replace

from app.agent.graph import _resolve_native_web_search, _web_dicts_to_citations
from app.core.models import Citation
from app.services.agent_runtime_config import env_runtime_config
from app.services.web_search_native import (
    build_native_search_tools,
    extract_web_citations,
    native_search_support,
)


class TestCapabilityMap:
    def test_anthropic_models_support_native_with_allowlist(self):
        for m in ("anthropic/claude-sonnet-5-5", "anthropic/claude-opus-5", "claude-haiku-4-5"):
            caps = native_search_support(m)
            assert caps is not None
            assert caps.provider == "anthropic"
            assert caps.enforces_allowlist is True

    def test_non_anthropic_models_have_no_native_support(self):
        for m in (
            "gemini-3.8-flash",
            "openai/gpt-5.6-sol",
            "deepseek/deepseek-v4-pro",
            "glm/glm-4.7",
        ):
            assert native_search_support(m) is None


class TestBuildTools:
    def test_anthropic_tool_with_allowlist(self):
        caps = native_search_support("anthropic/claude-sonnet-5-5")
        tools = build_native_search_tools(caps, ["revenue.example", "lex.bg"])
        assert len(tools) == 1
        t = tools[0]
        assert t["type"] == "web_search_20250305"
        assert t["name"] == "web_search"
        assert t["allowed_domains"] == ["revenue.example", "lex.bg"]
        assert t["max_uses"] >= 1

    def test_empty_allowlist_omits_the_field(self):
        caps = native_search_support("anthropic/claude-sonnet-5-5")
        tools = build_native_search_tools(caps, [])
        assert "allowed_domains" not in tools[0]
        tools_none = build_native_search_tools(caps, None)
        assert "allowed_domains" not in tools_none[0]


class TestExtractCitations:
    # The shape observed through the former proxy (see the module docstring).
    PSF = {
        "web_search_results": [
            {
                "type": "web_search_tool_result",
                "tool_use_id": "srvtoolu_1",
                "content": [
                    {
                        "type": "web_search_result",
                        "title": "VAT in Bulgaria",
                        "url": "https://revenue.example/vat",
                    },
                    {"type": "web_search_result", "title": "ЗДДС", "url": "https://lex.bg/zdds"},
                ],
            }
        ]
    }

    def test_extracts_title_url_pairs(self):
        out = extract_web_citations(self.PSF)
        assert [c["url"] for c in out] == ["https://revenue.example/vat", "https://lex.bg/zdds"]
        assert out[0]["title"] == "VAT in Bulgaria"

    def test_tolerates_garbage(self):
        assert extract_web_citations(None) == []
        assert extract_web_citations({}) == []
        assert extract_web_citations({"web_search_results": "nope"}) == []
        assert extract_web_citations({"web_search_results": [{"content": [{"url": None}]}]}) == []


class TestWebDictsToCitations:
    def test_converts_and_dedupes_by_url(self):
        sink: list[Citation] = []
        raw = [
            {"title": "A", "url": "https://revenue.example/a", "content": ""},
            {"title": "A dup", "url": "https://revenue.example/a", "content": ""},
            {"title": "", "url": "https://lex.bg/b", "content": ""},
        ]
        _web_dicts_to_citations(raw, sink)
        assert [c.url for c in sink] == ["https://revenue.example/a", "https://lex.bg/b"]
        assert all(c.source_type == "web" for c in sink)
        # Empty title falls back to the URL so the chip is never blank.
        assert sink[1].title == "https://lex.bg/b"

    def test_none_sink_is_noop(self):
        _web_dicts_to_citations([{"url": "https://x/y"}], None)  # must not raise


class TestResolver:
    def _cfg(self, **over):
        return replace(env_runtime_config(), **over)

    def test_native_anthropic_engages(self):
        cfg = self._cfg(
            web_search_enabled=True,
            web_search_mode="native",
            primary_model="anthropic/claude-sonnet-5-5",
            web_search_allowed_domains=["revenue.example"],
        )
        native = _resolve_native_web_search(cfg)
        assert native is not None
        assert native["tools"][0]["allowed_domains"] == ["revenue.example"]

    def test_external_mode_returns_none(self):
        cfg = self._cfg(web_search_mode="external", primary_model="anthropic/claude-sonnet-5-5")
        assert _resolve_native_web_search(cfg) is None

    def test_disabled_returns_none(self):
        cfg = self._cfg(
            web_search_enabled=False,
            web_search_mode="native",
            primary_model="anthropic/claude-sonnet-5-5",
        )
        assert _resolve_native_web_search(cfg) is None

    def test_gemini_with_require_allowlist_falls_back_to_external(self):
        cfg = self._cfg(
            web_search_mode="native",
            primary_model="gemini-3.8-flash",
            web_search_native_require_allowlist=True,
        )
        assert _resolve_native_web_search(cfg) is None

    def test_gemini_without_native_support_still_none_even_if_allowlist_not_required(self):
        # Gemini has no native support at all today, so it's external regardless.
        cfg = self._cfg(
            web_search_mode="native",
            primary_model="gemini-3.8-flash",
            web_search_native_require_allowlist=False,
        )
        assert _resolve_native_web_search(cfg) is None
