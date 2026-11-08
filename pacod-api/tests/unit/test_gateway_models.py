"""The gateway's model list: its granted models, then its templates, and the model a
template uses deciding what a call on it may send.

The answers are shaped as pacod-gw's ``GET /v1/models`` (``dataplane/openai.rs``): every entry
carries ``gateway.kind`` (``model`` | ``template``) and, for a template, ``gateway.model``.
"""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
import respx

from app.agent.graph import _resolve_native_web_search
from app.core.config import get_settings
from app.services import gateway_agents
from app.services import gateway_models as gm
from app.services.agent_runtime_config import env_runtime_config
from app.services.gateway_agents import AgentState

URL = "http://pacod-gw:8080/v1/models"


def _model(mid: str, owner: str) -> dict:
    return {
        "id": mid,
        "object": "model",
        "created": 0,
        "owned_by": owner,
        "gateway": {"kind": "model", "model": None, "deprecated": False, "state": "active"},
    }


def _template(name: str, uses: str) -> dict:
    return {
        "id": f"@{name}",
        "object": "model",
        "created": 0,
        "owned_by": "template",
        "gateway": {"kind": "template", "model": uses, "deprecated": False},
    }


LISTING = {
    "object": "list",
    "data": [
        _model("openai/gpt-5.6-sol", "openai"),
        _model("anthropic/claude-sonnet-5-5", "anthropic"),
        _model("gemini-3.8-flash", "gemini"),
        # Named like an adaptive Claude model, but uses Gemini.
        _template("sonnet-5-fast", "gemini-3.8-flash"),
        _template("careful", "anthropic/claude-sonnet-5-5"),
    ],
}


@pytest.fixture(autouse=True)
def _gateway(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "gateway_url", "http://pacod-gw:8080")
    monkeypatch.setattr(settings, "gateway_api_key", "pgw_test_service_key")
    gm.reset_cache()
    gateway_agents.set_cached_state(None)  # never published: the fallback agent
    yield
    gm.reset_cache()
    gateway_agents.reset_cache()


@respx.mock
async def test_the_list_is_asked_for_as_the_services_agent():
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=LISTING))
    await gm.gateway_model_list()
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer pgw_test_service_key"
    # A key bound to several agents must name one on the list too: the fallback, never a
    # profile's, whatever the published state says of profiles.
    assert req.headers["x-gw-agent-id"] == "pacod-ap"
    gm.reset_cache()
    gateway_agents.set_cached_state(
        AgentState(mode="registered", fallback="pacod-ap", bound=frozenset({"starter"}))
    )
    await gm.gateway_model_list()
    assert route.calls[1].request.headers["x-gw-agent-id"] == "pacod-ap"
    # A gateway without registered agents: no header (the key may name none).
    gm.reset_cache()
    gateway_agents.set_cached_state(AgentState(mode="labels", fallback="pacod-ap"))
    await gm.gateway_model_list()
    assert "x-gw-agent-id" not in route.calls[2].request.headers


@respx.mock
async def test_the_models_come_first_then_the_templates_each_by_id():
    respx.get(URL).mock(return_value=httpx.Response(200, json=LISTING))
    rows, reachable = await gm.gateway_model_list()
    assert reachable
    assert [(r["id"], r["kind"], r["model"]) for r in rows] == [
        ("anthropic/claude-sonnet-5-5", "model", None),
        ("gemini-3.8-flash", "model", None),
        ("openai/gpt-5.6-sol", "model", None),
        ("@careful", "template", "anthropic/claude-sonnet-5-5"),
        ("@sonnet-5-fast", "template", "gemini-3.8-flash"),
    ]
    assert await gm.available_models() == [r["id"] for r in rows]
    assert await gm.template_models() == {
        "@careful": "anthropic/claude-sonnet-5-5",
        "@sonnet-5-fast": "gemini-3.8-flash",
    }


@respx.mock
async def test_an_entry_without_the_gateway_object_is_told_apart_by_its_at_sign():
    body = {"data": [{"id": "gemini-3.8-flash"}, {"id": "@bare", "owned_by": "template"}]}
    respx.get(URL).mock(return_value=httpx.Response(200, json=body))
    rows, _ = await gm.gateway_model_list()
    assert [(r["id"], r["kind"]) for r in rows] == [
        ("gemini-3.8-flash", "model"),
        ("@bare", "template"),
    ]
    # No model named: it cannot be resolved, so it keeps its own id.
    assert await gm.capability_model("@bare") == "@bare"


