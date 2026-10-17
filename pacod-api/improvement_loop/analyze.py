"""Stage 3 — multi-LLM independent analysis.

Each selected conversation is analysed by two diverse models independently (so
their errors decorrelate). Both return the same fixed JSON schema. The two calls
run concurrently; each is independent, so one failing doesn't sink the other.
"""

from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.services.llm_client import LLMClient
from improvement_loop.llm import call_parsed
from improvement_loop.models import ConversationBundle, TurnAnalysis
from improvement_loop.prompts import conversation_user_block

log = get_logger(__name__)


async def _one(
    llm: LLMClient,
    model: str,
    user: str,
    purpose: str,
    system: str,
    max_tokens: int,
    thinking_budget_tokens: int = 0,
    params=None,
) -> TurnAnalysis | None:
    try:
        return await call_parsed(
            llm,
            model=model,
            system=system,
            user=user,
            purpose=purpose,
            max_tokens=max_tokens,
            schema=TurnAnalysis,
            thinking_budget_tokens=thinking_budget_tokens,
            params=params,
        )
    except Exception as e:
        log.warning("loop_analysis_call_failed", model=model, purpose=purpose, error=str(e))
        return None


async def analyze(
    bundle: ConversationBundle, llm: LLMClient, cfg, system: str, evidence=None, profile=None
) -> tuple[TurnAnalysis | None, TurnAnalysis | None]:
    """Run the two analysis models independently. Either result may be None.

    `system` is the effective analysis prompt (admin override → code default).
    `evidence` is the assembled EvidenceBlock — what the assistant actually read
    plus what search discarded. Both models get the SAME block: their errors are
    only decorrelated if they disagree about the same facts."""
    panel = cfg.resolve_panel()
    user = conversation_user_block(bundle, evidence, profile)
    pa = cfg.stage("analysis_a")

    # Single mode (configured, or degraded into because there is no distinct model
    # B): one analyst, and the critic pass in judge() does the verification. Calling
    # a second model here would either duplicate A or run the cheap classifier — the
    # two things resolve_panel exists to prevent.
    if panel.mode_effective == "single":
        analysis_a = await _one(
            llm,
            panel.models["analysis_a"],
            user,
            "loop_analyze_a",
            system,
            pa.max_tokens,
            pa.thinking_budget_tokens,
            pa,
        )
        return analysis_a, None

    pb = cfg.stage("analysis_b")
    analysis_a, analysis_b = await asyncio.gather(
        _one(
            llm,
            panel.models["analysis_a"],
            user,
            "loop_analyze_a",
            system,
            pa.max_tokens,
            pa.thinking_budget_tokens,
            pa,
        ),
        _one(
            llm,
            panel.models["analysis_b"],
            user,
            "loop_analyze_b",
            system,
            pb.max_tokens,
            pb.thinking_budget_tokens,
            pb,
        ),
    )
    return analysis_a, analysis_b
