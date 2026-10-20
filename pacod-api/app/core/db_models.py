"""SQLAlchemy ORM models.

Tables: users (the shadow of pacod-id accounts), reference_facts
(structured deterministic data, queried via MCP), usage_events (per-LLM-call
accounting for billing and audit), and audit_log (every guarded request,
including refusals).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(UTC)


# Multi-profile platform constants. Every profile-scoped row carries a
# ``profile_id``. ``DEFAULT_PROFILE_ID`` is the profile an admin resolves to when
# no profile is named. Its id follows the same convention as every other agent:
# id == slug == one lowercase word.
# ``GLOBAL_PROFILE_ID`` is the sentinel row-space in ``app_settings`` for
# genuinely platform-wide settings (the debug-audit / turn-evidence / retrieval-trace
# toggles, quota enforcement and settings, the editor assistant, the newsletter's public
# profile, the workers' day guards) that are not per-profile.
DEFAULT_PROFILE_ID = "starter"
GLOBAL_PROFILE_ID = "__global__"


class Base(DeclarativeBase):
    pass


class Profile(Base):
    """A dedicated agent within the multi-profile platform.

    Each profile is a self-contained agent with its own knowledge base
    (Qdrant collections), reference data, prompts, directives, runtime config,
    response cache, conversations, debug logs, usage, and reviews. The
    ``base_collection`` / ``curated_collection`` names are stored (not derived)
    so a row can keep pointing at whatever physical collection currently holds
    its data; new profiles follow ``profiles.collection_names_for_slug``.
    """

    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    # Per-language short display labels shown in the sidebar / top navigation.
    # One column per supported UI locale (mirrors the topic_description_bg/en
    # convention). NULL = fall back to the English short name, then ``name``.
    short_name_en: Mapped[str | None] = mapped_column(String(64), nullable=True)
    short_name_bg: Mapped[str | None] = mapped_column(String(64), nullable=True)
    short_name_de: Mapped[str | None] = mapped_column(String(64), nullable=True)
    short_name_ru: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Topic gating (used by the topic guard / classifier) — per profile.
    topic_name: Mapped[str] = mapped_column(String(64), default="general")
    topic_description_bg: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic_description_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    legal_year: Mapped[int] = mapped_column(Integer, default=2026)
    # CSV of language codes this profile answers in, e.g. "bg,en".
    languages: Mapped[str] = mapped_column(String(64), default="bg,en")
    # Stored Qdrant collection names (main + curated tier). The ``_parents``
    # companion is derived as f"{base_collection}_parents".
    base_collection: Mapped[str] = mapped_column(String(128))
    curated_collection: Mapped[str] = mapped_column(String(128))
    # Which front-end this agent is driven by. ``chat`` — the conversational RAG
    # agent surfaced in the chat sidebar — is the only kind today; the column is
    # the discriminator a future agent UI would add a value to.
    ui_kind: Mapped[str] = mapped_column(
        String(16), default="chat", server_default=text("'chat'"), index=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), index=True
    )
    # Agent-editor release state (see app/agent_editor). ``prod`` = visible to
    # assigned users (every pre-existing profile is backfilled to it); ``dev`` =
    # admins only (an editor-created agent before its first publish);
    # ``archived`` = retired. ``published_version_id`` NULL means the agent is
    # not editor-managed (legacy: config comes from app_settings alone);
    # ``dev_version_id`` is the admin-only dev snapshot (agent_versions.id).
    release_state: Mapped[str] = mapped_column(
        String(16), default="prod", server_default=text("'prod'"), index=True
    )
    published_version_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dev_version_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow
    )


class AgentDefinition(Base):
    """The editable draft of an agent built in the visual agent editor.

    One row per profile (an agent IS a profile). ``draft_graph`` is the node/edge
    graph JSON (stored as TEXT — same convention as the other JSON columns here,
    avoiding asyncpg JSONB codec issues). ``draft_revision`` is bumped on every
    save for optimistic concurrency between two editor tabs."""

    __tablename__ = "agent_definitions"

    profile_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    template: Mapped[str] = mapped_column(String(32))
    draft_graph: Mapped[str] = mapped_column(Text)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    draft_revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow
    )


class AgentVersion(Base):
    """An immutable snapshot of an agent graph. ``kind`` is ``release`` (a prod
    publish, eligible for rollback) or ``dev_snapshot`` (frozen by "deploy to
    dev"). Rows are insert-only — never UPDATE one (retired models are remapped
    at resolve time, not by rewriting history)."""

    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("profile_id", "version_no", name="uq_agent_version_no"),
        Index("ix_agent_versions_profile_kind", "profile_id", "kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(String(64), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    graph: Mapped[str] = mapped_column(Text)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    source_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )


class AgentDeployment(Base):
    """Append-only history of agent-editor pointer moves (deploy_dev / publish /
    rollback / unpublish / archive / adopt)."""

    __tablename__ = "agent_deployments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(16))
    variant: Mapped[str] = mapped_column(String(8))
    version_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )


class UserProfile(Base):
    """Assignment of a profile (agent) to a user (many-to-many).

    A user sees and may chat with exactly the profiles assigned here
    (admins/superusers bypass the membership check). Cascade-deletes with the
    user; the profile side is a plain indexed column (no FK) so a profile delete
    is handled explicitly by the admin route.
    """

    __tablename__ = "user_profiles"
    __table_args__ = (UniqueConstraint("user_id", "profile_id", name="uq_user_profile"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    profile_id: Mapped[str] = mapped_column(String(64), index=True)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # pacod-id's address for the account (``id`` is its ``sub``); not unique here — pacod-id
    # keeps addresses unique, and a stale local row must not lock a person out.
    email: Mapped[str] = mapped_column(String(255), index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Per-user quota overrides. quota_disabled exempts the user from all quota
    # enforcement; daily_token_limit overrides the token-quota limit and
    # request_limit overrides the request-quota limit (NULL = use the global
    # default; the validity period stays global). See app/services/quota_config.py.
    quota_disabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    daily_token_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    # Stamped when one of the person's pacod-id sessions is first seen here (a sign-in).
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReferenceFact(Base):
    """One authoritative reference fact of an agent: a number, rate, amount,
    date or short text the agent must quote exactly rather than recall.

    The agent reads these only through the MCP tools ``get_fact`` / ``list_facts``
    (``mcp_server/server.py``), with ``profile_id`` and the turn's reference year
    injected programmatically — the planner can name a ``fact_key``, never the
    profile. ``value_type`` says how the value columns are read:

      number / percent / count  ``value_num`` (+ ``unit``)
      money                     ``value_num`` in ``unit`` (a currency code)
      range                     ``value_num`` .. ``value_max`` (+ ``unit``)
      date                      ``value_text`` (ISO date)
      annual_date               ``value_text`` ``MM-DD`` (recurs every year)
      monthly_day               ``value_num`` = day of the month (recurs monthly)
      text / boolean            ``value_text`` / ``value_num`` 1|0

    ``extra`` (JSON-as-TEXT) holds any further typed attributes a domain needs,
    e.g. the same amount in a second currency; it is returned to the agent as is.
    ``legal_year`` is the *reference year* (NULL = valid in every year). A key is
    unique per (profile, variant, year).
    """

    __tablename__ = "reference_facts"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "fact_key",
            "variant",
            "legal_year",
            name="uq_reference_fact",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(String(64), index=True)
    fact_key: Mapped[str] = mapped_column(String(128), index=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    variant: Mapped[str] = mapped_column(String(64), default="default", server_default="default")
    legal_year: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    value_type: Mapped[str] = mapped_column(String(16), default="number", server_default="number")
    value_num: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-as-TEXT object
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    label_bg: Mapped[str | None] = mapped_column(String(256), nullable=True)
    label_en: Mapped[str | None] = mapped_column(String(256), nullable=True)
    description_bg: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow
    )


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        # Analytics: per-site and per-user time ranges (admin Usage & Analytics).
        Index("ix_usage_events_site_occurred", "site", "occurred_at"),
        Index("ix_usage_events_user_occurred", "user_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    turn_id: Mapped[str] = mapped_column(String(64), index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )
    model: Mapped[str] = mapped_column(String(128))
    purpose: Mapped[str] = mapped_column(String(32))  # classifier | planner | synthesizer
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    # Which product/entry point the call came from — see ``app/core/request_context``
    # (pacod | internal | unknown; ``openwebui`` on rows written before Open-WebUI was
    # retired). NULL on rows written before usage analytics existed.
    site: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Number of LLM calls folded into this (purpose, model) row. NULL on legacy
    # rows and on web-search rows (not an LLM call); analytics counts NULL as 1.
    call_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        # Chat-history lookups: (user_id, session_id) within request_completed rows.
        Index(
            "ix_audit_log_user_session",
            "user_id",
            "session_id",
            "occurred_at",
            postgresql_where=text("event_type = 'request_completed'"),
        ),
        # Analytics: per-site time ranges.
        Index("ix_audit_log_site_occurred", "site", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )
    profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    event_type: Mapped[str] = mapped_column(
        String(64)
    )  # request_accepted | request_completed | rejected_* | etc.
    detail: Mapped[str] = mapped_column(Text)
    # Enriched turn fields — null on rejection events that fire before agent runs.
    turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    user_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Stored as JSON-serialised TEXT to avoid asyncpg codec issues.
    # classification: {is_relevant, confidence, reason, needs_clarification, detected_language, is_complex, thinking_used}
    classification: Mapped[str | None] = mapped_column(Text, nullable=True)
    # tools_used: JSON list of tool names, e.g. '["kb_search","get_fact"]'
    tools_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    # flow_steps: JSON list of LangGraph nodes, e.g. '["plan","execute_tools","synthesize"]'
    flow_steps: Mapped[str | None] = mapped_column(Text, nullable=True)
    # citations: JSON list of the Sources shown under the answer (Citation.model_dump
    # shape), so chat history can re-render them. Kept out of `detail`, which is
    # truncated at 8000 chars and would corrupt the token usage stored there.
    citations: Mapped[str | None] = mapped_column(Text, nullable=True)
    # End-to-end wall-clock latency from request arrival to final response, in milliseconds.
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Agent-editor variant that answered ("prod" / "dev"; NULL = before the
    # editor existed, i.e. prod) and its version. Dev turns are admin test chats:
    # the improvement loop's window runs skip them.
    agent_variant: Mapped[str | None] = mapped_column(String(8), nullable=True)
    agent_version_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Admin review workflow (unified Conversations view). NULL is treated as 'new'.
    review_status: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # new | in_review | processed
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Product/entry point of the request (see UsageEvent.site). NULL on legacy rows.
    site: Mapped[str | None] = mapped_column(String(16), nullable=True)


class DeletedChatSession(Base):
    """User-hidden chat sessions.

    Chat history is derived from append-only ``audit_log`` rows, so a user
    "deleting" a chat does not remove the audit trail (admins still review it via
    /conversations). Instead we record the hidden (user_id, session_id) pair here
    and filter it out of the user-facing history queries.
    """

    __tablename__ = "deleted_chat_sessions"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )


class IngestionSource(Base):
    __tablename__ = "ingested_sources"
    __table_args__ = (
        UniqueConstraint("profile_id", "source_id", name="uq_ingested_source_profile"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    source_id: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(512))
    source_type: Mapped[str] = mapped_column(String(64), index=True)
    legal_year: Mapped[int] = mapped_column(Integer, index=True)
    language: Mapped[str] = mapped_column(String(8))
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )
    uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="NEW", index=True)
    # Which epistemic tier this source was written into: "authoritative" (the profile's
    # base collection) or "curated" (the physically separate curated collection, which
    # retrieval down-weights). Lets the admin UI list curated knowledge without scanning Qdrant.
    tier: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    # Authored body for manually-typed sources (curated notes, promoted Q&A). Only set by
    # the /ingest/text path; file/URL sources keep it NULL and are reconstructed from Qdrant.
    content: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentDirective(Base):
    """Admin-curated high-priority instructions injected at the top of the
    synthesizer prompt. Interpreted before all other rules (subordinate only to
    the ABSOLUTE RESTRICTION on illegal activity)."""

    __tablename__ = "agent_directives"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(8), default="all", index=True)  # bg | en | all
    legal_year: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )  # NULL = any year
    priority: Mapped[int] = mapped_column(
        Integer, default=100, index=True
    )  # lower = injected first
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow
    )


class AppSetting(Base):
    """Key/value store for runtime-mutable application settings.

    Unlike env-based `Settings` (a cached singleton read once at startup), rows
    here are read per request (with a short Redis cache) so admins can change
    behaviour without a redeploy. Currently holds `active_collection` — the
    Qdrant collection the agent retrieves from; absence means "use the
    profile's base collection".
    """

    __tablename__ = "app_settings"

    # Composite PK: settings are scoped per profile. Genuinely platform-wide
    # settings use the GLOBAL_PROFILE_ID sentinel.
    profile_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=GLOBAL_PROFILE_ID)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow
    )


class ProviderSecret(Base):
    """Web-search API keys managed from the admin panel, Fernet-encrypted at rest.

    Only 'live' keys remain (Tavily/Google), read directly by the app on the next
    request. LLM provider keys are the gateway's (its credentials API).
    """

    __tablename__ = "provider_secrets"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    last4: Mapped[str | None] = mapped_column(String(8), nullable=True)
    target: Mapped[str] = mapped_column(String(16), nullable=False, default="live")
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow
    )


class LlmTemplate(Base):
    """A reusable, named set of LLM call parameters (model + every knob).

    Global (no ``profile_id``): each LLM role of a profile (agent roles,
    improvement-loop stages, the editor assistant) references a template by id
    through an ``app_settings`` ``*_template`` row, and a template edit reaches
    every role using it — the "live link". A blank ``*_template`` keeps the role on
    its legacy flat settings. See ``app/services/llm_templates.py`` /
    ``llm_params.py``.

    NULL optional knobs are not sent: ``temperature`` NULL omits the field,
    ``reasoning_effort`` NULL keeps the client default, ``timeout_seconds`` NULL
    uses the client's (or the improvement loop's) timeout. Parameters a model
    does not accept are stored but stripped from every request
    (``llm_capabilities``).
    """

    __tablename__ = "llm_templates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    thinking_budget_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reasoning_effort: Mapped[str | None] = mapped_column(String(8), nullable=True)
    top_p: Mapped[float | None] = mapped_column(Float, nullable=True)
    frequency_penalty: Mapped[float | None] = mapped_column(Float, nullable=True)
    presence_penalty: Mapped[float | None] = mapped_column(Float, nullable=True)
    timeout_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow
    )


class DebugInteraction(Base):
    """One row per LLM/RAG/tool/decision interaction within a chat turn.

    Written only when debug audit mode is enabled (env flag
    `agent_debug_audit_enabled` or the runtime `debug_audit_enabled` toggle).
    Correlates to `audit_log.turn_id`. Payloads are stored full and untruncated;
    rows hold raw prompts/responses, so the read API is admin-only.
    """

    __tablename__ = "debug_interactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )
    profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))  # llm | rag | tool | decision
    name: Mapped[str] = mapped_column(
        String(128)
    )  # planner | qdrant_search | get_fact | plan | ...
    seq: Mapped[int | None] = mapped_column(Integer, nullable=True)  # monotonic per-turn order
    request: Mapped[str | None] = mapped_column(Text, nullable=True)  # full JSON, untruncated
    response: Mapped[str | None] = mapped_column(Text, nullable=True)  # full JSON/text, untruncated
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class RetrievalFeedback(Base):
    __tablename__ = "retrieval_feedback"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    turn_id: Mapped[str] = mapped_column(String(64), index=True)
    query: Mapped[str] = mapped_column(Text)
    chunk_id: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[str] = mapped_column(String(255))
    relevance: Mapped[int] = mapped_column(Integer)  # 1=positive, -1=negative
    feedback_source: Mapped[str] = mapped_column(String(16), default="explicit")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )


class LoopRun(Base):
    """One run of the continuous-improvement loop (the daily triage engine).

    Phase 1 is read-only: a run reads conversations + feedback + retrieval traces,
    analyses them with multiple LLMs, and records findings — but writes nothing to
    the KB. `stats` is a JSON summary (counts by failure_mode, triaged in/out,
    candidates, errors) and `report_md` the human-readable report. See
    improvement_loop/.
    """

    __tablename__ = "loop_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    run_date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD (UTC)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="running"
    )  # running | done | failed | cancelled
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stats: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-as-TEXT
    report_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class LoopRunRequest(Base):
    """A queued request to execute the improvement loop (manual or scheduled).

    The admin "Run now" action and the worker's daily scheduler both insert rows
    here; the dedicated improvement-loop-worker claims them one at a time
    (serialised), runs `run_loop`, and records the resulting `run_id` + terminal
    status. Keeping a single queue means all runs go through one executor.
    """

    __tablename__ = "loop_run_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="requested", index=True
    )  # requested|running|done|failed|cancelled
    trigger: Mapped[str] = mapped_column(String(16), default="manual")  # manual|scheduled
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    force: Mapped[bool] = mapped_column(
        Boolean, default=False
    )  # re-analyse turns even if already analysed (ignore idempotency)
    run_date: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )  # YYYY-MM-DD or NULL (lookback window)
    requested_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    run_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )  # → loop_runs.id once started
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set → a single-turn review: analyse exactly this turn instead of the lookback
    # window, and skip triage. Requested from the admin Conversations view.
    turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Re-analyse turns a reviewer already dismissed. Off by default: a dismissal is
    # meant to stop a false positive recurring, including under a forced re-run.
    include_dismissed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # An admin asked a RUNNING request to stop. The worker polls this while it
    # executes and abandons the run at the next safe point (findings analysed so far
    # are kept); a still-queued request is cancelled outright instead.
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class LoopFinding(Base):
    """One analysed conversation turn — the durable output of a loop run.

    The reconciled (judged) assessment for a turn: its failure mode, the
    remediation channel it was routed to, the novelty verdict against the
    authoritative tier, and (for content gaps) the synthesised candidate. Unique
    on `turn_id` so a re-run upserts rather than duplicating (idempotency); the
    `content_hash` lets a run skip turns whose conversation is unchanged.
    """

    __tablename__ = "loop_findings"
    __table_args__ = (
        UniqueConstraint("profile_id", "turn_id", name="uq_loop_finding_profile_turn"),
        Index("ix_loop_findings_disposition", "profile_id", "disposition"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    run_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    turn_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    assessment: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # correct|partial|incorrect|unverifiable
    failure_mode: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    route: Mapped[str | None] = mapped_column(
        String(24), nullable=True
    )  # retrieval|prompt_fix|content_gap|golden|none
    novelty: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # gap|augment|covered|n/a
    synthesis_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    candidate: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # JSON-as-TEXT (knowledge nugget)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Per-stage detail for the Findings detail view (all JSON-as-TEXT).
    triage: Mapped[str | None] = mapped_column(Text, nullable=True)  # {mode, analyze, reason}
    analysis_a: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # TurnAnalysis (None ⇔ model failed)
    analysis_b: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # TurnAnalysis (None ⇔ model failed)
    judge: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # JudgedCandidate (agreement/conflict_note)
    # Whether the analysis actually saw the retrieved text + tool results, or had to
    # judge from the transcript alone. A `degraded` verdict is a guess: it never
    # auto-inserts, and the dismissal-rate stats bucket by it so the value of the
    # evidence pipeline is measurable rather than assumed.
    # The default is the SAFE value on purpose: a row that doesn't say otherwise was
    # judged without the evidence. `full` is only ever set explicitly, by a run that
    # actually had the context snapshot in hand.
    evidence_completeness: Mapped[str] = mapped_column(
        String(16), default="degraded", server_default="degraded"
    )
    # The fix artifacts (JSON): corrected answer, assertable facts, and any proposed
    # directive rule. This is what turns a finding from a label into something a
    # reviewer can act on in one click.
    remediation: Mapped[str | None] = mapped_column(Text, nullable=True)
    remediation_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Which recurring problem this finding belongs to. Written ONLY by cluster.py,
    # which runs after persist — `persist_finding` must never include it in its
    # upsert `set_`, or every re-run would null the previous assignment.
    cluster_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    # Reviewer verdict on the FINDING itself (not on the answer it analysed). NULL =
    # not yet reviewed. This is the loop's only calibration signal: a dismissal rate
    # that falls after an analysis change is the evidence the change helped, and a
    # `dismissed` finding is excluded from future runs so false positives stop
    # recurring nightly. Written only by the admin route — `persist_finding` must
    # never include these in its upsert `set_` (see the CASE guard there).
    disposition: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # useful|dismissed|duplicate|fixed
    disposition_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )


class LoopFixCheck(Base):
    """Did a fix actually work? One row per re-asked golden test.

    Everything else in the loop measures the PROBLEM. This is the only thing that
    measures the REMEDY: after a directive is activated, a candidate approved, or a
    source ingested, the original question is re-asked through the live agent and
    the expectation is asserted against the new answer.

    Without it a remediation is an opinion. With it, "this change helped" is a
    number a reviewer can point at — which is the difference between a loop that
    converges and one that drifts.
    """

    __tablename__ = "loop_fix_checks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    golden_test_id: Mapped[int] = mapped_column(BigInteger, index=True)
    run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )
    passed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    missing_facts: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    forbidden_hits: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class LoopCluster(Base):
    """A recurring problem, assembled from many findings.

    Findings are per-turn, and a per-turn list is not actionable: forty turns that
    all failed because one primary source was never ingested produce forty rows and
    zero decisions. A reviewer facing 200 individual findings does nothing.

    A cluster is the unit a human can act on — "this source is missing", "this rule
    is misfiring" — and its `status` doubles as the ticket: `open` → `ticketed`
    (someone is on it) → `resolved` (the meta-eval or a fix-check confirmed it) or
    `dismissed`. That is why there is no separate ticket queue: a fourth run-request
    table with no executor would be dead weight.

    `centroid` is the running mean embedding of its members, so tonight's findings
    join last night's cluster instead of forming a duplicate.
    """

    __tablename__ = "loop_clusters"
    __table_args__ = (Index("ix_loop_clusters_profile_status", "profile_id", "status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    route: Mapped[str] = mapped_column(String(24), index=True)  # retrieval|content_gap|prompt_fix|…
    cluster_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    title: Mapped[str] = mapped_column(String(512))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    centroid: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON float list
    finding_count: Mapped[int] = mapped_column(Integer, default=0)
    first_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    ticket_source_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String(64), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow
    )


class KBCandidate(Base):
    """A synthesised knowledge candidate awaiting human review (Phase 2).

    Produced by the loop from a content-gap finding that passed the grounding gate.
    Nothing is written to the curated KB tier until a human approves it (or, in
    Phase 3, the flag-gated auto-insert path clears the high-confidence/low-stakes
    bar). The row itself is the auditable, reversible provenance record; on approval
    its `markdown` is upserted into `kb_curated` and `curated_source_id` is stamped.
    Unique on `content_hash` so re-promotion upserts rather than duplicating.
    """

    __tablename__ = "kb_candidates"
    __table_args__ = (
        UniqueConstraint("profile_id", "content_hash", name="uq_kb_candidate_profile_hash"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    source_turn_ids: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    topic: Mapped[str] = mapped_column(String(512))
    claim: Mapped[str] = mapped_column(Text)
    markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    grounding_citations: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    grounding_status: Mapped[str] = mapped_column(
        String(24), default="ungrounded"
    )  # grounded|contradicted|needs_source|ungrounded
    # Whether the cited passages actually SAY the claim. A cosine hit proves topical
    # proximity only, so this is what distinguishes a claim the article supports from
    # one it contradicts. `contradicted` blocks auto-insert outright.
    entailment: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # supported|contradicted|not_stated|n/a
    entailment_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency_basis: Mapped[str] = mapped_column(String(16), default="n/a")
    models_used: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    synthesis_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    novelty: Mapped[str] = mapped_column(String(16), default="n/a")  # gap|augment|covered|n/a
    failure_mode: Mapped[str] = mapped_column(String(24), default="content_gap")
    stakes: Mapped[str] = mapped_column(String(8), default="low", index=True)  # low|high
    status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True
    )  # pending|approved|rejected|inserted|superseded
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=True)
    reviewer: Mapped[str | None] = mapped_column(String(64), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    curated_source_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow
    )


class GoldenTest(Base):
    """A regression case mined from production traffic by the loop.

    Both excellent answers and confirmed errors become golden cases (the
    highest-leverage byproduct of the loop). Consumed by `scripts/run_eval.py`.
    Unique on `source_turn_id` so re-runs upsert.
    """

    __tablename__ = "golden_tests"
    __table_args__ = (
        UniqueConstraint("profile_id", "source_turn_id", name="uq_golden_test_profile_turn"),
        Index("ix_golden_tests_kind", "kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    source_turn_id: Mapped[str] = mapped_column(String(64), index=True)
    question: Mapped[str] = mapped_column(Text)
    expected: Mapped[str | None] = mapped_column(Text, nullable=True)
    citations: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    # The assertable expectations. `expected` is reference prose — for a good_answer
    # the analysis copy of the answer (identifiers masked as `[EMAIL_1]` …, amounts
    # kept) — so a substring check on it would not pass. These two lists are what
    # `run_eval.py` actually asserts: short factual substrings that must appear / must
    # not appear.
    expected_facts: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    forbidden_facts: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    kind: Mapped[str] = mapped_column(String(16), default="good_answer")  # good_answer|fixed_error
    legal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Set once a reviewer edits the expectation by hand; `upsert_golden` then refuses
    # to overwrite the row, so a hand-tuned case survives the next nightly run.
    edited_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )


class TurnEvidence(Base):
    """What the assistant actually READ when it answered one turn.

    A sibling of `retrieval_traces`, not a column on `audit_log`: the admin
    audit-log list and the loop's collector both do whole-row `select(AuditLog)`,
    and a 20–50 KB context blob would be dragged through every one of those reads.

    `context` is `prepared["context"]` verbatim — the block `_build_context`
    assembles and hands to synthesis. That single string is the answer to three
    otherwise-unanswerable review questions: it is already parent-expanded (so it is
    what the model saw, not the child chunks the trace records), it carries the
    `### Reference facts — <tool>` RESULTS (the actual figure returned, which nothing
    else persists), and with the directives beside it a reviewer can tell an
    intentional admin override from a hallucination.

    Without this table the improvement loop classifies retrieval-vs-content_gap-vs-
    hallucination from source ids and scores alone — i.e. it guesses.

    Written best-effort when `turn_evidence_enabled` is on; pruned by the loop
    worker after `turn_evidence_retention_days`.
    """

    __tablename__ = "turn_evidence"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    turn_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    legal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)  # prepared["context"]
    directives: Mapped[str | None] = mapped_column(Text, nullable=True)  # in force AT ANSWER TIME
    tool_calls: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON [{name, args}]
    # The conversation the turn was answered IN, as the planner/synthesizer saw it:
    # JSON {summary, recent: [{role, content}]}. Without it a follow-up ("and for a
    # company?") is reviewed as a context-free fragment and judged nonsense.
    history: Mapped[str | None] = mapped_column(Text, nullable=True)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )


class RetrievalTrace(Base):
    """One row per kb_search call — the retrieval trace for a turn.

    Captures the dense ANN candidates with their dense + rerank scores and which
    chunks were actually passed to synthesis (`used`). This is the prerequisite the
    continuous-improvement loop relies on to distinguish a *retrieval failure*
    (the right content existed but wasn't retrieved) from a *content gap*. Written
    best-effort (never blocks a turn) when `retrieval_trace_enabled` is on. Lighter
    than `debug_interactions`: scores only, no raw prompts.
    """

    __tablename__ = "retrieval_traces"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )
    profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    query: Mapped[str] = mapped_column(Text)
    legal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    collection: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # JSON-as-TEXT list (avoids asyncpg JSONB codec issues, same convention as
    # audit_log): [{chunk_id, source_id, source_type, tier, dense_score,
    # rerank_score, used}]
    candidates: Mapped[str | None] = mapped_column(Text, nullable=True)


class ResponseFeedback(Base):
    """Response-level thumbs up/down on the whole answer (one per user+turn).

    Distinct from chunk-level `retrieval_feedback`: this captures the user's
    overall judgement of the response, independent of citations. Re-voting upserts
    on `(user_id, turn_id)`. No FK on `user_id` (same convention as feedback/usage).
    """

    __tablename__ = "response_feedback"
    __table_args__ = (
        UniqueConstraint("profile_id", "user_id", "turn_id", name="uq_response_feedback_user_turn"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    turn_id: Mapped[str] = mapped_column(String(64), index=True)
    query: Mapped[str] = mapped_column(Text)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating: Mapped[int] = mapped_column(Integer)  # 1=like, -1=dislike
    # Source of the current rating: 'user' (chat vote) or 'admin' (override).
    # NULL is treated as 'user' (legacy rows predate the column).
    set_by: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow
    )


# ---------------------------------------------------------------------------
# Newsletter — per-profile curated-news digest.
#
# An admin defines a URL list per profile; a background worker (see
# newsletter_worker/) periodically crawls those URLs, an LLM classifies each
# article's relevance/importance/urgency into a topic (newsletter_items), the
# admin reviews the collected topics, and approved items are compiled into a
# modern HTML email (newsletter_issues) sent on a calendar schedule to the
# profile's subscribed users (newsletter_subscriptions). Collect and send jobs
# are enqueued to newsletter_run_requests (manual or scheduled) and drained by
# the worker one at a time. All tables are profile-scoped.
# ---------------------------------------------------------------------------


class NewsletterSource(Base):
    """One crawl source (URL) in a profile's newsletter URL list.

    ``kind`` selects how the crawler treats the URL: ``article`` (fetch the page
    and extract its main content as a single item), ``index`` (a listing page —
    discover article links, follow them), or ``feed`` (an RSS/Atom feed — parse
    its entries directly).
    """

    __tablename__ = "newsletter_sources"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    url: Mapped[str] = mapped_column(String(1024))
    kind: Mapped[str] = mapped_column(String(16), default="article")  # article | index | feed
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), index=True
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow
    )


class NewsletterItem(Base):
    """A collected + LLM-classified news topic awaiting review / sending.

    Produced by a collect run: the crawler yields candidate articles, the LLM
    scores each (relevance/importance/urgency) and writes a title + short
    summary. ``content_hash`` (a hash of the article URL/content) dedupes so the
    same article is never re-classified or re-sent — unique per profile. The
    admin reviews ``pending`` items; only ``approved`` items enter an issue, at
    which point they flip to ``sent`` and carry the ``issue_id``.
    """

    __tablename__ = "newsletter_items"
    __table_args__ = (
        UniqueConstraint("profile_id", "content_hash", name="uq_newsletter_item_profile_hash"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    source_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )  # → newsletter_sources.id
    article_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(512))
    summary: Mapped[str] = mapped_column(Text)
    source_label: Mapped[str | None] = mapped_column(String(512), nullable=True)
    relevance: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1
    importance: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1
    urgency: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1
    status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True
    )  # pending|approved|rejected|sent
    issue_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )  # → newsletter_issues.id
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )


class NewsletterIssue(Base):
    """One assembled + sent newsletter edition.

    A send run gathers the profile's ``approved`` items, renders the HTML email,
    records this row, and sends it to every subscribed + active + verified user
    assigned to the profile. ``html`` is the rendered body (kept for the admin
    preview / re-inspection); ``status`` tracks the send outcome.
    """

    __tablename__ = "newsletter_issues"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    subject: Mapped[str] = mapped_column(String(512))
    html: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    recipient_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(
        String(16), default="draft", index=True
    )  # draft|sent|failed
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class NewsletterSubscription(Base):
    """A subscription to a profile's newsletter — by account **or** by email only.

    Two kinds share this table, distinguished by ``user_id``:

    * **Account** (``user_id`` set): a logged-in user assigned to the profile
      toggles their subscription (enforced in the route). Already verified, so
      no double opt-in — ``status`` is ``subscribed`` immediately.
    * **Email-only** (``user_id`` NULL): a visitor subscribes from the public
      landing page without logging in. Double opt-in: the row starts ``pending``
      with a ``confirm_token``; clicking the emailed link flips it to
      ``subscribed`` (setting ``confirmed_at`` and clearing the token).

    ``email`` is stored **lowercased + stripped** and is the real de-duplication
    key — unique per ``(profile_id, email)`` prevents two entries (account or
    email-only) for the same address on the same newsletter. ``unsubscribe_token``
    is a stable, durable opaque secret for the one-click unsubscribe link in
    every email footer — deliberately NOT an expiring Redis token.
    """

    __tablename__ = "newsletter_subscriptions"
    __table_args__ = (
        UniqueConstraint("profile_id", "user_id", name="uq_newsletter_subscription_profile_user"),
        UniqueConstraint("profile_id", "email", name="uq_newsletter_subscription_profile_email"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    email: Mapped[str | None] = mapped_column(
        String(320), index=True, nullable=True
    )  # lowercased; dedup key
    status: Mapped[str] = mapped_column(
        String(16), default="subscribed", index=True
    )  # pending|subscribed|unsubscribed
    unsubscribe_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    confirm_token: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True
    )  # double opt-in (single-use)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), onupdate=_utcnow
    )


class NewsletterRunRequest(Base):
    """A queued newsletter job (collect or send), drained by the worker.

    The admin "Collect now"/"Send now" actions and the scheduler both insert
    rows here; the newsletter-worker claims them one at a time (serialised) and
    dispatches on ``job``. Mirrors ``LoopRunRequest`` with an added ``job``
    discriminator.
    """

    __tablename__ = "newsletter_run_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    job: Mapped[str] = mapped_column(String(16), default="collect", index=True)  # collect | send
    status: Mapped[str] = mapped_column(
        String(16), default="requested", index=True
    )  # requested|running|done|failed
    trigger: Mapped[str] = mapped_column(String(16), default="manual")  # manual|scheduled
    requested_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issue_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )  # → newsletter_issues.id (send jobs)
    stats: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-as-TEXT run summary
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class CrawlerVisit(Base):
    """One row per request from an identified web crawler.

    Search engines, AI/LLM crawlers, SEO tools and social link-preview fetchers.
    Written by the SvelteKit frontend (batched, best-effort) because crawlers
    never reach FastAPI: on the public vhost only ``/health`` proxies to Python,
    while ``/``, ``/agents/*``, ``/robots.txt`` and ``/sitemap.xml`` are served by
    the frontend. A middleware here would record nothing.

    Companion to ``pacod_frontend_crawler_requests_total``, which holds the same
    traffic as aggregate counters. This table exists for the two fields a
    Prometheus label cannot hold without unbounded cardinality: the exact URL and
    the client IP.

    **Only requests already identified as bots are stored.** No human IP address
    reaches this table — that ordering is what keeps it out of scope as personal
    data, so never relax the classification step into a store-then-filter.

    Not profile-scoped: ``site`` names the public site that was crawled, which is
    a different axis from the agent ``profile_id`` used elsewhere. Deliberately
    absent from ``PROFILE_SCOPED_TABLES``.

    Pruned by the improvement-loop worker per ``crawler_visit_retention_days``.
    """

    __tablename__ = "crawler_visits"

    # Every dashboard query is "recent activity, optionally for one bot", so the
    # composite index leads with time.
    __table_args__ = (Index("ix_crawler_visits_occurred_bot", "occurred_at", "bot"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )
    # Which public site was crawled (today always "pacod").
    site: Mapped[str] = mapped_column(String(16), default="pacod", index=True)
    # Stable crawler id from the frontend's bounded rule table, e.g. "googlebot".
    bot: Mapped[str] = mapped_column(String(64), index=True)
    # "search" | "ai" | "seo" | "social".
    category: Mapped[str] = mapped_column(String(16), index=True)
    # "verified" | "unverified" | "unknown" — reverse+forward DNS confirmation.
    # "unknown" means the operator publishes no contract, NOT that a check failed;
    # only "unverified" indicates a spoofed User-Agent.
    verified: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    # Client address as seen at the edge. IPv6-sized.
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True, index=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    method: Mapped[str] = mapped_column(String(8), default="GET")
    # The exact URL path fetched — the point of this table.
    path: Mapped[str] = mapped_column(Text)
    # Matched SvelteKit route template, for low-cardinality grouping.
    route: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[int] = mapped_column(Integer, default=200, index=True)


# SQLite (unit tests) only autoincrements an INTEGER PRIMARY KEY; Postgres keeps
# the BIGSERIAL. High-volume telemetry tables below use this variant.
_BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class LlmCallError(Base):
    """One row per LLM call that finally failed (after any retries).

    The gateway records every call it handled; this table also sees what it cannot —
    client timeouts and an unreachable gateway. Written best-effort from the
    ``LLMClient`` wrapper, once per *logical* call — a call that failed twice and
    then succeeded writes nothing here.

    Profile-scoped (listed in ``PROFILE_SCOPED_TABLES``). Pruned by the
    improvement-loop worker per ``analytics_retention_days``.
    """

    __tablename__ = "llm_call_errors"
    __table_args__ = (Index("ix_llm_call_errors_occurred_site", "occurred_at", "site"),)

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )
    site: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str] = mapped_column(String(128), default="")
    purpose: Mapped[str] = mapped_column(String(32), default="")
    # "timeout" | "upstream" (failed to get a response) | "stream" (broke mid-stream)
    error_type: Mapped[str] = mapped_column(String(16), index=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    message: Mapped[str | None] = mapped_column(String(512), nullable=True)


class WebRequest(Base):
    """One row per human web/API request, for per-site and per-user analytics.

    Two writers:
      * ``kind='page'`` — the SvelteKit frontend (page loads + client-navigation
        data requests), batched to ``POST /analytics/ingest/web-requests``.
        ``user_id`` is only stored when the visitor gave analytics consent;
        otherwise the row still counts volume (``user_type='registered'``).
      * ``kind='api'`` — the api's ``RequestContextMiddleware`` for browser and
        OpenAI-compatible (``/v1/*``) calls (SSR-internal calls are excluded).

    Deliberately minimal: no IP, no User-Agent, no query string, and ``route`` is
    always a route *template* (SvelteKit route id / FastAPI path), never a raw
    path. Crawlers are never stored here (see ``CrawlerVisit``).

    Not profile-scoped: ``site`` is the axis. Pruned per
    ``analytics_retention_days``.
    """

    __tablename__ = "web_requests"
    __table_args__ = (
        Index("ix_web_requests_occurred_site", "occurred_at", "site"),
        Index("ix_web_requests_user_occurred", "user_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    site: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(8))  # page | api
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_type: Mapped[str] = mapped_column(String(12))  # registered | anonymous
    method: Mapped[str] = mapped_column(String(8), default="GET")
    route: Mapped[str] = mapped_column(String(256))
    status: Mapped[int] = mapped_column(Integer, default=200)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


class PageView(Base):
    """Consent-gated page-view beacons with visible dwell time.

    The browser tracker (only active with analytics consent) sends one *start*
    beacon per page view and then dwell *deltas* whenever the tab is hidden or
    the view ends, so rows are append-only: page views = rows with ``is_start``;
    dwell = ``SUM(dwell_ms)`` grouped by ``pageview_id``. ``user_id`` and
    ``site`` are stamped server-side, never taken from the client.

    Not profile-scoped. Pruned per ``analytics_retention_days``.
    """

    __tablename__ = "page_views"
    __table_args__ = (
        Index("ix_page_views_occurred_site", "occurred_at", "site"),
        Index("ix_page_views_user_occurred", "user_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    pageview_id: Mapped[str] = mapped_column(String(36), index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    site: Mapped[str] = mapped_column(String(16))
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    route: Mapped[str] = mapped_column(String(256))
    locale: Mapped[str | None] = mapped_column(String(8), nullable=True)
    dwell_ms: Mapped[int] = mapped_column(Integer, default=0)
    is_start: Mapped[bool] = mapped_column(Boolean, default=False)
    referrer_host: Mapped[str | None] = mapped_column(String(128), nullable=True)


class UserIpAddress(Base):
    """Every IP address an account has used: one row per ``(user, ip)``.

    ``use_count`` is bumped for each authenticated api request the browser makes
    from that address — aggregated in-process by ``user_ip_sink`` and upserted in
    bulk. SvelteKit SSR calls (``X-Pacod-Internal``) are not counted: they carry
    no client IP of their own. Admin-only.

    Global (not profile-scoped); cascades with the user. Rows not used for
    ``activity_log_retention_months`` are pruned by the improvement-loop worker.
    """

    __tablename__ = "user_ip_addresses"
    __table_args__ = (
        UniqueConstraint("user_id", "ip", name="uq_user_ip_addresses_user_ip"),
        Index("ix_user_ip_addresses_user_last", "user_id", "last_used_at"),
    )

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id", ondelete="CASCADE"))
    ip: Mapped[str] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )
    use_count: Mapped[int] = mapped_column(BigInteger, default=0)


# Values of ``user_activity.action`` — the ones the app writes. Sign-out, sessions and
# passwords are pacod-id's (its own audit). The admin console's activity filter mirrors this
# tuple (pacod-ui features/analytics ACTIVITY_ACTIONS; analytics.py 422s a value not in it).
ACTIVITY_ACTIONS = (
    "login",
    "chat_turn",
    "chat_deleted",
)


class UserActivity(Base):
    """Per-account activity log: one row per meaningful action.

    Written by ``app.services.user_activity.record_activity`` (fire-and-forget)
    with the IP and site of the request that did it. ``session_id`` is the chat
    for ``chat_turn`` / ``chat_deleted``.
    Admin-only.

    Global (not profile-scoped — ``profile_id`` is display data); cascades with
    the user. Pruned after ``activity_log_retention_months``.
    """

    __tablename__ = "user_activity"
    __table_args__ = (Index("ix_user_activity_user_occurred", "user_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id", ondelete="CASCADE"))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )
    action: Mapped[str] = mapped_column(String(32))
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    site: Mapped[str | None] = mapped_column(String(16), nullable=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Short JSON, e.g. {"client": "pacod-web"} (a login's pacod-id client)
    detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
