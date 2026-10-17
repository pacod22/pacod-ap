"""Data models for the improvement loop.

`ConversationBundle` is the redacted input to analysis; the pydantic models mirror
the fixed JSON contract each analysis LLM returns (so synthesis stays
machine-routable); `Finding` is the reconciled per-turn outcome persisted to
`loop_findings`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Fixed vocabularies shared across stages.
Assessment = Literal["correct", "partial", "incorrect", "unverifiable"]
FailureMode = Literal["retrieval", "content_gap", "hallucination", "agent_behavior", "tone", "none"]
Route = Literal["retrieval", "prompt_fix", "content_gap", "golden", "none"]
Novelty = Literal["gap", "augment", "covered", "n/a"]

_ASSESSMENTS = {"correct", "partial", "incorrect", "unverifiable"}
_FAILURE_MODES = {"retrieval", "content_gap", "hallucination", "agent_behavior", "tone", "none"}
_STAKES = {"high", "low"}
# "single"   — one analyst plus a critic check (the configured single mode).
# "degraded" — dual was requested but only ONE analysis survived. Previously this
#              reported as "partial", which INFLATED apparent agreement: a run where
#              a model crashed looked the same as one where two models mostly
#              concurred. Both are ineligible for auto-insert (that gate requires
#              "full"), and the distinction is what makes the run report honest.
_AGREEMENT = {"full", "partial", "conflict", "single", "degraded"}


def _coerce_enum(value, allowed: set[str], default: str) -> str:
    """Map a model-supplied value into `allowed`, else the safe default.

    Critical for LLM output: pydantic v2 RAISES on a present-but-out-of-vocabulary
    Literal (the field default only applies when the key is absent), which would
    discard the whole analysis. A `mode="before"` validator using this keeps a
    stray value (`"reasoning"`, `"BGN"`, `"Partial"`) from sinking the parse.
    """
    if isinstance(value, str) and value.strip().lower() in allowed:
        return value.strip().lower()
    return default


def _coerce_confidence(value) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, f))


@dataclass
class ConversationBundle:
    """One completed turn plus its feedback and retrieval trace, already redacted."""

    turn_id: str
    session_id: str | None
    user_id: str | None
    occurred_at: datetime | None
    # Persistence-safe text: all PII redacted INCLUDING money amounts. Used wherever
    # transcript text is written into a loop table (golden tests, debug rows).
    user_message: str
    response: str
    content_hash: str
    # Analysis copies: identifiers redacted but money amounts kept, so the analysis
    # model can fact-check the figures. Default to the persistence-safe text when a
    # bundle is built directly (e.g. in tests); collect() sets them explicitly.
    user_message_analysis: str = ""
    response_analysis: str = ""
    classification: dict = field(default_factory=dict)
    tools_used: list[str] = field(default_factory=list)
    flow_steps: list[str] = field(default_factory=list)
    used_web_search: bool = False
    # Response-level rating (1 like / -1 dislike / None).
    response_rating: int | None = None
    # Chunk-level votes: [{chunk_id, source_id, relevance}].
    retrieval_votes: list[dict] = field(default_factory=list)
    # Retrieval trace candidates: [{chunk_id, source_id, source_type, tier,
    # dense_score, rerank_score, used}]. Empty when traces are off or no kb_search.
    retrieval_trace: list[dict] = field(default_factory=list)
    # Inline citation label (KB-1 / WEB-2 / …) → readable source ref, persisted on
    # the audit row at answer time. Rendered as a SOURCES legend in the analysis
    # prompt so the model can resolve whatever (often freeform) citation syntax the
    # synthesizer used. Empty for turns generated before this was persisted.
    citation_markers: dict = field(default_factory=dict)
    # The triage verdict that admitted this turn to deep analysis: {mode, analyze,
    # reason}. Set by triage() before the bundle reaches _analyse_one, then persisted
    # on the finding so the detail view can show why the turn was analysed.
    triage_decision: dict = field(default_factory=dict)
    # What the assistant actually READ, from `turn_evidence`: {context, directives,
    # tool_calls, legal_year, language, truncated}. Empty for turns answered before
    # evidence capture existed (or with it switched off) — the analysis then runs
    # `degraded` and says so rather than guessing from ids and scores.
    turn_evidence: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Fall back to the persistence-safe text when the analysis copies were not
        # provided (direct construction / tests), so consumers never see "".
        if not self.user_message_analysis:
            self.user_message_analysis = self.user_message
        if not self.response_analysis:
            self.response_analysis = self.response

    def has_explicit_feedback(self) -> bool:
        return self.response_rating is not None or bool(self.retrieval_votes)


def _empty_to_dict(value):
    """A null/non-dict candidate_knowledge from a model → an empty object."""
    return value if isinstance(value, dict) else {}


def _to_str_list(value):
    """Coerce required_citations to a list of strings (models sometimes send a str)."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return []


