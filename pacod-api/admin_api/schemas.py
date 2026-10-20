"""Pydantic schemas for the management CRUD endpoints."""

from __future__ import annotations

import json as _json
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.db_models import DEFAULT_PROFILE_ID

# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------


class UserCreate(BaseModel):
    # Ignored: pacod-id assigns the account id (usr_…), which becomes users.id.
    id: str | None = Field(None, max_length=64)
    email: str = Field(..., max_length=255)
    display_name: str = Field(..., max_length=128)
    # Optional: without one, pacod-id's reset link lets the person set it.
    password: str | None = Field(None, min_length=8)
    is_active: bool = True
    quota_disabled: bool = False
    daily_token_limit: int | None = Field(None, ge=1)
    request_limit: int | None = Field(None, ge=1)


class UserUpdate(BaseModel):
    email: str | None = Field(None, max_length=255)
    display_name: str | None = Field(None, max_length=128)
    is_active: bool | None = None
    roles: list[str] | None = None
    quota_disabled: bool | None = None
    # Explicit null clears the override (revert to the global default); omit to leave unchanged.
    daily_token_limit: int | None = Field(None, ge=1)
    request_limit: int | None = Field(None, ge=1)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    display_name: str
    roles: list[str] = []
    is_active: bool
    quota_disabled: bool = False
    daily_token_limit: int | None = None
    request_limit: int | None = None
    # Derived, read-only display fields: today's spend and the limit actually in
    # force (per-user override, else the global default).
    daily_tokens_used: int = 0
    effective_daily_limit: int = 0
    created_at: datetime
    # Activity: last interactive login, and last guarded request (any audit_log row).
    # Both NULL until the user has logged in / made a request.
    last_login_at: datetime | None = None
    last_action_at: datetime | None = None


# ---------------------------------------------------------------------------
# quota config (global enforcement toggle)
# ---------------------------------------------------------------------------

_QUOTA_PERIODS = ("hour", "day", "week", "month")


class QuotaConfigRead(BaseModel):
    enforcement_enabled: bool
    env_kill_switch: bool
    # Per-type settings (which quota types apply + their limits + validity periods).
    token_enabled: bool
    token_limit: int
    token_period: str
    request_enabled: bool
    request_limit: int
    request_period: str
    # Where the token_* fields come from: "gateway" (its user-daily-tokens limit) or
    # "unavailable" (the gateway did not answer; the values shown are the local defaults).
    token_source: str = "gateway"


class QuotaConfigUpdate(BaseModel):
    # All optional — a partial PATCH updates only the provided fields.
    enforcement_enabled: bool | None = None
    token_enabled: bool | None = None
    token_limit: int | None = Field(None, ge=1)
    token_period: str | None = None
    request_enabled: bool | None = None
    request_limit: int | None = Field(None, ge=1)
    request_period: str | None = None

    @field_validator("token_period", "request_period")
    @classmethod
    def _valid_period(cls, v: str | None) -> str | None:
        if v is not None and v not in _QUOTA_PERIODS:
            raise ValueError(f"period must be one of {_QUOTA_PERIODS}")
        return v


# ---------------------------------------------------------------------------
# llm config (active models, generation params, web search) — runtime
# ---------------------------------------------------------------------------


class LLMConfigValues(BaseModel):
    primary_model: str
    classifier_model: str
    planner_model: str  # "" → falls back to primary_model
    temperature: float
    temperature_enabled: bool
    max_output_tokens: int
    planner_max_tokens: int
    classifier_max_tokens: int
    suggestions_max_tokens: int
    suggestions_model: str  # "" → falls back to classifier_model
    newsletter_model: str  # "" → falls back to primary_model (newsletter worker)
    thinking_budget_tokens: int
    prompt_cache_enabled: bool
    verification_enabled: bool
    verification_complex_only: bool
    history_enabled: bool
    history_recent_turns: int
    history_summarize_after: int
    history_summary_max_tokens: int
    history_summary_model: str  # "" → falls back to classifier_model
    history_summary_ttl_seconds: int
    web_search_enabled: bool
    web_search_provider: str
    web_search_allowed_domains: list[str]
    web_search_mode: str  # "external" | "native"
    web_search_native_require_allowlist: bool
    # LLM template per role ("" → the legacy per-role fields above).
    synthesizer_template: str = ""
    verifier_template: str = ""
    planner_template: str = ""
    classifier_template: str = ""
    suggestions_template: str = ""
    history_summary_template: str = ""
    newsletter_template: str = ""


class LLMConfigRead(BaseModel):
    effective: LLMConfigValues
    env_default: LLMConfigValues
    available_models: list[str]
    # Fields owned by the agent's published Agent-editor version (read-only here).
    managed_fields: list[str] = []
    # What each role actually sends ({role: LLMCallParams dict}) — its template,
    # or the legacy fields; managed roles show the overlay-merged legacy values.
    role_params: dict[str, dict] = {}
    # Roles a published Agent-editor version owns: their template is ignored.
    managed_roles: list[str] = []


class LLMConfigUpdate(BaseModel):
    """All fields optional — only provided fields are updated."""

    primary_model: str | None = Field(None, min_length=1)
    classifier_model: str | None = Field(None, min_length=1)
    planner_model: str | None = None  # "" allowed → clears the override
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    temperature_enabled: bool | None = None
    max_output_tokens: int | None = Field(None, ge=1, le=200_000)
    planner_max_tokens: int | None = Field(None, ge=1, le=32_000)
    classifier_max_tokens: int | None = Field(None, ge=1, le=32_000)
    suggestions_max_tokens: int | None = Field(None, ge=1, le=32_000)
    suggestions_model: str | None = None  # "" allowed → clears the override
    newsletter_model: str | None = None  # "" allowed → clears the override
    thinking_budget_tokens: int | None = Field(None, ge=0, le=200_000)
    prompt_cache_enabled: bool | None = None
    verification_enabled: bool | None = None
    verification_complex_only: bool | None = None
    history_enabled: bool | None = None
    history_recent_turns: int | None = Field(None, ge=0, le=50)
    history_summarize_after: int | None = Field(None, ge=1, le=100)
    history_summary_max_tokens: int | None = Field(None, ge=1, le=32_000)
    history_summary_model: str | None = None  # "" allowed → clears the override
    history_summary_ttl_seconds: int | None = Field(None, ge=60, le=2_592_000)
    web_search_enabled: bool | None = None
    web_search_provider: Literal["auto", "tavily", "google"] | None = None
    web_search_allowed_domains: list[str] | None = None
    web_search_mode: Literal["external", "native"] | None = None
    web_search_native_require_allowlist: bool | None = None
    # LLM template id per role; "" → back to the legacy per-role fields.
    synthesizer_template: str | None = None
    verifier_template: str | None = None
    planner_template: str | None = None
    classifier_template: str | None = None
    suggestions_template: str | None = None
    history_summary_template: str | None = None
    newsletter_template: str | None = None


# ---------------------------------------------------------------------------
# LLM templates (global) — /api/v1/llm-config/templates
# ---------------------------------------------------------------------------

ReasoningEffort = Literal["low", "medium", "high", "xhigh", "max"]


class LLMTemplateUsage(BaseModel):
    profile_id: str
    group: str  # agent | loop | assistant
    role: str


class LLMTemplateOut(BaseModel):
    id: str
    name: str
    description: str = ""
    model: str
    max_output_tokens: int
    temperature: float | None = None
    thinking_budget_tokens: int = 0
    reasoning_effort: str | None = None
    top_p: float | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    timeout_seconds: float | None = None
    updated_by: str | None = None
    updated_at: str | None = None
    usages: list[LLMTemplateUsage] = []
    # Stored parameters this template's model rejects (stripped from requests).
    ignored_params: list[str] = []


class LLMTemplatesRead(BaseModel):
    templates: list[LLMTemplateOut]
    available_models: list[str]
    # {model: ModelCapabilities} for every available model and template model.
    capabilities: dict[str, dict]


class _LLMTemplateParams(BaseModel):
    description: str | None = Field(None, max_length=2000)
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    thinking_budget_tokens: int | None = Field(None, ge=0, le=200_000)
    reasoning_effort: ReasoningEffort | None = None
    top_p: float | None = Field(None, ge=0.0, le=1.0)
    frequency_penalty: float | None = Field(None, ge=-2.0, le=2.0)
    presence_penalty: float | None = Field(None, ge=-2.0, le=2.0)
    # ≤ the gateway's provider timeout (600 s) or the gateway gives up first.
    timeout_seconds: float | None = Field(None, ge=1.0, le=600.0)