@respx.mock
async def test_a_template_resolves_to_the_model_it_uses_and_a_model_reads_no_list():
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=LISTING))
    assert await gm.capability_model("anthropic/claude-sonnet-5-5") == "anthropic/claude-sonnet-5-5"
    assert await gm.capability_model("") == ""
    assert route.call_count == 0
    assert await gm.capability_model("@sonnet-5-fast") == "gemini-3.8-flash"
    assert await gm.capability_model("@careful") == "anthropic/claude-sonnet-5-5"
    assert await gm.capability_model("@deleted") == "@deleted"
    assert route.call_count == 1  # cached


@respx.mock
async def test_an_unreachable_gateway_resolves_nothing_and_never_raises():
    respx.get(URL).mock(side_effect=httpx.ConnectError("refused"))
    assert await gm.capability_model("@careful") == "@careful"
    assert await gm.available_models() == []


def test_the_picker_order_matches_the_list():
    ids = {"@careful", "openai/gpt-5.6-sol", "anthropic/claude-sonnet-5-5", "@a"}
    assert sorted(ids, key=gm.picker_order) == [
        "anthropic/claude-sonnet-5-5",
        "openai/gpt-5.6-sol",
        "@a",
        "@careful",
    ]


@respx.mock
async def test_the_models_page_lists_the_models_only():
    from admin_api.routes.llm_models import list_llm_models

    respx.get(URL).mock(return_value=httpx.Response(200, json=LISTING))
    page = await list_llm_models(None)
    assert [m.id for m in page.models] == [
        "anthropic/claude-sonnet-5-5",
        "gemini-3.8-flash",
        "openai/gpt-5.6-sol",
    ]


@respx.mock
async def test_the_template_editor_gets_a_gateway_templates_capabilities_from_its_model(
    monkeypatch,
):
    from admin_api.routes import llm_templates as routes
    from app.services import llm_templates as svc

    row = {
        "id": "on-careful",
        "name": "On careful",
        "description": "",
        "model": "@careful",
        "max_output_tokens": 600,
        "temperature": 0.2,
        "thinking_budget_tokens": 0,
        "reasoning_effort": None,
        "top_p": None,
        "frequency_penalty": None,
        "presence_penalty": None,
        "timeout_seconds": None,
    }

    async def list_templates():
        return [row]

    async def template_usages(tid=None):
        return []

    monkeypatch.setattr(svc, "list_templates", list_templates)
    monkeypatch.setattr(svc, "template_usages", template_usages)
    respx.get(URL).mock(return_value=httpx.Response(200, json=LISTING))

    read = await routes.list_llm_templates(None)
    assert read.available_models[-2:] == ["@careful", "@sonnet-5-fast"]
    assert read.capabilities["@careful"]["family"] == "adaptive_claude"
    assert read.capabilities["@sonnet-5-fast"]["family"] == "other"
    # Adaptive Claude takes no temperature: the template's is stored but never sent.
    assert read.templates[0].ignored_params == ["temperature"]


def test_native_web_search_follows_the_model_a_template_uses():
    cfg = replace(
        env_runtime_config(),
        web_search_enabled=True,
        web_search_mode="native",
        primary_model="@careful",
        web_search_allowed_domains=["revenue.example"],
    )
    # Judged on "@careful" itself it has no native search; on its model it has.
    assert _resolve_native_web_search(cfg) is None
    native = _resolve_native_web_search(cfg, "anthropic/claude-sonnet-5-5")
    assert native is not None
    assert native["tools"][0]["allowed_domains"] == ["revenue.example"]


@respx.mock
async def test_an_app_template_can_be_saved_on_a_gateway_template(monkeypatch):
    from fastapi import HTTPException

    from admin_api.routes import llm_templates as routes
    from admin_api.schemas import LLMTemplateCreate
    from app.guards.auth import AuthenticatedUser
    from app.services import llm_templates as svc

    async def create_template(data, *, updated_by, redis):
        return {**data, "temperature": data.get("temperature")}

    monkeypatch.setattr(svc, "create_template", create_template)
    respx.get(URL).mock(return_value=httpx.Response(200, json=LISTING))

    await routes._validate_model("@careful")  # no raise
    with pytest.raises(HTTPException) as info:
        await routes._validate_model("@deleted")
    assert info.value.detail["code"] == "unknown_model"

    body = LLMTemplateCreate(name="On careful", model="@careful", max_output_tokens=600)
    out = await routes.create_llm_template(
        body, AuthenticatedUser(user_id="a", email="a@x", roles=("admin",)), None
    )
    assert out.model == "@careful"
