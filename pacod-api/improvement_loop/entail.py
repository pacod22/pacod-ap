"""Stage 7b — entailment check: do the cited passages actually SAY this?

The vector search in `grounding.py` answers "is there a passage about roughly this
topic in the authoritative tier?" — a similarity question. That is not grounding.
A claim can score 0.7 against the right article and still state the wrong figure,
and cosine similarity cannot tell the difference. Since `grounding_status` gates
`auto_insert_eligible` — the only path that writes the curated tier with no human
in the loop — a similarity proxy there is a hallucination-injection risk.

This module asks the real question of the retrieved text: does it SUPPORT the
claim, CONTRADICT it, or simply not address it. `contradicted` is the verdict
worth the extra call: it is invisible to a threshold and is exactly the case that
must never be curated.

One cheap LLM call per candidate, and only for candidates that already cleared the
cosine gate — so the volume is a small fraction of the analysed turns.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, field_validator

from app.core.logging import get_logger
from app.services.llm_client import LLMClient
from improvement_loop.llm import call_parsed
from improvement_loop.models import _coerce_enum

log = get_logger(__name__)

_VERDICTS = {"supported", "contradicted", "not_stated"}

# How much of each retrieved passage the verifier sees. Articles are long; the
# decisive sentence is nearly always near the top of the matched chunk.
_PASSAGE_CHARS = 1200
_MAX_PASSAGES = 4


class EntailmentVerdict(BaseModel):
    """The verifier's reading of the retrieved passages."""

    verdict: str = "not_stated"
    cited_span: str = ""
    note: str = ""

    @field_validator("verdict", mode="before")
    @classmethod
    def _v_verdict(cls, v):
        # An out-of-vocabulary verdict must not become "supported" by accident —
        # the safe default is the one that blocks auto-insert.
        return _coerce_enum(v, _VERDICTS, "not_stated")


@dataclass
class EntailmentResult:
    verdict: str  # supported | contradicted | not_stated
    cited_span: str
    note: str


def entailment_user_block(claim: str, citation: str, passages: list[str]) -> str:
    """Render the claim + its citation + the retrieved passages for the verifier."""
    blocks = []
    for i, text in enumerate(passages[:_MAX_PASSAGES], start=1):
        snippet = (text or "").strip()[:_PASSAGE_CHARS]
        if snippet:
            blocks.append(f"[PASSAGE {i}]\n{snippet}")
    return (
        f"CITATION NAMED BY THE CLAIM: {citation}\n\n"
        f"CANDIDATE CLAIM:\n{claim}\n\n"
        f"RETRIEVED PASSAGES FROM THE AUTHORITATIVE SOURCE:\n"
        + ("\n\n".join(blocks) or "(no passages retrieved)")
    )


async def check_entailment(
    *,
    claim: str,
    citation: str,
    passages: list[str],
    llm: LLMClient,
    model: str,
    system: str,
    max_tokens: int = 800,
    thinking_budget_tokens: int = 0,
    params=None,
) -> EntailmentResult:
    """Ask whether `passages` support / contradict / don't state `claim`.

    Never raises — a verifier outage must not fail the run. On any failure the
    verdict is `not_stated`, which blocks auto-insert but still lets a human
    reviewer approve the candidate; the note records why.
    """
    if not claim.strip() or not any((p or "").strip() for p in passages):
        return EntailmentResult("not_stated", "", "no claim or no passages to check")

    try:
        parsed = await call_parsed(
            llm,
            model=model,
            system=system,
            user=entailment_user_block(claim, citation, passages),
            purpose="loop_entailment",
            max_tokens=max_tokens,
            schema=EntailmentVerdict,
            thinking_budget_tokens=thinking_budget_tokens,
            params=params,
        )
    except Exception as e:
        log.warning("loop_entailment_call_failed", citation=citation, error=str(e))
        return EntailmentResult("not_stated", "", f"verifier unavailable: {e}"[:200])

    if parsed is None:
        return EntailmentResult("not_stated", "", "verifier returned unparseable JSON")
    return EntailmentResult(parsed.verdict, parsed.cited_span[:1000], parsed.note[:500])