class LLMTemplateCreate(_LLMTemplateParams):
    id: str | None = Field(None, max_length=64)  # blank → slugified name
    name: str = Field(..., min_length=1, max_length=160)
    model: str = Field(..., min_length=1, max_length=128)
    max_output_tokens: int = Field(..., ge=1, le=200_000)


class LLMTemplateUpdate(_LLMTemplateParams):
    """Only provided fields change; an explicit null clears an optional knob."""

    name: str | None = Field(None, min_length=1, max_length=160)
    model: str | None = Field(None, min_length=1, max_length=128)
    max_output_tokens: int | None = Field(None, ge=1, le=200_000)


# Provider API keys (masked). Phase 2.
class ProviderKeyStatus(BaseModel):
    name: str
    configured: bool
    last4: str | None = None
    target: Literal["live"]
    pending_restart: bool = False


class ProviderKeysRead(BaseModel):
    editable: bool  # False when APP_SECRETS_KEY is unset
    keys: list[ProviderKeyStatus]


class ProviderKeyUpdate(BaseModel):
    value: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# newsletter
# ---------------------------------------------------------------------------


class NewsletterConfigValues(BaseModel):
    collect_enabled: bool
    collect_days: str  # CSV weekdays Mon=0..Sun=6 (blank → every day)
    collect_hour: int
    collect_minute: int
    send_enabled: bool
    send_days: str
    send_hour: int
    send_minute: int
    max_sources: int
    max_articles_per_source: int
    crawl_concurrency: int
    max_items_per_issue: int
    min_relevance: float
    subject_template: str


class NewsletterConfigUpdate(BaseModel):
    """All fields optional — only provided fields are updated."""

    # Global (not per-profile): make THIS profile the public landing-page
    # subscribe target (true), or clear it if it currently is (false).
    public_subscribe: bool | None = None
    collect_enabled: bool | None = None
    collect_days: str | None = None
    collect_hour: int | None = Field(None, ge=0, le=23)
    collect_minute: int | None = Field(None, ge=0, le=59)
    send_enabled: bool | None = None
    send_days: str | None = None
    send_hour: int | None = Field(None, ge=0, le=23)
    send_minute: int | None = Field(None, ge=0, le=59)
    max_sources: int | None = Field(None, ge=1, le=500)
    max_articles_per_source: int | None = Field(None, ge=1, le=200)
    crawl_concurrency: int | None = Field(None, ge=1, le=32)
    max_items_per_issue: int | None = Field(None, ge=1, le=200)
    min_relevance: float | None = Field(None, ge=0.0, le=1.0)
    subject_template: str | None = Field(None, max_length=512)


class NewsletterRunRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job: str
    status: str
    trigger: str
    requested_by: str | None
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    issue_id: int | None
    error: str | None


class NewsletterIssueSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subject: str
    item_count: int
    recipient_count: int
    status: str
    created_at: datetime
    sent_at: datetime | None
    error: str | None


class NewsletterIssueDetail(NewsletterIssueSummary):
    html: str | None = None


class NewsletterConfigRead(BaseModel):
    effective: NewsletterConfigValues
    env_default: NewsletterConfigValues
    available_models: list[str]
    recent_issues: list[NewsletterIssueSummary]
    recent_requests: list[NewsletterRunRequestRead]
    # True when this profile is the target of the public landing-page subscribe
    # form (global setting `newsletter.public_profile_id`).
    public_subscribe: bool = False


class NewsletterSubscriberRead(BaseModel):
    """One newsletter subscriber — account-based or email-only."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    profile_id: str | None
    email: str | None
    kind: str  # 'account' | 'email'
    status: str  # pending | subscribed | unsubscribed
    confirmed: bool
    user_id: str | None
    display_name: str | None
    created_at: datetime


class NewsletterSubscriberUpdate(BaseModel):
    status: Literal["subscribed", "unsubscribed"]


class NewsletterRunTrigger(BaseModel):
    """Body for enqueuing a manual collect/send (job is set by the route)."""

    profile_id: str | None = None


class NewsletterSourceCreate(BaseModel):
    url: str = Field(..., max_length=1024)
    kind: Literal["article", "index", "feed"] = "article"
    title: str | None = Field(None, max_length=512)
    is_active: bool = True
    profile_id: str | None = None


class NewsletterSourceUpdate(BaseModel):
    url: str | None = Field(None, max_length=1024)
    kind: Literal["article", "index", "feed"] | None = None
    title: str | None = Field(None, max_length=512)
    is_active: bool | None = None


class NewsletterSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    profile_id: str | None
    url: str
    kind: str
    title: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class NewsletterItemUpdate(BaseModel):
    title: str | None = Field(None, max_length=512)
    summary: str | None = None
    relevance: float | None = Field(None, ge=0.0, le=1.0)
    importance: float | None = Field(None, ge=0.0, le=1.0)
    urgency: float | None = Field(None, ge=0.0, le=1.0)


class NewsletterItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    profile_id: str | None
    source_id: int | None
    article_url: str | None
    title: str
    summary: str
    source_label: str | None
    relevance: float
    importance: float
    urgency: float
    status: str
    issue_id: int | None
    collected_at: datetime


# ---------------------------------------------------------------------------
# response feedback (read-only)
# ---------------------------------------------------------------------------


class ResponseFeedbackRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: str
    session_id: str
    turn_id: str
    query: str
    response: str | None
    rating: int  # 1 = like, -1 = dislike
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# profiles (multi-agent)
# ---------------------------------------------------------------------------


class ProfileCreate(BaseModel):
    slug: str = Field(..., max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(..., max_length=128)
    short_name_en: str | None = Field(None, max_length=64)
    short_name_bg: str | None = Field(None, max_length=64)
    short_name_de: str | None = Field(None, max_length=64)
    short_name_ru: str | None = Field(None, max_length=64)
    description: str | None = None
    topic_name: str = Field("general", max_length=64)
    topic_description_bg: str | None = None
    topic_description_en: str | None = None
    legal_year: int = Field(2026, ge=2000, le=2100)
    # CSV of language codes, e.g. "bg,en".
    languages: str = Field("bg,en", max_length=64)
    # Which front-end drives this agent: "chat" (conversational RAG) is the
    # only kind today.
    ui_kind: Literal["chat"] = "chat"


class ProfileUpdate(BaseModel):
    name: str | None = Field(None, max_length=128)
    short_name_en: str | None = Field(None, max_length=64)
    short_name_bg: str | None = Field(None, max_length=64)
    short_name_de: str | None = Field(None, max_length=64)
    short_name_ru: str | None = Field(None, max_length=64)
    description: str | None = None
    topic_name: str | None = Field(None, max_length=64)
    topic_description_bg: str | None = None
    topic_description_en: str | None = None
    legal_year: int | None = Field(None, ge=2000, le=2100)
    languages: str | None = Field(None, max_length=64)
    ui_kind: Literal["chat"] | None = None
    is_active: bool | None = None


class ProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    name: str
    short_name_en: str | None
    short_name_bg: str | None
    short_name_de: str | None
    short_name_ru: str | None
    description: str | None
    topic_name: str
    topic_description_bg: str | None
    topic_description_en: str | None
    legal_year: int
    languages: str
    base_collection: str
    curated_collection: str
    ui_kind: str = "chat"
    is_active: bool
    # Visual agent editor: prod | dev (admins only) | archived, + version pointers.
    release_state: str = "prod"
    published_version_id: int | None = None
    dev_version_id: int | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Visual agent editor (superuser)
# ---------------------------------------------------------------------------


class AgentCreate(BaseModel):
    slug: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
    name: str = Field(..., min_length=1, max_length=128)
    template: Literal["rag_chat_v1"] = "rag_chat_v1"


class AgentDraftSave(BaseModel):
    graph: dict
    revision: int = Field(..., ge=1)


class AgentGraphCheck(BaseModel):
    graph: dict


class AgentImport(BaseModel):
    force: bool = False


class AgentPublish(BaseModel):
    note: str | None = Field(None, max_length=500)
    assign_to_all_users: bool = False


class AgentVersionRef(BaseModel):
    version_id: int


class AgentAssist(BaseModel):
    """Ask the editor's AI assistant to draft a component's text fields."""

    graph: dict  # the CURRENT (possibly unsaved) draft — gives the assistant context
    node_id: str = Field(..., max_length=64)
    field: str | None = Field(None, max_length=64)  # None → all text fields
    instruction: str = Field("", max_length=4000)


class AssistantConfigUpdate(BaseModel):
    """All fields optional — only provided fields are updated."""

    enabled: bool | None = None
    model: str | None = Field(None, max_length=128)  # "" → the platform's primary model
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    temperature_enabled: bool | None = None
    max_output_tokens: int | None = Field(None, ge=256, le=64000)
    thinking_budget_tokens: int | None = Field(None, ge=0, le=64000)
    guidelines: str | None = Field(None, max_length=8000)
    template: str | None = Field(None, max_length=64)  # LLM template; "" → fields above


class AgentReleaseState(BaseModel):
    state: Literal["dev", "prod", "archived"]


# ---------------------------------------------------------------------------
# user_profiles (assignment)
# ---------------------------------------------------------------------------


class UserProfileAssign(BaseModel):
    profile_id: str = Field(..., max_length=64)


class UserProfileRead(BaseModel):
    user_id: str
    profile_id: str
    profile_slug: str
    profile_name: str
    granted_at: datetime


# ---------------------------------------------------------------------------
# reference_facts
# ---------------------------------------------------------------------------

ReferenceValueType = Literal[
    "number",
    "percent",
    "count",
    "money",
    "range",
    "date",
    "annual_date",
    "monthly_day",
    "text",
    "boolean",
]


class ReferenceFactFields(BaseModel):
    """Every editable field of a reference fact (see ``ReferenceFact``)."""

    fact_key: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    category: str | None = Field(None, max_length=64)
    variant: str = Field("default", min_length=1, max_length=64)
    legal_year: int | None = Field(None, ge=1900, le=2200)
    value_type: ReferenceValueType = "number"
    value_num: float | None = None
    value_max: float | None = None
    unit: str | None = Field(None, max_length=32)
    value_text: str | None = None
    extra: dict | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    label_bg: str | None = Field(None, max_length=256)
    label_en: str | None = Field(None, max_length=256)
    description_bg: str | None = None
    description_en: str | None = None
    source_url: str | None = Field(None, max_length=512)
    source_note: str | None = None


class ReferenceFactCreate(ReferenceFactFields):
    profile_id: str = Field(DEFAULT_PROFILE_ID, max_length=64)


class ReferenceFactUpdate(BaseModel):
    fact_key: str | None = Field(None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    category: str | None = Field(None, max_length=64)
    variant: str | None = Field(None, min_length=1, max_length=64)
    legal_year: int | None = Field(None, ge=1900, le=2200)
    value_type: ReferenceValueType | None = None
    value_num: float | None = None
    value_max: float | None = None
    unit: str | None = Field(None, max_length=32)
    value_text: str | None = None
    extra: dict | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    label_bg: str | None = Field(None, max_length=256)
    label_en: str | None = Field(None, max_length=256)
    description_bg: str | None = None
    description_en: str | None = None
    source_url: str | None = Field(None, max_length=512)
    source_note: str | None = None


class ReferenceFactRead(ReferenceFactFields):
    id: int
    profile_id: str
    updated_by: str | None = None
    created_at: datetime
    updated_at: datetime


class ReferenceFactImport(BaseModel):
    """Bulk upsert, keyed on (fact_key, variant, legal_year) within one profile.
    ``replace`` first deletes every fact of the profile that is not in ``facts``."""

    facts: list[ReferenceFactFields] = Field(..., max_length=5000)
    replace: bool = False


class ReferenceFactImportResult(BaseModel):
    created: int
    updated: int
    deleted: int


# ---------------------------------------------------------------------------
# usage_events  (append-only — no update schema)
# ---------------------------------------------------------------------------


class UsageEventCreate(BaseModel):
    user_id: str = Field(..., max_length=64)
    session_id: str = Field(..., max_length=128)
    turn_id: str = Field(..., max_length=64)
    model: str = Field(..., max_length=128)
    purpose: str = Field(..., max_length=32)
    input_tokens: int = Field(..., ge=0)
    output_tokens: int = Field(..., ge=0)
    cached_tokens: int = Field(0, ge=0)
    cost_usd: float = Field(0.0, ge=0.0)


class UsageEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: str
    session_id: str
    turn_id: str
    occurred_at: datetime
    model: str
    purpose: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cost_usd: float


# ---------------------------------------------------------------------------
# usage aggregation (read-only; backs the LLM & Search "Usage & costs" tab)
# ---------------------------------------------------------------------------


class UsageBucketPoint(BaseModel):
    """One time bucket in the usage time series."""

    bucket_start: datetime
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cost_usd: float
    event_count: int


class UsageBreakdownRow(BaseModel):
    """Aggregated usage for one key (provider, model, purpose or user)."""

    key: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cost_usd: float
    event_count: int


class UsageWindowTotals(BaseModel):
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    event_count: int


class UsageOverviewResponse(BaseModel):
    """Totals for the current calendar hour, day, week (from Monday) and month (from the
    1st), UTC — the gateway's periods, so they match its console. LLM figures are the
    gateway's; web-search cost is added from the local rows."""

    hour: UsageWindowTotals
    day: UsageWindowTotals
    week: UsageWindowTotals
    month: UsageWindowTotals
    # Set when the gateway's reports could not be read: the LLM figures are then 0.
    gateway_error: str | None = None
    console_url: str | None = None


