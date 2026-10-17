"""Admin routes for the agent's editable phase prompts.

GET  /api/v1/agent-prompts            — list every phase prompt (default + effective)
GET  /api/v1/agent-prompts/{key}      — fetch one
PUT  /api/v1/agent-prompts/{key}      — set an override  (superuser only)
POST /api/v1/agent-prompts/{key}/reset — drop the override, back to code default (superuser only)

Reads require the `admin` role; mutations require the `superuser` role — regular
admins get read-only access. Overrides live in `app_settings` (`prompt.<key>`) and
take effect on the next chat turn (Redis-cached, busted on write).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis

from admin_api.dependencies import get_redis, require_admin, require_superuser
from admin_api.schemas import PromptRead, PromptUpdate
from app.agent_editor.legacy_lock import MANAGED_CODE, published_overlay
from app.core.db_models import DEFAULT_PROFILE_ID
from app.core.logging import get_logger
from app.guards.auth import AuthenticatedUser
from app.services.agent_prompts import (
    PromptValidationError,
    get_prompt_state,
    list_prompt_states,
    reset_prompt,
    set_prompt_override,
)

router = APIRouter(prefix="/api/v1/agent-prompts", tags=["agent-prompts"])
log = get_logger(__name__)


@router.get("", response_model=list[PromptRead])
async def list_prompts(
    profile_id: str = DEFAULT_PROFILE_ID, _: AuthenticatedUser = Depends(require_admin)
):
    states = await list_prompt_states(profile_id)
    log.debug("admin_agent_prompts_listed", count=len(states))
    return await _with_owned(states, profile_id)


@router.get("/{key}", response_model=PromptRead)
async def get_prompt(
    key: str, profile_id: str = DEFAULT_PROFILE_ID, _: AuthenticatedUser = Depends(require_admin)
):
    state = await get_prompt_state(key, profile_id)
    if state is None:
        log.warning("admin_agent_prompt_not_found", key=key)
        raise HTTPException(status_code=404, detail="Prompt not found")
    return (await _with_owned([state], profile_id))[0]


async def _with_owned(states, profile_id: str) -> list[PromptRead]:
    """Prompts owned by the agent's published Agent-editor version show that
    version's text and are flagged read-only."""
    overlay = await published_overlay(profile_id, get_redis())
    owned = overlay.prompts if overlay else {}
    out = []
    for st in states:
        read = PromptRead.model_validate(st)
        if st.key in owned:
            read = read.model_copy(update={"content": owned[st.key], "managed": True})
        out.append(read)
    return out


async def _refuse_if_owned(key: str, profile_id: str, redis) -> None:
    overlay = await published_overlay(profile_id, redis)
    if overlay and key in overlay.prompts:
        raise HTTPException(
            status_code=409,
            detail={
                "code": MANAGED_CODE,
                "message": "This prompt is managed by the Agent editor.",
                "fields": [key],
            },
        )


@router.put("/{key}", response_model=PromptRead)
async def update_prompt(
    key: str,
    body: PromptUpdate,
    profile_id: str = DEFAULT_PROFILE_ID,
    caller: AuthenticatedUser = Depends(require_superuser),
    redis: Redis = Depends(get_redis),
):
    if await get_prompt_state(key, profile_id) is None:
        log.warning("admin_agent_prompt_not_found", key=key)
        raise HTTPException(status_code=404, detail="Prompt not found")
    await _refuse_if_owned(key, profile_id, redis)
    try:
        state = await set_prompt_override(
            key, body.content, updated_by=caller.user_id, redis=redis, profile_id=profile_id
        )
    except PromptValidationError as e:
        log.warning("admin_agent_prompt_invalid", key=key, error=str(e))
        raise HTTPException(
            status_code=422, detail={"code": "invalid_prompt", "message": str(e)}
        ) from e
    log.info("admin_agent_prompt_updated", key=key, caller=caller.user_id, profile_id=profile_id)
    return state


@router.post("/{key}/reset", response_model=PromptRead)
async def reset_prompt_route(
    key: str,
    profile_id: str = DEFAULT_PROFILE_ID,
    caller: AuthenticatedUser = Depends(require_superuser),
    redis: Redis = Depends(get_redis),
):
    if await get_prompt_state(key, profile_id) is None:
        log.warning("admin_agent_prompt_not_found", key=key)
        raise HTTPException(status_code=404, detail="Prompt not found")
    await _refuse_if_owned(key, profile_id, redis)
    state = await reset_prompt(key, redis=redis, profile_id=profile_id)
    log.info("admin_agent_prompt_reset", key=key, caller=caller.user_id, profile_id=profile_id)
    return state
