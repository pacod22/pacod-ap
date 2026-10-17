"""The models the platform may use: the gateway's list, not a catalogue of our own.

The gateway (pacod-gw) holds the model repository, the prices, the provider credentials and
each model's life cycle; what this service's key may call is what ``GET /v1/models`` returns
(the workspace's grants). ``available_models()`` is the one resolver behind every model
dropdown and every "is this a model?" check in the admin API — the same contract the old
local catalogue had, without its restart flow.

Entries whose id starts with ``@`` are the workspace's gateway templates; the app's own LLM
templates and per-role settings pick models, not gateway templates, so those are left out.

``RETIRED_MODELS``/``remap_model`` stay for the agent editor: an immutable agent version may
name an id that has since been retired in the app. The gateway resolves successors of models
it retires itself (``follow_successor`` grants), so nothing here rewrites stored settings.
"""

from __future__ import annotations

import time

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

# How long one answer of the gateway's model list is reused (per process).
CACHE_SECONDS = 60.0
_cache: tuple[float, list[dict]] | None = None


async def _fetch() -> list[dict]:
    s = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{s.gateway_url.rstrip('/')}/v1/models",
            headers={"Authorization": f"Bearer {s.gateway_api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()
    return [m for m in data.get("data", []) if m.get("id") and not str(m["id"]).startswith("@")]


async def gateway_model_list() -> tuple[list[dict], bool]:
    """``([{"id", "owned_by"}, …], reachable)``, cached for ``CACHE_SECONDS``. On a
    gateway outage: the last good answer if there is one, else ``[]``; never raises."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_SECONDS:
        return _cache[1], True
    try:
        rows = [{"id": m["id"], "owned_by": m.get("owned_by")} for m in await _fetch()]
    except Exception as e:
        log.warning("gateway_models_list_failed", error=str(e))
        return (_cache[1] if _cache is not None else []), False
    rows.sort(key=lambda m: m["id"])
    _cache = (now, rows)
    return rows, True


async def available_models() -> list[str]:
    """The model ids offered anywhere in the admin UI (and validated against): what the
    gateway lets this platform call. Empty when the gateway is unreachable and nothing
    is cached — callers treat an empty list as "skip the check", as before."""
    rows, _ = await gateway_model_list()
    return [m["id"] for m in rows]


def reset_cache() -> None:
    global _cache
    _cache = None


# A retired model id → its successor. Kept for ids an immutable agent version may still name;
# validation flags them in drafts. The live configuration never stores one (the platform
# starts with none, and the gateway resolves the successors of what it retires).
RETIRED_MODELS: dict[str, str] = {
    "anthropic/claude-sonnet-4-6": "anthropic/claude-sonnet-5-5",
    "anthropic/claude-sonnet-5": "anthropic/claude-sonnet-5-5",
    "anthropic/claude-opus-4-8": "anthropic/claude-opus-5",
    "openai/gpt-4.1": "openai/gpt-5.6-sol",
    "openai/gpt-4.1-mini": "openai/gpt-5.6-luna",
    "gemini-3.5-flash": "gemini-3.8-flash",
    "gemini-3.1-flash-lite": "gemini-3.5-flash-lite",
    "gemini-3-flash-preview": "gemini-3.8-flash",
    "deepseek/deepseek-v4-flash": "deepseek/deepseek-flash",
    "glm/glm-5.2": "glm/glm-5.3",
    "glm/glm-4.6": "glm/glm-4.7",
}


def remap_model(model: str) -> str:
    """A retired model id → its successor; anything else unchanged. Used where a stored
    selection must not be rewritten in place (immutable agent versions)."""
    return RETIRED_MODELS.get(model, model) if model else model
