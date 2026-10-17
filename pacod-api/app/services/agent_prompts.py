"""Runtime-editable agent phase prompts.

The agent's system prompts (planner, synthesizer, verifier, suggestions, topic
classifier) are authored as constants in code (``app/agent/prompts.py`` and
``app/guards/topic.py``). This module layers an *override* on top of each one so
a superuser can edit them from the admin panel without a redeploy.

Same shape as ``app/services/agent_runtime_config.py``:
  code default → DB override (``app_settings`` row ``prompt.<key>``) → Redis cache.
The code remains the source of truth for the default, so "reset" simply drops the
override row and future code changes to a default propagate to anyone who hasn't
overridden it.

Two safety layers protect the editable templates (some use ``str.format`` keys
like ``{legal_year}``, the planner uses a ``[[ADMIN_DIRECTIVES]]`` sentinel):
  * write time — ``validate_prompt_content`` rejects an edit (→ 422) that drops a
    required token or would fail to render (stray brace / unknown ``{field}``);
  * read time — ``get_effective_prompts`` only ever returns text; the prompt
    builders fall back to the code default if an override still fails to render,
    so a bad override can never break a chat turn.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.agent.loop_prompts import (
    LOOP_ANALYSIS_SYSTEM,
    LOOP_CLUSTER_SYSTEM,
    LOOP_CRITIC_SYSTEM,
    LOOP_ENTAILMENT_SYSTEM,
    LOOP_FACTS_SYSTEM,
    LOOP_JUDGE_SYSTEM,
    LOOP_REMEDIATION_SYSTEM,
    LOOP_TRIAGE_SYSTEM,
)
from app.agent.newsletter_prompts import NEWSLETTER_CLASSIFIER_SYSTEM
from app.agent.prompts import (
    PLANNER_SYSTEM,
    SUGGESTIONS_SYSTEM_BG,
    SUGGESTIONS_SYSTEM_EN,
    SUMMARIZER_SYSTEM_BG,
    SUMMARIZER_SYSTEM_EN,
    SYNTHESIZER_PREFIX_BG,
    SYNTHESIZER_PREFIX_EN,
    TOPIC_CLASSIFIER_SYSTEM,
    VERIFIER_SYSTEM_BG,
    VERIFIER_SYSTEM_EN,
)
from app.core.db import session_scope
from app.core.db_models import DEFAULT_PROFILE_ID, AppSetting
from app.core.logging import get_logger

log = get_logger(__name__)

_CACHE_PREFIX = "config:agent_prompts"
_CACHE_TTL_SECONDS = 60
_SETTING_PREFIX = "prompt."


def _cache_key(profile_id: str) -> str:
    return f"{_CACHE_PREFIX}:{profile_id}"


# Render modes drive how an override is validated at write time and applied at
# read time (see the prompt builders in app/agent/prompts.py + the topic guard).
RENDER_PLAIN = "plain"  # used verbatim, no substitution
RENDER_FORMAT = "format"  # str.format(**format_keys); literal braces must be {{ }}
RENDER_SENTINEL = "sentinel"  # str.replace on a [[TOKEN]] marker; braces are literal


@dataclass(frozen=True)
class PromptSpec:
    """Static metadata + the code default for one editable phase prompt."""

    key: str
    phase: str  # human label for the agent phase
    language: str  # "bg" | "en" | "all"
    label: str  # short UI title
    description: str
    render: str
    default_text: str
    # Substrings that MUST remain in the override (placeholders / sentinels).
    required_tokens: tuple[str, ...] = ()
    # str.format keys the template is rendered with (RENDER_FORMAT only).
    format_keys: tuple[str, ...] = ()


# Dummy values used to dry-run str.format at validation time.
_FORMAT_DUMMIES: dict[str, object] = {
    "directives": "",
    "legal_year": 2026,
    "topic_description": "scope",
}


PROMPT_SPECS: tuple[PromptSpec, ...] = (
    PromptSpec(
        key="topic_classifier",
        phase="Guard — topic classifier",
        language="all",
        label="Topic classifier",
        description=(
            "Pre-agent guard. Classifies whether a message is in scope, needs "
            "clarification, or is an illegal request. Must keep the {topic_description} "
            "placeholder and emit the documented JSON schema."
        ),
        render=RENDER_FORMAT,
        default_text=TOPIC_CLASSIFIER_SYSTEM,
        required_tokens=("{topic_description}",),
        format_keys=("topic_description",),
    ),
    PromptSpec(
        key="planner",
        phase="Plan",
        language="all",
        label="Planner",
        description=(
            "Decides which tools to call and whether the question is complex. The "
            "[[ADMIN_DIRECTIVES]] marker is where active admin directives are spliced "
            "in — keep it so directives can steer tool selection."
        ),
        render=RENDER_SENTINEL,
        default_text=PLANNER_SYSTEM,
        required_tokens=("[[ADMIN_DIRECTIVES]]",),
    ),
    PromptSpec(
        key="synthesizer_bg",
        phase="Synthesize (BG)",
        language="bg",
        label="Synthesizer — Bulgarian",
        description=(
            "Bulgarian answer prompt prefix. Must keep {directives} (admin directive "
            "injection point) and {legal_year} (reference-year disclaimer)."
        ),
        render=RENDER_FORMAT,
        default_text=SYNTHESIZER_PREFIX_BG,
        required_tokens=("{directives}", "{legal_year}"),
        format_keys=("directives", "legal_year"),
    ),
    PromptSpec(
        key="synthesizer_en",
        phase="Synthesize (EN)",
        language="en",
        label="Synthesizer — English",
        description=(
            "English answer prompt prefix. Must keep {directives} (admin directive "
            "injection point) and {legal_year} (reference-year disclaimer)."
        ),
        render=RENDER_FORMAT,
        default_text=SYNTHESIZER_PREFIX_EN,
        required_tokens=("{directives}", "{legal_year}"),
        format_keys=("directives", "legal_year"),
    ),
    PromptSpec(
        key="verifier_bg",
        phase="Verify (BG)",
        language="bg",
        label="Verifier — Bulgarian",
        description="Optional fact-checking pass over the Bulgarian draft answer.",
        render=RENDER_PLAIN,
        default_text=VERIFIER_SYSTEM_BG,
    ),
    PromptSpec(
        key="verifier_en",
        phase="Verify (EN)",
        language="en",
        label="Verifier — English",
        description="Optional fact-checking pass over the English draft answer.",
        render=RENDER_PLAIN,
        default_text=VERIFIER_SYSTEM_EN,
    ),
    PromptSpec(
        key="suggestions_bg",
        phase="Suggestions (BG)",
        language="bg",
        label="Suggestions — Bulgarian",
        description="Generates 3 Bulgarian follow-up questions after the answer.",
        render=RENDER_PLAIN,
        default_text=SUGGESTIONS_SYSTEM_BG,
    ),
    PromptSpec(
        key="suggestions_en",
        phase="Suggestions (EN)",
        language="en",
        label="Suggestions — English",
        description="Generates 3 English follow-up questions after the answer.",
        render=RENDER_PLAIN,
        default_text=SUGGESTIONS_SYSTEM_EN,
    ),
    PromptSpec(
        key="summarizer_bg",
        phase="Conversation memory (BG)",
        language="bg",
        label="Summarizer — Bulgarian",
        description=(
            "Rolling summary of older conversation turns (Bulgarian), fed back into "
            "the planner and synthesizer so long follow-up chats keep context."
        ),
        render=RENDER_PLAIN,
        default_text=SUMMARIZER_SYSTEM_BG,
    ),
    PromptSpec(
        key="summarizer_en",
        phase="Conversation memory (EN)",
        language="en",
        label="Summarizer — English",
        description=(
            "Rolling summary of older conversation turns (English), fed back into "
            "the planner and synthesizer so long follow-up chats keep context."
        ),
        render=RENDER_PLAIN,
        default_text=SUMMARIZER_SYSTEM_EN,
    ),
    # ---- Continuous-improvement loop (Review process) ----
    PromptSpec(
        key="loop_triage",
        phase="Review — triage",
        language="all",
        label="Loop · Triage",
        description=(
            "Closed-loop learning. Cheap pre-filter deciding whether a conversation is "
            "worth deep multi-LLM analysis. Must keep the {topic_description} placeholder "
            "(the profile's scope) and the documented strict-JSON output; literal "
            "braces must be doubled."
        ),
        render=RENDER_FORMAT,
        required_tokens=("{topic_description}",),
        format_keys=("topic_description",),
        default_text=LOOP_TRIAGE_SYSTEM,
    ),
    PromptSpec(
        key="loop_analysis",
        phase="Review — analysis",
        language="all",
        label="Loop · Analysis",
        description=(
            "Closed-loop learning. Each of the two diverse analysis models assesses a "
            "conversation (failure mode, candidate knowledge). Keep the "
            "{topic_description} placeholder and the strict-JSON schema (double literal "
            "braces)."
        ),
        render=RENDER_FORMAT,
        required_tokens=("{topic_description}",),
        format_keys=("topic_description",),
        default_text=LOOP_ANALYSIS_SYSTEM,
    ),
    PromptSpec(
        key="loop_judge",
        phase="Review — judge",
        language="all",
        label="Loop · Judge",
        description=(
            "Closed-loop learning. Reconciles the two analyses into one candidate + "
            "confidence (disagreement is signal). Keep the strict-JSON schema."
        ),
        render=RENDER_FORMAT,
        required_tokens=("{topic_description}",),
        format_keys=("topic_description",),
        default_text=LOOP_JUDGE_SYSTEM,
    ),
    PromptSpec(
        key="loop_cluster",
        phase="Review — clustering",
        language="all",
        label="Loop · Cluster naming",
        description=(
            "Closed-loop learning. Names a RECURRING problem assembled from many "
            "findings and states the single most useful next step — the unit a "
            "reviewer actually acts on. Keep the strict-JSON schema."
        ),
        render=RENDER_FORMAT,
        required_tokens=("{topic_description}",),
        format_keys=("topic_description",),
        default_text=LOOP_CLUSTER_SYSTEM,
    ),
    PromptSpec(
        key="loop_remediation",
        phase="Review — remediation",
        language="all",
        label="Loop · Remediation",
        description=(
            "Closed-loop learning. Turns a diagnosis into ARTIFACTS: the answer that "
            "should have been given, the facts a regression test can assert, and an "
            "imperative directive rule when a standing behaviour fix is warranted. "
            "Keep the strict-JSON schema."
        ),
        render=RENDER_FORMAT,
        required_tokens=("{topic_description}",),
        format_keys=("topic_description",),
        default_text=LOOP_REMEDIATION_SYSTEM,
    ),
    PromptSpec(
        key="loop_critic",
        phase="Review — critic (single mode)",
        language="all",
        label="Loop · Critic",
        description=(
            "Closed-loop learning. Used INSTEAD of the judge when the analysis mode is "
            "`single`: re-checks one analyst's verdict against the same evidence rather "
            'than reconciling two. Keep the strict-JSON schema and agreement="single".'
        ),
        render=RENDER_FORMAT,
        required_tokens=("{topic_description}",),
        format_keys=("topic_description",),
        default_text=LOOP_CRITIC_SYSTEM,
    ),
    PromptSpec(
        key="loop_entailment",
        phase="Review — grounding",
        language="all",
        label="Loop · Grounding entailment",
        description=(
            "Closed-loop learning. Decides whether the retrieved primary-law passages "
            "actually SUPPORT, CONTRADICT, or simply don't state a candidate claim — the "
            "gate that keeps a contradicting claim out of the curated tier. Keep the "
            "strict-JSON schema."
        ),
        render=RENDER_FORMAT,
        required_tokens=("{topic_description}",),
        format_keys=("topic_description",),
        default_text=LOOP_ENTAILMENT_SYSTEM,
    ),
    PromptSpec(
        key="loop_facts",
        phase="Review — regression facts",
        language="all",
        label="Loop · Golden-test facts",
        description=(
            "Closed-loop learning. For an answer confirmed CORRECT, names the short "
            "verbatim fragments a regression test must still find in a future answer. "
            "Keep the {topic_description} placeholder and the strict-JSON schema."
        ),
        render=RENDER_FORMAT,
        default_text=LOOP_FACTS_SYSTEM,
        required_tokens=("{topic_description}",),
        format_keys=("topic_description",),
    ),
    # ---- Newsletter ----
    PromptSpec(
        key="newsletter_classifier",
        phase="Newsletter — classifier",
        language="all",
        label="Newsletter · Classifier",
        description=(
            "Scores a crawled article's relevance/importance/urgency and writes its "
            "title + short summary for the newsletter. The article rides in the user "
            "message; keep the documented strict-JSON output."
        ),
        render=RENDER_PLAIN,
        default_text=NEWSLETTER_CLASSIFIER_SYSTEM,
    ),
)

_SPEC_BY_KEY: dict[str, PromptSpec] = {s.key: s for s in PROMPT_SPECS}


def get_spec(key: str) -> PromptSpec | None:
    return _SPEC_BY_KEY.get(key)


def _setting_key(prompt_key: str) -> str:
    return f"{_SETTING_PREFIX}{prompt_key}"


def _now() -> datetime:
    return datetime.now(UTC)


class PromptValidationError(ValueError):
    """Raised when an override would drop a required token or fail to render."""


def validate_prompt_content(spec: PromptSpec, content: str) -> None:
    """Reject an override that is structurally unsafe. Raises PromptValidationError.

    Per render mode:
      * required tokens (placeholders / sentinel) must all be present;
      * RENDER_FORMAT templates must render with the known format keys — this
        catches a stray single brace or an unknown ``{field}`` before it can break
        a live turn (literal braces in the text must be escaped as ``{{`` / ``}}``).
    """
    if not content or not content.strip():
        raise PromptValidationError("Prompt content must not be empty.")

    for token in spec.required_tokens:
        if token not in content:
            raise PromptValidationError(
                f"Prompt is missing the required placeholder {token!r}; the agent "
                "relies on it. Add it back or reset to default."
            )

    if spec.render == RENDER_FORMAT:
        dummies = {k: _FORMAT_DUMMIES.get(k, "") for k in spec.format_keys}
        try:
            content.format(**dummies)
        except (KeyError, IndexError, ValueError) as e:
            raise PromptValidationError(
                "Prompt has an invalid placeholder or unescaped brace "
                f"({e.__class__.__name__}: {e}). Use {{{{ and }}}} for literal braces, "
                f"and only these placeholders: {', '.join(f'{{{k}}}' for k in spec.format_keys) or 'none'}."
            ) from e


def _render_ok(spec: PromptSpec, content: str) -> bool:
    """Read-time guard: True if `content` is safe to use as the effective prompt."""
    try:
        validate_prompt_content(spec, content)
        return True
    except PromptValidationError:
        return False


async def _load_overrides(profile_id: str) -> dict[str, str]:
    """Return {prompt_key: override_text} for every persisted row. Never raises."""
    try:
        async with session_scope() as db:
            keys = [_setting_key(s.key) for s in PROMPT_SPECS]
            rows = (
                await db.execute(
                    select(AppSetting.key, AppSetting.value).where(
                        AppSetting.profile_id == profile_id, AppSetting.key.in_(keys)
                    )
                )
            ).all()
    except Exception as e:  # pragma: no cover - a config outage must not block a turn
        log.warning("agent_prompts_load_failed", error=str(e))
        return {}

    out: dict[str, str] = {}
    for setting_key, value in rows:
        if not value:
            continue
        prompt_key = setting_key[len(_SETTING_PREFIX) :]
        out[prompt_key] = value
    return out


def _effective_from_overrides(overrides: dict[str, str]) -> dict[str, str]:
    """Code default for every prompt, replaced by a valid override where present."""
    effective: dict[str, str] = {}
    for spec in PROMPT_SPECS:
        override = overrides.get(spec.key)
        if override is not None and _render_ok(spec, override):
            effective[spec.key] = override
        else:
            if override is not None:
                log.warning("agent_prompt_override_invalid_using_default", key=spec.key)
            effective[spec.key] = spec.default_text
    return effective


def default_prompts() -> dict[str, str]:
    """Effective prompts built purely from code defaults (no DB). Safety fallback."""
    return {s.key: s.default_text for s in PROMPT_SPECS}


async def get_effective_prompts(redis=None, profile_id: str = DEFAULT_PROFILE_ID) -> dict[str, str]:
    """Effective prompt text per key for this turn. Redis-cached; never raises."""
    key = _cache_key(profile_id)
    if redis is not None:
        try:
            cached = await redis.get(key)
            if cached is not None:
                raw = cached.decode() if isinstance(cached, bytes) else cached
                return json.loads(raw)
        except Exception as e:  # pragma: no cover - cache is best-effort
            log.warning("agent_prompts_cache_read_failed", error=str(e))

    effective = _effective_from_overrides(await _load_overrides(profile_id))

    if redis is not None:
        try:
            await redis.set(key, json.dumps(effective), ex=_CACHE_TTL_SECONDS)
        except Exception as e:  # pragma: no cover - cache is best-effort
            log.warning("agent_prompts_cache_write_failed", error=str(e))

    return effective


@dataclass(frozen=True)
class PromptState:
    """Full per-prompt view for the admin UI."""

    key: str
    phase: str
    language: str
    label: str
    description: str
    render: str
    required_tokens: list[str] = field(default_factory=list)
    default_text: str = ""
    content: str = ""  # effective text (override or default)
    is_overridden: bool = False
    updated_by: str | None = None
    updated_at: str | None = None


async def list_prompt_states(profile_id: str = DEFAULT_PROFILE_ID) -> list[PromptState]:
    """Metadata + default + effective text + override status for every prompt."""
    try:
        async with session_scope() as db:
            keys = [_setting_key(s.key) for s in PROMPT_SPECS]
            rows = (
                await db.execute(
                    select(
                        AppSetting.key,
                        AppSetting.value,
                        AppSetting.updated_by,
                        AppSetting.updated_at,
                    ).where(AppSetting.profile_id == profile_id, AppSetting.key.in_(keys))
                )
            ).all()
    except Exception as e:
        log.warning("agent_prompts_list_failed", error=str(e))
        rows = []

    meta = {
        setting_key[len(_SETTING_PREFIX) :]: (value, updated_by, updated_at)
        for setting_key, value, updated_by, updated_at in rows
    }

    states: list[PromptState] = []
    for spec in PROMPT_SPECS:
        value, updated_by, updated_at = meta.get(spec.key, (None, None, None))
        overridden = bool(value) and _render_ok(spec, value)
        states.append(
            PromptState(
                key=spec.key,
                phase=spec.phase,
                language=spec.language,
                label=spec.label,
                description=spec.description,
                render=spec.render,
                required_tokens=list(spec.required_tokens),
                default_text=spec.default_text,
                content=value if overridden else spec.default_text,
                is_overridden=overridden,
                updated_by=updated_by if overridden else None,
                updated_at=updated_at.isoformat() if (overridden and updated_at) else None,
            )
        )
    return states


async def get_prompt_state(key: str, profile_id: str = DEFAULT_PROFILE_ID) -> PromptState | None:
    spec = _SPEC_BY_KEY.get(key)
    if spec is None:
        return None
    for state in await list_prompt_states(profile_id):
        if state.key == key:
            return state
    return None  # pragma: no cover - spec exists so a state always exists


async def set_prompt_override(
    key: str,
    content: str,
    *,
    updated_by: str | None = None,
    redis=None,
    profile_id: str = DEFAULT_PROFILE_ID,
) -> PromptState:
    """Validate and persist an override, busting the cache. Raises on a bad key or
    PromptValidationError on unsafe content."""
    spec = _SPEC_BY_KEY.get(key)
    if spec is None:
        raise KeyError(key)
    validate_prompt_content(spec, content)

    async with session_scope() as db:
        stmt = (
            pg_insert(AppSetting)
            .values(
                profile_id=profile_id, key=_setting_key(key), value=content, updated_by=updated_by
            )
            .on_conflict_do_update(
                index_elements=[AppSetting.profile_id, AppSetting.key],
                set_={"value": content, "updated_by": updated_by, "updated_at": _now()},
            )
        )
        await db.execute(stmt)
    await invalidate_agent_prompts_cache(redis, profile_id=profile_id)
    log.info("agent_prompt_override_set", key=key, updated_by=updated_by, profile_id=profile_id)
    return await get_prompt_state(key, profile_id)  # type: ignore[return-value]


async def reset_prompt(
    key: str, *, redis=None, profile_id: str = DEFAULT_PROFILE_ID
) -> PromptState:
    """Drop the override row (back to the code default) and bust the cache."""
    spec = _SPEC_BY_KEY.get(key)
    if spec is None:
        raise KeyError(key)
    async with session_scope() as db:
        await db.execute(
            delete(AppSetting).where(
                AppSetting.profile_id == profile_id, AppSetting.key == _setting_key(key)
            )
        )
    await invalidate_agent_prompts_cache(redis, profile_id=profile_id)
    log.info("agent_prompt_reset", key=key, profile_id=profile_id)
    return await get_prompt_state(key, profile_id)  # type: ignore[return-value]


async def invalidate_agent_prompts_cache(redis, profile_id: str = DEFAULT_PROFILE_ID) -> None:
    if redis is None:
        return
    try:
        await redis.delete(_cache_key(profile_id))
    except Exception as e:  # pragma: no cover - best-effort
        log.warning("agent_prompts_cache_invalidate_failed", error=str(e))
