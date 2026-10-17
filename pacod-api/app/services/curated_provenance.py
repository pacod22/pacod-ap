"""Provenance + rendering for LLM-curated KB notes (shared by the loop and admin).

Lives in `app.services` (not `improvement_loop`) so the admin API — whose container
does not ship the loop package — can render/insert on approval while the loop can
pre-render candidates. Two renderings:

- `render_note_body(...)`  — the human-readable note text that is embedded into
  `kb_curated` (no YAML, so the front-matter never pollutes the vectors).
- `render_full_markdown(...)` — the note PLUS a YAML provenance block, stored on the
  candidate as the auditable/reversible record (mirrors the corpus front-matter
  format). `tier: llm_curated`, `trust_weight`, `valid_until`, `currency_basis`,
  and the deriving conversations/models are all recorded here.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date

from app.core.config import get_settings


def candidate_content_hash(topic: str, claim: str, citations: list[str]) -> str:
    """Stable identity for a candidate → re-promotion upserts instead of duplicating."""
    basis = json.dumps(
        {
            "topic": topic.strip().lower(),
            "claim": claim.strip().lower(),
            "citations": sorted(c.strip() for c in citations),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def curated_source_id(content_hash: str) -> str:
    """Stable, collision-resistant source id for the curated note (upsert key)."""
    return f"curated_{content_hash[:16]}"


def _slug(text: str, limit: int = 48) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.UNICODE).strip()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:limit].strip("-") or "note"


def render_note_body(topic: str, claim: str, citations: list[str], currency_basis: str) -> str:
    """The text embedded into kb_curated — readable note, no YAML."""
    lines = [f"# {topic}".rstrip(), "", claim.strip()]
    cites = [c for c in (citations or []) if c.strip()]
    if cites:
        lines += ["", "Основания / Grounding: " + "; ".join(cites)]
    if currency_basis and currency_basis != "n/a":
        lines += ["", f"Валутна основа / currency basis: {currency_basis}"]
    return "\n".join(lines).strip()


def _yaml_list(values: list[str]) -> str:
    if not values:
        return "[]"
    return "[" + ", ".join(json.dumps(v, ensure_ascii=False) for v in values) + "]"


def render_full_markdown(
    *,
    topic: str,
    claim: str,
    citations: list[str],
    currency_basis: str,
    derived_from_turns: list[str],
    models_used: list[str],
    synthesis_confidence: float,
    human_reviewed: bool,
    reviewer: str | None,
    generated_at: date | None = None,
    source_id: str | None = None,
) -> str:
    """Note body + YAML provenance front-matter (the auditable curated record)."""
    s = get_settings()
    gen = (generated_at or date.today()).isoformat()
    sid = source_id or "curated-pending"
    cite_block = "\n".join(f"  - {json.dumps(c, ensure_ascii=False)}" for c in citations) or "  []"
    front = [
        "---",
        f"id: {sid}",
        "tier: llm_curated",
        "source_type: synthesized",
        f"derived_from_conversations: {_yaml_list(derived_from_turns)}",
        f"models_used: {_yaml_list(models_used)}",
        f"synthesis_confidence: {round(synthesis_confidence, 3)}",
        f"human_reviewed: {str(bool(human_reviewed)).lower()}",
        f"reviewer: {json.dumps(reviewer) if reviewer else 'null'}",
        f"generated_at: {gen}",
        "valid_until: null",
        f"currency_basis: {currency_basis or 'n/a'}",
        "grounding_citations:",
        cite_block,
        f"trust_weight: {s.curated_trust_weight}",
        "---",
        "",
    ]
    return "\n".join(front) + render_note_body(topic, claim, citations, currency_basis) + "\n"
