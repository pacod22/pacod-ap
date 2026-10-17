"""(profile, variant) → ``EffectiveAgent``: everything one turn needs to know
about the agent's configuration.

Layering (lowest first): env defaults → app_settings overrides (the existing
getters, unchanged, with their own Redis caches) → the compiled overlay of the
active version → retired-model remap. A profile with no version pointer for the
variant gets exactly the legacy getters' output, all pipeline steps on and no
tool restriction — i.e. today's behaviour.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, fields, replace
from typing import Literal

from sqlalchemy import select

from app.agent_editor.compile import CompiledOverlay, PipelineFlags, compile_graph
from app.agent_editor.graph import parse_graph
from app.core.db import session_scope
from app.core.db_models import AgentVersion
from app.core.logging import get_logger
from app.services.agent_prompts import _SPEC_BY_KEY, _render_ok, get_effective_prompts
from app.services.agent_runtime_config import AgentRuntimeConfig, get_agent_runtime_config
from app.services.gateway_models import remap_model
from app.services.llm_params import ROLE_FLAT_FIELDS
from app.services.profiles import ProfileContext
from app.services.response_cache import ResponseCacheConfig, get_response_cache_config

log = get_logger(__name__)

Variant = Literal["prod", "dev"]
VARIANTS: tuple[str, ...] = ("prod", "dev")

_COMPILED_PREFIX = "agent:compiled"
_COMPILED_TTL_SECONDS = 3600  # versions are immutable → never needs invalidating

_RUNTIME_MODEL_FIELDS = (
    "primary_model",
    "classifier_model",
    "planner_model",
    "suggestions_model",
    "newsletter_model",
    "history_summary_model",
)


class NoDevDeployment(LookupError):
    """The dev variant was requested for an agent with no dev deployment."""


class NotReleased(LookupError):
    """An editor-created agent that was never published has no production
    configuration — serving its 'prod' variant would silently run the platform
    defaults (another agent's prompts and scope)."""


def turn_variant(profile: ProfileContext, requested: Variant) -> Variant:
    """The variant a turn actually runs. An unreleased agent (``release_state
    'dev'`` and never published) has only its dev deployment: a 'prod' request
    (the chat default, from an admin — users cannot reach it) runs that, or is
    refused when there is none yet."""
    if (
        requested == "prod"
        and profile.release_state == "dev"
        and profile.published_version_id is None
    ):
        if profile.dev_version_id is None:
            raise NotReleased(profile.id)
        return "dev"
    return requested


@dataclass(frozen=True)
class EffectiveAgent:
    profile: ProfileContext
    variant: Variant
    version_id: int | None
    runtime: AgentRuntimeConfig
    prompts: dict[str, str]
    respcache: ResponseCacheConfig
    pipeline: PipelineFlags
    # None → no restriction (legacy).
    allowed_tools: frozenset[str] | None

    @property
    def cache_namespace(self) -> str:
        """The response-cache profile namespace: dev answers never mix with prod."""
        return self.profile.id if self.variant == "prod" else f"{self.profile.id}~dev"

    @property
    def managed(self) -> bool:
        return self.version_id is not None

    def tool_allowed(self, name: str) -> bool:
        return self.allowed_tools is None or name in self.allowed_tools


def version_id_for(profile: ProfileContext, variant: Variant) -> int | None:
    if variant == "dev":
        if profile.dev_version_id is None:
            raise NoDevDeployment(profile.id)
        return profile.dev_version_id
    return profile.published_version_id


# --------------------------------------------------------------------------- #
# Pure overlay application (unit-tested by the golden import tests)
# --------------------------------------------------------------------------- #


def _known(cls, values: dict) -> dict:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in values.items() if k in names}


