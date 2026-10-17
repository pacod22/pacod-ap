"""Orchestration for one improvement-loop run (Stages 1–6).

Idempotent (skips turns already analysed with the same content hash), bounded
concurrency over the per-conversation pipeline (429/cooldown guard), and read-only
w.r.t. the KB. Its LLM calls are recorded by the gateway under `loop_*` purposes,
which is where the loop's cost shows up in the admin usage views; `usage_events`
holds web searches only.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.agent.loop_prompts import (
    LOOP_ANALYSIS_SYSTEM,
    LOOP_CLUSTER_SYSTEM,
    LOOP_CRITIC_SYSTEM,
    LOOP_ENTAILMENT_SYSTEM,
    LOOP_FACTS_SYSTEM,
    LOOP_JUDGE_SYSTEM,
    LOOP_REMEDIATION_SYSTEM,
    LOOP_TRIAGE_SYSTEM,
)
from app.core.config import get_settings
from app.core.db import get_engine
from app.core.db_models import DEFAULT_PROFILE_ID, Base
from app.core.logging import get_logger
from app.guards.topic import profile_topic_description
from app.services.agent_prompts import get_effective_prompts
from app.services.audit import record_usage_events
from app.services.debug_audit import debug_scope, is_debug_audit_enabled, record_debug_interaction
from app.services.embeddings_client import EmbeddingsClient
from app.services.llm_client import LLMClient, usage_scope
from app.services.loop_runtime_config import LoopConfig, get_loop_config
from app.services.profiles import ProfileContext, env_profile_context, get_profile
from app.services.vector_store import VectorStore
from improvement_loop import report as report_mod
from improvement_loop.analyze import analyze
from improvement_loop.cluster import cluster_run
from improvement_loop.collect import collect_conversations
from improvement_loop.curated_insert import auto_insert_eligible, insert_curated, should_auto_insert
from improvement_loop.evidence import build_evidence, resolve_authoritative_collection
from improvement_loop.grounding import GroundingChecker
from improvement_loop.judge import judge
from improvement_loop.llm import call_counters
from improvement_loop.models import ConversationBundle, Finding
from improvement_loop.novelty import NoveltyChecker
from improvement_loop.promote import build_candidate, build_golden
from improvement_loop.prompts import render_loop_prompt, resolve_legal_year
from improvement_loop.remediate import extract_facts, remediate, should_remediate
from improvement_loop.route import is_golden_candidate, route_for
from improvement_loop.triage import triage

log = get_logger(__name__)


@dataclass
class RunResult:
    run_id: int | None
    report_md: str
    stats: dict
    findings: list[Finding]
    # The run's terminal status. `run_loop` never raises — it records the failure
    # on the `loop_runs` row — so the worker has to read it from here to mark the
    # request row honestly (it used to mark every finished request `done`).
    status: str = "done"
    error: str | None = None


class _Cancelled:
    """Sentinel: the turn was skipped because the run was stopped."""


CANCELLED = _Cancelled()


@dataclass
class Outcome:
    """Per-conversation result: the finding plus any review-queue artefacts."""

    finding: Finding
    candidate: dict | None = None  # kb_candidates row values (content gaps)
    golden: dict | None = None  # golden_tests row values
    auto_insert_ok: bool = False  # cleared the Phase-3 auto-insert eligibility bar


async def ensure_tables() -> None:
    """Create any missing tables (idempotent) so the loop runs standalone, even
    with the `api` container — which normally runs migrations — stopped."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _stopped(cancel: asyncio.Event | None) -> bool:
    return cancel is not None and cancel.is_set()


def _window(date: str | None, lookback_hours: int) -> tuple[datetime, datetime]:
    if date:
        start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
        return start, start + timedelta(days=1)
    end = datetime.now(UTC)
    return end - timedelta(hours=lookback_hours), end


def _filter_idempotent(
    bundles: list[ConversationBundle], existing: dict[str, str]
) -> tuple[list[ConversationBundle], int]:
    """Drop turns already analysed with an unchanged conversation (same hash)."""
    fresh: list[ConversationBundle] = []
    skipped = 0
    for b in bundles:
        if existing.get(b.turn_id) == b.content_hash:
            skipped += 1
            continue
        fresh.append(b)
    return fresh, skipped


