"""Assemble the evidence base the analysis stage reasons over.

Before this existed the analysis models saw the question, the answer, tool NAMES,
and a trace rendered as `source_id [tier/type] dense=0.71 rerank=0.88 used=True`.
From that they were asked to choose between `retrieval`, `content_gap`,
`hallucination` and `agent_behavior` — distinctions that are only decidable if you
can read what the assistant actually had in front of it. The verdict was a guess,
and `route_for` branched on it, so novelty, grounding, candidates, golden tests and
every remediation button inherited the error.

Two halves, deliberately sourced differently:

**Used side** — `turn_evidence.context`, verbatim. This is `_build_context`'s
output: the parent-expanded KB passages plus the `### Structured data` tool results.
It is NOT reconstructed from the retrieval trace, and that restraint is the whole
point. The trace is written *before* `_expand_to_parents` and keeps the CHILD
chunk id while synthesis read the PARENT block, so rehydrating from it would show
the reviewer text the assistant never saw — a subtler failure than showing nothing.

**Rejected side** — hydrated from the trace, because these are exactly the rows
`turn_evidence` does not contain, and they are what separates "the KB had it and
retrieval missed it" from "the KB never had it". Rejected candidates are never
parent-expanded (only the reranked survivors are), so fetching them by `chunk_id`
returns precisely the text that lost.

Everything degrades: no snapshot, no trace, Qdrant down, or a profile whose
collections have gone → the block still renders, `completeness` says `degraded`,
and the analysis prompt is instructed to prefer `unverifiable` over inventing a
failure mode. `build_evidence` never raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.services.collection_config import get_active_collection
from app.services.profiles import ProfileContext, env_profile_context
from improvement_loop.models import ConversationBundle

log = get_logger(__name__)

# Fallbacks when no runtime cfg is supplied (direct construction / tests).
_DEFAULT_REJECTED_MAX = 10
_DEFAULT_REJECTED_CHARS = 400
_DEFAULT_CONTEXT_CHARS = 48000
# How much of the prior conversation the reviewer sees.
_HISTORY_MESSAGES = 6
_HISTORY_MESSAGE_CHARS = 1500
_HISTORY_SUMMARY_CHARS = 3000
_TOOL_ARGS_CHARS = 300


@dataclass
class RejectedChunk:
    """A candidate that was retrieved and then NOT shown to the assistant."""

    source_id: str
    tier: str
    source_type: str
    score: float | None
    text: str


@dataclass
class EvidenceBlock:
    used_context: str = ""
    rejected: list[RejectedChunk] = field(default_factory=list)
    directives: str = ""
    legal_year: int | None = None
    # full     — the reviewer has the whole context the assistant read.
    # partial  — it has the context, but cut (at capture or for this review):
    #            absence from the block proves nothing.
    # degraded — no snapshot at all: the reviewer cannot see the sources.
    completeness: str = "degraded"
    notes: list[str] = field(default_factory=list)
    # The conversation the turn was answered in (redacted).
    history_summary: str = ""
    history_recent: list[dict] = field(default_factory=list)  # [{role, content}]
    # What the planner asked the tools for: [{name, args}].
    tool_calls: list[dict] = field(default_factory=list)

    @property
    def is_full(self) -> bool:
        return self.completeness == "full"

    @property
    def is_usable(self) -> bool:
        """The reviewer saw the sources (all or some) — enough to write a fix from."""
        return self.completeness in ("full", "partial")


def _truncate(text: str, limit: int) -> str:
    """Cut at a word boundary so a snippet never ends mid-token."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit // 2 else cut).rstrip() + "…"


def _sort_key(entry: dict) -> float:
    """Rank rejected candidates by how close they came to being used.

    `rerank_score` is None outside `rerank_input_k`, and None for the whole list on
    the dense-confidence early-exit and rerank-failure paths — so fall back to the
    dense score rather than dropping those rows to the bottom.
    """
    rr = entry.get("rerank_score")
    if rr is not None:
        return float(rr)
    dense = entry.get("dense_score")
    return float(dense) if dense is not None else 0.0


async def resolve_authoritative_collection(profile: ProfileContext) -> str:
    """The profile's live authoritative collection (alias-resolved).

    Resolved ONCE per run by the caller and threaded into `build_evidence` — it is
    a DB/Redis round-trip, and doing it per analysed turn would repeat it for every
    turn in the window. Mirrors the `_authoritative` memoisation that
    `NoveltyChecker`/`GroundingChecker` do per instance.
    """
    try:
        return await get_active_collection(
            None, profile_id=profile.id, default_collection=profile.base_collection
        )
    except Exception as e:
        log.warning("loop_evidence_collection_resolve_failed", error=str(e))
        return profile.base_collection