class CandidateKnowledge(BaseModel):
    """A groundable knowledge nugget a turn suggests adding/curating."""

    topic: str = ""
    claim: str = ""
    required_citations: list[str] = Field(default_factory=list)
    # ISO 4217 code a money/threshold claim is stated in (e.g. "EUR"), else "n/a".
    currency_basis: str = "n/a"
    # The analyst's own judgement of what a wrong claim here would cost the user.
    # The env keyword list is a tax-shaped floor; this is what makes the
    # high-stakes brake on auto-insert meaningful for every other profile.
    stakes: Literal["high", "low"] = "low"

    @field_validator("required_citations", mode="before")
    @classmethod
    def _v_cites(cls, v):
        return _to_str_list(v)

    @field_validator("currency_basis", mode="before")
    @classmethod
    def _v_cb(cls, v):
        code = str(v or "").strip().upper()
        return code if re.fullmatch(r"[A-Z]{3}", code) else "n/a"

    @field_validator("stakes", mode="before")
    @classmethod
    def _v_stakes(cls, v):
        return _coerce_enum(v, _STAKES, "low")


class TurnAnalysis(BaseModel):
    """The fixed schema each analysis model returns for one conversation."""

    user_information_need: str = ""
    answer_assessment: Assessment = "unverifiable"
    failure_mode: FailureMode = "none"
    evidence: str = ""
    feedback_alignment: str = ""
    candidate_knowledge: CandidateKnowledge = Field(default_factory=CandidateKnowledge)
    confidence: float = 0.0

    @field_validator("answer_assessment", mode="before")
    @classmethod
    def _v_aa(cls, v):
        return _coerce_enum(v, _ASSESSMENTS, "unverifiable")

    @field_validator("failure_mode", mode="before")
    @classmethod
    def _v_fm(cls, v):
        return _coerce_enum(v, _FAILURE_MODES, "none")

    @field_validator("candidate_knowledge", mode="before")
    @classmethod
    def _v_ck(cls, v):
        return _empty_to_dict(v)

    @field_validator("confidence", mode="before")
    @classmethod
    def _v_conf(cls, v):
        return _coerce_confidence(v)


class JudgedCandidate(BaseModel):
    """The judge's reconciliation of the two independent analyses into one verdict."""

    answer_assessment: Assessment = "unverifiable"
    failure_mode: FailureMode = "none"
    agreement: Literal["full", "partial", "conflict", "single", "degraded"] = "partial"
    synthesis_confidence: float = 0.0
    candidate_knowledge: CandidateKnowledge = Field(default_factory=CandidateKnowledge)
    rationale: str = ""
    # When the two analyses conflict, both positions are preserved for the reviewer.
    conflict_note: str = ""

    @field_validator("answer_assessment", mode="before")
    @classmethod
    def _v_aa(cls, v):
        return _coerce_enum(v, _ASSESSMENTS, "unverifiable")

    @field_validator("failure_mode", mode="before")
    @classmethod
    def _v_fm(cls, v):
        return _coerce_enum(v, _FAILURE_MODES, "none")

    @field_validator("agreement", mode="before")
    @classmethod
    def _v_agree(cls, v):
        return _coerce_enum(v, _AGREEMENT, "partial")

    @field_validator("candidate_knowledge", mode="before")
    @classmethod
    def _v_ck(cls, v):
        return _empty_to_dict(v)

    @field_validator("synthesis_confidence", mode="before")
    @classmethod
    def _v_conf(cls, v):
        return _coerce_confidence(v)


