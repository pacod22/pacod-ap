"""Admin routes for runtime LLM & web-search configuration.

GET   /api/v1/llm-config   — effective + env-default config and the available models
PATCH /api/v1/llm-config   — switch active models, tune params, configure web search

Live config (models, generation params, web-search enable/provider/allowlist) is
persisted in `app_settings` and cached in Redis (see app/services/agent_runtime_config.py);
changes take effect on the next chat turn with no restart. The available-model list
is what the gateway lets this platform call (app/services/gateway_models.py).

The web-search key endpoints live in this router too. LLM provider keys are the
gateway's (its console, or scripts/gw_bootstrap.py from providers.env).
"""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis

from admin_api.dependencies import get_redis, require_admin
from admin_api.schemas import (
    LLMConfigRead,
    LLMConfigUpdate,
    ProviderKeysRead,
    ProviderKeyUpdate,
)
from app.agent_editor.legacy_lock import published_overlay, with_owned, without_owned
from app.core.db_models import DEFAULT_PROFILE_ID
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser
from app.services import provider_secrets
from app.services.agent_runtime_config import (
    AgentRuntimeConfig,
    get_agent_runtime_config,
    get_agent_runtime_config_state,
    set_agent_runtime_config,
)
from app.services.gateway_models import available_models
from app.services.llm_params import AGENT_ROLES, ROLE_FLAT_FIELDS
from app.services.llm_templates import get_template_map

router = APIRouter(prefix="/api/v1/llm-config", tags=["llm-config"])
log = get_logger(__name__)

_MODEL_FIELDS = (
    "primary_model",
    "classifier_model",
    "planner_model",
    "suggestions_model",
    "newsletter_model",
    "history_summary_model",
)


async def _available_models() -> list[str]:
    """The models an admin may pick — the global catalogue ∪ the live gateway
    registry. Kept as a thin alias so every panel reads one resolver
    (`app/services/gateway_models.available_models`); empty on a gateway outage."""
    return await available_models()


_TEMPLATE_FIELDS = tuple(f"{role}_template" for role in AGENT_ROLES)


def _role_view(rc: AgentRuntimeConfig, owned: dict) -> tuple[dict[str, dict], list[str]]:
    """({role: effective call params}, managed roles) — mirrors the runtime: a role
    whose legacy fields the published version sets ignores its template and runs
    the overlay-merged legacy values (see app/agent_editor/effective.apply_overlay)."""
    known = {k: v for k, v in owned.items() if k in AgentRuntimeConfig.__dataclass_fields__}
    merged = replace(rc, **known) if known else rc
    managed = [r for r in AGENT_ROLES if ROLE_FLAT_FIELDS[r] & known.keys()]
    if managed:
        merged = replace(merged, roles={k: v for k, v in rc.roles.items() if k not in managed})
    return {r: merged.call(r).to_dict() for r in AGENT_ROLES}, managed


async def _read(redis: Redis, profile_id: str = DEFAULT_PROFILE_ID) -> LLMConfigRead:
    state = await get_agent_runtime_config_state(redis, profile_id=profile_id)
    overlay = await published_overlay(profile_id, redis)
    owned = overlay.runtime if overlay else {}
    role_params, managed_roles = _role_view(
        await get_agent_runtime_config(redis, profile_id=profile_id), owned
    )
    return LLMConfigRead(
        effective=with_owned(state["effective"], owned),
        env_default=state["env_default"],
        available_models=await _available_models(),
        managed_fields=sorted(owned),
        role_params=role_params,
        managed_roles=managed_roles,
    )


async def validate_template_ids(updates: dict, fields: tuple[str, ...]) -> None:
    """422 ``unknown_template`` for a non-blank template id that does not exist.
    Shared by every panel that assigns templates."""
    chosen = {f: updates[f] for f in fields if updates.get(f)}
    if not chosen:
        return
    templates = await get_template_map(None)
    for field, tid in chosen.items():
        if tid not in templates:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "unknown_template",
                    "message": f"{field}={tid!r} is not an LLM template",
                },
            )


@router.get("", response_model=LLMConfigRead)
async def read_llm_config(
    profile_id: str = DEFAULT_PROFILE_ID,
    _: AuthenticatedUser = Depends(require_admin),
    redis: Redis = Depends(get_redis),
):
    return await _read(redis, profile_id)


@router.patch("", response_model=LLMConfigRead)
async def update_llm_config(
    body: LLMConfigUpdate,
    profile_id: str = DEFAULT_PROFILE_ID,
    caller: AuthenticatedUser = Depends(require_admin),
    redis: Redis = Depends(get_redis),
):
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return await _read(redis, profile_id)
    overlay = await published_overlay(profile_id, redis)
    updates = without_owned(updates, overlay.runtime if overlay else None)
    if not updates:
        return await _read(redis, profile_id)

    # Validate any model selection against the live registry (skip when the
    # registry is unavailable, and allow an empty planner_model = "fall back").
    available = await _available_models()
    if available:
        for field in _MODEL_FIELDS:
            val = updates.get(field)
            if val and val not in available:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "unknown_model",
                        "message": f"{field}={val!r} is not a registered model",
                    },
                )

    await validate_template_ids(updates, _TEMPLATE_FIELDS)

    await set_agent_runtime_config(
        updates, updated_by=caller.user_id, redis=redis, profile_id=profile_id
    )
    log.info(
        "admin_llm_config_updated",
        fields=sorted(updates),
        caller=caller.user_id,
        profile_id=profile_id,
    )
    return await _read(redis, profile_id)


# --------------------------------------------------------------------------- #
# Provider API keys
# --------------------------------------------------------------------------- #
@router.get("/keys", response_model=ProviderKeysRead)
async def read_keys(_: AuthenticatedUser = Depends(require_admin)):
    return ProviderKeysRead(
        editable=provider_secrets.is_editable(),
        keys=await provider_secrets.list_secret_status(),
    )


@router.put("/keys/{name}", response_model=ProviderKeysRead)
async def set_key(
    name: str,
    body: ProviderKeyUpdate,
    caller: AuthenticatedUser = Depends(require_admin),
):
    if not provider_secrets.known_key(name):
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_key", "message": f"{name} is not a managed key"},
        )
    if not provider_secrets.is_editable():
        raise HTTPException(
            status_code=503,
            detail={"code": "secrets_disabled", "message": "APP_SECRETS_KEY is not configured"},
        )
    await provider_secrets.set_secret(name, body.value, updated_by=caller.user_id)
    log.info("admin_provider_key_set", name=name, caller=caller.user_id)
    return ProviderKeysRead(editable=True, keys=await provider_secrets.list_secret_status())


@router.delete("/keys/{name}", response_model=ProviderKeysRead)
async def delete_key(
    name: str,
    caller: AuthenticatedUser = Depends(require_admin),
):
    if not provider_secrets.known_key(name):
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_key", "message": f"{name} is not a managed key"},
        )
    await provider_secrets.delete_secret(name)
    log.info("admin_provider_key_deleted", name=name, caller=caller.user_id)
    return ProviderKeysRead(
        editable=provider_secrets.is_editable(),
        keys=await provider_secrets.list_secret_status(),
    )
