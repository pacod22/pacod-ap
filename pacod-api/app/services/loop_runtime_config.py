"""Runtime-mutable configuration for the continuous-improvement loop.

Lets admins configure the daily schedule and the loop's knobs (lookback, models,
thresholds, auto-insert) from the Review-process panel **without a redeploy**.
Built on `app/services/runtime_settings.py` (SettingsGroup): DB override → Redis
cache (60 s) → env default; admin writes bust the cache.

Loaded **fresh at the top of each run** (the worker is long-running, so reading the
env singleton inside stages would make UI changes invisible until restart). Only
the UI-exposed knobs live here; infra URLs / legal year / token budgets stay on
`Settings`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.db_models import DEFAULT_PROFILE_ID
from app.core.logging import get_logger
from app.services.llm_params import LOOP_STAGES, LLMCallParams, legacy_loop_params
from app.services.runtime_settings import SettingsGroup

log = get_logger(__name__)


@dataclass(frozen=True)
class PanelResolution:
    """Which models a run will actually call, and whether that matches the request.

    `degraded_reason` is non-empty exactly when the admin asked for `dual` but the
    configuration cannot deliver two independent opinions. It is surfaced in the run
    stats and report so a silently-single run is never mistaken for a corroborated
    one.
    """

    models: dict[str, str]
    mode_requested: str  # dual | single (what the admin configured)
    mode_effective: str  # dual | single (what will actually run)
    degraded_reason: str  # "" when the requested mode was honoured
    judge_independent: bool  # judge differs from BOTH analysts

    @property
    def is_degraded(self) -> bool:
        return bool(self.degraded_reason)


@dataclass(frozen=True)
class LoopConfig:
    """Effective loop configuration for one run (+ the schedule, read by the worker)."""

    schedule_enabled: bool
    schedule_hour: int
    schedule_minute: int
    lookback_hours: int
    max_conversations: int
    concurrency: int
    triage_mode: str  # model | all | feedback_only (borderline-turn selectivity)
    triage_model: str  # "" → agent classifier model
    triage_max_tokens: int  # output budget for the triage call
    # dual   → two independent analysts + a reconciling judge. Only meaningful when
    #          A and B are genuinely different models: the premise is decorrelated
    #          errors, not redundancy.
    # single → one analyst + a critic that re-checks it against the same evidence.
    #          Cheaper, and honest: no agreement/decorrelation confidence is reported.
    analysis_mode: str
    analysis_model_a: str  # "" → agent primary model
    analysis_a_max_tokens: int  # output budget for analysis model A
    # "" → NO fallback. A blank B degrades the run to single mode with a stated
    # reason rather than silently pairing A with the cheap classifier model (which
    # made "disagreement" mean "B is weaker") or with a hardcoded model id that may
    # not be granted by the gateway.
    analysis_model_b: str
    analysis_b_max_tokens: int  # output budget for analysis model B
    judge_model: str  # "" → analysis A's model (see resolve_panel)
    judge_max_tokens: int  # output budget for the judge/critic call
    # Remediation writes the corrected answer + directive proposal. "" → analysis
    # A's EFFECTIVE model, so upgrading A upgrades this too.
    remediation_model: str
    remediation_max_tokens: int
    # Extended-thinking budget for every review-stage call (0 = off).
    thinking_budget_tokens: int
    # Evidence assembly (what the analysis models get to read).
    evidence_rejected_max: int  # how many discarded candidates to re-read
    evidence_rejected_chars: int  # per-snippet truncation for those
    evidence_max_context_chars: int  # prompt-side cap on the used-context block
    novelty_covered_threshold: float
    novelty_gap_threshold: float
    grounding_threshold: float
    # Second half of the grounding gate: after a citation anchors by cosine, ask a
    # model whether the retrieved passage SUPPORTS or CONTRADICTS the claim. Off →
    # the historical similarity-only behaviour (and `contradicted` stays invisible).
    entailment_required: bool
    # Group findings into recurring problems (the unit a reviewer acts on).
    cluster_enabled: bool
    cluster_threshold: float  # cosine similarity to join an existing cluster
    # Re-ask the questions behind claimed fixes through the LIVE agent. OFF by
    # default: it issues real LLM + MCP traffic (one full agent turn per case).
    verification_enabled: bool
    verification_max_cases: int
    auto_insert_enabled: bool
    auto_insert_min_confidence: float
    curate_web_search_gaps: bool  # queue content gaps even on web-search turns
    meta_retire_enabled: bool
    # Stage-1 PII redaction packs applied to every collected conversation.
    redaction_packs: list[str]
    # LLM template id per stage ("" → the legacy model/max-tokens fields above +
    # the loop thinking budget at temperature 0). Resolved into `roles`.
    triage_template: str
    analysis_a_template: str
    analysis_b_template: str
    judge_template: str
    remediation_template: str
    # {stage: LLMCallParams dict} for stages whose template exists. Not persisted.
    roles: dict = field(default_factory=dict, compare=False)

    def template_assignments(self) -> dict[str, str]:
        return {stage: getattr(self, f"{stage}_template") for stage in LOOP_STAGES}

    def _template_model(self, stage: str) -> str:
        return (self.roles.get(stage) or {}).get("model") or ""

    def stage(self, stage: str) -> LLMCallParams:
        """Call params for one review stage: its template when assigned, else the
        legacy values (resolved panel model, the stage's max tokens, temperature 0,
        the loop thinking budget)."""
        resolved = self.roles.get(stage)
        if resolved:
            return LLMCallParams(**resolved)
        return legacy_loop_params(self, stage)

    def resolve_panel(self) -> PanelResolution:
        """Resolve the model panel, reporting any degradation instead of hiding it.

        The old `models()` filled every blank slot from the agent models, which in a
        default deployment produced A=primary, B=*classifier*, judge=*A's model*.
        "Decorrelated errors" then really meant "B is weaker", and the judge shared
        A's blind spots — while `agreement` and `synthesis_confidence` were reported
        as if the panel were independent. This resolver refuses to pretend:

        - dual + B blank, or B == A → the run DEGRADES to single mode and says why.
          No second model is guessed: a hardcoded id may not be granted by the
          gateway, and the cheap classifier is not a peer reviewer.
        - judge defaults to A's model, but `judge_independent` records whether it is
          actually a third opinion, so the UI can warn.
        """
        s = get_settings()
        # A stage on a template takes its model from it; otherwise the flat field
        # with its historical fallback. A blank B (no template, no flat model)
        # still degrades the run below — it never falls back.
        triage = self._template_model("triage") or self.triage_model or s.agent_classifier_model
        analysis_a = (
            self._template_model("analysis_a") or self.analysis_model_a or s.agent_primary_model
        )
        analysis_b = (self._template_model("analysis_b") or self.analysis_model_b or "").strip()
        judge_choice = self._template_model("judge") or self.judge_model

        requested = (self.analysis_mode or "dual").strip().lower()
        if requested not in ("dual", "single"):
            requested = "dual"

        degraded_reason = ""
        if requested == "dual" and not analysis_b:
            degraded_reason = (
                "analysis_model_b is unset — pick a second model from a DIFFERENT "
                "family for genuine decorrelation"
            )
        elif requested == "dual" and analysis_b == analysis_a:
            degraded_reason = (
                "analysis_model_b equals analysis_model_a — two runs of one model "
                "agree by construction, which is not evidence"
            )
        mode_effective = "single" if (requested == "single" or degraded_reason) else "dual"

        # In single mode the "judge" slot is the critic. When a distinct B exists
        # but is not being run as an analyst, it is the natural critic: a model
        # re-checking its own verdict against the same evidence is not a check.
        if mode_effective == "single" and analysis_b and analysis_b != analysis_a:
            judge = judge_choice or analysis_b
        else:
            judge = judge_choice or analysis_a
        panel = {"triage": triage, "analysis_a": analysis_a, "judge": judge}
        analysts = {analysis_a}
        if mode_effective == "dual":
            panel["analysis_b"] = analysis_b
            analysts.add(analysis_b)

        return PanelResolution(
            models=panel,
            mode_requested=requested,
            mode_effective=mode_effective,
            degraded_reason=degraded_reason,
            judge_independent=judge not in analysts,
        )

    def models(self) -> dict[str, str]:
        """Effective model id per loop role.

        Kept as the thin accessor the stages use (`cfg.models()["judge"]`). In single
        mode there is no `analysis_b` key at all — a caller that reaches for one is
        asking for a model that will not be called.
        """
        return self.resolve_panel().models


_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("schedule_enabled", "loop.schedule_enabled", "bool"),
    ("schedule_hour", "loop.schedule_hour", "int"),
    ("schedule_minute", "loop.schedule_minute", "int"),
    ("lookback_hours", "loop.lookback_hours", "int"),
    ("max_conversations", "loop.max_conversations", "int"),
    ("concurrency", "loop.concurrency", "int"),
    ("triage_mode", "loop.triage_mode", "str"),
    ("triage_model", "loop.triage_model", "str"),
    ("triage_max_tokens", "loop.triage_max_tokens", "int"),
    ("analysis_mode", "loop.analysis_mode", "str"),
    ("analysis_model_a", "loop.analysis_model_a", "str"),
    ("analysis_a_max_tokens", "loop.analysis_a_max_tokens", "int"),
    ("analysis_model_b", "loop.analysis_model_b", "str"),
    ("analysis_b_max_tokens", "loop.analysis_b_max_tokens", "int"),
    ("judge_model", "loop.judge_model", "str"),
    ("judge_max_tokens", "loop.judge_max_tokens", "int"),
    ("remediation_model", "loop.remediation_model", "str"),
    ("remediation_max_tokens", "loop.remediation_max_tokens", "int"),
    ("thinking_budget_tokens", "loop.thinking_budget_tokens", "int"),
    ("evidence_rejected_max", "loop.evidence_rejected_max", "int"),
    ("evidence_rejected_chars", "loop.evidence_rejected_chars", "int"),
    ("evidence_max_context_chars", "loop.evidence_max_context_chars", "int"),
    ("novelty_covered_threshold", "loop.novelty_covered_threshold", "float"),
    ("novelty_gap_threshold", "loop.novelty_gap_threshold", "float"),
    ("grounding_threshold", "loop.grounding_threshold", "float"),
    ("entailment_required", "loop.entailment_required", "bool"),
    ("cluster_enabled", "loop.cluster_enabled", "bool"),
    ("cluster_threshold", "loop.cluster_threshold", "float"),
    ("verification_enabled", "loop.verification_enabled", "bool"),
    ("verification_max_cases", "loop.verification_max_cases", "int"),
    ("auto_insert_enabled", "loop.auto_insert_enabled", "bool"),
    ("auto_insert_min_confidence", "loop.auto_insert_min_confidence", "float"),
    ("curate_web_search_gaps", "loop.curate_web_search_gaps", "bool"),
    ("meta_retire_enabled", "loop.meta_retire_enabled", "bool"),
    ("redaction_packs", "loop.redaction_packs", "csv"),
    *((f"{stage}_template", f"loop.{stage}_template", "str") for stage in LOOP_STAGES),
)


def _env_defaults() -> dict:
    s = get_settings()
    return {
        "schedule_enabled": s.loop_schedule_enabled,
        "schedule_hour": s.loop_schedule_hour,
        "schedule_minute": s.loop_schedule_minute,
        "lookback_hours": s.loop_lookback_hours,
        "max_conversations": s.loop_max_conversations,
        "concurrency": s.loop_concurrency,
        "triage_mode": s.loop_triage_mode,
        "triage_model": s.loop_triage_model,
        "triage_max_tokens": s.loop_triage_max_tokens,
        "analysis_mode": s.loop_analysis_mode,
        "analysis_model_a": s.loop_analysis_model_a,
        "analysis_a_max_tokens": s.loop_analysis_max_tokens,
        "analysis_model_b": s.loop_analysis_model_b,
        "analysis_b_max_tokens": s.loop_analysis_max_tokens,
        "judge_model": s.loop_judge_model,
        "judge_max_tokens": s.loop_judge_max_tokens,
        "remediation_model": s.loop_remediation_model,
        "remediation_max_tokens": s.loop_remediation_max_tokens,
        "thinking_budget_tokens": s.loop_thinking_budget_tokens,
        "evidence_rejected_max": s.loop_evidence_rejected_max,
        "evidence_rejected_chars": s.loop_evidence_rejected_chars,
        "evidence_max_context_chars": s.loop_evidence_max_context_chars,
        "novelty_covered_threshold": s.loop_novelty_covered_threshold,
        "novelty_gap_threshold": s.loop_novelty_gap_threshold,
        "grounding_threshold": s.loop_grounding_threshold,
        "entailment_required": s.loop_entailment_required,
        "cluster_enabled": s.loop_cluster_enabled,
        "cluster_threshold": s.loop_cluster_threshold,
        "verification_enabled": s.loop_verification_enabled,
        "verification_max_cases": s.loop_verification_max_cases,
        "auto_insert_enabled": s.loop_auto_insert_enabled,
        "auto_insert_min_confidence": s.loop_auto_insert_min_confidence,
        "curate_web_search_gaps": s.loop_curate_web_search_gaps,
        "meta_retire_enabled": s.loop_meta_retire_enabled,
        "redaction_packs": [
            p.strip() for p in (s.loop_redaction_packs or "").split(",") if p.strip()
        ],
        **{f"{stage}_template": "" for stage in LOOP_STAGES},
    }


# Shared app_settings → Redis → env implementation. The lambdas re-resolve this
# module's `_env_defaults` / `_load_overrides` / `session_scope` on every call so
# the unit tests' monkeypatches of those names keep working (see runtime_settings).
GROUP: SettingsGroup[LoopConfig] = SettingsGroup(
    name="loop_runtime_config",
    cache_prefix="config:loop_runtime",
    fields=_FIELDS,
    config_cls=LoopConfig,
    env_defaults=lambda: _env_defaults(),
    load_overrides=lambda pid: _load_overrides(pid),
    session_scope=lambda: session_scope,
)


def _cache_key(profile_id: str) -> str:
    return GROUP.cache_key(profile_id)


async def _load_overrides(profile_id: str) -> dict:
    """{field: parsed_value} for every persisted row. Never raises. (Seam kept for
    tests; the body lives in SettingsGroup.read_overrides.)"""
    return await GROUP.read_overrides(profile_id)


def env_loop_config() -> LoopConfig:
    """Config built purely from env defaults (no DB). Safety fallback."""
    return GROUP.env_config()


async def get_loop_config(redis=None, profile_id: str = DEFAULT_PROFILE_ID) -> LoopConfig:
    """Effective config for this profile. Redis-cached; never raises. Stage
    templates are resolved on every read (live link, see llm_templates)."""
    cfg = await GROUP.get(redis, profile_id=profile_id)
    from app.services.llm_templates import resolve_assignments

    roles = await resolve_assignments(redis, cfg.template_assignments())
    return replace(cfg, roles=roles) if roles else cfg


async def get_loop_config_state(redis=None, profile_id: str = DEFAULT_PROFILE_ID) -> dict:
    """{effective, env_default} for the admin UI."""
    return await GROUP.get_state(redis, profile_id=profile_id)


async def set_loop_config(
    updates: dict,
    *,
    updated_by: str | None = None,
    redis=None,
    profile_id: str = DEFAULT_PROFILE_ID,
) -> None:
    """Upsert the given fields and flush the cache. Unknown fields are ignored."""
    await GROUP.set(updates, updated_by=updated_by, redis=redis, profile_id=profile_id)


async def invalidate_loop_config_cache(redis, profile_id: str = DEFAULT_PROFILE_ID) -> None:
    await GROUP.invalidate(redis, profile_id=profile_id)