def apply_overlay(
    overlay: CompiledOverlay | None,
    *,
    profile: ProfileContext,
    runtime: AgentRuntimeConfig,
    prompts: dict[str, str],
    respcache: ResponseCacheConfig,
) -> tuple[ProfileContext, AgentRuntimeConfig, dict[str, str], ResponseCacheConfig]:
    if overlay is not None:
        owned_runtime = _known(AgentRuntimeConfig, overlay.runtime)
        runtime = replace(runtime, **owned_runtime)
        # A role whose legacy fields the version sets is owned by the version:
        # it drops its LLM template and keeps the overlay-merged legacy values,
        # so a published agent behaves exactly as it did before templates.
        if runtime.roles:
            runtime = replace(
                runtime,
                roles={
                    role: p
                    for role, p in runtime.roles.items()
                    if not (ROLE_FLAT_FIELDS.get(role, frozenset()) & owned_runtime.keys())
                },
            )
        merged = dict(prompts)
        for key, text in overlay.prompts.items():
            spec = _SPEC_BY_KEY.get(key)
            if spec is not None and _render_ok(spec, text):
                merged[key] = text
            else:
                log.warning("agent_editor_prompt_invalid_using_base", key=key)
        prompts = merged
        respcache = replace(respcache, **_known(ResponseCacheConfig, overlay.respcache))
        prof = _known(ProfileContext, overlay.profile)
        if "languages" in prof:
            prof["languages"] = tuple(prof["languages"])
        profile = replace(profile, **prof)
    # Retired ids are remapped at resolve time — version rows are never rewritten.
    runtime = replace(
        runtime, **{f: remap_model(getattr(runtime, f)) for f in _RUNTIME_MODEL_FIELDS}
    )
    return profile, runtime, prompts, respcache


# --------------------------------------------------------------------------- #
# I/O
# --------------------------------------------------------------------------- #


async def _load_version_graph(version_id: int) -> str | None:
    """The stored graph JSON of a version (seam for tests)."""
    async with session_scope() as db:
        return (
            await db.execute(select(AgentVersion.graph).where(AgentVersion.id == version_id))
        ).scalar_one_or_none()


async def load_compiled(version_id: int, redis=None) -> CompiledOverlay | None:
    """The compiled overlay of a version (Redis-cached). None if it is missing or
    no longer compiles — the caller then falls back to the legacy config."""
    key = f"{_COMPILED_PREFIX}:{version_id}"
    if redis is not None:
        try:
            cached = await redis.get(key)
            if cached is not None:
                return CompiledOverlay.from_json(
                    cached.decode() if isinstance(cached, bytes) else cached
                )
        except Exception as e:  # pragma: no cover - cache is best-effort
            log.warning("agent_compiled_cache_read_failed", error=str(e))
    try:
        raw = await _load_version_graph(version_id)
        if raw is None:
            log.warning("agent_version_missing", version_id=version_id)
            return None
        overlay = compile_graph(parse_graph(json.loads(raw)))
    except Exception as e:
        log.warning("agent_version_compile_failed", version_id=version_id, error=str(e))
        return None
    if redis is not None:
        try:
            await redis.set(key, overlay.to_json(), ex=_COMPILED_TTL_SECONDS)
        except Exception as e:  # pragma: no cover - cache is best-effort
            log.warning("agent_compiled_cache_write_failed", error=str(e))
    return overlay


async def resolve_with_overlay(
    profile: ProfileContext,
    overlay: CompiledOverlay | None,
    *,
    variant: Variant = "prod",
    version_id: int | None = None,
    redis=None,
) -> EffectiveAgent:
    """Base getters (concurrently) + the given overlay. Never raises on config
    outages — the getters degrade to env defaults."""
    runtime, prompts, respcache = await asyncio.gather(
        get_agent_runtime_config(redis, profile_id=profile.id),
        get_effective_prompts(redis, profile_id=profile.id),
        get_response_cache_config(redis, profile_id=profile.id),
    )
    prof, runtime, prompts, respcache = apply_overlay(
        overlay,
        profile=profile,
        runtime=runtime,
        prompts=prompts,
        respcache=respcache,
    )
    return EffectiveAgent(
        profile=prof,
        variant=variant,
        version_id=version_id if overlay is not None else None,
        runtime=runtime,
        prompts=prompts,
        respcache=respcache,
        pipeline=overlay.pipeline if overlay is not None else PipelineFlags(),
        allowed_tools=(
            frozenset(overlay.allowed_tools)
            if overlay is not None and overlay.allowed_tools is not None
            else None
        ),
    )


async def resolve_effective_agent(
    profile: ProfileContext, variant: Variant = "prod", *, redis=None
) -> EffectiveAgent:
    """The agent configuration for one turn. Raises NoDevDeployment for
    ``variant='dev'`` without a dev deployment (authorization is the caller's job)."""
    version_id = version_id_for(profile, variant)
    overlay = await load_compiled(version_id, redis) if version_id is not None else None
    return await resolve_with_overlay(
        profile, overlay, variant=variant, version_id=version_id, redis=redis
    )
