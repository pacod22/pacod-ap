"""The models the platform may use: the gateway's list, not a catalogue of our own.

The gateway (pacod-gw) holds the model repository, the prices, the provider credentials and
each model's life cycle; what this service's key may call is what ``GET /v1/models`` returns
(the workspace's grants). ``available_models()`` is the one resolver behind every model
dropdown and every "is this a model?" check in the admin API — the same contract the old
local catalogue had, without its restart flow.

The list also holds the workspace's gateway templates (``@name``, ``gateway.kind ==
"template"``): every picker offers them after the models, and a call names one as its model.
What such a call may send is decided by the model the template uses (``gateway.model``,
``capability_model``) — matched on ``@name`` itself, a template named ``@sonnet-5-fast`` that
uses Gemini would get Claude's dialect. ``gateway.model`` is the template's first model; a
template whose failover pool spans families gets the first one's dialect, and the gateway's
capability filter drops what another one rejects.

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
    return [m for m in data.get("data", []) if m.get("id")]


def _row(m: dict) -> dict:
    """One entry of ``GET /v1/models`` → ``{"id", "owned_by", "kind", "model"}``: ``kind`` is
    ``model`` or ``template``, ``model`` the model a template uses (None for a model)."""
    gw = m.get("gateway") if isinstance(m.get("gateway"), dict) else {}
    kind = gw.get("kind") or ("template" if str(m["id"]).startswith("@") else "model")
    return {
        "id": m["id"],
        "owned_by": m.get("owned_by"),
        "kind": kind,
        "model": gw.get("model") if kind == "template" else None,
    }


async def gateway_model_list() -> tuple[list[dict], bool]:
    """``([{"id", "owned_by", "kind", "model"}, …], reachable)``, the models then the
    templates, each by id; cached for ``CACHE_SECONDS``. On a gateway outage: the last good
    answer if there is one, else ``[]``; never raises."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_SECONDS:
        return _cache[1], True
    try:
        rows = [_row(m) for m in await _fetch()]
    except Exception as e:
        log.warning("gateway_models_list_failed", error=str(e))
        return (_cache[1] if _cache is not None else []), False
    rows.sort(key=lambda m: (m["kind"] != "model", m["id"]))
    _cache = (now, rows)
    return rows, True


async def available_models() -> list[str]:
    """The model ids offered anywhere in the admin UI (and validated against): what the
    gateway lets this platform call — its granted models, then its templates. Empty when
    the gateway is unreachable and nothing is cached — callers treat an empty list as
    "skip the check", as before."""
    rows, _ = await gateway_model_list()
    return [m["id"] for m in rows]


def picker_order(model_id: str) -> tuple[bool, str]:
    """Sort key for a set of ids: the models by id, then the templates (``@name``) by id —
    the order of ``available_models()`` (a plain sort would put ``@`` first)."""
    return (model_id.startswith("@"), model_id)


async def template_models() -> dict[str, str]:
    """``{"@name": the model it uses}`` for the gateway's templates in the list."""
    rows, _ = await gateway_model_list()
    return {m["id"]: m["model"] for m in rows if m["kind"] == "template" and m["model"]}


async def capability_model(model: str) -> str:
    """The model whose parameters a call on ``model`` follows: for a gateway template the
    model it uses, else ``model`` itself. Only an ``@`` id reads the list, so a call on a
    model never waits for it; a template the list does not know is returned as is (the
    gateway refuses a call on it)."""
    if not model or not model.startswith("@"):
        return model
    return (await template_models()).get(model, model)


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
