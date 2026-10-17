"""Pydantic models for API I/O and internal data flow."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Language(str, Enum):
    BG = "bg"
    EN = "en"


# ---------- API request/response ----------


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: str = Field(..., min_length=1, max_length=128)
    legal_year: int | None = None  # override the profile's legal_year if set
    history: list[ConversationMessage] = Field(default_factory=list)
    # The profile (agent) to chat with. None → the user's sole assigned profile,
    # else the default profile. Validated against the user's assignments for
    # non-admins (403 on an unassigned profile).
    profile_id: str | None = Field(None, max_length=64)
    # Agent-editor variant: "dev" runs the agent's admin-only dev deployment
    # (403 for non-admins, 409 when the agent has none). Default: production.
    agent_variant: Literal["prod", "dev"] = "prod"


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
        )


class QuotaState(BaseModel):
    # Token-based quota (kept `daily_tokens_*` names for wire compatibility; the
    # window is now the configured token period, not necessarily a day).
    daily_tokens_used: int
    daily_tokens_limit: int
    daily_tokens_remaining: int
    token_enabled: bool = True
    token_period: str = "day"  # hour|day|week|month
    # Request-based quota (N requests per the configured request period).
    request_enabled: bool = False
    request_used: int = 0
    request_limit: int = 0
    request_remaining: int = 0
    request_period: str = "day"
    # Independent anti-burst throttle (sliding 60s window).
    requests_this_minute: int
    requests_per_minute_limit: int


class ActivitySummary(BaseModel):
    """Completed-request counts for a user over rolling time windows."""

    last_24h: int
    last_7d: int
    last_30d: int


class Citation(BaseModel):
    source_id: str
    source_type: Literal["law", "guidance", "rate_table", "web", "structured"]
    title: str
    url: str | None = None
    snippet: str | None = None
    legal_year: int | None = None
    chunk_id: str | None = None  # present for KB hits; used by feedback endpoint


class ChatResponseEvent(BaseModel):
    """One SSE event. Type discriminates payload."""

    type: Literal["token", "tool_call", "citation", "error", "done"]
    data: dict


# ---------- Internal types ----------


class RetrievalResult(BaseModel):
    chunk_id: str
    text: str
    score: float
    source_id: str
    source_type: str
    title: str
    url: str | None = None
    legal_year: int | None = None
    language: Language
    parent_id: str | None = None
    # Which KB tier the hit came from: "authoritative" (primary law/guidance) or
    # "curated" (LLM-synthesised, lower trust). Curated hits are score-down-weighted
    # at merge so primary law outranks them. Defaults authoritative for legacy hits.
    tier: Literal["authoritative", "curated"] = "authoritative"


class UsageEvent(BaseModel):
    user_id: str
    session_id: str
    turn_id: str
    occurred_at: datetime
    model: str
    purpose: str  # classifier | planner | synthesizer
    input_tokens: int
    output_tokens: int
    cost_usd: float


# ---------- Topic classifier ----------


class TopicClassification(BaseModel):
    is_relevant: bool
    confidence: float = Field(..., ge=0.0, le=1.0)
    detected_language: Language
    reason: str
    needs_clarification: bool = False
    follow_ups: list[str] = Field(default_factory=list)
    is_illegal: bool = False