class UsageTurnRow(BaseModel):
    """One per-(purpose, model) usage line for a single chat turn."""

    purpose: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cost_usd: float
    event_count: int


class UsageTurnBreakdown(BaseModel):
    """Token/cost breakdown for one turn (backs the conversation detail dialog).

    The LLM rows are the gateway's record of the turn (its trace, ``trace_id`` = the turn
    id), priced when the calls ran; web-search rows are local.
    """

    turn_id: str
    rows: list[UsageTurnRow]
    total: UsageWindowTotals
    gateway_error: str | None = None
    # The turn's trace in the gateway console (every call, timings, errors).
    console_url: str | None = None


# ---------------------------------------------------------------------------
# models (read-only: the gateway's list for this platform)
# ---------------------------------------------------------------------------


class LlmModelRead(BaseModel):
    """One model the gateway lets this platform call."""

    id: str
    owned_by: str | None = None  # the gateway provider that serves it


class LlmModelsResponse(BaseModel):
    """The LLM models admin page: the gateway's list, and where to manage it."""

    models: list[LlmModelRead]
    # The gateway console, for adding models, prices and provider keys (None: not set).
    console_url: str | None = None
    # False when the gateway did not answer (the list is then the last one seen, or empty).
    reachable: bool = True


class DashboardStats(BaseModel):
    """Platform-wide headline metrics for the admin dashboard.

    No Qdrant call, so a vector-store outage can never blank the panel; the token and
    cost totals are the gateway's (zeros and ``gateway_error`` when it does not answer).
    Request/active-user windows are *rolling* (now - delta), not calendar-aligned, to match
    the card labels.
    """

    # Knowledge base (ingested_sources)
    sources_total: int  # distinct ingested sources
    chunks_total: int  # sum of chunk_count across sources

    # Tokens and cost, platform-wide all-time (the gateway's record + web search)
    tokens_total: int  # input + output
    tokens_input: int
    tokens_output: int
    cost_usd_total: float

    # Users
    users_total: int  # registered accounts
    active_users_5min: int  # distinct user_id seen in the last 5 minutes
    active_users_24h: int  # distinct user_id seen in the last 24 hours

    # Answered requests (request_completed; excludes rejected turns)
    requests_last_hour: int
    requests_last_24h: int
    requests_last_30d: int

    # The token and cost figures are the gateway's: set when it could not be read.
    gateway_error: str | None = None


class UsageModelSeries(BaseModel):
    """Per-model values aligned to the `timeseries` buckets (for a stacked chart).

    Both `cost_usd` and `tokens` (input+output) are provided so the chart can
    switch its y-axis metric without re-querying.
    """

    model: str
    cost_usd: list[float]
    tokens: list[int]


class UsageSummaryResponse(BaseModel):
    # `model_series` starts with the Pydantic-protected `model_` prefix; opt out
    # so the field name is allowed without a warning.
    model_config = ConfigDict(protected_namespaces=())

    bucket: Literal["hour", "day", "week", "month"]
    range_start: datetime
    range_end: datetime
    totals: UsageBreakdownRow
    timeseries: list[UsageBucketPoint]
    # One series per model (top N by cost; the rest grouped into "Other"), each
    # cost_usd list aligned index-for-index with `timeseries` buckets.
    model_series: list[UsageModelSeries]
    by_provider: list[UsageBreakdownRow]
    by_model: list[UsageBreakdownRow]
    by_purpose: list[UsageBreakdownRow]
    by_user: list[UsageBreakdownRow]
    # Per-profile (agent) cost/token breakdown. In global mode this drives the
    # "filter by profile" dropdown; when scoped it's the single selected profile.
    by_profile: list[UsageBreakdownRow] = []
    gateway_error: str | None = None
    console_url: str | None = None