async def _analyse_one(
    bundle: ConversationBundle,
    llm: LLMClient,
    novelty: NoveltyChecker,
    grounding: GroundingChecker,
    models_used: list[str],
    run_id: int | None,
    sem: asyncio.Semaphore,
    cfg: LoopConfig,
    analysis_system: str,
    judge_system: str,
    debug_enabled: bool,
    vector_store: VectorStore,
    profile: ProfileContext,
    critic_system: str,
    authoritative_collection: str,
    remediation_system: str,
    facts_system: str = "",
    cancel: asyncio.Event | None = None,
) -> Outcome | _Cancelled | None:
    s = get_settings()
    async with sem:
        # A stop request is honoured BETWEEN turns: the turns already in flight
        # finish (their findings are kept), the ones still waiting on the
        # semaphore are skipped.
        if _stopped(cancel):
            return CANCELLED
        # Each analysed conversation is its own Debug Console "turn": the analysis/judge
        # LLM calls and novelty/grounding vector searches auto-record inside this scope.
        # `profile_id` MUST be passed — `debug_interactions` is profile-scoped and the
        # Debug Console filters on it, so a NULL leaves every Review turn invisible to
        # any admin who has a profile selected in the scope selector (only global scope,
        # which applies no filter, would show them).
        debug_turn_id = f"loop:{run_id or 'dry'}:{bundle.turn_id}"
        with debug_scope(
            debug_turn_id,
            s.loop_system_user_id,
            f"loop-run-{run_id or 'dry'}",
            debug_enabled,
            profile_id=profile.id,
        ):
            await record_debug_interaction(
                kind="decision",
                name="loop_analysis",
                request={
                    "question": bundle.user_message[:1000],
                    "orig_turn_id": bundle.turn_id,
                    "tools_used": bundle.tools_used,
                    "response_rating": bundle.response_rating,
                },
            )
            legal_year = resolve_legal_year(bundle, profile)

            # Assemble what the assistant actually read BEFORE any model is asked to
            # judge it. Never raises: a missing snapshot or an unreachable vector store
            # yields a `degraded` block, which the analysis prompt is told to treat as
            # "you cannot see the sources" rather than as evidence of a content gap.
            evidence = await build_evidence(
                bundle,
                vector_store=vector_store,
                profile=profile,
                cfg=cfg,
                authoritative_collection=authoritative_collection,
            )
            await record_debug_interaction(
                kind="decision",
                name="loop_evidence",
                response={
                    "completeness": evidence.completeness,
                    "used_context_chars": len(evidence.used_context),
                    "rejected_candidates": len(evidence.rejected),
                    "has_directives": bool(evidence.directives),
                    "notes": evidence.notes,
                },
            )

            analysis_a, analysis_b = await analyze(
                bundle, llm, cfg, analysis_system, evidence, profile
            )
            judged = await judge(
                bundle,
                analysis_a,
                analysis_b,
                llm,
                cfg,
                judge_system,
                evidence,
                critic_system=critic_system,
            )
            if judged is None:
                await record_debug_interaction(
                    kind="decision",
                    name="loop_verdict",
                    response={"result": "no usable analysis (both models failed)"},
                )
                return None

            route = route_for(judged)
            ck = judged.candidate_knowledge
            novelty_verdict = "n/a"

            # Stage 6: only genuine-coverage candidates get a novelty check, and it runs
            # against the AUTHORITATIVE tier only (model-collapse firewall). A "covered"
            # result means the failure was really findability → downgrade to retrieval.
            if route == "content_gap" and (ck.claim or ck.topic):
                novelty_verdict, _score = await novelty.check(ck, legal_year)
                if novelty_verdict == "covered":
                    route = "retrieval"

            # Web-search turns answer from volatile data — by default never curate them.
            # The admin can opt in (curate_web_search_gaps) to still queue these gaps.
            if route == "content_gap" and bundle.used_web_search and not cfg.curate_web_search_gaps:
                route = "none"

            # Stage 4b: for turns that were actually wrong, produce the FIX — the answer
            # that should have been given, the facts a regression test can assert, and
            # (only when a standing behaviour rule is warranted) a directive written as
            # an imperative. Skipped on degraded evidence: a "correction" written without
            # the sources is written from memory.
            remediation = None
            if should_remediate(judged, route, evidence):
                remediation = await remediate(
                    bundle, judged, evidence, llm, cfg, remediation_system
                )

            candidate_json = ck.model_dump() if (ck.claim or ck.topic) else None
            finding = Finding(
                turn_id=bundle.turn_id,
                session_id=bundle.session_id,
                user_id=bundle.user_id,
                content_hash=bundle.content_hash,
                assessment=judged.answer_assessment,
                failure_mode=judged.failure_mode,
                route=route,
                novelty=novelty_verdict,
                synthesis_confidence=judged.synthesis_confidence,
                candidate=candidate_json,
                evidence=(judged.rationale or "")[:4000],
                triage=bundle.triage_decision or None,
                analysis_a=analysis_a.model_dump() if analysis_a else None,
                analysis_b=analysis_b.model_dump() if analysis_b else None,
                judge=judged.model_dump(),
                evidence_completeness=evidence.completeness,
                remediation=remediation.model_dump() if remediation else None,
                remediation_kind=remediation.remediation_kind if remediation else None,
                question=bundle.user_message_analysis[:300],
            )

            # Stage 7→8: a surviving content gap is grounded against the authoritative
            # tier and queued as a review candidate. Excellent answers / confirmed errors
            # become golden tests. Neither writes to the KB here.
            candidate_row: dict | None = None
            golden_row: dict | None = None
            auto_ok = False
            if finding.route == "content_gap" and (ck.claim or ck.topic):
                gr = await grounding.ground(ck, legal_year)
                candidate_row = build_candidate(finding, gr, models_used=models_used, run_id=run_id)
                auto_ok = auto_insert_eligible(
                    enabled=cfg.auto_insert_enabled,
                    grounding_status=gr.status,
                    stakes=candidate_row["stakes"],
                    agreement=judged.agreement,
                    confidence=judged.synthesis_confidence,
                    min_confidence=cfg.auto_insert_min_confidence,
                    entailment=gr.entailment,
                    evidence_completeness=evidence.completeness,
                )
            # A confirmed-correct answer gets its regression assertions from the
            # cheap facts pass (remediation only runs for wrong answers). Not
            # stored on the finding — it is not a fix.
            facts = None
            if (
                judged.answer_assessment == "correct"
                and remediation is None
                and evidence.is_usable
                and facts_system
            ):
                facts = await extract_facts(bundle, evidence, llm, cfg, facts_system)
            if is_golden_candidate(judged, remediation):
                # The ANALYSIS copies: identifiers masked, amounts kept. The
                # persistence-safe copy masks money as `[AMOUNT_1]`, and a golden
                # question with a redaction token in it cannot be re-asked.
                golden_row = build_golden(
                    finding,
                    bundle.user_message_analysis,
                    bundle.response_analysis,
                    legal_year,
                    remediation=remediation or facts,
                )

            await record_debug_interaction(
                kind="decision",
                name="loop_verdict",
                response={
                    "assessment": finding.assessment,
                    "failure_mode": finding.failure_mode,
                    "route": finding.route,
                    "novelty": finding.novelty,
                    "synthesis_confidence": finding.synthesis_confidence,
                    "candidate_queued": candidate_row is not None,
                    "auto_insert_eligible": auto_ok,
                    "golden_test": golden_row is not None,
                },
            )
            return Outcome(
                finding=finding, candidate=candidate_row, golden=golden_row, auto_insert_ok=auto_ok
            )