_REMEDIATION_KINDS = {"answer_fix", "directive", "ingest", "none"}


class DirectiveProposal(BaseModel):
    """A standing behaviour rule, written as an instruction rather than a diagnosis.

    `rule` goes into a directive body verbatim, and a directive outranks tool data
    on every future turn — so it must read as an imperative that stands alone, not
    as a note about the conversation that prompted it.
    """

    title: str = ""
    rule: str = ""
    applies_when: str = ""

    def is_usable(self) -> bool:
        return bool(self.rule.strip())


class Remediation(BaseModel):
    """The artifacts a reviewer needs to actually fix a bad answer.

    This is what turns the loop from a classifier into something that improves the
    agent: a corrected answer (which also gives golden tests a real `expected`),
    assertable facts for the regression set, and — only when warranted — a
    properly-written directive.
    """

    corrected_answer: str = ""
    checkable_facts: list[str] = Field(default_factory=list)
    forbidden_facts: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    directive_proposal: DirectiveProposal = Field(default_factory=DirectiveProposal)
    remediation_kind: str = "none"
    confidence: float = 0.0
    rationale: str = ""

    @field_validator("checkable_facts", "forbidden_facts", "citations", mode="before")
    @classmethod
    def _v_lists(cls, v):
        return _to_str_list(v)

    @field_validator("directive_proposal", mode="before")
    @classmethod
    def _v_proposal(cls, v):
        return _empty_to_dict(v)

    @field_validator("remediation_kind", mode="before")
    @classmethod
    def _v_kind(cls, v):
        return _coerce_enum(v, _REMEDIATION_KINDS, "none")

    @field_validator("confidence", mode="before")
    @classmethod
    def _v_conf(cls, v):
        return _coerce_confidence(v)

    def has_answer(self) -> bool:
        return bool(self.corrected_answer.strip())


@dataclass
class Finding:
    """The reconciled per-turn outcome persisted to loop_findings."""

    turn_id: str
    session_id: str | None
    user_id: str | None
    content_hash: str
    assessment: str
    failure_mode: str
    route: str
    novelty: str
    synthesis_confidence: float
    candidate: dict | None
    evidence: str
    # Per-stage detail, surfaced in the Findings detail view. Defaulted so existing
    # Finding constructions / tests don't break.
    triage: dict | None = None  # {mode, analyze, reason}
    analysis_a: dict | None = None  # TurnAnalysis.model_dump() (None ⇔ model failed)
    analysis_b: dict | None = None  # TurnAnalysis.model_dump() (None ⇔ model failed)
    judge: dict | None = None  # JudgedCandidate.model_dump() (agreement/conflict_note)
    # Did the analysis see the sources, or judge from the transcript alone? Defaults
    # to the SAFE value: a finding that doesn't claim otherwise was a guess. Only a
    # run that actually had the context snapshot sets "full".
    evidence_completeness: str = "degraded"
    # The fix artifacts (Remediation.model_dump()) — corrected answer, assertable
    # facts, and any proposed directive. None when the turn needed no remediation
    # or the stage was skipped.
    remediation: dict | None = None
    remediation_kind: str | None = None  # answer_fix | directive | ingest | none
    # The question (analysis copy, truncated) — for the run report only, NOT a
    # loop_findings column (the Findings view joins audit_log for the full text).
    question: str = ""