# ---------------------------------------------------------------------------
# audit_log  (append-only — no update schema)
# ---------------------------------------------------------------------------


class AuditLogCreate(BaseModel):
    user_id: str | None = Field(None, max_length=64)
    session_id: str | None = Field(None, max_length=128)
    event_type: str = Field(..., max_length=64)
    detail: str
    turn_id: str | None = Field(None, max_length=64)
    user_message: str | None = None
    response: str | None = None
    classification: dict | None = None
    tools_used: list | None = None
    flow_steps: list | None = None


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    occurred_at: datetime
    user_id: str | None
    session_id: str | None
    event_type: str
    detail: str
    turn_id: str | None = None
    user_message: str | None = None
    response: str | None = None
    # Stored as JSON text in the DB; parsed back to Python objects here.
    classification: dict | None = None
    tools_used: list | None = None
    flow_steps: list | None = None

    @field_validator("classification", mode="before")
    @classmethod
    def _parse_classification(cls, v):
        return _json.loads(v) if isinstance(v, str) else v

    @field_validator("tools_used", "flow_steps", mode="before")
    @classmethod
    def _parse_json_list(cls, v):
        return _json.loads(v) if isinstance(v, str) else v


# ---------------------------------------------------------------------------
# ingested_sources  (read + delete — written exclusively by the pipeline)
# ---------------------------------------------------------------------------

# The `source_type` values the KB payload uses. Mirrors ingestion_api.schemas.SourceType
# (duplicated, not imported — the admin-api image ships only app/ and admin_api/).
# `curated_note` is the manual counterpart of `curated_qa`: hand-authored curated
# knowledge that isn't a Q&A.
SourceType = Literal[
    "law",
    "guidance",
    "rate_table",
    "web",
    "structured",
    "document",
    "curated_qa",
    "curated_note",
]


class IngestionSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: str
    title: str
    source_type: str
    legal_year: int
    language: str
    url: str | None
    file_path: str | None
    chunk_count: int
    ingested_at: datetime
    uploaded_at: datetime | None
    status: str
    # Which epistemic tier the source lives in: "curated" (down-weighted, physically
    # separate collection) or "authoritative". NULL only on rows predating the column.
    tier: str | None = None
    # The owning profile — with `tier`, it names the collection the source lives in.
    # NULL only on rows predating multi-profile (read as the default profile).
    profile_id: str | None = None


class IngestionSourceStatusUpdate(BaseModel):
    status: Literal["OUTDATED", "NEW"]


class IngestUrlRequest(BaseModel):
    url: str
    title: str
    legal_year: int | None = None
    profile_id: str | None = None


class IngestResponse(BaseModel):
    source: IngestionSourceRead
    chunks: int


class IngestTextRequest(BaseModel):
    """Promote arbitrary curated text (e.g. an edited Q&A) into the KB."""

    title: str = Field(..., min_length=1, max_length=512)
    body: str = Field(..., min_length=1)
    legal_year: int | None = None
    language: Literal["bg", "en"] | None = None
    source_id: str | None = Field(None, max_length=255)
    # Which KB tier to write into. This endpoint exists to promote curated Q&A,
    # so it defaults to the profile's curated collection (pacod_<slug>_curated);
    # pass tier="authoritative" explicitly to target the main collection instead.
    tier: Literal["authoritative", "curated"] = "curated"
    profile_id: str | None = None
    # Payload label. Defaults to curated_qa (this route began as the promote-a-Q&A
    # path); a hand-authored note should pass curated_note or document instead.
    source_type: SourceType = "curated_qa"


class CuratedContentRead(BaseModel):
    """The authored body of a source, for the curated editor."""

    source_id: str
    title: str
    source_type: str
    tier: str
    language: str
    legal_year: int
    profile_id: str | None = None
    # The Qdrant collection the source's vectors live in (resolved from profile +
    # tier), so the editor can show where an entry actually is.
    collection: str | None = None
    body: str
    # True when `body` was rebuilt from the Qdrant chunks because no authored copy
    # was stored (pre-`content` rows, loop-approved notes). Round-tripping is
    # faithful but not byte-identical to the original input.
    reconstructed: bool = False


class CuratedContentUpdate(BaseModel):
    """Rewrite a source's body (and optionally its metadata), then re-ingest."""

    title: str = Field(..., min_length=1, max_length=512)
    body: str = Field(..., min_length=1)
    tier: Literal["authoritative", "curated"] = "curated"
    source_type: SourceType = "curated_note"
    language: Literal["bg", "en"] | None = None
    legal_year: int | None = None
    # Must name the row's own profile (or be omitted). Moving a source to another
    # profile's collection goes through POST /{id}/move — re-ingesting under a new
    # profile here would upsert a SECOND row (unique on (profile_id, source_id))
    # and leave the original behind with its vectors already deleted.
    profile_id: str | None = None


class CuratedMoveRequest(BaseModel):
    """Relocate a curated source into another profile's curated collection."""

    profile_id: str = Field(..., min_length=1, max_length=64)


class IngestImportRowError(BaseModel):
    """A single CSV row that could not be imported."""

    row: int  # 1-based data-row index (header excluded)
    source_id: str | None = None
    error: str


class IngestImportResult(BaseModel):
    """Summary of a metadata-only CSV import into ingested_sources."""

    total_rows: int
    created: int
    skipped: int  # rows skipped because the source_id already exists
    errors: list[IngestImportRowError] = Field(default_factory=list)
    created_source_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# agent_directives  (admin-curated high-priority synthesis instructions)
# ---------------------------------------------------------------------------


class DirectiveCreate(BaseModel):
    profile_id: str = Field(DEFAULT_PROFILE_ID, max_length=64)
    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1)
    language: Literal["bg", "en", "all"] = "all"
    legal_year: int | None = None
    priority: int = Field(100, ge=0, le=10000)
    is_active: bool = True


class DirectiveUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    content: str | None = Field(None, min_length=1)
    language: Literal["bg", "en", "all"] | None = None
    legal_year: int | None = None
    priority: int | None = Field(None, ge=0, le=10000)
    is_active: bool | None = None


class DirectiveRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    profile_id: str | None = None
    title: str
    content: str
    language: str
    legal_year: int | None
    priority: int
    is_active: bool
    created_by: str | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# conversations  (unified view over audit_log: Q&A turns + raw audit events,
# enriched with the user's response rating and the admin review status)
# ---------------------------------------------------------------------------

REVIEW_STATUSES = ("new", "in_review", "processed")


class ConversationRowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    occurred_at: datetime
    # Which profile (agent) this turn belongs to — lets the UI route a promoted
    # Q&A to the right profile's curated collection even in the all-profiles view.
    profile_id: str | None = None
    user_id: str | None
    # The user's account email (users.email), resolved from user_id. Shown in place
    # of the raw user_id; null for dev/system principals with no users row.
    user_email: str | None = None
    session_id: str | None
    event_type: str
    detail: str | None = None
    turn_id: str | None = None
    user_message: str | None = None
    response: str | None = None
    classification: dict | None = None
    tools_used: list | None = None
    flow_steps: list | None = None
    duration_ms: int | None = None
    # User's 👍/👎 on the answer (response_feedback.rating), joined per turn.
    rating: int | None = None
    # Source of that rating: 'user' (chat vote) or 'admin' (override); NULL/'user' = user.
    rating_set_by: str | None = None
    # Admin review workflow. NULL is surfaced as 'new'.
    review_status: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

    @field_validator("classification", mode="before")
    @classmethod
    def _parse_classification(cls, v):
        return _json.loads(v) if isinstance(v, str) else v

    @field_validator("tools_used", "flow_steps", mode="before")
    @classmethod
    def _parse_json_list(cls, v):
        return _json.loads(v) if isinstance(v, str) else v


class ReviewStatusUpdate(BaseModel):
    status: Literal["new", "in_review", "processed"]


class RatingUpdate(BaseModel):
    # 1 = like, -1 = dislike, null = remove the rating.
    rating: Literal[1, -1] | None


# ---------------------------------------------------------------------------
# vector_store (Qdrant)
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Natural-language query to embed and search")
    legal_year: int | None = Field(
        None, description="Filter by legal year; omit to search all years"
    )
    language: str | None = Field(
        None, description="Filter by language ('bg' or 'en'); omit for both"
    )
    top_k: int = Field(
        10, ge=1, le=100, description="Number of results to return (after optional reranking)"
    )
    rerank: bool = Field(True, description="Apply cross-encoder reranking to the initial hits")


