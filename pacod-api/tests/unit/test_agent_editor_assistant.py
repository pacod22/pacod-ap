"""The visual editor's AI assistant: field selection, context, validation and
the one-shot repair round — with a scripted fake LLM."""

from __future__ import annotations

import json

import pytest

from app.agent_editor import assistant
from app.agent_editor.assistant import AssistantError, assistable_fields, suggest
from app.agent_editor.assistant_config import AssistantConfig
from app.agent_editor.graph import parse_graph
from app.agent_editor.importer import blank_graph
from app.agent_editor.registry import NODE_TYPES

GOOD_CLASSIFIER = (
    "You classify gardening questions.\nScope:\n{topic_description}\n"
    'Return {{"is_relevant": true}}'
)


def _cfg(**kw) -> AssistantConfig:
    base = {
        "enabled": True,
        "model": "m-test",
        "temperature": 0.4,
        "temperature_enabled": True,
        "max_output_tokens": 2000,
        "thinking_budget_tokens": 0,
        "guidelines": "",
        "template": "",
    }
    base.update(kw)
    return AssistantConfig(**base)


class _LLM:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def chat_json(self, **kw):
        self.calls.append(kw)
        return json.dumps(self.replies.pop(0))


@pytest.fixture(autouse=True)
def _no_usage_writes(monkeypatch):
    recorded = []

    async def rec(**kw):
        recorded.append(kw)

    monkeypatch.setattr(assistant, "record_usage_events", rec)
    return recorded


def _graph():
    g = blank_graph("rag_chat_v1", name="Garden helper")
    topic = next(n for n in g["nodes"] if n["type"] == "guard.topic")
    topic["config"]["topic_description_en"] = "Gardening and plant care"
    # Not set on the node → the assistant starts from the INHERITED text.
    topic["config"].pop("topic_classifier_prompt", None)
    return parse_graph(g)


async def _run(llm, node_id="topic", field=None, cfg=None, instruction="Make it about gardening"):
    return await suggest(
        graph=_graph(),
        node_id=node_id,
        instruction=instruction,
        field_name=field,
        base={"prompts": {"topic_classifier": "INHERITED CLASSIFIER {topic_description}"}},
        profile_desc={"name": "Garden helper", "languages": ["bg", "en"]},
        cfg=cfg or _cfg(),
        llm=llm,
        user_id="su",
        profile_id="garden",
    )


def test_only_text_fields_are_assistable():
    assert set(assistable_fields(NODE_TYPES["plan.planner"])) == {"planner_prompt"}
    topic = set(assistable_fields(NODE_TYPES["guard.topic"]))
    assert {"topic_classifier_prompt", "topic_description_en", "scope_phrase_bg"} <= topic
    assert "classifier_model" not in topic and "classifier_max_tokens" not in topic
    assert set(assistable_fields(NODE_TYPES["tool.web_search"])) == {"allowed_domains"}
    assert assistable_fields(NODE_TYPES["guard.quota"]) == {}


async def test_suggests_valid_fields_with_rich_context(_no_usage_writes):
    llm = _LLM(
        {
            "fields": {"topic_classifier_prompt": GOOD_CLASSIFIER, "scope_phrase_en": "gardening"},
            "notes": "ok",
        }
    )
    res = await _run(llm, cfg=_cfg(guidelines="Always be polite."))
    assert res.fields == {
        "topic_classifier_prompt": GOOD_CLASSIFIER,
        "scope_phrase_en": "gardening",
    }
    assert res.issues == [] and res.notes == "ok" and res.model == "m-test"
    call = llm.calls[0]
    assert call["purpose"] == "agent_editor_assistant" and call["temperature"] == 0.4
    assert "REQUIRED verbatim: {topic_description}" in call["user"]
    assert "INHERITED CLASSIFIER" in call["user"]  # inherited text is the starting point
    assert "Gardening and plant care" in call["user"]  # agent context
    assert "Always be polite." in call["system"]
    assert _no_usage_writes and _no_usage_writes[0]["profile_id"] == "garden"


async def test_invalid_prompt_gets_one_repair_round():
    broken = "No placeholder and a stray { brace"
    llm = _LLM(
        {"fields": {"topic_classifier_prompt": broken, "scope_phrase_en": "gardening"}},
        {"fields": {"topic_classifier_prompt": GOOD_CLASSIFIER}},
    )
    res = await _run(llm)
    assert len(llm.calls) == 2
    assert "FAILED VALIDATION" in llm.calls[1]["user"]
    assert res.fields["topic_classifier_prompt"] == GOOD_CLASSIFIER and res.issues == []


async def test_still_invalid_after_repair_is_reported_not_applied():
    broken = "still no placeholder"
    llm = _LLM({"fields": {"topic_classifier_prompt": broken}}, {"fields": {}})
    res = await _run(llm, field="topic_classifier_prompt")
    assert res.fields == {}
    assert [i["field"] for i in res.issues] == ["topic_classifier_prompt"]
    assert "value" not in res.issues[0]


async def test_string_list_and_length_limits():
    llm = _LLM({"fields": {"allowed_domains": "rhs.org.uk\nwikipedia.org"}})
    res = await _run(llm, node_id="web")
    assert res.fields == {"allowed_domains": ["rhs.org.uk", "wikipedia.org"]}
    llm = _LLM({"fields": {"scope_phrase_en": "x" * 400}}, {"fields": {}})
    res = await _run(llm, field="scope_phrase_en")
    assert res.fields == {} and res.issues[0]["field"] == "scope_phrase_en"


async def test_unknown_fields_and_other_components_are_ignored():
    llm = _LLM(
        {
            "fields": {
                "classifier_model": "evil",
                "planner_prompt": "x",
                "scope_phrase_bg": "градинарство",
            }
        }
    )
    res = await _run(llm)
    assert res.fields == {"scope_phrase_bg": "градинарство"}


async def test_guards():
    with pytest.raises(AssistantError) as e:
        await _run(_LLM(), cfg=_cfg(enabled=False))
    assert e.value.status == 503
    with pytest.raises(AssistantError) as e:
        await _run(_LLM(), field="classifier_model")
    assert e.value.status == 422
    with pytest.raises(AssistantError) as e:
        await _run(_LLM(), node_id="quota")
    assert e.value.code == "nothing_to_generate"
    with pytest.raises(AssistantError) as e:
        await _run(_LLM(), node_id="nope")
    assert e.value.status == 404


async def test_send_temperature_switch():
    llm = _LLM({"fields": {"scope_phrase_en": "gardening"}})
    await _run(
        llm,
        field="scope_phrase_en",
        cfg=_cfg(temperature_enabled=False, model="anthropic/claude-sonnet-5"),
    )
    assert llm.calls[0]["temperature"] is None
    assert llm.calls[0]["model"] == "anthropic/claude-sonnet-5-5"  # retired id remapped
