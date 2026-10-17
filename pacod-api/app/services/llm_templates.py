"""LLM templates — reusable, named sets of LLM call parameters (global).

Each LLM role of a profile references a template through an ``app_settings``
``*_template`` row (``TEMPLATE_SETTING_KEYS``); a blank or absent row keeps the
role on its legacy flat settings. Templates are a live link: the per-profile
config caches hold only template *ids*, and the template rows themselves are
read through ONE global cache (``config:llm_templates``, 60 s, busted on every
template write) and resolved per call (``llm_params.resolve_roles``) — so a
template edit reaches every profile without fanning out per-profile cache busts.

The template map is never-raising: an outage yields ``{}`` and every role falls
back to its legacy params (logged), which is the pre-template behaviour.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from sqlalchemy import delete, select

from app.core.db import session_scope
from app.core.db_models import AppSetting, LlmTemplate
from app.core.logging import get_logger

log = get_logger(__name__)

CACHE_KEY = "config:llm_templates"
CACHE_TTL_SECONDS = 60

# app_settings key → (group, role). Groups: "agent" / "loop" are per profile;
# "assistant" lives under GLOBAL_PROFILE_ID. Mirrors the `*_template` entries
# of the three config field tables (pinned by a unit test).
TEMPLATE_SETTING_KEYS: dict[str, tuple[str, str]] = {
    "agent.synthesizer_template": ("agent", "synthesizer"),
    "agent.verifier_template": ("agent", "verifier"),
    "agent.planner_template": ("agent", "planner"),
    "agent.classifier_template": ("agent", "classifier"),
    "agent.suggestions_template": ("agent", "suggestions"),
    "agent.history_summary_template": ("agent", "history_summary"),
    "agent.newsletter_template": ("agent", "newsletter"),
    "loop.triage_template": ("loop", "triage"),
    "loop.analysis_a_template": ("loop", "analysis_a"),
    "loop.analysis_b_template": ("loop", "analysis_b"),
    "loop.judge_template": ("loop", "judge"),
    "loop.remediation_template": ("loop", "remediation"),
    "editor_assistant.template": ("assistant", "assistant"),
}

TEMPLATE_COLUMNS = (
    "name",
    "description",
    "model",
    "max_output_tokens",
    "temperature",
    "thinking_budget_tokens",
    "reasoning_effort",
    "top_p",
    "frequency_penalty",
    "presence_penalty",
    "timeout_seconds",
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class TemplateInUse(Exception):
    """Deleting a template that roles still reference."""

    def __init__(self, usages: list[dict]):
        super().__init__("template in use")
        self.usages = usages


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s or "template")[:64].rstrip("-")


def valid_id(template_id: str) -> bool:
    return bool(_ID_RE.match(template_id or ""))


def row_to_dict(r: LlmTemplate) -> dict:
    return {
        "id": r.id,
        **{c: getattr(r, c) for c in TEMPLATE_COLUMNS},
        "updated_by": r.updated_by,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
async def _fetch_all() -> list[dict]:
    """Every template row, ordered by name. Raises on DB trouble."""
    async with session_scope() as db:
        rows = (await db.execute(select(LlmTemplate).order_by(LlmTemplate.name))).scalars().all()
        return [row_to_dict(r) for r in rows]


async def list_templates() -> list[dict]:
    try:
        return await _fetch_all()
    except Exception as e:  # pragma: no cover - a list must not break a page
        log.warning("llm_templates_list_failed", error=str(e))
        return []


async def get_template_map(redis=None) -> dict[str, dict]:
    """{template_id: row dict} — Redis-cached, never raises (``{}`` on outage)."""
    if redis is not None:
        try:
            cached = await redis.get(CACHE_KEY)
            if cached is not None:
                return json.loads(cached.decode() if isinstance(cached, bytes) else cached)
        except Exception as e:  # pragma: no cover - cache is best-effort
            log.warning("llm_templates_cache_read_failed", error=str(e))
    try:
        rows = await _fetch_all()
    except Exception as e:
        log.warning("llm_templates_load_failed", error=str(e))
        return {}
    out = {r["id"]: r for r in rows}
    if redis is not None:
        try:
            await redis.set(CACHE_KEY, json.dumps(out), ex=CACHE_TTL_SECONDS)
        except Exception as e:  # pragma: no cover - cache is best-effort
            log.warning("llm_templates_cache_write_failed", error=str(e))
    return out


async def resolve_assignments(redis, assignments: dict[str, str]) -> dict[str, dict]:
    """{role: template_id} → {role: call-params dict}. Reads the template map only
    when some role is actually assigned (a legacy-only config costs nothing)."""
    from app.services.llm_params import resolve_roles

    if not any(assignments.values()):
        return {}
    return resolve_roles(await get_template_map(redis), assignments)


async def invalidate_template_cache(redis) -> None:
    if redis is None:
        return
    try:
        await redis.delete(CACHE_KEY)
    except Exception as e:  # pragma: no cover - best-effort
        log.warning("llm_templates_cache_invalidate_failed", error=str(e))


async def template_usages(template_id: str | None = None) -> list[dict]:
    """Every role assignment ({template_id, profile_id, group, role}), optionally
    for one template. Raises on DB trouble (a delete must not run on a guess)."""
    stmt = select(AppSetting.profile_id, AppSetting.key, AppSetting.value).where(
        AppSetting.key.in_(list(TEMPLATE_SETTING_KEYS))
    )
    if template_id is not None:
        stmt = stmt.where(AppSetting.value == template_id)
    async with session_scope() as db:
        rows = (await db.execute(stmt)).all()
    out = []
    for profile_id, key, value in rows:
        if not value:
            continue
        group, role = TEMPLATE_SETTING_KEYS[key]
        out.append({"template_id": value, "profile_id": profile_id, "group": group, "role": role})
    out.sort(key=lambda u: (u["template_id"], u["profile_id"], u["group"], u["role"]))
    return out


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #
async def create_template(data: dict, *, updated_by: str | None, redis=None) -> dict:
    """Insert a template. ``data`` carries ``id`` + ``TEMPLATE_COLUMNS``.
    Raises ``ValueError("exists")`` when the id is taken."""
    async with session_scope() as db:
        if await db.get(LlmTemplate, data["id"]) is not None:
            raise ValueError("exists")
        row = LlmTemplate(
            id=data["id"],
            **{c: data.get(c) for c in TEMPLATE_COLUMNS if c in data},
            updated_by=updated_by,
        )
        if row.description is None:
            row.description = ""
        if row.thinking_budget_tokens is None:
            row.thinking_budget_tokens = 0
        db.add(row)
        await db.flush()
        out = row_to_dict(row)
    await invalidate_template_cache(redis)
    log.info("llm_template_created", template_id=data["id"], updated_by=updated_by)
    return out


async def update_template(
    template_id: str, changes: dict, *, updated_by: str | None, redis=None
) -> dict | None:
    """Apply ``changes`` (a subset of ``TEMPLATE_COLUMNS``; explicit None clears a
    nullable knob). Returns the new row, or None when the template is missing."""
    async with session_scope() as db:
        row = await db.get(LlmTemplate, template_id)
        if row is None:
            return None
        for c, v in changes.items():
            if c in TEMPLATE_COLUMNS:
                setattr(row, c, v)
        row.updated_by = updated_by
        row.updated_at = datetime.now(UTC)
        await db.flush()
        out = row_to_dict(row)
    await invalidate_template_cache(redis)
    log.info(
        "llm_template_updated",
        template_id=template_id,
        fields=sorted(changes),
        updated_by=updated_by,
    )
    return out


async def delete_template(template_id: str, *, redis=None) -> bool:
    """Delete an unused template. Raises ``TemplateInUse`` when roles reference it;
    returns False when it does not exist."""
    usages = await template_usages(template_id)
    if usages:
        raise TemplateInUse(usages)
    async with session_scope() as db:
        res = await db.execute(delete(LlmTemplate).where(LlmTemplate.id == template_id))
        deleted = (res.rowcount or 0) > 0
    await invalidate_template_cache(redis)
    if deleted:
        log.info("llm_template_deleted", template_id=template_id)
    return deleted