class SearchResultItem(BaseModel):
    chunk_id: str
    score: float
    text: str
    source_id: str
    source_type: str
    title: str
    url: str | None
    legal_year: int | None
    language: str


class CollectionInfo(BaseModel):
    name: str
    points_count: int
    vectors_count: int | None
    status: str
    config: dict


class CollectionSummary(BaseModel):
    """One Qdrant collection, for the Collections management list."""

    name: str
    points_count: int
    vector_size: int | None  # dense dim; None for multi/unnamed-vector configs
    distance: str | None
    status: str
    aliases: list[str] = []  # aliases pointing at this collection
    is_active: bool  # the collection the agent currently retrieves from
    is_default: bool  # matches the env `retrieval_collection_name`


class ActiveCollectionState(BaseModel):
    active: str  # effective collection (override or default)
    default: str  # env `retrieval_collection_name`
    is_override: bool  # True when an admin override is set


class ActiveCollectionUpdate(BaseModel):
    # None clears the override and reverts the agent to the env default.
    collection: str | None = None


class PointCreate(BaseModel):
    """Manually add a single point to the KB collection.

    `text` is embedded with the same bge-m3 service the pipeline uses; the
    payload keys mirror the ingestion pipeline so filtering and search keep
    working. A hand-created point has no `ingested_sources` row — it may be
    overwritten or orphaned if its `source_id` is later (re)ingested or dropped.
    """

    text: str = Field(..., min_length=1, description="Chunk text — embedded into the dense vector")
    source_id: str = Field(..., min_length=1, max_length=255)
    source_type: str = Field("document", max_length=64)
    title: str = Field("", max_length=512)
    legal_year: int | None = None
    language: Literal["bg", "en"] = "bg"
    url: str | None = Field(None, max_length=1024)
    id: str | None = Field(
        None, description="Explicit point id (int or UUID); a UUID is generated when omitted"
    )


class PointUpdate(BaseModel):
    """Patch a point. Only the keys present are changed.

    When `text` is present the point is re-embedded (its vector is replaced);
    otherwise only the payload is updated and the vector is left untouched.
    """

    text: str | None = Field(None, min_length=1)
    source_id: str | None = Field(None, min_length=1, max_length=255)
    source_type: str | None = Field(None, max_length=64)
    title: str | None = Field(None, max_length=512)
    legal_year: int | None = None
    language: str | None = None
    url: str | None = Field(None, max_length=1024)


class PointRead(BaseModel):
    id: str
    payload: dict


class PointsPage(BaseModel):
    items: list[PointRead]
    next_offset_id: str | None


class PointsFacets(BaseModel):
    """Distinct filterable payload values in the active collection (filter dropdowns)."""

    source_types: list[str]
    titles: list[str]


class PointsExport(BaseModel):
    """Whole-collection dump, round-trips with POST /points/import.

    Each item in `points` is a point's payload plus `_point_id` (its Qdrant id),
    matching the shape `import_points` expects.
    """

    collection: str
    exported_at: str
    point_count: int
    points: list[dict]


class BulkDeleteFilter(BaseModel):
    legal_year: int | None = None
    language: str | None = None
    source_type: str | None = None
    source_id: str | None = None


class DeleteResult(BaseModel):
    deleted: int


class PointsImportRequest(BaseModel):
    """Bulk import of points from an export dump.

    Each item in `points` is stored verbatim as the Qdrant payload (its `text`
    is embedded into the dense vector). `_point_id` becomes the point id; any
    `content_hash` is kept, otherwise computed from the text. The top-level
    `collection`/`exported_at`/`doc_id`/`point_count` fields are informational —
    points are imported into the *active* collection regardless.
    """

    collection: str | None = None
    points: list[dict] = Field(..., min_length=1)


class PointsImportResult(BaseModel):
    imported: int
    skipped: int  # items dropped for missing/empty text
    collection: str  # the collection imported into (the active one)
    source_collection: str | None = None  # the `collection` named in the dump


class SnapshotInfo(BaseModel):
    collection: str  # which KB collection this snapshot belongs to (main or `_parents`)
    name: str
    creation_time: str | None = None
    size: int | None = None
    checksum: str | None = None


class CollectionActionResult(BaseModel):
    collection: str
    status: str  # deleted | recovered | recovered_from_upload | snapshot_deleted
    detail: str | None = None


# ---------------------------------------------------------------------------
# redis
# ---------------------------------------------------------------------------


class RedisServerInfo(BaseModel):
    redis_version: str
    uptime_in_seconds: int
    connected_clients: int
    used_memory_human: str
    used_memory_peak_human: str
    total_commands_processed: int
    keyspace_hits: int
    keyspace_misses: int


class UserQuotaRead(BaseModel):
    user_id: str
    daily_tokens_used: int
    daily_tokens_limit: int
    daily_tokens_remaining: int
    token_enabled: bool = True
    token_period: str = "day"
    request_enabled: bool = False
    request_used: int = 0
    request_limit: int = 0
    request_remaining: int = 0
    request_period: str = "day"
    requests_this_minute: int
    requests_per_minute_limit: int


class TopicCacheStats(BaseModel):
    cached_entries: int


class TopicCacheEntry(BaseModel):
    key_suffix: str  # first 12 chars of the sha256 hash — enough to identify
    ttl_seconds: int  # -1 means no expiry (should not happen)
    is_relevant: bool
    is_illegal: bool
    needs_clarification: bool
    confidence: float
    detected_language: str
    reason: str


class ConversationSummaryEntry(BaseModel):
    """One rolling conversation summary cached under convsummary:{profile}:{session}."""

    profile_id: str
    session_id: str
    ttl_seconds: int  # -1 means no expiry (should not happen)
    covered_count: int  # number of older messages folded into the summary
    language: str
    updated_at: str | None
    summary_preview: str  # summary text, truncated for the list view


# Same shape as DeleteResult; kept as a name so the Redis routes read naturally.
FlushResult = DeleteResult

# ---------------------------------------------------------------------------
# debug audit (runtime toggle + per-interaction records)
# ---------------------------------------------------------------------------


class DebugConfigRead(BaseModel):
    enabled: bool  # the effective runtime toggle
    env_default: bool  # the env baseline (agent_debug_audit_enabled)
    # Turn-evidence capture — the second, much lighter capture toggle. Debug audit
    # records every prompt (off by default); this records only what the assistant
    # READ per turn, and the improvement loop's diagnosis quality depends on it, so
    # it is on by default. Grouped here because both answer "what do we retain".
    turn_evidence_enabled: bool = True
    turn_evidence_env_default: bool = True
    turn_evidence_max_chars: int = 0
    turn_evidence_retention_days: int = 0


class DebugConfigUpdate(BaseModel):
    """Both toggles optional — a PATCH updates only what it names."""

    enabled: bool | None = None
    turn_evidence_enabled: bool | None = None


class DebugTurnSummary(BaseModel):
    """One row per request (turn) for the Debug Console list."""

    turn_id: str
    started_at: datetime
    user_id: str | None
    # The user's account email (users.email), resolved from user_id. Shown in place
    # of the raw user_id; null for dev/system principals with no users row.
    user_email: str | None = None
    session_id: str | None
    interaction_count: int
    total_duration_ms: int
    error_count: int
    user_message: str | None = None
    # Set for turns that failed a guard *before* the debug scope opened (e.g. the
    # daily token-limit / quota rejection): they never wrote a debug_interactions
    # row, so the reason is derived from the audit_log event type. None for normal
    # turns (their failures, if any, are in the recorded interactions).
    error: str | None = None
    # The agent (profile) that triggered this flow, and its display name
    # (profiles.name). Null for rows written before profile stamping.
    profile_id: str | None = None
    profile_name: str | None = None


class DebugInteractionListItem(BaseModel):
    """Lightweight list row — metadata plus a short payload preview.

    Built from a SQL projection in the route, so request/response are truncated
    server-side rather than shipping the full untruncated TEXT columns.
    """

    id: int
    occurred_at: datetime
    turn_id: str | None
    user_id: str | None
    session_id: str | None
    kind: str
    name: str
    seq: int | None
    duration_ms: int | None
    error: str | None
    request_preview: str | None
    response_preview: str | None


