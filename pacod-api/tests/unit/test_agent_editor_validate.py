"""Agent-editor graph validation + compilation."""

from __future__ import annotations

import copy

from app.agent_editor.compile import CompiledOverlay, compile_graph
from app.agent_editor.graph import parse_graph
from app.agent_editor.importer import blank_graph
from app.agent_editor.layout import normalize_edges
from app.agent_editor.validate import has_errors, validate_graph


def _codes(issues):
    return {i.code for i in issues}


def _node(graph, type_):
    return next(n for n in graph["nodes"] if n["type"] == type_)


def _chat(**kw):
    """A blank chat graph with its topic scope filled in (valid)."""
    g = blank_graph("rag_chat_v1", **kw)
    _node(g, "guard.topic")["config"]["topic_description_en"] = "Gardening questions"
    return g


def test_blank_chat_graph_is_valid():
    g = _chat(name="X")
    _, issues = validate_graph(g)
    assert not has_errors(issues), issues


def test_new_chat_agent_must_describe_its_topic():
    g = blank_graph("rag_chat_v1")
    issues = validate_graph(g)[1]
    assert [i.code for i in issues if i.severity == "error"] == ["topic_scope_missing"]
    _node(g, "guard.topic")["enabled"] = False  # no guard → no scope needed
    assert not has_errors(validate_graph(g)[1])


def test_missing_required_node():
    g = _chat()
    g["nodes"] = [n for n in g["nodes"] if n["type"] != "generate.synthesizer"]
    _, issues = validate_graph(g)
    assert "missing_required_node" in _codes(issues)


def test_required_node_cannot_be_disabled():
    g = _chat()
    _node(g, "generate.synthesizer")["enabled"] = False
    _, issues = validate_graph(g)
    assert "node_required_enabled" in _codes(issues)


def test_duplicate_tool_and_single_slot_overfill():
    g = _chat()
    g["nodes"].append({"id": "kb2", "type": "tool.kb_search", "config": {}})
    g["nodes"].append({"id": "p2", "type": "plan.planner", "config": {}})
    codes = _codes(validate_graph(g)[1])
    assert {"duplicate_tool", "slot_overfilled"} <= codes


def test_invalid_config_value_and_unknown_field():
    g = _chat()
    _node(g, "generate.synthesizer")["config"] = {"temperature": 7, "nope": 1}
    issues = validate_graph(g)[1]
    assert "invalid_config" in _codes(issues)
    assert all(i.node_id == "synth" for i in issues if i.code == "invalid_config")


def test_prompt_must_keep_required_tokens():
    g = _chat()
    _node(g, "guard.topic")["config"] = {"topic_classifier_prompt": "no placeholder here"}
    issues = validate_graph(g)[1]
    bad = [i for i in issues if i.code == "invalid_prompt"]
    assert bad and bad[0].path.endswith("/config/topic_classifier_prompt")


def test_unknown_and_retired_models():
    g = _chat()
    _node(g, "generate.synthesizer")["config"] = {"primary_model": "nope/model"}
    _node(g, "plan.planner")["config"] = {"planner_model": "anthropic/claude-sonnet-5"}
    issues = validate_graph(g, known_models={"anthropic/claude-sonnet-5-5"})[1]
    codes = {(i.code, i.severity) for i in issues}
    assert ("unknown_model", "error") in codes
    assert ("retired_model", "warning") in codes
    # No registry → model ids are not checked.
    assert "unknown_model" not in _codes(validate_graph(g, known_models=set())[1])


def test_planner_off_without_direct_tools_warns():
    g = _chat()
    _node(g, "plan.planner")["enabled"] = False
    g["nodes"] = [n for n in g["nodes"] if n["type"] != "tool.kb_search"]
    issues = validate_graph(g)[1]
    assert "planner_off_no_direct_tools" in _codes(issues)
    assert not has_errors(issues)