async def build_evidence(
    bundle: ConversationBundle,
    *,
    vector_store,
    profile: ProfileContext | None = None,
    cfg=None,
    authoritative_collection: str | None = None,
) -> EvidenceBlock:
    """Build the evidence block for one analysed turn. Never raises.

    `authoritative_collection` should be resolved once per run and passed in; when
    omitted it is resolved here (convenient for one-off calls, wasteful in a loop).
    """
    profile = profile or env_profile_context()
    rejected_max = int(getattr(cfg, "evidence_rejected_max", _DEFAULT_REJECTED_MAX) or 0)
    rejected_chars = int(getattr(cfg, "evidence_rejected_chars", _DEFAULT_REJECTED_CHARS) or 0)
    context_chars = int(getattr(cfg, "evidence_max_context_chars", _DEFAULT_CONTEXT_CHARS) or 0)

    ev = bundle.turn_evidence or {}
    notes: list[str] = []

    used_context = (ev.get("context") or "").strip()
    # `full` means the analysis can actually adjudicate: it has the text the
    # assistant read. The rejected side is a bonus — a turn with no kb_search has
    # nothing to reject and is still fully evidenced. Any cut — at capture or here —
    # makes it `partial`: the reviewer still saw the sources, but "not in the block"
    # no longer means "not in the KB".
    completeness = "full" if used_context else "degraded"
    if not used_context:
        notes.append(
            "no context snapshot for this turn (answered before evidence capture, "
            "or capture was off) — the sources the assistant read are unknown"
        )
    else:
        if ev.get("truncated"):
            notes.append("the stored context was truncated at capture time")
            completeness = "partial"
        if context_chars and len(used_context) > context_chars:
            used_context = _truncate(used_context, context_chars)
            notes.append(
                f"the context was cut to {context_chars} characters for this review — "
                "you may not be seeing everything the assistant read"
            )
            completeness = "partial"

    directives = (ev.get("directives") or "").strip()
    legal_year = ev.get("legal_year")
    history = ev.get("history") or {}
    if not isinstance(history, dict):
        history = {}
    tool_calls = [tc for tc in (ev.get("tool_calls") or []) if isinstance(tc, dict)]

    rejected = await _hydrate_rejected(
        bundle,
        vector_store=vector_store,
        profile=profile,
        limit=rejected_max,
        chars=rejected_chars,
        notes=notes,
        authoritative_collection=authoritative_collection,
    )

    return EvidenceBlock(
        used_context=used_context,
        rejected=rejected,
        directives=directives,
        legal_year=legal_year,
        completeness=completeness,
        notes=notes,
        history_summary=(history.get("summary") or "").strip(),
        history_recent=[
            m for m in (history.get("recent") or []) if isinstance(m, dict) and m.get("content")
        ],
        tool_calls=tool_calls,
    )


async def _hydrate_rejected(
    bundle: ConversationBundle,
    *,
    vector_store,
    profile: ProfileContext,
    limit: int,
    chars: int,
    notes: list[str],
    authoritative_collection: str | None = None,
) -> list[RejectedChunk]:
    """Fetch the text of the top candidates that were retrieved but not used."""
    trace = bundle.retrieval_trace or []
    if not trace or limit <= 0 or vector_store is None:
        return []

    candidates = [c for c in trace if isinstance(c, dict) and not c.get("used")]
    if not candidates:
        return []
    candidates.sort(key=_sort_key, reverse=True)
    candidates = candidates[:limit]

    # Resolve the collection PER TIER: a curated hit was searched in the profile's
    # curated collection, and the trace row records only the authoritative one, so
    # fetching everything from one collection would silently lose the curated rows.
    by_tier: dict[str, list[dict]] = {}
    for c in candidates:
        by_tier.setdefault((c.get("tier") or "authoritative"), []).append(c)

    payloads: dict[str, dict] = {}
    for tier, rows in by_tier.items():
        if tier == "curated":
            collection = profile.curated_collection
        else:
            collection = authoritative_collection or await resolve_authoritative_collection(profile)
        if not collection:
            continue
        ids = [str(c.get("chunk_id")) for c in rows if c.get("chunk_id")]
        if not ids:
            continue
        try:
            payloads.update(await vector_store.fetch_chunks(ids, collection_name=collection))
        except Exception as e:  # fetch_chunks already swallows, this is belt-and-braces
            log.warning("loop_evidence_fetch_failed", tier=tier, error=str(e))

    out: list[RejectedChunk] = []
    missing = 0
    for c in candidates:
        payload = payloads.get(str(c.get("chunk_id"))) or {}
        text = payload.get("text") or ""
        if not text:
            missing += 1
            continue
        out.append(
            RejectedChunk(
                source_id=str(c.get("source_id") or "?"),
                tier=str(c.get("tier") or "?"),
                source_type=str(c.get("source_type") or "?"),
                score=_sort_key(c),
                text=_truncate(text, chars),
            )
        )
    if missing:
        notes.append(
            f"{missing} discarded candidate(s) could not be re-read from the vector "
            "store (re-ingested or pruned since)"
        )
    return out