class DebugInteractionRead(BaseModel):
    """Full record, including the untruncated request/response payloads."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    occurred_at: datetime
    turn_id: str | None
    user_id: str | None
    session_id: str | None
    kind: str
    name: str
    seq: int | None
    request: Any = None
    response: Any = None
    duration_ms: int | None = None
    error: str | None = None

    @field_validator("request", "response", mode="before")
    @classmethod
    def _parse_json(cls, v):
        if isinstance(v, str):
            try:
                return _json.loads(v)
            except (ValueError, TypeError):
                return v
        return v


# ----------------------------------------------------------------------------
# Agent phase prompts (runtime-editable; superuser to mutate, admin to read)
# ----------------------------------------------------------------------------
class PromptRead(BaseModel):
    """One editable agent phase prompt: metadata + code default + effective text."""

    model_config = ConfigDict(from_attributes=True)

    key: str
    phase: str
    language: str
    label: str
    description: str
    render: str
    required_tokens: list[str]
    default_text: str
    content: str  # effective text (override if set & valid, else the default)
    is_overridden: bool
    # Owned by the agent's published Agent-editor version: `content` is the
    # version's text and the prompt is read-only here.
    managed: bool = False
    updated_by: str | None = None
    updated_at: str | None = None


class PromptUpdate(BaseModel):
    """Set a prompt override. Server-side validation rejects content that drops a
    required placeholder or would fail to render (→ 422)."""

    content: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Semantic response cache (runtime config + stats)
# ---------------------------------------------------------------------------


class ResponseCacheValues(BaseModel):
    enabled: bool
    similarity_threshold: float  # cosine in [0, 1]; higher = safer / fewer hits
    ttl_seconds: int


class ResponseCacheStats(BaseModel):
    entries: int
    hits: int
    misses: int
    hit_rate: float


class ResponseCacheConfigRead(BaseModel):
    effective: ResponseCacheValues
    env_default: ResponseCacheValues
    stats: ResponseCacheStats
    managed_fields: list[str] = []


class ResponseCacheConfigUpdate(BaseModel):
    """All fields optional — only provided fields are updated."""

    enabled: bool | None = None
    similarity_threshold: float | None = Field(None, ge=0.0, le=1.0)
    ttl_seconds: int | None = Field(None, ge=0)


# ---------------------------------------------------------------------------
# kb_candidates  (Phase 2 — synthesised KB candidates awaiting human review)
# ---------------------------------------------------------------------------


def _as_list(v):
    """Coerce a JSON-text column (or list/None) into a list for the API."""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            parsed = _json.loads(v)
            return parsed if isinstance(parsed, list) else [v]
        except (ValueError, TypeError):
            return [v] if v.strip() else []
    return []


class KBCandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    content_hash: str
    run_id: int | None = None
    source_turn_ids: list[str] = Field(default_factory=list)
    topic: str
    claim: str
    markdown: str | None = None
    grounding_citations: list[str] = Field(default_factory=list)
    grounding_status: str
    # Why the grounding verdict came out that way. `contradicted` means the cited
    # passage says something incompatible — the reviewer needs the quoted span.
    entailment: str | None = None
    entailment_note: str | None = None
    currency_basis: str
    models_used: list[str] = Field(default_factory=list)
    synthesis_confidence: float
    novelty: str
    failure_mode: str
    stakes: str
    status: str
    needs_human_review: bool
    reviewer: str | None = None
    review_notes: str | None = None
    curated_source_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("source_turn_ids", "grounding_citations", "models_used", mode="before")
    @classmethod
    def _v_lists(cls, v):
        return _as_list(v)


class KBCandidateUpdate(BaseModel):
    """Reviewer edits before approval. All fields optional."""

    topic: str | None = Field(None, max_length=512)
    claim: str | None = None
    markdown: str | None = None
    grounding_citations: list[str] | None = None
    currency_basis: str | None = Field(None, max_length=16)  # ISO 4217 code or "n/a"
    stakes: Literal["low", "high"] | None = None
    # Only a manual revert to pending is meaningful via PATCH; approval/rejection
    # and the actual KB write go through the dedicated /approve and /reject routes
    # (which also drive the inserted/rejected terminal states).
    status: Literal["pending"] | None = None
    review_notes: str | None = None


class KBCandidateReject(BaseModel):
    review_notes: str | None = None


class KBCandidateCreate(BaseModel):
    """Hand-create a candidate from a finding the loop routed elsewhere.

    Lands in the review queue as `pending` — approval (the only path that writes
    the curated tier) is still a separate, deliberate action.
    """

    topic: str = Field(..., min_length=1, max_length=512)
    claim: str = Field(..., min_length=1)
    markdown: str | None = None
    grounding_citations: list[str] = Field(default_factory=list)
    stakes: Literal["low", "high"] = "low"
    review_notes: str | None = None
    source_turn_id: str | None = Field(None, max_length=64)  # provenance
    profile_id: str | None = Field(None, max_length=64)


class GoldenTestCreate(BaseModel):
    """Hand-add a regression case (upserts on the turn, like the loop's own)."""

    source_turn_id: str = Field(..., min_length=1, max_length=64)
    question: str = Field(..., min_length=1)
    expected: str | None = None
    # What the eval asserts — without these a hand-added case can never pass.
    expected_facts: list[str] = Field(default_factory=list)
    forbidden_facts: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    kind: Literal["good_answer", "fixed_error"] = "good_answer"
    legal_year: int | None = None
    profile_id: str | None = Field(None, max_length=64)


class GoldenTestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int | None = None
    source_turn_id: str
    question: str
    expected: str | None = None
    # What the eval actually asserts. `expected` is reference prose and — for a
    # good_answer — the REDACTED transcript, so a substring check on it can never
    # match the figures that matter.
    expected_facts: list[str] = Field(default_factory=list)
    forbidden_facts: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    kind: str
    legal_year: int | None = None
    # Set once a reviewer hand-edits the expectation; the loop then stops
    # overwriting this row on its nightly run.
    edited_by: str | None = None
    created_at: datetime

    @field_validator("citations", "expected_facts", "forbidden_facts", mode="before")
    @classmethod
    def _v_cites(cls, v):
        return _as_list(v)


# ---------------------------------------------------------------------------
# improvement loop — runtime config, runs, run requests (Review-process panel)
# ---------------------------------------------------------------------------


class LoopFindingRead(BaseModel):
    """One analysed turn (loop_findings) + the original question/answer text.

    Surfaces non-content-gap findings (prompt_fix / retrieval) that the review
    queue doesn't — so a wrong answer is still actionable even when its fix is a
    prompt/directive change or retrieval tuning rather than new KB content."""

    id: int
    run_id: int | None = None
    turn_id: str
    # The agent this finding belongs to. Load-bearing for the UI's remediation
    # actions (directive / golden test / KB candidate): they must target the
    # finding's OWN profile, not the admin's ambient scope selector, which is
    # blank in global mode and would silently write to the default profile.
    profile_id: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    assessment: str | None = None
    failure_mode: str | None = None
    route: str | None = None
    novelty: str | None = None
    synthesis_confidence: float | None = None
    evidence: str | None = None
    created_at: datetime
    question: str | None = None  # joined from audit_log.user_message
    answer: str | None = None  # joined from audit_log.response
    # Per-stage detail (parsed from the JSON-text columns) for the detail view.
    triage: dict | None = None  # {mode, analyze, reason}
    analysis_a: dict | None = None  # TurnAnalysis (None ⇔ model failed / pre-feature row)
    analysis_b: dict | None = None  # TurnAnalysis (None ⇔ model failed / pre-feature row)
    judge: dict | None = None  # JudgedCandidate (agreement / conflict_note)
    # Did the analysis read the sources, or judge from the transcript alone?
    # `degraded` findings never auto-insert and are bucketed separately in the
    # dismissal-rate stats, so the value of the evidence pipeline is measurable.
    evidence_completeness: str = "degraded"
    # The fix artifacts: corrected answer, checkable/forbidden facts, and any
    # proposed directive rule. `directive_proposal.rule` is what the "Create
    # directive" action pre-fills — an imperative instruction, not the diagnosis.
    remediation: dict | None = None
    remediation_kind: str | None = None  # answer_fix | directive | ingest | none
    # Linked KB candidate (content-gap turns awaiting human confirmation).
    # Matched by turn_id ∈ kb_candidates.source_turn_ids.
    candidate_id: int | None = None
    candidate_status: str | None = None  # pending|inserted|rejected|superseded
    needs_confirmation: bool = False  # True ⇔ a candidate with status=pending exists
    # Reviewer verdict on the FINDING itself. NULL = not yet reviewed. Drives both
    # the dismissal-rate calibration and the exclusion of dismissed turns from
    # future runs.
    disposition: str | None = None  # useful|dismissed|duplicate|fixed
    disposition_reason: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    # The recurring problem this finding was grouped into (loop_clusters.id), so a
    # reviewer can drill from a cluster to its members and back.
    cluster_id: int | None = None


class LoopFixCheckRead(BaseModel):
    """One re-ask of a golden test through the live agent (loop_fix_checks)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    profile_id: str | None = None
    golden_test_id: int
    run_id: int | None = None
    checked_at: datetime
    passed: bool
    answer: str | None = None
    missing_facts: list[str] = Field(default_factory=list)
    forbidden_hits: list[str] = Field(default_factory=list)
    error: str | None = None
    # Joined from the golden test, so the list reads without a second fetch.
    question: str | None = None
    kind: str | None = None
    source_turn_id: str | None = None


class LoopQueueStatus(BaseModel):
    """Is the worker draining the queue? The UI shows a banner when it is not."""

    requested: int = 0
    oldest_requested_age_seconds: int | None = None
    running: int = 0
    running_since: datetime | None = None
    # A request has been `running` longer than `loop_run_stale_hours` — the worker
    # most likely died mid-run (the worker reaps these on its next boot).
    stale: bool = False
    # A request has waited longer than a few poll intervals without being claimed.
    waiting_too_long: bool = False


class LoopClusterRead(BaseModel):
    """A recurring problem — the unit a reviewer actually acts on.

    The row IS the ticket: `status` moves open → ticketed → resolved/dismissed, so
    a retrieval cluster naming a missing source is worked from here rather than
    from a separate queue.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    profile_id: str | None = None
    route: str
    title: str
    summary: str | None = None
    recommended_action: str | None = None
    finding_count: int = 0
    first_run_id: int | None = None
    last_run_id: int | None = None
    status: str
    ticket_source_id: str | None = None
    reviewer: str | None = None
    review_notes: str | None = None
    created_at: datetime
    updated_at: datetime


class LoopClusterUpdate(BaseModel):
    """Reviewer actions on a cluster. All fields optional."""

    status: Literal["open", "ticketed", "resolved", "dismissed"] | None = None
    review_notes: str | None = Field(None, max_length=4000)
    # Set when the recommended ingestion has been performed, linking the cluster to
    # the source that closed it.
    ticket_source_id: str | None = Field(None, max_length=255)


class LoopFindingDispositionUpdate(BaseModel):
    """A reviewer's verdict on a finding (not on the answer the finding analysed)."""

    disposition: Literal["useful", "dismissed", "duplicate", "fixed"] | None = None
    disposition_reason: str | None = Field(None, max_length=4000)


class LoopFindingStatRow(BaseModel):
    """One (failure_mode × evidence_completeness) precision bucket."""

    failure_mode: str | None = None
    evidence_completeness: str | None = None
    total: int = 0
    reviewed: int = 0
    dismissed: int = 0
    useful: int = 0
    dismissal_rate: float | None = None  # None ⇔ nothing reviewed in this bucket yet


class LoopFindingStats(BaseModel):
    """Calibration for the loop itself: how often reviewers reject its findings.

    The headline number the Review-process panel needs — a dismissal rate that falls
    after an analysis change is the evidence the change actually helped.
    """

    total: int = 0
    reviewed: int = 0
    dismissed: int = 0
    dismissal_rate: float | None = None
    by_bucket: list[LoopFindingStatRow] = Field(default_factory=list)


class LoopConfigValues(BaseModel):
    """Mirror of app/services/loop_runtime_config.py LoopConfig."""

    schedule_enabled: bool
    schedule_hour: int
    schedule_minute: int
    lookback_hours: int
    max_conversations: int
    concurrency: int
    triage_mode: str
    triage_model: str
    triage_max_tokens: int
    analysis_mode: str
    analysis_model_a: str
    analysis_a_max_tokens: int
    analysis_model_b: str
    analysis_b_max_tokens: int
    judge_model: str
    judge_max_tokens: int
    remediation_model: str
    remediation_max_tokens: int
    thinking_budget_tokens: int
    evidence_rejected_max: int
    evidence_rejected_chars: int
    evidence_max_context_chars: int
    novelty_covered_threshold: float
    novelty_gap_threshold: float
    grounding_threshold: float
    entailment_required: bool
    cluster_enabled: bool
    cluster_threshold: float
    verification_enabled: bool
    verification_max_cases: int
    auto_insert_enabled: bool
    auto_insert_min_confidence: float
    curate_web_search_gaps: bool
    meta_retire_enabled: bool
    # LLM template per stage ("" → the legacy model/max-tokens fields above).
    triage_template: str = ""
    analysis_a_template: str = ""
    analysis_b_template: str = ""
    judge_template: str = ""
    remediation_template: str = ""


class LoopRunSummary(BaseModel):
    id: int
    run_date: str
    status: str
    dry_run: bool
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    stats: dict = Field(default_factory=dict)


class LoopRunDetail(LoopRunSummary):
    report_md: str | None = None


class LoopRunRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    # The request's own agent. Without it the poll endpoint can't tell the UI which
    # profile a single-turn review belongs to, so the follow-up finding fetch has to
    # guess (and in global scope guesses `default`).
    profile_id: str | None = None
    status: str
    trigger: str
    dry_run: bool
    force: bool = False
    run_date: str | None = None
    requested_by: str | None = None
    requested_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    run_id: int | None = None
    error: str | None = None
    turn_id: str | None = None  # set → this request is a single-turn review
    include_dismissed: bool = False  # re-analyse turns a reviewer dismissed
    # An admin asked a running request to stop; it stays `running` until the
    # worker reaches its next safe point and finishes it as `cancelled`.
    cancel_requested: bool = False


class LoopPanelRead(BaseModel):
    """What the configured model panel will ACTUALLY do on the next run.

    Lets the panel warn before a run rather than after: `degraded_reason` is
    non-empty exactly when `dual` was asked for but cannot be delivered, and
    `judge_independent` is False when the judge shares an analyst's model and so
    cannot act as a check on its blind spots.
    """

    mode_requested: str
    mode_effective: str
    degraded_reason: str = ""
    judge_independent: bool = True
    models: dict[str, str] = Field(default_factory=dict)


class LoopConfigRead(BaseModel):
    effective: LoopConfigValues
    env_default: LoopConfigValues
    available_models: list[str] = Field(default_factory=list)
    recent_runs: list[LoopRunSummary] = Field(default_factory=list)
    recent_requests: list[LoopRunRequestRead] = Field(default_factory=list)
    panel: LoopPanelRead | None = None
    # {stage: LLMCallParams dict} — what each review stage will send.
    stage_params: dict[str, dict] = Field(default_factory=dict)


class LoopConfigUpdate(BaseModel):
    """All fields optional — only provided fields are updated."""

    schedule_enabled: bool | None = None
    schedule_hour: int | None = Field(None, ge=0, le=23)
    schedule_minute: int | None = Field(None, ge=0, le=59)
    lookback_hours: int | None = Field(None, ge=1, le=168)
    max_conversations: int | None = Field(None, ge=0, le=10000)
    concurrency: int | None = Field(None, ge=1, le=32)
    triage_mode: Literal["model", "all", "feedback_only"] | None = None
    triage_model: str | None = None
    triage_max_tokens: int | None = Field(None, ge=1, le=32000)
    analysis_mode: Literal["dual", "single"] | None = None
    analysis_model_a: str | None = None
    analysis_a_max_tokens: int | None = Field(None, ge=1, le=32000)
    analysis_model_b: str | None = None
    analysis_b_max_tokens: int | None = Field(None, ge=1, le=32000)
    judge_model: str | None = None
    judge_max_tokens: int | None = Field(None, ge=1, le=32000)
    remediation_model: str | None = None
    remediation_max_tokens: int | None = Field(None, ge=1, le=32000)
    thinking_budget_tokens: int | None = Field(None, ge=0, le=32000)
    evidence_rejected_max: int | None = Field(None, ge=0, le=50)
    evidence_rejected_chars: int | None = Field(None, ge=0, le=4000)
    evidence_max_context_chars: int | None = Field(None, ge=0, le=200000)
    novelty_covered_threshold: float | None = Field(None, ge=0.0, le=1.0)
    novelty_gap_threshold: float | None = Field(None, ge=0.0, le=1.0)
    grounding_threshold: float | None = Field(None, ge=0.0, le=1.0)
    entailment_required: bool | None = None
    cluster_enabled: bool | None = None
    cluster_threshold: float | None = Field(None, ge=0.0, le=1.0)
    verification_enabled: bool | None = None
    verification_max_cases: int | None = Field(None, ge=1, le=500)
    auto_insert_enabled: bool | None = None
    auto_insert_min_confidence: float | None = Field(None, ge=0.0, le=1.0)
    curate_web_search_gaps: bool | None = None
    meta_retire_enabled: bool | None = None
    # LLM template id per stage; "" → back to the legacy fields.
    triage_template: str | None = None
    analysis_a_template: str | None = None
    analysis_b_template: str | None = None
    judge_template: str | None = None
    remediation_template: str | None = None


class LoopRunTrigger(BaseModel):
    """POST /loop-config/run body — enqueue a manual run."""

    dry_run: bool = False
    force: bool = False  # re-analyse turns even if already analysed (ignore idempotency)
    run_date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    profile_id: str | None = Field(None, max_length=64)  # None → default profile
    # Set → analyse exactly this turn instead of a time window (triage skipped,
    # force implied). Backs "Review this turn" in the Conversations view.
    turn_id: str | None = Field(None, max_length=64)
    # Re-analyse turns a reviewer dismissed. Off by default — `force` alone must not
    # resurrect findings the reviewer already rejected.
    include_dismissed: bool = False


# ---------------------------------------------------------------------------
# Crawler visits — search-engine / AI / SEO / social bot traffic
# ---------------------------------------------------------------------------


class CrawlerVisitRead(BaseModel):
    """One recorded crawler request."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    occurred_at: datetime
    site: str
    bot: str
    category: str
    # "verified" | "unverified" | "unknown". "unknown" means the operator
    # publishes no reverse-DNS contract — only "unverified" is a spoof signal.
    verified: str
    ip: str | None = None
    user_agent: str | None = None
    method: str
    path: str
    route: str | None = None
    status: int


class CrawlerVisitIn(BaseModel):
    """One row as posted by the frontend sink."""

    occurred_at: datetime | None = None  # server stamps it when absent
    site: str = Field("pacod", max_length=16)
    bot: str = Field(..., max_length=64)
    category: str = Field(..., max_length=16)
    verified: str = Field("unknown", max_length=16)
    ip: str | None = Field(None, max_length=45)
    user_agent: str | None = Field(None, max_length=512)
    method: str = Field("GET", max_length=8)
    path: str
    route: str | None = Field(None, max_length=128)
    status: int = 200


class CrawlerVisitIngest(BaseModel):
    """POST /crawler-visits/ingest body — one flushed batch from the frontend.

    Capped so a misbehaving or spoofed caller cannot post an unbounded batch;
    the frontend flushes at 100 rows.
    """

    visits: list[CrawlerVisitIn] = Field(..., max_length=500)


class CrawlerBotStat(BaseModel):
    """Per-bot rollup for the admin summary cards."""

    bot: str
    category: str
    hits: int
    errors: int  # status >= 400
    verified_hits: int
    unverified_hits: int
    last_seen: datetime | None = None


class CrawlerPathStat(BaseModel):
    path: str
    hits: int
    errors: int


class CrawlerCategoryStat(BaseModel):
    category: str
    hits: int


class CrawlerVisitStats(BaseModel):
    """GET /crawler-visits/stats — everything the dashboard cards need."""

    window_hours: int
    total_hits: int
    total_errors: int
    unverified_hits: int
    by_bot: list[CrawlerBotStat]
    by_category: list[CrawlerCategoryStat]
    top_paths: list[CrawlerPathStat]


# ---------------------------------------------------------------------------
# Usage analytics (requests + LLM usage per site / profile / user)
# ---------------------------------------------------------------------------

# Sites a frontend may post rows for. The other sites (internal, unknown) are api-side
# only, so they are not accepted on ingest.
AnalyticsIngestSite = Literal["pacod"]


class WebRequestIn(BaseModel):
    """One page request as posted by a frontend sink (kind is always 'page')."""

    occurred_at: datetime | None = None
    site: AnalyticsIngestSite
    user_id: str | None = Field(None, max_length=64)
    user_type: Literal["registered", "anonymous"]
    method: str = Field("GET", max_length=8)
    route: str = Field(..., max_length=256)
    status: int = Field(200, ge=100, le=599)
    duration_ms: int = Field(0, ge=0, le=3_600_000)


class WebRequestIngest(BaseModel):
    rows: list[WebRequestIn] = Field(..., max_length=500)


_UUID_RE = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


class PageViewIn(BaseModel):
    occurred_at: datetime | None = None
    pageview_id: str = Field(..., pattern=_UUID_RE)
    site: AnalyticsIngestSite
    user_id: str | None = Field(None, max_length=64)
    route: str = Field(..., max_length=256)
    locale: str | None = Field(None, max_length=8)
    # Server-side clamp too (the frontend endpoint already clamps): 0..30 min.
    dwell_ms: int = Field(0, ge=0)
    is_start: bool = False
    referrer_host: str | None = Field(None, max_length=128)


class PageViewIngest(BaseModel):
    rows: list[PageViewIn] = Field(..., max_length=500)


class AnalyticsMetrics(BaseModel):
    page_requests: int = 0
    api_requests: int = 0
    turns: int = 0
    rejections: int = 0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    llm_errors: int = 0
    page_views: int = 0
    dwell_ms: int = 0


class AnalyticsTotals(AnalyticsMetrics):
    unique_users: int = 0


class AnalyticsSeriesPoint(AnalyticsMetrics):
    bucket: str


class AnalyticsSiteRow(AnalyticsMetrics):
    site: str


class AnalyticsSiteProfileRow(AnalyticsMetrics):
    site: str
    profile_id: str


class AnalyticsModelRow(BaseModel):
    model: str
    llm_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    llm_errors: int
    error_rate: float


class AnalyticsOverview(BaseModel):
    since: datetime
    totals: AnalyticsTotals
    errors_by_type: dict[str, int]
    series: list[AnalyticsSeriesPoint]
    by_site: list[AnalyticsSiteRow]
    by_site_profile: list[AnalyticsSiteProfileRow]
    by_model: list[AnalyticsModelRow]
    # LLM figures are the gateway's: set when its reports could not be read (they are 0).
    gateway_error: str | None = None
    # The same view in the gateway console (for a user: their end-user page), and the
    # console's Errors page for the same filters (the provider/gateway side of failures;
    # llm_errors here are the app's own count).
    console_url: str | None = None
    errors_console_url: str | None = None


class AnalyticsUserRow(AnalyticsMetrics):
    user_id: str
    email: str | None = None
    display_name: str | None = None
    tokens: int = 0
    last_seen: datetime | None = None


class AnalyticsUserList(BaseModel):
    total: int
    items: list[AnalyticsUserRow]
    # The users' LLM figures are the gateway's: set when it could not be read.
    gateway_error: str | None = None


class AnalyticsUserInfo(BaseModel):
    user_id: str
    email: str | None = None
    display_name: str | None = None
    created_at: datetime | None = None
    last_login_at: datetime | None = None
    is_active: bool | None = None
    roles: list[str] = Field(default_factory=list)
    last_seen: datetime | None = None


class AnalyticsTopPage(BaseModel):
    route: str
    views: int
    dwell_ms: int
    avg_dwell_ms: int


class AnalyticsTopApiRoute(BaseModel):
    route: str
    requests: int
    avg_duration_ms: int
    errors: int


class AnalyticsErrorRow(BaseModel):
    occurred_at: datetime
    site: str | None = None
    profile_id: str | None = None
    model: str
    purpose: str
    error_type: str
    status_code: int | None = None
    message: str | None = None


class AnalyticsDevice(BaseModel):
    id: str
    name: str
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked: bool = False


class AnalyticsUserDetail(AnalyticsOverview):
    user: AnalyticsUserInfo
    top_pages: list[AnalyticsTopPage]
    top_api_routes: list[AnalyticsTopApiRoute]
    recent_errors: list[AnalyticsErrorRow]
    devices: list[AnalyticsDevice]


class UserIpAddressRead(BaseModel):
    ip: str
    use_count: int
    first_seen_at: datetime | None = None
    last_used_at: datetime | None = None


class UserIpAddressList(BaseModel):
    items: list[UserIpAddressRead]


class UserActivityRead(BaseModel):
    id: int
    occurred_at: datetime
    action: str
    ip: str | None = None
    site: str | None = None
    profile_id: str | None = None
    profile_name: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    detail: dict[str, Any] | None = None


class UserActivityList(BaseModel):
    total: int
    retention_months: int
    items: list[UserActivityRead]
