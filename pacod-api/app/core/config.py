"""Application configuration loaded from environment variables.

All configurable knobs live here. The agent topic, the LLM provider, quotas,
and the web-search allowlist are all hot-swappable via env without code changes.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- Environment ----------
    app_env: Literal["development", "staging", "production"] = "development"
    # Second lock on the `dev-token-<user_id>` bypass (see app/guards/auth.py),
    # which grants admin+superuser without a password. `app_env` alone used to
    # gate it, and it DEFAULTS to "development" — safe only while every /api/*
    # request goes through SvelteKit, which 401s a bearer-only caller. Any edge
    # route that reaches the api directly removes that shield, so both must say yes.
    auth_allow_dev_tokens: bool = False
    app_log_level: str = "INFO"
    app_log_dir: str = ""  # absolute path; empty = file logging disabled
    # Fernet key (urlsafe base64, 32 bytes) used to encrypt provider API keys
    # managed from the admin panel. Empty → key editing is disabled and the panel
    # shows read-only env status. Generate with: Fernet.generate_key().decode()
    app_secrets_key: str = ""

    # ---------- Service URLs (Docker DNS names) ----------
    postgres_host: str = "pgbouncer"
    postgres_port: int = 5432
    postgres_user: str = "pacod"
    postgres_password: str = ""
    postgres_db: str = "pacod"

    redis_url: str = "redis://redis:6379/0"
    qdrant_url: str = "http://qdrant:6333"
    embeddings_url: str = "http://embeddings:8080"
    # ---------- Identity: pacod-id ----------
    # The issuers whose access tokens the API takes (CSV, one per realm; blank = pacod-id
    # not configured, only dev tokens work), pacod-id's internal listener, and the API's
    # own client there (pacod-api: pushes the `pacod` catalogue, follows the feed,
    # introspects service tokens, exchanges a caller's token for the admin facades).
    id_issuers: str = ""
    id_audience: str = "pacod-api"
    id_internal_url: str = "http://pacod-id:8071"
    id_client_id: str = "pacod-api"
    id_client_secret: str = ""
    # Admin surfaces refuse (503 identity_unavailable) while the copy of pacod-id's
    # bindings is older than this; chat keeps working on the last copy.
    id_max_staleness: float = 60.0

    # ---------- The LLM gateway (pacod-gw) ----------
    # Every LLM call goes through its OpenAI-compatible data plane. Each service
    # has a key of its own (pgw_…), which compose passes as GATEWAY_API_KEY from
    # pacod-api/.env.gateway (scripts/gw_bootstrap.py writes that file).
    gateway_url: str = "http://gw-core:8080"
    gateway_api_key: str = ""
    # The management API (admin facades, usage reports; never routed publicly),
    # the console as the browser reaches it (deep links; blank hides them), and
    # the platform's organisation and workspace there (gw_bootstrap writes both).
    gateway_admin_url: str = "http://gw-core:8081"
    gateway_console_url: str = ""
    gateway_org_id: str = ""
    gateway_workspace_id: str = ""
    # admin-api only: a read-only token (viewer of the workspace) for its usage
    # reports, and one that may change the workspace's limits (quota overrides).
    gateway_report_token: str = ""
    gateway_admin_token: str = ""
    # Client-side timeout for ONE LLM request (seconds). A request that times
    # out is NEVER retried: the provider keeps generating after the client hangs
    # up and bills the result, so a retry pays twice for one answer. Size it for
    # the slowest legitimate call (thinking on + a long answer), not the typical
    # one; the gateway's provider timeouts (600 s, platform-config) sit above it.
    # The improvement loop uses `loop_llm_timeout_seconds` instead.
    llm_timeout_seconds: float = 180.0
    mcp_server_url: str = "http://mcp-server:8000"
    # Shared secret the agent presents to the MCP server (X-MCP-Token). FAILS
    # CLOSED on the server: blank → every tool call 503s, because the server
    # otherwise answers any container on agent-net with any profile's reference
    # data. Same env var on api, mcp-server and improvement-loop-worker.
    mcp_shared_secret: str = ""
    ingestion_url: str = "http://ingestion:8000"
    documents_root: str = "/srv/documents"

    # ---------- Languages (app/core/i18n.py) ----------
    # Answer languages this deployment offers (CSV of bg | en) and the fallback
    # language. Each profile narrows the set with its own `languages`.
    platform_languages: str = "bg,en"
    platform_default_language: str = "en"

    # ---------- Agent topic configuration ----------
    # The DEFAULT profile's identity, seeded once at first boot
    # (profiles.seed_default_profile; admins edit it afterwards). The shipped
    # default is the `starter` demo agent: a help desk for Nimbus Notes, a
    # fictional note-taking service, so every platform feature has data to show.
    default_profile_name: str = "Starter Agent"
    default_profile_description: str = (
        "Demo agent of the platform: the help desk of Nimbus Notes, a fictional "
        "online note-taking service. Start here and clone it for your own agent."
    )
    # CSV of the default profile's answer languages (each must be enabled in
    # PLATFORM_LANGUAGES).
    default_profile_languages: str = "en,bg"
    agent_topic_name: str = "starter"
    agent_topic_description_bg: str = (
        "Клиентска поддръжка на Nimbus Notes — измислена онлайн услуга за бележки, "
        "използвана за демонстрация на платформата: планове и цени, лимити и "
        "хранилище, плащания и фактури, акаунти, вход и сигурност, споделяне и "
        "интеграции, експорт на данни, нива на обслужване (SLA) и работно време "
        "на поддръжката."
    )
    agent_topic_description_en: str = (
        "Customer support for Nimbus Notes, a fictional online note-taking service "
        "used to demonstrate the platform: plans and prices, usage limits and "
        "storage, billing and invoices, accounts, sign-in and security, sharing "
        "and integrations, data export, service levels (SLA) and support hours."
    )
    agent_legal_year: int = 2026

    # ---------- LLM configuration ----------
    # Code defaults; `.env` ships the Anthropic pair (claude-sonnet-5-5 /
    # claude-haiku-4-5). Ids must be models the gateway grants this platform.
    agent_primary_model: str = "gemini-3.1-pro-preview"
    agent_classifier_model: str = "gemini-3.8-flash"
    agent_temperature: float = 0.2
    # When False, the `temperature` parameter is omitted from every LLM request
    # (some models — e.g. reasoning models — reject a non-default temperature).
    agent_temperature_enabled: bool = True
    agent_max_output_tokens: int = 8196  # synthesis answer budget
    # Max output tokens for the internal phases (compact JSON / classification),
    # separate from the synthesis budget above.
    agent_planner_max_tokens: int = 400
    agent_classifier_max_tokens: int = 400
    agent_suggestions_max_tokens: int = 300
    # Follow-up suggestions model. Blank → use agent_classifier_model.
    agent_suggestions_model: str = ""
    agent_thinking_budget_tokens: int = 8196  # 0 disables extended thinking
    # Planner model. Empty → use agent_primary_model (current behaviour). Set to a
    # fast model (e.g. anthropic/claude-haiku-4-5) to cut the serial pre-tool TTFT;
    # validate tool-choice quality with `make eval` before keeping it.
    agent_planner_model: str = ""
    # Newsletter classification model (newsletter_worker collect stage). Blank →
    # use agent_primary_model. Surfaced in the LLM-config panel's model roles.
    agent_newsletter_model: str = ""
    # Emit a provider prompt-caching breakpoint (Anthropic cache_control) on the
    # static system prefix of the planner and synthesizer. The static-prefix/dynamic-
    # context split is behaviour-preserving, so off is a safe no-op. Default OFF until
    # the array-typed `system` payload is smoke-tested end-to-end through
    # OpenAI-SDK → the gateway → Anthropic (confirm no upstream error AND that
    # cache_read_input_tokens appear on a repeat call); then flip on via env.
    agent_prompt_cache_enabled: bool = False

    # ---------- Conversation memory (multi-turn history + compaction) ----------
    # When True, prior turns (client-supplied `history`) are fed to the planner and
    # synthesizer so follow-ups keep context. The most recent turns are sent verbatim;
    # older turns are folded into a rolling per-session summary cached in Redis.
    agent_history_enabled: bool = True
    # Number of recent turn-pairs kept verbatim (as real chat messages).
    agent_history_recent_turns: int = 4
    # Only invoke the summarizer once this many not-yet-summarised messages (not
    # turn-pairs) have aged out past the verbatim window (decouples the summariser LLM
    # call from every turn → fires ~1-in-M turns).
    agent_history_summarize_after: int = 4
    # Output-token budget for the rolling summary.
    agent_history_summary_max_tokens: int = 512
    # Model for the summariser. Blank → use agent_classifier_model (cheap).
    agent_history_summary_model: str = ""
    # TTL for the cached rolling summary (refreshed on each write); idle sessions age out.
    agent_history_summary_ttl_seconds: int = 86400

    # ---------- Retrieval ----------
    retrieval_stage1_top_k: int = 20  # dense ANN candidates before cross-encoder rerank
    retrieval_rerank_top_k: int = 5
    # How many of the top dense candidates to feed the cross-encoder. 0 → rerank all
    # stage-1 candidates (current behaviour). Caps cross-encoder work when stage-1 is
    # raised for recall.
    retrieval_rerank_input_k: int = 0
    # Dense-confidence early-exit: if the top retrieval_rerank_top_k candidates all
    # have a dense score ≥ this threshold, skip the cross-encoder entirely. 0 → off.
    # Trades precision for latency — leave off until profiling + eval justify it.
    retrieval_rerank_score_threshold: float = 0.0
    # Default profile's base KB collection (env RETRIEVAL_COLLECTION_NAME).
    # Only the DEFAULT profile's name is configurable here and it MUST follow the
    # same convention as every other profile — pacod_<slug>_kb for slug `starter`
    # (see profiles.collection_names_for_slug). May be an alias after a fine-tune
    # swap.
    retrieval_collection_name: str = "pacod_starter_kb"
    retrieval_hnsw_ef: int = 256  # HNSW ef at query time; 256 balances recall and speed

    # ---------- Curated KB tier ----------
    # A physically separate Qdrant collection holding LLM-curated, lower-authority
    # notes synthesised by the continuous-improvement loop. The live agent reads
    # BOTH this and the authoritative collection but down-weights curated hits by
    # `curated_trust_weight` so primary law outranks a curated note. The loop's
    # grounding/novelty search queries the authoritative collection ONLY (the
    # model-collapse firewall). See improvement_loop/ and app/tools/kb_retrieval.py.
    # Default profile's curated collection (env CURATED_COLLECTION_NAME).
    # Convention: pacod_<slug>_curated — the slug sits in the same position as in
    # the base collection name.
    curated_collection_name: str = "pacod_starter_curated"
    curated_trust_weight: float = 0.6  # multiplier applied to curated scores at merge
    curated_tier_enabled: bool = True  # include kb_curated in live retrieval

    # ---------- Continuous-improvement loop ----------
    # The daily triage engine (standalone `improvement_loop` service). All knobs
    # are env-driven so the job can run outside the stack. Model ids default BLANK
    # and fall back to the agent's models so the loop always runs; for the
    # decorrelated-error benefit set A and B to two DIFFERENT families
    # (e.g. a Claude and a Gemini) and the judge to a Sonnet/Opus-class model.
    # Schedule (the dedicated improvement-loop-worker fires a daily run). OFF by
    # default — enable from the admin Review-process panel after a watched manual run.
    loop_schedule_enabled: bool = False
    loop_schedule_hour: int = 2  # UTC hour for the daily run
    loop_schedule_minute: int = 0  # UTC minute
    loop_worker_poll_seconds: int = 30  # how often the worker polls for work
    # A run request left `running` longer than this is treated as orphaned by a
    # worker crash and marked failed (env-only; the single worker reaps on boot too).
    loop_run_stale_hours: int = 6
    loop_lookback_hours: int = 24  # conversation window per run
    loop_max_conversations: int = 0  # 0 → no cap (else sample newest N)
    loop_concurrency: int = 4  # bounded LLM fan-out (429/cooldown guard)
    loop_triage_model: str = ""  # blank → agent_classifier_model (cheap)
    loop_triage_mode: str = "model"  # model | all | feedback_only (selectivity lever)
    # dual → two independent analysts + a reconciling judge; single → one analyst +
    # a critic (no agreement/decorrelation confidence reported). A `dual` run with
    # no distinct model B degrades to single with a stated reason.
    loop_analysis_mode: str = "dual"
    loop_analysis_model_a: str = ""  # blank → agent_primary_model
    # Blank does NOT fall back — see LoopConfig.resolve_panel. Must be a model from
    # a DIFFERENT family than A (all granted by the gateway) so their
    # errors are decorrelated; pairing A with the cheap classifier made
    # "disagreement" mean "B is weaker". Ships as a frontier OpenAI model because
    # A ships as a Claude. Only a blank B, or one equal to A, degrades a dual run to
    # single mode (and says so); a B the gateway does not grant fails its calls.
    loop_analysis_model_b: str = "openai/gpt-5.6-sol"
    loop_judge_model: str = ""  # blank → analysis A's model
    # Writes the corrected answer + directive proposal for turns that were wrong.
    # Blank → analysis A's effective model (upgrading A upgrades this too).
    loop_remediation_model: str = ""
    # Output budgets. Every stage returns JSON with free-text rationale in Cyrillic,
    # which tokenises far less efficiently than English — the old 300/1200/1500/2000
    # budgets truncated regularly and the cut-off was reported as a parse error.
    loop_remediation_max_tokens: int = 4000
    loop_analysis_max_tokens: int = 3000
    loop_judge_max_tokens: int = 3000
    loop_triage_max_tokens: int = 600
    # Extended-thinking budget for the loop's review stages (0 disables). The
    # answers being reviewed were produced WITH reasoning; reviewing them without
    # it asks a weaker pass to fact-check a stronger one.
    loop_thinking_budget_tokens: int = 4096
    # Timeout for the loop's review calls (non-streaming, thinking on, 3–4k output
    # tokens — GPT-5.x routinely needs more than a minute). Batch work, nobody is
    # waiting, so far above `llm_timeout_seconds`. Same rule: a timeout is never
    # retried, the turn is counted as `errors_timeout` and picked up by a re-run.
    loop_llm_timeout_seconds: float = 600.0
    # Novelty thresholds (cosine vs the AUTHORITATIVE tier only): a candidate topic
    # scoring ≥ covered is already in the KB (→ retrieval problem, not a gap); below
    # gap is a genuine coverage gap; in between is a partial/augment candidate.
    # Evidence assembly for the analysis stage. The used-context block comes from
    # `turn_evidence`; these bound the DISCARDED-candidate side, which is re-read
    # from Qdrant and is what distinguishes a retrieval failure from a content gap.
    loop_evidence_rejected_max: int = 10
    loop_evidence_rejected_chars: int = 400
    # ≥ TURN_EVIDENCE_MAX_CHARS, so the loop normally re-reads the whole snapshot;
    # when it does have to cut, the evidence is marked `partial`, never `full`.
    loop_evidence_max_context_chars: int = 48000
    loop_novelty_covered_threshold: float = 0.82
    loop_novelty_gap_threshold: float = 0.60
    # Synthetic principal recorded on the loop's usage_events rows.
    loop_system_user_id: str = "system:improvement_loop"
    # Stage 7 grounding gate: a candidate claim's citation is considered anchored
    # when its best match in the AUTHORITATIVE tier scores ≥ this (cosine).
    loop_grounding_threshold: float = 0.55
    # Second half of the grounding gate. The cosine threshold above only proves the
    # KB has a passage about roughly this topic; it cannot tell a claim the article
    # SUPPORTS from one it CONTRADICTS (same topic, different figure). When on, each
    # anchored citation's retrieved text goes to a cheap entailment check and a
    # `contradicted` verdict blocks curation outright. One extra LLM call per
    # content-gap candidate that already cleared the cosine gate.
    loop_entailment_required: bool = True
    # Group findings into recurring problems. A per-turn findings list doesn't
    # converge: 40 turns failing on one missing source produce 40 rows and zero
    # decisions. The threshold is the cosine similarity needed to JOIN an existing
    # open cluster (higher = more, tighter clusters).
    loop_cluster_enabled: bool = True
    loop_cluster_threshold: float = 0.82
    # Re-ask the questions behind claimed fixes through the LIVE agent and assert the
    # expectation against the new answer — the only measurement of whether a
    # remediation worked. OFF by default: one full agent turn (LLM + MCP + retrieval)
    # per case, and it needs MCP_SERVER_URL reachable from the loop worker.
    loop_verification_enabled: bool = False
    loop_verification_max_cases: int = 25
    # High-stakes topics where auto-insertion is NEVER allowed regardless of
    # confidence (Phase 3 gate); also surfaced as `stakes` on every candidate.
    # Comma-separated substrings matched case-insensitively against topic+claim.
    # Empty by default: the analyst's own `stakes` judgement covers every domain;
    # list domain keywords here (e.g. "penalty,threshold") to add a hard floor.
    loop_high_stakes_keywords: str = ""

    # Phase 3 — flag-gated auto-insertion of high-confidence, grounded, low-stakes
    # candidates (default OFF; earns trust only after the review approval rate proves
    # the generator). Even when on, high-stakes candidates are forced to review.
    loop_auto_insert_enabled: bool = False
    loop_auto_insert_min_confidence: float = 0.85
    # By default a content gap on a web-search turn is dropped (web data is volatile,
    # not curatable). Flip on to still queue such gaps as review candidates.
    loop_curate_web_search_gaps: bool = False

    # Stage 10 — meta-evaluation: monitor whether inserted curated entries actually
    # helped (surfaced/used in later turns, feedback on those turns). Underperformers
    # can be retired (status→superseded) only when retire is explicitly enabled.
    loop_meta_lookback_days: int = 14
    loop_meta_retire_enabled: bool = False
    loop_meta_min_surfaced_for_signal: int = 3  # below this we say "no signal yet"
    # Stage-1 PII redaction packs (improvement_loop/redact.py), CSV: "generic"
    # (email, IBAN, card, international phone, amounts) and "bg" (ЕГН, ЕИК,
    # Bulgarian phone numbers). Per-profile override: app_settings loop.redaction_packs.
    loop_redaction_packs: str = "generic"

    # ---------- Retrieval trace logging ----------
    # Persist one `retrieval_traces` row per kb_search call (retrieved chunk ids +
    # dense/rerank scores + which were used) so the improvement loop can tell a
    # retrieval failure from a content gap. Always-on baseline default; the runtime
    # `retrieval_trace_enabled` toggle (app_settings) overrides it live. Lightweight
    # (one row per kb_search, scores only) — distinct from full debug audit.
    retrieval_trace_enabled: bool = True
    # Snapshot of what the assistant actually READ per turn (the verbatim synthesis
    # context + the directives in force). On by default: without it the improvement
    # loop classifies retrieval vs content_gap vs hallucination from source ids and
    # scores alone. Runtime-overridable via the `turn_evidence_enabled` toggle.
    turn_evidence_enabled: bool = True
    # Per-row cap on the stored context (the KB blocks are the bulk of it).
    turn_evidence_max_chars: int = 40000
    # Pruned by the loop worker. Well beyond `loop_lookback_hours` so an admin can
    # still run a single-turn review on an older turn and get full evidence.
    turn_evidence_retention_days: int = 120

    # ---------- Crawler monitoring ----------
    # Search-engine / AI / SEO / social bot traffic, recorded by the SvelteKit
    # frontend (crawlers never reach FastAPI — the public vhost sends everything
    # but /health to the frontend) and posted here in batches.
    #
    # Shared secret the frontend sink presents on POST /crawler-visits/ingest.
    # BLANK DISABLES INGEST ENTIRELY — the endpoint 503s rather than accepting
    # unauthenticated writes. Generate with: openssl rand -base64 32
    crawler_ingest_token: str = ""
    # Pruned daily by the improvement-loop worker. 0 → keep forever.
    # Well under the 12 months the Privacy Policy declares for server logs; these
    # rows are bot traffic only, never a human IP.
    crawler_visit_retention_days: int = 90

    # ---------- Usage analytics ----------
    # Per-site / per-profile / per-user request + LLM analytics (admin Analytics
    # page). Ingest from the frontends reuses ``crawler_ingest_token``.
    #
    # Raw rows in web_requests / page_views / llm_call_errors are pruned daily by
    # the improvement-loop worker. 0 → keep forever.
    analytics_retention_days: int = 180
    # Kill switch for the api's per-request rows (kind='api' in web_requests).
    analytics_api_requests_enabled: bool = True
    # Values of the X-Pacod-Site header the api accepts. The header is always
    # overwritten by a trusted hop (the SvelteKit proxy / SSR fetch); anything
    # else resolves to "unknown".
    analytics_trusted_sites: str = "pacod"

    # ---------- Per-user IP addresses + activity log ----------
    # Admin-only (Analytics → user page). ``user_ip_addresses`` holds one row per
    # (user, IP) with a use count bumped on every authenticated api request;
    # ``user_activity`` one row per meaningful action (login, chat turn, chat
    # deleted) with IP, time and chat. Kill switch for both.
    user_activity_enabled: bool = True
    # Calendar months of activity (and of IPs not used since) kept before the
    # improvement-loop worker's daily prune deletes them. 0 → keep forever.
    activity_log_retention_months: int = 6

    # ---------- Answer verification ----------
    # When enabled, the synthesizer draft is passed through a second LLM call
    # that checks every number and legal claim against the tool context before
    # the answer is streamed to the client.
    agent_verification_enabled: bool = False
    # When True, verification only runs for requests the planner marks as
    # is_complex=true (multi-rule, cross-border, strategy). Straight lookups
    # are left unverified for lower latency.
    agent_verification_complex_only: bool = False

    # ---------- Visual agent editor ----------
    # Every profile gets an agent definition + version 1 at boot (insert-only).
    # When this is True, profiles that are not yet editor-managed also get that
    # version 1 published (``published_version_id`` set), which hands their
    # owned settings to the editor and locks them on the legacy config pages.
    # Behaviour-neutral by construction (see app/agent_editor/importer.py).
    agent_editor_adopt_on_boot: bool = False
    # The editor's AI assistant (drafts prompts/content per component). These
    # are env DEFAULTS of a global runtime config edited on the editor's
    # "AI assistant" page (app_settings `editor_assistant.*` under __global__).
    # Blank model → AGENT_PRIMARY_MODEL.
    agent_editor_assistant_enabled: bool = True
    agent_editor_assistant_model: str = ""
    agent_editor_assistant_temperature: float = 0.4
    agent_editor_assistant_max_output_tokens: int = 6000
    agent_editor_assistant_thinking_budget_tokens: int = 0

    # ---------- Debug audit ----------
    # Opt-in: persist every LLM/RAG/tool/decision interaction (full request+response,
    # per-interaction duration) to the debug_interactions table. This is the baseline
    # default; an admin runtime toggle (app_settings 'debug_audit_enabled') overrides
    # it live. Off by default — rows hold raw prompts/answers and add per-turn writes.
    agent_debug_audit_enabled: bool = False

    # ---------- Response cache (semantic) ----------
    # Cache answered first-turn questions and serve a cached answer when a new
    # question is semantically similar above the threshold (see
    # app/services/response_cache.py). On by default; admins can flip it and tune
    # the threshold / TTL live via the admin panel (app_settings 'respcache.*').
    response_cache_enabled: bool = True
    # Cosine similarity in [0, 1]; higher = safer / fewer hits. 0.90 is a starting
    # point that needs empirical tuning against known-distinct questions.
    response_cache_similarity_threshold: float = 0.90
    response_cache_ttl_seconds: int = 86_400  # 24h — stale answers age out
    response_cache_max_entries_per_bucket: int = 500  # env-only (not runtime-editable)

    # ---------- Quotas ----------
    agent_quota_disabled: bool = False  # set True in dev/test to bypass all quota checks
    # Two independently-enableable quota types, each with a configurable validity
    # period (hour|day|week|month). These are env *defaults*; the active type(s),
    # limits, and periods are runtime-overridable per the admin quota-config panel
    # (app_settings, no restart). Token/day on + request off ⇒ historical behavior.
    agent_quota_token_enabled: bool = True
    agent_per_user_daily_token_limit: int = 100_000  # token-quota default limit
    agent_quota_token_period: str = "day"
    agent_quota_request_enabled: bool = False
    agent_per_user_request_limit: int = 1_000  # request-quota default limit
    agent_quota_request_period: str = "day"
    # Independent anti-burst throttle (sliding 60s window); unaffected by the quota types above.
    agent_per_user_per_minute_requests: int = 10

    # ---------- Ingestion ----------
    ingest_doc_concurrency: int = Field(4, ge=1, le=32)
    # "http"       — use the external embeddings HTTP service (GPU pipeline default)
    # "inline_cpu" — load bge-m3 in-process via CpuEmbedder (CPU pipeline default)
    embeddings_backend: Literal["http", "inline_cpu"] = "http"

    # ---------- Web search ----------
    web_search_enabled: bool = True
    # Backend for web search:
    #   "external" — the dedicated Tavily/Google service (app/services/web_search.py)
    #   "native"   — the model's integrated server-side search where supported
    #                (Anthropic), falling back to external otherwise.
    # Runtime-overridable via the admin LLM-config panel (websearch.mode).
    web_search_mode: Literal["external", "native"] = "native"
    # In native mode, when the active model can't enforce the domain allowlist
    # (Gemini/OpenAI), fall back to external (which can) rather than searching the
    # open web. Anthropic enforces the allowlist, so native always engages for
    # Claude models regardless. Keep True for the compliance allowlist guarantee.
    web_search_native_require_allowlist: bool = True
    web_search_provider: Literal["auto", "tavily", "google"] = "auto"
    tavily_api_key: str = ""
    google_search_api_key: str = ""
    google_search_cx: str = ""  # Google Custom Search Engine ID
    # Allow-list of domains web search may return (CSV). Empty → web search
    # returns nothing. Per-profile override: app_settings websearch.allowed_domains.
    web_search_allowed_domains: str = ""
    # Estimated per-search cost (USD) used only for the admin usage/cost
    # aggregation; not a billed figure. Tunable as providers change pricing.
    web_search_cost_usd_tavily: float = 0.008
    web_search_cost_usd_google: float = 0.005

    # ---------- Email / SMTP (default: Gmail) ----------
    # Outbound email for the newsletter (account mail is pacod-id's). Defaults target
    # Gmail's SMTP relay over STARTTLS (port 587). For a Gmail account, set
    # SMTP_USERNAME to the address and SMTP_PASSWORD to a 16-char App Password
    # (https://myaccount.google.com/apppasswords) — not the account password.
    # When SMTP is unconfigured (no host or no username), sending is a no-op that
    # logs a warning, so dev/test runs without email don't break a subscribe or a send.
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = (
        True  # STARTTLS on port 587; set False + port 465 for implicit TLS via smtp_use_ssl
    )
    smtp_use_ssl: bool = False  # implicit TLS (port 465)
    smtp_timeout_seconds: int = 15
    # The From: address. Blank → fall back to smtp_username.
    email_from_address: str = ""
    email_from_name: str = "PACOD"
    # Public base URL of the frontend, used to build the links in outbound mail
    # (newsletter unsubscribe). Account mail is pacod-id's.
    app_public_base_url: str = "https://www.pacod.io"

    # ---------- Newsletter ----------
    # Per-profile curated-news digest driven by the newsletter-worker. All knobs
    # here are env DEFAULTS; the schedule + bounds are runtime-overridable per
    # profile via the admin Newsletter panel (app_settings `newsletter.*`, no
    # restart — see app/services/newsletter_runtime_config.py). Both schedules are
    # OFF by default; enable + configure from the panel after a watched manual run.
    newsletter_worker_poll_seconds: int = 30  # how often the worker polls for work
    # Collect schedule (calendar-aligned): fire on the selected weekdays at HH:MM UTC.
    # collect_days is a CSV of weekday numbers, Mon=0 … Sun=6 (empty → every day).
    newsletter_collect_enabled: bool = False
    newsletter_collect_days: str = "0,1,2,3,4,5,6"
    newsletter_collect_hour: int = 6
    newsletter_collect_minute: int = 0
    # Send schedule (calendar-aligned): same day-of-week + HH:MM shape.
    newsletter_send_enabled: bool = False
    newsletter_send_days: str = "0"  # Mondays by default (weekly digest)
    newsletter_send_hour: int = 8
    newsletter_send_minute: int = 0
    # Bounds / cost guards.
    newsletter_max_sources: int = 50  # max sources crawled per collect run
    newsletter_max_articles_per_source: int = 20  # cap links followed from an index/feed
    newsletter_crawl_concurrency: int = 4  # bounded fan-out (fetch + classify)
    newsletter_max_items_per_issue: int = 25  # top-N approved items per send
    # Relevance floor [0..1]: articles the classifier scores below this are dropped
    # (never stored as pending), keeping the queue tightly on the profile's topic.
    # 0.0 → model-only (the classifier's own include decision decides).
    newsletter_min_relevance: float = 0.5
    newsletter_subject_template: str = "{profile_name} — News digest"
    # Which profile's newsletter a logged-out landing-page visitor subscribes to.
    # Blank → the default profile. Runtime-overridable (global app_settings
    # `newsletter.public_profile_id`) via the admin Newsletter panel.
    newsletter_public_profile_id: str = ""

    # ---------- Language gating ----------
    language_detection_min_confidence: float = 0.4

    # ---------- Derived ----------
    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def web_search_allowed_domains_list(self) -> list[str]:
        return [d.strip() for d in self.web_search_allowed_domains.split(",") if d.strip()]

    @field_validator("agent_legal_year")
    @classmethod
    def _legal_year_sane(cls, v: int) -> int:
        if not (2000 <= v <= 2100):
            raise ValueError("agent_legal_year out of plausible range")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Never instantiate Settings() directly elsewhere."""
    return Settings()