def _run_scope(run_id, profile_id: str):
    """The run's own scope for its calls outside a turn (triage, cluster labels): debug
    recording off, but the gateway sees the agent (tag ``profile``) and the run as the
    trace — the turns' analyses carry ``loop:<run>:<turn>`` instead."""
    return debug_scope(
        f"loop:{run_id or 'dry'}",
        get_settings().loop_system_user_id,
        f"loop-run-{run_id or 'dry'}",
        False,
        profile_id=profile_id,
    )


async def run_loop(
    *,
    date: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    cfg: LoopConfig | None = None,
    profile_id: str = DEFAULT_PROFILE_ID,
    turn_id: str | None = None,
    include_dismissed: bool = False,
    cancel: asyncio.Event | None = None,
) -> RunResult:
    """Run the improvement loop for one profile.

    `cancel` is the admin's stop switch (set by the worker when the request row's
    `cancel_requested` flips). It is checked between stages and between turns —
    never mid-call — so a stopped run ends with `status="cancelled"`, keeps every
    finding it had already produced, and skips clustering and the closing passes.

    `turn_id` turns this into a **single-turn review**: exactly that turn is
    collected (the window is ignored) and triage is skipped. The caller is
    expected to pass `force=True` as well, since a turn a previous run already
    analysed would otherwise be filtered out as idempotent.

    Turns a reviewer marked `dismissed` are dropped even under `force` (that is the
    point of a dismissal) unless `include_dismissed` is set. An explicit single-turn
    review always wins — an admin who asked for THIS turn gets it.
    """
    s = get_settings()
    run_date = date or datetime.now(UTC).strftime("%Y-%m-%d")

    await ensure_tables()

    # Load the runtime config fresh for THIS run (the worker is long-running, so the
    # env singleton would hide UI changes). Caller may pass a pre-loaded cfg.
    if cfg is None:
        cfg = await get_loop_config(None, profile_id=profile_id)
    window_start, window_end = _window(date, cfg.lookback_hours)

    # The run's profile: its scope text is rendered into every loop prompt (the
    # loop reviews each agent against ITS domain), and its collections/legal year
    # scope the checkers below.
    profile = await get_profile(profile_id) or env_profile_context()
    topic = profile_topic_description(profile)

    # Effective (admin override → code default) prompts for the loop's LLM stages,
    # loaded fresh per run from the runtime agent-prompts registry and rendered
    # with this profile's scope.
    prompts = await get_effective_prompts(None, profile_id=profile_id)

    def _prompt(key: str, default: str) -> str:
        return render_loop_prompt(prompts.get(key), default, topic_description=topic)

    triage_system = _prompt("loop_triage", LOOP_TRIAGE_SYSTEM)
    analysis_system = _prompt("loop_analysis", LOOP_ANALYSIS_SYSTEM)
    judge_system = _prompt("loop_judge", LOOP_JUDGE_SYSTEM)
    critic_system = _prompt("loop_critic", LOOP_CRITIC_SYSTEM)
    remediation_system = _prompt("loop_remediation", LOOP_REMEDIATION_SYSTEM)
    facts_system = _prompt("loop_facts", LOOP_FACTS_SYSTEM)
    cluster_system = _prompt("loop_cluster", LOOP_CLUSTER_SYSTEM)
    entailment_system = _prompt("loop_entailment", LOOP_ENTAILMENT_SYSTEM)

    # When debug audit is on, each analysed conversation records a full step trace
    # (analysis/judge LLM calls + novelty/grounding searches) into the Debug Console.
    debug_enabled = await is_debug_audit_enabled(None)

    llm = LLMClient()
    embeddings = EmbeddingsClient()
    vector_store = VectorStore()
    # Both checkers search the AUTHORITATIVE tier of THIS profile — without the
    # profile they resolve the default profile's collection, so a non-default agent
    # would get its novelty/grounding verdicts from someone else's KB.
    novelty = NoveltyChecker(embeddings, vector_store, cfg, profile=profile)
    # The grounding checker also runs the entailment verifier, so it needs the LLM
    # and the effective entailment prompt (override → code default) like every other
    # loop stage.
    grounding = GroundingChecker(
        embeddings,
        vector_store,
        cfg,
        profile=profile,
        llm=llm,
        entailment_system=entailment_system,
    )
    # Resolve the model panel ONCE and record what it actually is. `models_used` must
    # list only models that will be called — in single mode there is no analysis B,
    # and stamping a phantom model on every candidate's provenance would misrepresent
    # how the claim was reached.
    # One DB/Redis round-trip for the whole run, not one per analysed turn.
    authoritative_collection = await resolve_authoritative_collection(profile)

    panel = cfg.resolve_panel()
    models_used = [m for k, m in panel.models.items() if k != "triage"]
    if panel.is_degraded:
        log.warning(
            "loop_panel_degraded",
            profile_id=profile_id,
            requested=panel.mode_requested,
            effective=panel.mode_effective,
            reason=panel.degraded_reason,
        )

    run_id = await report_mod.start_run(
        run_date, dry_run, window_start, window_end, profile_id=profile_id
    )
    stats: dict = {
        # Surfaced in the report + run-detail modal so a silently-single run is never
        # mistaken for a corroborated one.
        "analysis_mode_requested": panel.mode_requested,
        "analysis_mode": panel.mode_effective,
        "analysis_degraded_reason": panel.degraded_reason,
        "judge_independent": panel.judge_independent,
        "models_used": models_used,
    }
    findings: list[Finding] = []
    status = "done"
    error: str | None = None

    try:
        # All loop LLM calls share one usage scope; cost is recorded once at the
        # end. The call counters separate "cut off" / "unparseable" / "proxy down"
        # so the report can say which, instead of one opaque `errors: N`.
        with usage_scope() as acc, call_counters() as counters:
            bundles = await collect_conversations(
                window_start,
                window_end,
                max_conversations=cfg.max_conversations,
                profile_id=profile_id,
                turn_ids=[turn_id] if turn_id else None,
                redaction_packs=cfg.redaction_packs,
            )
            stats["collected"] = len(bundles)

            # A forced re-run re-analyses every turn in the window (overwriting prior
            # findings) — used by the admin "Re-run" action after a config change.
            if force:
                skipped = 0
            else:
                existing = await report_mod.existing_finding_hashes(
                    [b.turn_id for b in bundles], profile_id=profile_id
                )
                bundles, skipped = _filter_idempotent(bundles, existing)
            stats["skipped_idempotent"] = skipped

            # Dismissed turns are dropped even under `force` — a dismissal exists to
            # stop a false positive recurring, and "Re-run" is exactly where it would
            # otherwise come back. An explicit single-turn review overrides this.
            if not include_dismissed and not turn_id:
                dismissed = await report_mod.dismissed_turns(
                    [b.turn_id for b in bundles], profile_id=profile_id
                )
                if dismissed:
                    before = len(bundles)
                    bundles = [b for b in bundles if b.turn_id not in dismissed]
                    stats["skipped_dismissed"] = before - len(bundles)

            if turn_id:
                # Triage is the cost lever for a whole window. An admin who asked for
                # THIS turn must never get "0 findings — triage dropped it", so a
                # single-turn review goes straight to analysis.
                selected, triage_stats = list(bundles), {"skipped": "single_turn"}
                stats["single_turn"] = True
            elif _stopped(cancel):
                selected, triage_stats = [], {"skipped": "cancelled"}
            else:
                # A run-level scope, recording off: only so the triage calls carry the
                # profile and the run (trace) to the gateway, like the per-turn analysis.
                with _run_scope(run_id, profile_id):
                    selected, triage_stats = await triage(
                        bundles, llm, cfg, triage_system, cancel=cancel
                    )
            stats["triaged_in"] = len(selected)
            stats["triaged_out"] = len(bundles) - len(selected)
            stats["triage_detail"] = triage_stats

            sem = asyncio.Semaphore(max(1, cfg.concurrency))
            results = await asyncio.gather(
                *[
                    _analyse_one(
                        b,
                        llm,
                        novelty,
                        grounding,
                        models_used,
                        run_id,
                        sem,
                        cfg,
                        analysis_system,
                        judge_system,
                        debug_enabled,
                        vector_store,
                        profile,
                        critic_system,
                        authoritative_collection,
                        remediation_system,
                        facts_system,
                        cancel,
                    )
                    for b in selected
                ],
                return_exceptions=True,
            )

            errors = 0
            skipped_cancelled = 0
            outcomes: list[Outcome] = []
            for r in results:
                if isinstance(r, Exception):
                    log.warning("loop_analyse_one_failed", error=str(r))
                    errors += 1
                elif r is CANCELLED:
                    skipped_cancelled += 1
                elif r is None:
                    errors += 1
                else:
                    outcomes.append(r)
                    findings.append(r.finding)
            stats["errors"] = errors
            if skipped_cancelled:
                stats["skipped_cancelled"] = skipped_cancelled
            stats.update(counters.as_stats())

            candidates = sum(1 for o in outcomes if o.candidate)
            goldens = sum(1 for o in outcomes if o.golden)
            stats["candidates"] = candidates
            stats["golden_tests"] = goldens

            # Persist findings always; candidates/golden only on a real (non-dry) run.
            auto_inserted = 0
            for o in outcomes:
                await report_mod.persist_finding(run_id, o.finding, profile_id=profile_id)
                if not dry_run:
                    if o.candidate:
                        # Phase 3: flag-gated auto-insert for eligible candidates;
                        # everything else stays pending for human review. Respect a
                        # prior decision — never resurrect a rejected/inserted gap.
                        if o.auto_insert_ok:
                            prior = await report_mod.candidate_status(
                                o.candidate["content_hash"], profile_id=profile_id
                            )
                            if should_auto_insert(prior):
                                source_id = await insert_curated(o.candidate, profile_id=profile_id)
                                if source_id:
                                    o.candidate["status"] = "inserted"
                                    o.candidate["needs_human_review"] = False
                                    o.candidate["curated_source_id"] = source_id
                                    auto_inserted += 1
                        await report_mod.upsert_candidate(o.candidate, profile_id=profile_id)
                    if o.golden:
                        await report_mod.upsert_golden(o.golden, run_id, profile_id=profile_id)
            stats["auto_inserted"] = auto_inserted

            # Stage 9: group the findings into recurring problems. Runs AFTER
            # persist (it needs the row ids) and only on a real run — which is also
            # why `cluster_id` must never appear in `persist_finding`'s upsert
            # `set_`: that would null the previous run's assignment every night.
            if run_id is not None and not _stopped(cancel):
                finding_ids = await report_mod.finding_ids_by_turn(
                    [f.turn_id for f in findings], profile_id=profile_id
                )
                with _run_scope(run_id, profile_id):
                    stats["cluster_detail"] = await cluster_run(
                        run_id=run_id,
                        profile_id=profile_id,
                        findings=findings,
                        finding_ids=finding_ids,
                        embeddings=embeddings,
                        llm=llm,
                        cfg=cfg,
                        system=cluster_system,
                    )

        # Hand the run's accumulator to record_usage_events (one synthetic turn id per
        # run, attributed to the system principal). It writes only web-search rows;
        # the loop's LLM calls and their cost are the gateway's record.
        if not dry_run:
            try:
                await record_usage_events(
                    user_id=s.loop_system_user_id,
                    session_id=f"loop:{run_date}",
                    turn_id=f"run:{run_id}",
                    accumulator=acc,
                    profile_id=profile_id,
                )
            except Exception as e:  # pragma: no cover - usage is best-effort
                log.warning("loop_usage_record_failed", error=str(e))

    except Exception as e:
        status = "failed"
        error = str(e)
        log.error("loop_run_failed", error=str(e), exc_info=True)

    if status == "done" and _stopped(cancel):
        # Stopped by an admin. What was analysed is persisted; the run says so
        # rather than passing as a small but complete one.
        status = "cancelled"
        error = "stopped by an admin"
        stats["cancelled"] = True

    report_md = report_mod.build_report(run_date, stats, findings, dry_run=dry_run)
    await report_mod.finish_run(
        run_id, status=status, stats=stats, report_md=report_md, error=error
    )
    log.info(
        "loop_run_complete",
        run_id=run_id,
        status=status,
        findings=len(findings),
        **{k: v for k, v in stats.items() if isinstance(v, int)},
    )
    return RunResult(
        run_id=run_id,
        report_md=report_md,
        stats=stats,
        findings=findings,
        status=status,
        error=error,
    )