def render_history(evidence: EvidenceBlock | None) -> str:
    """The prior conversation, for the top of every review prompt.

    Rendered BEFORE the question being judged: a follow-up like "и за фирма?"
    is only assessable once the reviewer knows what "it" was. Returns "" when
    the turn had no history, so first-turn prompts are unchanged.
    """
    if evidence is None or (not evidence.history_summary and not evidence.history_recent):
        return ""
    lines = [
        "CONVERSATION SO FAR (earlier turns of this chat — the question below may "
        "depend on them; judge it in this context):"
    ]
    if evidence.history_summary:
        lines.append(
            f"SUMMARY OF EARLIER TURNS: {evidence.history_summary[:_HISTORY_SUMMARY_CHARS]}"
        )
    for m in evidence.history_recent[-_HISTORY_MESSAGES:]:
        role = "user" if m.get("role") == "user" else "assistant"
        lines.append(f"- {role}: {_truncate(str(m.get('content') or ''), _HISTORY_MESSAGE_CHARS)}")
    return "\n".join(lines)


def render_evidence(evidence: EvidenceBlock) -> str:
    """Render the block for the analysis/judge/remediation user prompts."""
    import json as _json

    parts: list[str] = []

    if evidence.directives:
        parts.append(
            "ACTIVE DIRECTIVES AT ANSWER TIME (admin instructions that OUTRANK tool "
            "data — an answer following one of these is behaving correctly):\n"
            + evidence.directives
        )
    else:
        parts.append("ACTIVE DIRECTIVES AT ANSWER TIME:\n(none recorded for this turn)")

    if evidence.tool_calls:
        calls = []
        for tc in evidence.tool_calls:
            try:
                args = _json.dumps(tc.get("args") or {}, ensure_ascii=False)
            except (TypeError, ValueError):
                args = str(tc.get("args"))
            calls.append(f"- {tc.get('name') or '?'} {_truncate(args, _TOOL_ARGS_CHARS)}")
        parts.append(
            "TOOL CALLS THE PLANNER MADE (the arguments it chose — a wrong year or "
            "wrong tax type here is an agent_behavior failure, not bad data):\n" + "\n".join(calls)
        )

    if evidence.used_context:
        parts.append(
            "RETRIEVED CONTEXT THE ASSISTANT ACTUALLY READ (verbatim, including the "
            "structured-tool results it was given):\n" + evidence.used_context
        )
    else:
        parts.append(
            "RETRIEVED CONTEXT THE ASSISTANT ACTUALLY READ:\n"
            "(not recorded — you cannot see what it read)"
        )

    if evidence.rejected:
        lines = [
            f"- {r.source_id} [{r.tier}/{r.source_type}] score={r.score:.3f}\n  «{r.text}»"
            if r.score is not None
            else f"- {r.source_id} [{r.tier}/{r.source_type}]\n  «{r.text}»"
            for r in evidence.rejected
        ]
        parts.append(
            "RETRIEVED BUT DISCARDED — found by search, NEVER shown to the assistant. "
            "If the answer's missing fact appears here, this is a RETRIEVAL failure, "
            "not a content gap:\n" + "\n".join(lines)
        )

    note = f" — {'; '.join(evidence.notes)}" if evidence.notes else ""
    parts.append(f"EVIDENCE COMPLETENESS: {evidence.completeness}{note}")
    return "\n\n".join(parts)