def test_marketing_completeness_and_icon_allowlist():
    g = _chat()
    g["meta"]["marketing"] = {"slug": "Bad Slug", "show_on_landing": True, "icon": "<svg>"}
    codes = _codes(validate_graph(g, taken_marketing_slugs={"tax"})[1])
    assert {"invalid_slug", "invalid_icon", "incomplete_card", "incomplete_page"} <= codes
    g["meta"]["marketing"]["slug"] = "tax"
    assert "slug_taken" in _codes(validate_graph(g, taken_marketing_slugs={"tax"})[1])


def test_normalize_edges_follows_template_order():
    g = _chat()
    shuffled = copy.deepcopy(g)
    shuffled["nodes"].reverse()
    shuffled["edges"] = []
    out = normalize_edges(shuffled)
    flow = [(e["source"], e["target"]) for e in out["edges"] if e["kind"] == "flow"]
    assert flow[0] == ("input", "language")
    assert ("planner", "kb") in [
        (e["source"], e["target"]) for e in out["edges"] if e["kind"] == "tool"
    ]


class TestCompile:
    def test_blank_chat_compiles_to_switches_only(self):
        ov = compile_graph(parse_graph(_chat()))
        # A new agent starts from the profile-neutral prompts (+ its topic scope).
        assert set(ov.prompts) == {
            "topic_classifier",
            "planner",
            "synthesizer_bg",
            "synthesizer_en",
            "verifier_bg",
            "verifier_en",
            "summarizer_bg",
            "summarizer_en",
        }
        assert ov.runtime == {
            "history_enabled": True,
            "verification_enabled": True,
            "web_search_enabled": True,
        }
        assert ov.respcache == {"enabled": True}
        assert set(ov.allowed_tools) == {"kb_search", "get_fact", "list_facts", "web_search"}
        assert ov.pipeline.planner and ov.pipeline.topic_guard and ov.pipeline.suggestions

    def test_disabled_and_absent_nodes_switch_features_off(self):
        g = _chat()
        _node(g, "guard.topic")["enabled"] = False
        _node(g, "post.suggestions")["enabled"] = False
        planner = _node(g, "plan.planner")
        planner["enabled"] = False
        planner["config"] = {"direct_tools": ["kb_search", "web_search"]}
        g["nodes"] = [
            n for n in g["nodes"] if n["type"] not in ("tool.web_search", "generate.verifier")
        ]
        ov = compile_graph(parse_graph(g))
        assert (
            not ov.pipeline.topic_guard and not ov.pipeline.suggestions and not ov.pipeline.planner
        )
        assert ov.runtime["web_search_enabled"] is False
        assert ov.runtime["verification_enabled"] is False
        assert "web_search" not in ov.allowed_tools  # absent node → no tool
        # Direct tools are clipped to the allowlist.
        assert ov.pipeline.direct_tools == ("kb_search",)

    def test_explicit_values_and_meta(self):
        g = _chat(legal_year=2027)
        _node(g, "generate.synthesizer")["config"] = {"primary_model": "m1", "temperature": 0.5}
        _node(g, "guard.language")["config"] = {"languages": ["bg"]}
        _node(g, "guard.topic")["config"]["scope_phrase_en"] = "gardening"
        ov = compile_graph(parse_graph(g))
        assert ov.runtime["primary_model"] == "m1" and ov.runtime["temperature"] == 0.5
        assert ov.profile == {
            "languages": ("bg",),
            "topic_description_en": "Gardening questions",
            "scope_phrase_en": "gardening",
            "legal_year": 2027,
        }

    def test_disabled_web_node_keeps_tool_but_switches_search_off(self):
        g = _chat()
        _node(g, "tool.web_search")["enabled"] = False
        _node(g, "tool.kb_search")["enabled"] = False
        ov = compile_graph(parse_graph(g))
        assert ov.runtime["web_search_enabled"] is False
        assert "web_search" in ov.allowed_tools
        assert "kb_search" not in ov.allowed_tools

    def test_json_roundtrip(self):
        ov = compile_graph(parse_graph(_chat()))
        assert CompiledOverlay.from_json(ov.to_json()) == ov
