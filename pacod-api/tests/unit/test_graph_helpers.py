"""Tests for graph helper functions: context building and citation extraction."""

from __future__ import annotations

from app.agent.graph import _build_context


class TestBuildContext:
    def test_empty_results_empty_context(self):
        context, citations, markers = _build_context([])
        assert context == ""
        assert citations == []
        assert markers == {}

    def test_kb_search_results_render_with_citations(self):
        results = [
            {
                "name": "kb_search",
                "args": {"query": "VAT"},
                "result": {
                    "hits": [
                        {
                            "chunk_id": "c1",
                            "text": "VAT is collected on supplies.",
                            "title": "VAT Act",
                            "source_id": "vat-act",
                            "source_type": "law",
                            "url": "https://lex.bg/vat",
                            "legal_year": 2026,
                            "language": "en",
                        }
                    ]
                },
            }
        ]
        context, citations, _markers = _build_context(results)
        assert "VAT Act" in context
        assert "VAT is collected" in context
        assert len(citations) == 1
        assert citations[0].source_type == "law"
        assert citations[0].url == "https://lex.bg/vat"

    def test_get_fact_rendered_and_cited(self):
        results = [
            {
                "name": "get_fact",
                "args": {"fact_key": "plan_price_monthly"},
                "result": {
                    "found": True,
                    "fact_key": "plan_price_monthly",
                    "legal_year": 2026,
                    "facts": [
                        {
                            "fact_key": "plan_price_monthly",
                            "variant": "plus",
                            "value_type": "money",
                            "value_num": 6,
                            "unit": "EUR",
                            "label_en": "Plus plan — price per month",
                            "source_url": "https://nimbus-notes.example/pricing",
                            "legal_year": 2026,
                        }
                    ],
                },
            }
        ]
        context, citations, _markers = _build_context(results)
        assert "Reference facts — get_fact" in context
        assert "plan_price_monthly" in context and '"value_num": 6' in context
        # A structured citation per fact that names its source.
        assert len(citations) == 1
        assert citations[0].source_type == "structured"
        assert citations[0].title == "Plus plan — price per month"
        assert citations[0].source_id == "get_fact:plan_price_monthly:plus"

    def test_web_search_results_rendered(self):
        results = [
            {
                "name": "web_search",
                "args": {"query": "ddfl"},
                "result": {
                    "results": [
                        {
                            "url": "https://revenue.example/article",
                            "title": "Article",
                            "content": "Some text from NRA...",
                            "score": 0.9,
                        }
                    ]
                },
            }
        ]
        context, citations, markers = _build_context(results)
        assert "revenue.example" in context
        assert len(citations) == 1
        assert citations[0].source_type == "web"
        assert citations[0].url == "https://revenue.example/article"
        # The WEB-1 marker the synthesizer may copy into its answer resolves to a
        # readable source reference for the offline review loop.
        assert markers["WEB-1"] == "Article — https://revenue.example/article"

    def test_fact_without_source_is_rendered_but_not_cited(self):
        results = [
            {
                "name": "get_fact",
                "args": {"fact_key": "invoice_day"},
                "result": {
                    "found": True,
                    "facts": [
                        {
                            "fact_key": "invoice_day",
                            "variant": "default",
                            "value_type": "monthly_day",
                            "value_num": 1,
                        }
                    ],
                },
            }
        ]
        context, citations, _markers = _build_context(results)
        assert "monthly_day" in context and "invoice_day" in context
        # _build_context only mints a structured citation when source_url is present.
        assert len(citations) == 0

    def test_list_facts_rendered(self):
        results = [
            {
                "name": "list_facts",
                "args": {"category": "limits"},
                "result": {
                    "legal_year": 2026,
                    "facts": [
                        {"fact_key": "storage_limit", "variant": "free", "value_num": 1},
                        {"fact_key": "storage_limit", "variant": "plus", "value_num": 50},
                    ],
                    "truncated": False,
                },
            }
        ]
        context, _citations, _markers = _build_context(results)
        assert "Reference facts — list_facts" in context
        assert context.count("storage_limit") == 2

    def test_multiple_kb_searches_get_globally_unique_markers(self):
        # A turn can issue several kb_search calls; their markers must not collide.
        results = [
            {
                "name": "kb_search",
                "args": {},
                "result": {
                    "hits": [
                        {"text": "first", "title": "A", "source_id": "a", "url": "https://x/a"},
                        {"text": "second", "title": "B", "source_id": "b", "url": "https://x/b"},
                    ]
                },
            },
            {
                "name": "kb_search",
                "args": {},
                "result": {
                    "hits": [
                        {"text": "third", "title": "C", "source_id": "c", "url": "https://x/c"},
                    ]
                },
            },
        ]
        context, _citations, markers = _build_context(results)
        # Continues numbering across the second call rather than restarting at KB-1.
        assert set(markers) == {"KB-1", "KB-2", "KB-3"}
        assert markers["KB-3"] == "C — https://x/c"
        assert "[KB-3]" in context and context.count("[KB-1]") == 1

    def test_multiple_tool_results_combined(self):
        results = [
            {
                "name": "kb_search",
                "args": {},
                "result": {"hits": []},
            },
            {
                "name": "list_facts",
                "args": {},
                "result": {"facts": [{"fact_key": "vat_rate", "value_num": 20.0}]},
            },
        ]
        context, citations, _markers = _build_context(results)
        assert "Knowledge base" in context
        assert "list_facts" in context
