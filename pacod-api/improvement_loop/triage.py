"""Stage 2 — triage filter (the primary cost lever).

Deterministic rules first, then the configured handling of borderline turns.
Explicit feedback (👍 or 👎, or chunk votes) always earns deep analysis;
clarifications, empty answers, and classifier-flagged off-topic turns are dropped
without a model call. What happens to everything else depends on `cfg.triage_mode`:

  - ``model`` (default): a cheap triage model decides each borderline turn.
  - ``all``: analyse every borderline turn (no model call) — most thorough/costly.
  - ``feedback_only``: drop every borderline turn — only explicitly-rated turns
    are analysed (cheapest; nothing reaches the LLM stages without feedback).

The drop-rules and the explicit-feedback keep-rule apply in all modes.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.core.logging import get_logger
from app.services.llm_client import LLMClient
from improvement_loop.llm import call_parsed
from improvement_loop.models import ConversationBundle

log = get_logger(__name__)


class TriageDecision(BaseModel):
    analyze: bool = False
    reason: str = ""


def _rule_decision(b: ConversationBundle) -> tuple[bool, str] | None:
    """Deterministic verdict, or None to defer to the model."""
    if b.has_explicit_feedback():
        return True, "explicit_feedback"
    if not b.response.strip():
        return False, "empty_response"
    if b.classification.get("needs_clarification"):
        return False, "clarification_turn"
    # A semantic-cache hit replays an answer that was already reviewed when it was
    # first produced; the replay built no context and has no trace or evidence, so
    # analysing it yields only a "degraded / unverifiable" finding. Dropped here,
    # which a single-turn review (triage skipped) can still override.
    if b.classification.get("cached"):
        return False, "cache_hit"
    # `is_relevant` is always True on a completed turn (off-topic requests are
    # rejected before a turn_id exists); kept for rows written by older code.
    if b.classification.get("is_relevant") is False:
        return False, "off_topic"
    return None


async def triage(
    bundles: list[ConversationBundle], llm: LLMClient, cfg, system: str, cancel=None
) -> tuple[list[ConversationBundle], dict[str, int]]:
    """Return (selected_bundles, stats). Stats counts rule/model in/out.

    `system` is the effective triage prompt (admin override → code default).
    `cancel` (an asyncio.Event) stops the pass between turns."""
    models = cfg.models()
    mode = (getattr(cfg, "triage_mode", "model") or "model").strip().lower()
    if mode not in ("model", "all", "feedback_only"):
        mode = "model"
    selected: list[ConversationBundle] = []
    stats = {"in_rule": 0, "out_rule": 0, "in_model": 0, "out_model": 0, "errors": 0}

    for b in bundles:
        if cancel is not None and cancel.is_set():
            stats["cancelled"] = 1
            break
        rule = _rule_decision(b)
        if rule is not None:
            keep, reason = rule
            stats["in_rule" if keep else "out_rule"] += 1
            if keep:
                b.triage_decision = {"mode": "rule", "analyze": True, "reason": reason}
                selected.append(b)
            continue

        # Borderline turn. Modes other than "model" decide it deterministically (no
        # model call), so they count as rule outcomes.
        if mode == "all":
            stats["in_rule"] += 1
            b.triage_decision = {"mode": "all", "analyze": True, "reason": "all_mode"}
            selected.append(b)
            continue
        if mode == "feedback_only":
            stats["out_rule"] += 1
            continue

        # mode == "model": ask the cheap model. Use the analysis copies (amounts kept)
        # so the triage model sees the real figures.
        user = (
            f"USER QUESTION:\n{b.user_message_analysis}\n\n"
            f"ASSISTANT ANSWER:\n{b.response_analysis[:1500]}"
        )
        pt = cfg.stage("triage")
        try:
            decision = await call_parsed(
                llm,
                model=models["triage"],
                system=system,
                user=user,
                purpose="loop_triage",
                max_tokens=pt.max_tokens,
                schema=TriageDecision,
                thinking_budget_tokens=pt.thinking_budget_tokens,
                params=pt,
            )
        except Exception as e:
            # On a triage error, keep the turn (fail open — better to spend than to
            # silently drop a possibly-important conversation).
            log.warning("loop_triage_call_failed", turn_id=b.turn_id, error=str(e))
            stats["errors"] += 1
            b.triage_decision = {
                "mode": "model",
                "analyze": True,
                "reason": f"error_fail_open: {e}"[:200],
            }
            selected.append(b)
            continue

        decision = decision or TriageDecision(analyze=True, reason="parse_fallback")
        stats["in_model" if decision.analyze else "out_model"] += 1
        if decision.analyze:
            b.triage_decision = {"mode": "model", "analyze": True, "reason": decision.reason}
            selected.append(b)

    log.info("loop_triage_done", selected=len(selected), total=len(bundles), **stats)
    return selected, stats
