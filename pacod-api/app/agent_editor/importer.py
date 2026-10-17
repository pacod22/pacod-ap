"""Existing profile → an equivalent agent graph (and blank graphs for new agents).

The import is **behaviour-neutral by construction**: config fields carry only
the values a profile has actually overridden in ``app_settings`` (everything
else stays ``None`` = inherit), and every node-presence switch is set to the
profile's *current effective* value. Compiling the result and applying it on
top of the same getters reproduces today's config exactly — pinned by
``tests/unit/test_agent_editor_golden.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.agent.prompts import GENERIC_PROMPTS
from app.agent_editor.graph import SCHEMA_VERSION
from app.agent_editor.layout import auto_layout, normalize_edges
from app.agent_editor.registry import NODE_TYPES, TEMPLATE_FOR_UI_KIND, TEMPLATES
from app.services import agent_prompts, response_cache
from app.services import agent_runtime_config as runtime_config
from app.services.profiles import ProfileContext

# Short stable node ids per type (readable in URLs / issue paths).
_NODE_IDS = {
    "input.chat": "input",
    "guard.language": "language",
    "guard.quota": "quota",
    "guard.topic": "topic",
    "cache.semantic": "cache",
    "memory.compaction": "memory",
    "plan.planner": "planner",
    "tool.kb_search": "kb",
    "tool.reference_facts": "facts",
    "tool.web_search": "web",
    "generate.synthesizer": "synth",
    "generate.verifier": "verify",
    "post.suggestions": "suggest",
    "output.chat_stream": "output",
}


def node_id_for(node_type: str) -> str:
    return _NODE_IDS.get(node_type, node_type.replace(".", "_"))


_MARKETING_SEED_DIR = Path(__file__).parent / "marketing_seed"


def marketing_seed(profile_id: str) -> dict | None:
    """The public-listing copy the shipped agents had as hand-written frontend
    modules (exported once to ``marketing_seed/<profile_id>.json``), so importing
    them keeps their landing card and /agents page."""
    path = _MARKETING_SEED_DIR / f"{profile_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _meta_from_profile(profile: ProfileContext) -> dict:
    return {
        "name": profile.name,
        "description": "",
        "short_names": {
            loc: getattr(profile, f"short_name_{loc}")
            for loc in ("en", "bg", "de", "ru")
            if getattr(profile, f"short_name_{loc}", "")
        },
        "legal_year": profile.legal_year,
        "marketing": marketing_seed(profile.id),
    }


def _finish(graph: dict) -> dict:
    return auto_layout(normalize_edges(graph))


def _generic_config(node_type: str) -> dict:
    """Starting config of a node in a NEW agent: the profile-neutral prompt
    defaults (``GENERIC_PROMPTS``, baked in so the agent does not drift when a code
    default changes) + answer languages."""
    config: dict = {}
    for name, target in NODE_TYPES[node_type].field_targets().items():
        group, key = target.split(".", 1)
        if group == "prompt" and key in GENERIC_PROMPTS:
            config[name] = GENERIC_PROMPTS[key]
        elif target == "profile.languages":
            config[name] = ["bg", "en"]
    return config


def blank_graph(template_id: str, *, name: str = "", legal_year: int | None = None) -> dict:
    """A new agent: every node of the template present, generic prompts, all
    other config inherited. The topic guard's scope is left for the author to
    fill in (validation insists — see ``topic_scope_missing``)."""
    template = TEMPLATES[template_id]
    nodes = []
    for slot in template.slots:
        for t in slot.allowed:
            nodes.append(
                {"id": node_id_for(t), "type": t, "enabled": True, "config": _generic_config(t)}
            )
            if not slot.multi:
                break
    return _finish(
        {
            "schema_version": SCHEMA_VERSION,
            "template": template_id,
            "meta": {
                "name": name,
                "description": "",
                "short_names": {},
                "legal_year": legal_year,
                "marketing": None,
            },
            "nodes": nodes,
            "edges": [],
        }
    )


async def build_graph_from_profile(profile: ProfileContext, description: str | None = None) -> dict:
    """The graph equivalent of the profile's current (legacy) configuration."""
    template_id = TEMPLATE_FOR_UI_KIND.get(profile.ui_kind, "rag_chat_v1")
    template = TEMPLATES[template_id]
    pid = profile.id

    # Explicit overrides only (seams the unit tests monkeypatch).
    overrides = {
        "runtime": await runtime_config._load_overrides(pid),
        "respcache": await response_cache._load_overrides(pid),
        "prompt": {
            k: v
            for k, v in (await agent_prompts._load_overrides(pid)).items()
            if k in agent_prompts._SPEC_BY_KEY
            and agent_prompts._render_ok(agent_prompts._SPEC_BY_KEY[k], v)
        },
        "profile": {},
        "pipeline": {},
    }
    # Profile fields the agent graph owns are carried explicitly (identical to the
    # profiles row) so the topic guard shows its real scope; the guard-message
    # scope phrase moves from the code table into config.
    overrides["profile"] = {
        "languages": list(profile.languages),
        "topic_name": profile.topic_name,
        "topic_description_bg": profile.topic_description_bg,
        "topic_description_en": profile.topic_description_en,
    }

    # Current effective values for the node-presence switches.
    eff_runtime = await runtime_config.get_agent_runtime_config(None, profile_id=pid)
    eff_respcache = await response_cache.get_response_cache_config(None, profile_id=pid)
    switch_now = {
        "runtime.history_enabled": eff_runtime.history_enabled,
        "runtime.web_search_enabled": eff_runtime.web_search_enabled,
        "runtime.verification_enabled": eff_runtime.verification_enabled,
        "respcache.enabled": eff_respcache.enabled,
    }

    nodes = []
    for slot in template.slots:
        for t in slot.allowed:
            nt = NODE_TYPES[t]
            config = {}
            for name, target in nt.field_targets().items():
                group, key = target.split(".", 1)
                if key in overrides.get(group, {}):
                    value = overrides[group][key]
                    config[name] = list(value) if isinstance(value, tuple) else value
            enabled = all(switch_now.get(tgt, True) for tgt in nt.enabled_targets)
            nodes.append({"id": node_id_for(t), "type": t, "enabled": enabled, "config": config})
            if not slot.multi:
                break

    meta = _meta_from_profile(profile)
    if description:
        meta["description"] = description
    return _finish(
        {
            "schema_version": SCHEMA_VERSION,
            "template": template_id,
            "meta": meta,
            "nodes": nodes,
            "edges": [],
        }
    )
