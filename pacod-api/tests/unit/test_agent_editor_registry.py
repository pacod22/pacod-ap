"""The agent-editor component registry stays in sync with the settings it writes."""

from __future__ import annotations

from dataclasses import fields

from app.agent_editor.compile import PipelineFlags
from app.agent_editor.registry import (
    NODE_TYPES,
    TARGET_GROUPS,
    TEMPLATES,
    all_owned_targets,
    registry_payload,
)
from app.services.agent_prompts import _SPEC_BY_KEY
from app.services.agent_runtime_config import AgentRuntimeConfig
from app.services.profiles import ProfileContext
from app.services.response_cache import ResponseCacheConfig

_GROUP_FIELDS = {
    "runtime": {f.name for f in fields(AgentRuntimeConfig)},
    "prompt": set(_SPEC_BY_KEY),
    "respcache": {f.name for f in fields(ResponseCacheConfig)},
    "profile": {f.name for f in fields(ProfileContext)},
    "pipeline": {f.name for f in fields(PipelineFlags)},
}


def test_every_target_exists():
    for target in all_owned_targets():
        group, name = target.split(".", 1)
        assert group in TARGET_GROUPS, target
        assert name in _GROUP_FIELDS[group], f"{target} does not exist"


def test_no_target_owned_twice():
    targets = all_owned_targets()
    dupes = {t for t in targets if targets.count(t) > 1}
    assert not dupes, dupes


def test_every_template_slot_type_is_registered_with_matching_kind():
    for template in TEMPLATES.values():
        for slot in template.slots:
            for t in slot.allowed:
                assert t in NODE_TYPES, t
                assert NODE_TYPES[t].kind == template.kind, t


def test_chat_pipeline_prompts_are_all_owned():
    owned = {t.split(".", 1)[1] for t in all_owned_targets() if t.startswith("prompt.")}
    for key in (
        "topic_classifier",
        "planner",
        "synthesizer_bg",
        "synthesizer_en",
        "verifier_bg",
        "verifier_en",
        "suggestions_bg",
        "suggestions_en",
        "summarizer_bg",
        "summarizer_en",
    ):
        assert key in owned, key


def test_payload_has_json_schema_with_widget_hints():
    payload = registry_payload()
    synth = next(n for n in payload["node_types"] if n["type"] == "generate.synthesizer")
    props = synth["config_schema"]["properties"]
    assert props["primary_model"]["x-widget"] == "model"
    assert props["synthesizer_bg"]["x-prompt-key"] == "synthesizer_bg"
    assert props["primary_model"]["x-target"] == "runtime.primary_model"
