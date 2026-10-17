"""User-block builders for the loop's LLM stages.

The SYSTEM prompts (triage/analysis/judge) are defined in ``app.agent.loop_prompts``
so they can be registered in the runtime agent-prompts registry and edited from the
admin Prompts panel; the loop reads the *effective* (override → default) text at run
time. They are re-exported here for backward-compatible imports and as the fallback
constant. This module keeps the per-conversation user blocks.
"""

from __future__ import annotations

from app.agent.loop_prompts import (  # noqa: F401  (re-exported as the source of truth)
    LOOP_ANALYSIS_SYSTEM as ANALYSIS_SYSTEM,
)
from app.core.logging import get_logger

log = get_logger(__name__)


def render_loop_prompt(effective: str | None, default: str, *, topic_description: str) -> str:
    """Render a loop system prompt for one profile.

    The loop prompts are `RENDER_FORMAT` templates carrying the profile's scope as
    `{topic_description}`. An admin override that fails to render (a stray brace,
    a missing placeholder that slipped past validation) falls back to the code
    default rather than failing the run — the same policy as the topic guard.
    """
    template = effective or default
    try:
        return template.format(topic_description=topic_description)
    except (KeyError, IndexError, ValueError):
        log.warning("loop_prompt_override_render_failed_using_default")
        return default.format(topic_description=topic_description)


def resolve_legal_year(bundle, profile=None) -> int:
    """The legal year this turn was answered under.

    Prefers the value captured WITH the turn (`turn_evidence.legal_year`), then the
    run's profile (its own `legal_year`, not the worker's env default — a
    non-default agent reviewed against the env year would fail every novelty and
    grounding search, which filter on the year), and only then the settings
    default. The audit classification never carried a year.
    """
    from app.core.config import get_settings

    ev = getattr(bundle, "turn_evidence", None) or {}
    return int(
        ev.get("legal_year")
        or getattr(profile, "legal_year", None)
        or get_settings().agent_legal_year
    )


def conversation_user_block(bundle, evidence=None, profile=None) -> str:
    """Render a redacted conversation + its evidence/trace/feedback for analysis.

    `evidence` is an `improvement_loop.evidence.EvidenceBlock`. When omitted the
    block falls back to the historical id/score-only rendering — which is the shape
    that made the failure-mode classification a guess, so callers in the pipeline
    always pass one.
    """
    from improvement_loop.evidence import render_evidence

    legal_year = resolve_legal_year(bundle, profile)

    trace = bundle.retrieval_trace or []
    trace_lines = [
        f"- {c.get('source_id', '?')} [{c.get('tier', '?')}/{c.get('source_type', '?')}]"
        f" dense={c.get('dense_score')} rerank={c.get('rerank_score')} used={c.get('used')}"
        for c in trace[:20]
    ]
    fb = []
    if bundle.response_rating is not None:
        fb.append(f"response_rating={'👍' if bundle.response_rating > 0 else '👎'}")
    for v in bundle.retrieval_votes:
        fb.append(f"chunk {v.get('source_id')} relevance={v.get('relevance')}")

    # SOURCES legend: the synthesizer cites sources inline with labels like [KB-1]
    # or [WEB-2] — often in freeform variants ([KB-2 от втория набор], combined, or
    # annotated). Rather than rewrite each variant, hand the model the label→source
    # map so it can resolve any citation form itself.
    sources_block = _sources_legend(getattr(bundle, "citation_markers", None))

    # Evidence FIRST, answer second: the model should read the sources before the
    # answer it is judging, not rationalise the answer and then look for support.
    # The prior conversation goes before the question, which may depend on it.
    evidence_block = f"{render_evidence(evidence)}\n\n" if evidence is not None else ""
    history_block = _history_block(evidence)

    return (
        f"LEGAL YEAR: {legal_year}\n"
        f"TOOLS USED: {', '.join(bundle.tools_used) or 'none'}\n"
        + history_block
        + f"USER QUESTION:\n{bundle.user_message_analysis}\n\n"
        + evidence_block
        + f"ASSISTANT ANSWER:\n{bundle.response_analysis}\n\n"
        + sources_block
        + "RETRIEVAL TRACE (scores only — the text is in the evidence above):\n"
        + ("\n".join(trace_lines) or "(no kb_search / no trace)")
        + "\n\nUSER FEEDBACK:\n"
        + ("; ".join(fb) or "(none)")
    )


def _history_block(evidence) -> str:
    """CONVERSATION SO FAR + trailing blank line, or '' for a first turn."""
    from improvement_loop.evidence import render_history

    rendered = render_history(evidence)
    return f"{rendered}\n\n" if rendered else ""


def _sources_legend(markers: dict | None) -> str:
    """Render the citation label→source map as a legend, or '' when absent.

    Sorted KB-* then WEB-* by index so the list reads in citation order. Returns a
    trailing blank line so it slots cleanly between the answer and the trace.
    """
    if not isinstance(markers, dict) or not markers:
        return ""

    def _key(item):
        label = item[0]
        kind, _, num = label.partition("-")
        return (0 if kind == "KB" else 1, int(num) if num.isdigit() else 0, label)

    lines = [f"- {label} = {ref}" for label, ref in sorted(markers.items(), key=_key)]
    return (
        "SOURCES REFERENCED (resolve any [KB-n]/[WEB-n] citation labels in the answer "
        "to these):\n" + "\n".join(lines) + "\n\n"
    )


def critic_user_block(bundle, analysis, evidence=None) -> str:
    """Single-mode verification input: the evidence, the answer, and ONE verdict.

    Labelled "ANALYST VERDICT (to be verified)" rather than "ANALYSIS A" so the
    critic treats it as a claim to check against the sources, not as one of two
    positions to average.
    """
    import json as _json

    from improvement_loop.evidence import render_evidence

    evidence_block = f"{render_evidence(evidence)}\n\n" if evidence is not None else ""
    return (
        _history_block(evidence)
        + f"USER QUESTION:\n{bundle.user_message_analysis}\n\n"
        + evidence_block
        + f"ASSISTANT ANSWER:\n{bundle.response_analysis}\n\n"
        f"ANALYST VERDICT (to be verified):\n"
        f"{_json.dumps(analysis.model_dump(), ensure_ascii=False)}"
    )


def judge_user_block(bundle, analysis_a, analysis_b, evidence=None) -> str:
    """Reconciliation input: the same evidence both analysts saw, plus their verdicts.

    The judge gets the evidence too. Adjudicating a retrieval-vs-hallucination
    disagreement means checking which analyst read the sources correctly — without
    them the judge can only weigh assertions against each other.
    """
    import json as _json

    from improvement_loop.evidence import render_evidence

    evidence_block = f"{render_evidence(evidence)}\n\n" if evidence is not None else ""
    return (
        _history_block(evidence)
        + f"USER QUESTION:\n{bundle.user_message_analysis}\n\n"
        + evidence_block
        + f"ASSISTANT ANSWER:\n{bundle.response_analysis}\n\n"
        f"ANALYSIS A:\n{_json.dumps(analysis_a.model_dump(), ensure_ascii=False)}\n\n"
        f"ANALYSIS B:\n{_json.dumps(analysis_b.model_dump(), ensure_ascii=False)}"
    )
