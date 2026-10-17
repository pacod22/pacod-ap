"""LLM templates: the live link, the settings-key table, and the knobs reaching
the client (stream timeout, loop stage params). No database: the service's
DB reads are monkeypatched.
"""

from __future__ import annotations

import fakeredis.aioredis
import pytest

import app.services.llm_client as lc
from app.agent_editor import assistant_config
from app.services import agent_runtime_config, llm_templates, loop_runtime_config
from app.services.llm_client import ChatReply
from app.services.llm_params import LLMCallParams
from improvement_loop.llm import call_parsed

pytestmark = pytest.mark.asyncio

ROW = {
    "id": "fast",
    "name": "Fast",
    "description": "",
    "model": "openai/gpt-5.6-luna",
    "max_output_tokens": 600,
    "temperature": None,
    "thinking_budget_tokens": 0,
    "reasoning_effort": "low",
    "top_p": None,
    "frequency_penalty": None,
    "presence_penalty": None,
    "timeout_seconds": 30.0,
}


# ---- the settings-key table mirrors the three field tables -----------------


async def test_template_setting_keys_match_the_config_field_tables():
    declared = set()
    for fields in (
        agent_runtime_config._FIELDS,
        loop_runtime_config._FIELDS,
        assistant_config._FIELDS,
    ):
        declared |= {key for name, key, _ in fields if name.endswith("template")}
    assert declared == set(llm_templates.TEMPLATE_SETTING_KEYS)


# ---- the live link -----------------------------------------------------------


@pytest.fixture
def templates_db(monkeypatch):
    rows = {"fast": dict(ROW)}
    calls = {"n": 0}

    async def fetch_all():
        calls["n"] += 1
        return [dict(r) for r in rows.values()]

    monkeypatch.setattr(llm_templates, "_fetch_all", fetch_all)
    return rows, calls


async def test_template_edit_reaches_every_profile_without_a_profile_cache_bust(
    templates_db, monkeypatch
):
    rows, _ = templates_db
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async def overrides(pid):
        return {"planner_template": "fast"}

    monkeypatch.setattr(agent_runtime_config, "_load_overrides", overrides)
    for pid in ("p1", "p2"):
        rc = await agent_runtime_config.get_agent_runtime_config(redis, profile_id=pid)
        assert rc.call("planner").max_tokens == 600

    # Edit the template: only the global template cache is busted.
    rows["fast"]["max_output_tokens"] = 1234
    await llm_templates.invalidate_template_cache(redis)
    assert await redis.get("config:agent_runtime:p1") is not None  # still cached (ids only)
    for pid in ("p1", "p2"):
        rc = await agent_runtime_config.get_agent_runtime_config(redis, profile_id=pid)
        assert rc.call("planner").max_tokens == 1234


async def test_legacy_only_config_never_reads_templates(templates_db, monkeypatch):
    _, calls = templates_db

    async def overrides(pid):
        return {}

    monkeypatch.setattr(agent_runtime_config, "_load_overrides", overrides)
    rc = await agent_runtime_config.get_agent_runtime_config(None, profile_id="p")
    assert rc.roles == {} and calls["n"] == 0


async def test_template_map_is_cached_and_outage_safe(templates_db, monkeypatch):
    _, calls = templates_db
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    assert "fast" in await llm_templates.get_template_map(redis)
    assert "fast" in await llm_templates.get_template_map(redis)
    assert calls["n"] == 1

    async def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(llm_templates, "_fetch_all", boom)
    assert await llm_templates.get_template_map(None) == {}


async def test_loop_and_assistant_resolve_their_templates(templates_db, monkeypatch):
    async def loop_ov(pid):
        return {"judge_template": "fast"}

    async def assistant_ov(pid):
        return {"template": "fast"}

    monkeypatch.setattr(loop_runtime_config, "_load_overrides", loop_ov)
    monkeypatch.setattr(assistant_config, "_load_overrides", assistant_ov)
    loop = await loop_runtime_config.get_loop_config(None, profile_id="p")
    assert loop.stage("judge").model == "openai/gpt-5.6-luna"
    assert loop.resolve_panel().models["judge"] == "openai/gpt-5.6-luna"
    assistant = await assistant_config.get_assistant_config(None)
    assert assistant.call().reasoning_effort == "low"


async def test_delete_in_use_is_refused(monkeypatch):
    async def usages(tid=None):
        return [{"template_id": "fast", "profile_id": "p", "group": "agent", "role": "planner"}]

    monkeypatch.setattr(llm_templates, "template_usages", usages)
    with pytest.raises(llm_templates.TemplateInUse) as info:
        await llm_templates.delete_template("fast")
    assert info.value.usages[0]["role"] == "planner"


