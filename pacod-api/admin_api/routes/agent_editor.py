"""Visual agent editor API — **superuser only** (every route).

The editor works on one draft graph per agent and moves two pointers:
``dev`` (admins only — deploy-dev freezes the draft as a snapshot) and
``prod`` (publish releases the draft to every assigned user; rollback re-points
it at an earlier release). All logic lives in ``app/agent_editor/service.py``.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from admin_api.dependencies import get_redis, get_vector_store, require_superuser
from admin_api.routes.llm_config import validate_template_ids
from admin_api.schemas import (
    AgentAssist,
    AgentCreate,
    AgentDraftSave,
    AgentGraphCheck,
    AgentImport,
    AgentPublish,
    AgentReleaseState,
    AgentVersionRef,
    AssistantConfigUpdate,
)
from app.agent_editor import service
from app.agent_editor.assistant import AssistantError, suggest
from app.agent_editor.assistant_config import (
    get_assistant_config,
    get_assistant_config_state,
    set_assistant_config,
)
from app.agent_editor.compile import compile_graph
from app.agent_editor.effective import (
    EffectiveAgent,
    NoDevDeployment,
    load_compiled,
    resolve_effective_agent,
    resolve_with_overlay,
)
from app.agent_editor.graph import parse_graph
from app.agent_editor.registry import registry_payload
from app.agent_editor.service import AgentEditorError
from app.core.config import get_settings
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser
from app.services.agent_prompts import PROMPT_SPECS
from app.services.gateway_models import RETIRED_MODELS, remap_model
from app.services.llm_client import LLMClient
from app.services.profiles import collection_names_for_slug, get_profile

router = APIRouter(prefix="/api/v1/agent-editor", tags=["agent-editor"])
log = get_logger(__name__)

Superuser = Annotated[AuthenticatedUser, Depends(require_superuser)]


def _raise(e: AgentEditorError) -> None:
    raise HTTPException(status_code=e.status, detail=e.detail()) from e


@router.get("/registry")
async def get_registry(_: Superuser):
    """Component library (node types + config JSON schemas), templates, prompt
    metadata (defaults + required tokens) and the selectable models."""
    payload = registry_payload()
    payload["prompts"] = {
        s.key: {
            "label": s.label,
            "description": s.description,
            "render": s.render,
            "language": s.language,
            "required_tokens": list(s.required_tokens),
            "format_keys": list(s.format_keys),
            "default_text": s.default_text,
        }
        for s in PROMPT_SPECS
    }
    payload["models"] = sorted(await service.known_models())
    payload["retired_models"] = RETIRED_MODELS
    return payload


@router.get("/agents")
async def list_agents(_: Superuser):
    return await service.list_agents()


@router.post("/agents", status_code=201)
async def create_agent(body: AgentCreate, caller: Superuser):
    # A chat agent gets its (empty) knowledge-base collections up front, like
    # POST /profiles.
    if body.template == "rag_chat_v1":
        base, _parents, curated = collection_names_for_slug(body.slug)
        vs = get_vector_store()
        try:
            await vs.ensure_collection(base)
            await vs.ensure_parents_collection(base)
            await vs.ensure_collection(curated)
        except Exception as e:
            log.warning("agent_editor_collection_provision_failed", slug=body.slug, error=str(e))
            raise HTTPException(
                status_code=502,
                detail={"code": "qdrant_unavailable", "message": "Could not provision collections"},
            ) from e
    try:
        return await service.create_agent(
            slug=body.slug, name=body.name, template_id=body.template, actor=caller.user_id
        )
    except AgentEditorError as e:
        _raise(e)


@router.post("/import-all")
async def import_all(caller: Superuser):
    """Import every profile that has no definition yet (never overwrites)."""
    return await service.ensure_agent_definitions(adopt=False, redis=get_redis())


@router.post("/adopt-all")
async def adopt_all(caller: Superuser):
    """Hand every not-yet-managed agent to the editor (behaviour-neutral)."""
    count = await service.adopt_all(redis=get_redis())
    log.info("agent_editor_adopt_all", count=count, caller=caller.user_id)
    return {"adopted": count}


@router.get("/agents/{profile_id}")
async def get_agent(profile_id: str, _: Superuser):
    try:
        return await service.get_agent(profile_id)
    except AgentEditorError as e:
        _raise(e)


@router.post("/agents/{profile_id}/import")
async def import_agent(profile_id: str, body: AgentImport, caller: Superuser):
    try:
        return await service.import_agent(profile_id, force=body.force, actor=caller.user_id)
    except AgentEditorError as e:
        _raise(e)


@router.put("/agents/{profile_id}/draft")
async def save_draft(profile_id: str, body: AgentDraftSave, caller: Superuser):
    try:
        return await service.save_draft(profile_id, body.graph, body.revision, actor=caller.user_id)
    except AgentEditorError as e:
        _raise(e)


@router.post("/agents/{profile_id}/validate")
async def validate(profile_id: str, body: AgentGraphCheck, _: Superuser):
    issues = await service.check_graph(body.graph, profile_id)
    return {"issues": [i.to_dict() for i in issues]}


def _effective_json(eff: EffectiveAgent) -> dict:
    return {
        "variant": eff.variant,
        "version_id": eff.version_id,
        "profile": asdict(eff.profile),
        "runtime": asdict(eff.runtime),
        "prompts": eff.prompts,
        "respcache": asdict(eff.respcache),
        "pipeline": asdict(eff.pipeline),
        "allowed_tools": sorted(eff.allowed_tools) if eff.allowed_tools is not None else None,
        "cache_namespace": eff.cache_namespace,
    }


def _diff(a: dict, b: dict) -> list[dict]:
    """Leaf differences between two effective-config JSONs (a vs b)."""
    out: list[dict] = []
    for group in ("runtime", "prompts", "respcache", "pipeline", "allowed_tools"):
        va, vb = a.get(group), b.get(group)
        if isinstance(va, dict) and isinstance(vb, dict):
            for key in sorted(set(va) | set(vb)):
                if va.get(key) != vb.get(key):
                    out.append(
                        {"group": group, "key": key, "value": va.get(key), "base": vb.get(key)}
                    )
        elif va != vb:
            out.append({"group": group, "key": None, "value": va, "base": vb})
    return out


@router.get("/agents/{profile_id}/effective")
async def effective(
    profile_id: str,
    _: Superuser,
    source: str = Query("draft", pattern=r"^(base|draft|prod|dev|version:\d+)$"),
):
    """The configuration a turn would run with for ``source``, plus its diff
    against what production runs today. ``base`` = the app_settings/env config
    with no version applied (what an editor field "inherits")."""
    redis = get_redis()
    profile = await get_profile(profile_id, redis=redis)
    if profile is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    try:
        if source == "base":
            eff = await resolve_with_overlay(profile, None, redis=redis)
        elif source == "draft":
            agent = await service.get_agent(profile_id)
            overlay = compile_graph(parse_graph(agent["graph"]))
            eff = await resolve_with_overlay(profile, overlay, variant="dev", redis=redis)
        elif source.startswith("version:"):
            vid = int(source.split(":", 1)[1])
            await service.get_version(profile_id, vid)  # ownership check → 404
            overlay = await load_compiled(vid, redis)
            eff = await resolve_with_overlay(
                profile, overlay, variant="dev", version_id=vid, redis=redis
            )
        else:
            eff = await resolve_effective_agent(profile, source, redis=redis)  # type: ignore[arg-type]
    except AgentEditorError as e:
        _raise(e)
    except NoDevDeployment as e:
        raise HTTPException(status_code=409, detail={"code": "no_dev_deployment"}) from e
    except Exception as e:  # unparseable draft
        raise HTTPException(
            status_code=422, detail={"code": "invalid_graph", "message": str(e)}
        ) from e
    prod = await resolve_effective_agent(profile, "prod", redis=redis)
    current = _effective_json(eff)
    return {**current, "diff_vs_prod": _diff(current, _effective_json(prod))}


@router.post("/agents/{profile_id}/deploy-dev")
async def deploy_dev(profile_id: str, caller: Superuser):
    try:
        return await service.deploy_dev(profile_id, actor=caller.user_id, redis=get_redis())
    except AgentEditorError as e:
        _raise(e)


@router.post("/agents/{profile_id}/undeploy-dev")
async def undeploy_dev(profile_id: str, caller: Superuser):
    try:
        return await service.undeploy_dev(profile_id, actor=caller.user_id, redis=get_redis())
    except AgentEditorError as e:
        _raise(e)


@router.post("/agents/{profile_id}/publish")
async def publish(profile_id: str, body: AgentPublish, caller: Superuser):
    try:
        return await service.publish(
            profile_id,
            note=body.note,
            actor=caller.user_id,
            assign_to_all_users=body.assign_to_all_users,
            redis=get_redis(),
        )
    except AgentEditorError as e:
        _raise(e)


@router.post("/agents/{profile_id}/rollback")
async def rollback(profile_id: str, body: AgentVersionRef, caller: Superuser):
    try:
        return await service.rollback(
            profile_id, body.version_id, actor=caller.user_id, redis=get_redis()
        )
    except AgentEditorError as e:
        _raise(e)


@router.post("/agents/{profile_id}/release-state")
async def release_state(profile_id: str, body: AgentReleaseState, caller: Superuser):
    try:
        return await service.set_release_state(
            profile_id, body.state, actor=caller.user_id, redis=get_redis()
        )
    except AgentEditorError as e:
        _raise(e)


@router.post("/agents/{profile_id}/draft/reset")
async def reset_draft(profile_id: str, body: AgentVersionRef, caller: Superuser):
    try:
        return await service.reset_draft(profile_id, body.version_id, actor=caller.user_id)
    except AgentEditorError as e:
        _raise(e)


@router.get("/agents/{profile_id}/versions")
async def versions(profile_id: str, _: Superuser):
    return await service.list_versions(profile_id)


@router.get("/agents/{profile_id}/versions/{version_id}")
async def version(profile_id: str, version_id: int, _: Superuser):
    try:
        return await service.get_version(profile_id, version_id)
    except AgentEditorError as e:
        _raise(e)


@router.get("/agents/{profile_id}/deployments")
async def deployments(profile_id: str, _: Superuser):
    return await service.list_deployments(profile_id)


# --------------------------------------------------------------------------- #
# AI assistant (drafts component content) + its global LLM config
# --------------------------------------------------------------------------- #


async def _assistant_state(redis) -> dict:
    state = await get_assistant_config_state(redis)
    cfg = await get_assistant_config(redis)
    return {
        **state,
        "effective_model": cfg.call().model,
        # What the assistant sends (its LLM template, or the fields above).
        "call_params": cfg.call().to_dict(),
        # What a blank model means: the platform's primary model.
        "platform_default_model": remap_model(get_settings().agent_primary_model),
        "available_models": sorted(await service.known_models()),
    }


@router.get("/assistant-config")
async def read_assistant_config(_: Superuser):
    return await _assistant_state(get_redis())


@router.patch("/assistant-config")
async def update_assistant_config(body: AssistantConfigUpdate, caller: Superuser):
    updates = body.model_dump(exclude_unset=True, exclude_none=True)
    model = updates.get("model")
    if model:
        available = await service.known_models()
        if available and model not in available:
            raise HTTPException(
                status_code=422,
                detail={"code": "unknown_model", "message": f"{model!r} is not a registered model"},
            )
    await validate_template_ids(updates, ("template",))
    redis = get_redis()
    await set_assistant_config(updates, updated_by=caller.user_id, redis=redis)
    log.info("agent_editor_assistant_config_updated", fields=sorted(updates), caller=caller.user_id)
    return await _assistant_state(redis)


@router.post("/agents/{profile_id}/assist")
async def assist(profile_id: str, body: AgentAssist, caller: Superuser):
    """Draft values for one component's text fields (nothing is saved)."""
    redis = get_redis()
    profile = await get_profile(profile_id, redis=redis)
    if profile is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    try:
        graph = parse_graph(body.graph)
    except Exception as e:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_graph", "message": str(e)}
        ) from e
    # What an un-overridden field inherits (app_settings → env): the starting text.
    base = _effective_json(await resolve_with_overlay(profile, None, redis=redis))
    profile_desc = {
        "name": profile.name,
        "languages": list(profile.languages),
        "topic_description_bg": profile.topic_description_bg,
        "topic_description_en": profile.topic_description_en,
    }
    try:
        result = await suggest(
            graph=graph,
            node_id=body.node_id,
            instruction=body.instruction,
            field_name=body.field,
            base=base,
            profile_desc=profile_desc,
            cfg=await get_assistant_config(redis),
            llm=LLMClient(),
            user_id=caller.user_id,
            profile_id=profile_id,
        )
    except AssistantError as e:
        raise HTTPException(
            status_code=e.status, detail={"code": e.code, "message": e.message}
        ) from e
    return result.to_dict()