async def test_slugify_and_id_rules():
    assert llm_templates.slugify("Fast · GPT 5.6!") == "fast-gpt-5-6"
    assert llm_templates.valid_id("planner-1a2b3c4d")
    assert not llm_templates.valid_id("Planner")
    assert not llm_templates.valid_id("-x")


# ---- the knobs reach the client ---------------------------------------------


async def test_stream_honours_a_per_call_timeout_and_template_knobs():
    captured: dict = {}

    class _Stream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    async def create(**kw):
        captured.update(kw)
        return _Stream()

    client = lc.LLMClient()
    client._client.chat.completions.create = create  # type: ignore[method-assign]
    async for _ in client.chat_stream(
        model="deepseek/deepseek-v4-pro",
        system="s",
        user="u",
        purpose="t",
        thinking_budget_tokens=1000,
        timeout=77.0,
        reasoning_effort="low",
        top_p=0.8,
    ):
        pass
    assert captured["timeout"] == 77.0
    assert captured["top_p"] == 0.8
    # The template's effort, next to the gateway's attribution object (never in place of it).
    assert captured["extra_body"]["reasoning_effort"] == "low"
    assert captured["extra_body"]["gateway"]["task"] == "t"

    captured.clear()
    async for _ in client.chat_stream(model="m", system="s", user="u", purpose="t"):
        pass
    assert "timeout" not in captured and "top_p" not in captured


class _Meta:
    def __init__(self):
        self.calls: list[dict] = []

    async def chat_json_meta(self, **kw) -> ChatReply:
        self.calls.append(kw)
        return ChatReply(text='{"verdict": "ok"}', finish_reason="stop")


async def test_loop_stage_params_reach_the_client():
    from pydantic import BaseModel

    class _Out(BaseModel):
        verdict: str = ""

    llm = _Meta()
    p = LLMCallParams("m", 100, None, 0, "low", None, None, None, 33.0)
    await call_parsed(
        llm, model="m", system="s", user="u", purpose="x", max_tokens=100, schema=_Out, params=p
    )
    kw = llm.calls[0]
    assert kw["temperature"] is None
    assert kw["reasoning_effort"] == "low"
    assert kw["timeout"] == 33.0

    llm = _Meta()
    await call_parsed(
        llm, model="m", system="s", user="u", purpose="x", max_tokens=100, schema=_Out
    )
    kw = llm.calls[0]
    assert kw["temperature"] == 0.0 and "reasoning_effort" not in kw  # legacy shape


async def test_roles_never_reach_the_profile_cache(templates_db, monkeypatch):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async def overrides(pid):
        return {"planner_template": "fast"}

    monkeypatch.setattr(agent_runtime_config, "_load_overrides", overrides)
    rc = await agent_runtime_config.get_agent_runtime_config(redis, profile_id="p")
    assert rc.roles
    import json

    cached = json.loads(await redis.get("config:agent_runtime:p"))
    assert cached["roles"] == {} and cached["planner_template"] == "fast"


async def test_a_pre_template_cache_entry_falls_through_to_the_db(templates_db, monkeypatch):
    # An old-shape entry (written by a not-yet-rebuilt process) must NOT parse as
    # "every role Legacy": a full-form save from that view would wipe the
    # assignments. The *_template fields have no dataclass default for this.
    import json
    from dataclasses import asdict

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    old = asdict(agent_runtime_config.env_runtime_config())
    for k in [k for k in old if k.endswith("_template")] + ["roles"]:
        old.pop(k)
    await redis.set("config:agent_runtime:p", json.dumps(old))

    async def overrides(pid):
        return {"planner_template": "fast"}

    monkeypatch.setattr(agent_runtime_config, "_load_overrides", overrides)
    rc = await agent_runtime_config.get_agent_runtime_config(redis, profile_id="p")
    assert rc.planner_template == "fast"
    assert rc.call("planner").model == "openai/gpt-5.6-luna"


async def test_only_a_superuser_may_change_a_template_the_assistant_uses(monkeypatch):
    from fastapi import HTTPException

    from admin_api.routes import llm_templates as routes
    from app.guards.auth import AuthenticatedUser

    async def usages(tid=None):
        return [
            {
                "template_id": tid,
                "profile_id": "__global__",
                "group": "assistant",
                "role": "assistant",
            }
        ]

    monkeypatch.setattr(llm_templates, "template_usages", usages)
    admin = AuthenticatedUser(user_id="a", email="a@x", roles=("admin",))
    su = AuthenticatedUser(user_id="s", email="s@x", roles=("admin", "superuser"))
    with pytest.raises(HTTPException) as info:
        await routes._guard_assistant_template("t", admin)
    assert info.value.status_code == 403
    await routes._guard_assistant_template("t", su)  # no raise
