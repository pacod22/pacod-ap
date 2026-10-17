"""Stage 11 — did the fix actually work?

Every other stage measures the problem. This one measures the remedy: it re-asks
the original question through the LIVE agent and asserts the expectation against
the new answer. Until this runs, "we fixed it" is an opinion.

Scope is deliberately narrow — only golden tests whose finding a reviewer marked
`fixed`, or whose cluster is `ticketed`. Re-asking every golden test nightly would
double the loop's cost to produce a number nobody acts on; re-asking the ones
someone claims to have fixed produces the one number that matters.

**Why in-process rather than over HTTP.** `scripts/run_eval.py` talks to
`/api/v1/chat` with a `dev-token-…` bearer, which `AuthGuard` rejects outside
`APP_ENV=development`. Making that work in production would mean minting a service
token (new secret, new rotation burden) or weakening the dev-token guard — and it
would couple the worker to the `api` container, which the loop deliberately avoids.
The worker image already ships all of `app/`, and `get_agent_runner()` is an
argument-less `lru_cache`d factory, so building the real agent here is cheap.

Off by default (`verification_enabled`): this issues real LLM + MCP traffic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import insert, select

from app.agent_editor.effective import resolve_effective_agent
from app.core.db import session_scope
from app.core.db_models import (
    DEFAULT_PROFILE_ID,
    GoldenTest,
    LoopCluster,
    LoopFinding,
    LoopFixCheck,
)
from app.core.logging import get_logger
from app.core.models import Language
from app.services.directives import load_active_directives
from app.services.loop_runtime_config import get_loop_config
from app.services.profiles import env_profile_context, get_profile

log = get_logger(__name__)


@dataclass
class FixCheck:
    golden_test_id: int
    question: str
    passed: bool
    missing_facts: list[str] = field(default_factory=list)
    forbidden_hits: list[str] = field(default_factory=list)
    answer: str = ""
    error: str = ""


@dataclass
class FixVerificationResult:
    checks: list[FixCheck]
    resolved_clusters: list[int]
    report_md: str

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.passed)


def _json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(x) for x in parsed] if isinstance(parsed, list) else []


async def _candidate_tests(profile_id: str, limit: int) -> list[tuple[GoldenTest, int | None]]:
    """Golden tests worth re-asking, with the cluster each one might close.

    A test qualifies when its source finding was marked `fixed`, or when it belongs
    to a cluster someone has claimed (`ticketed`). Both mean a human asserts the
    problem is addressed — which is exactly the claim this stage exists to check.
    """
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(GoldenTest, LoopFinding.cluster_id)
                .join(
                    LoopFinding,
                    (LoopFinding.turn_id == GoldenTest.source_turn_id)
                    & (LoopFinding.profile_id == GoldenTest.profile_id),
                )
                .outerjoin(LoopCluster, LoopCluster.id == LoopFinding.cluster_id)
                .where(
                    GoldenTest.profile_id == profile_id,
                    (LoopFinding.disposition == "fixed") | (LoopCluster.status == "ticketed"),
                )
                .order_by(GoldenTest.created_at.desc())
                .limit(limit)
            )
        ).all()
    return list(rows)


async def run_fix_verification(
    *,
    profile_id: str = DEFAULT_PROFILE_ID,
    run_id: int | None = None,
    runner=None,
    dry_run: bool = False,
) -> FixVerificationResult:
    """Re-ask the questions behind claimed fixes and record whether they hold."""
    cfg = await get_loop_config(None, profile_id=profile_id)
    if not getattr(cfg, "verification_enabled", False):
        return FixVerificationResult([], [], "")

    limit = int(getattr(cfg, "verification_max_cases", 25))
    pairs = await _candidate_tests(profile_id, limit)
    if not pairs:
        return FixVerificationResult([], [], "# Fix verification\n\nNothing to re-check.")

    profile = await get_profile(profile_id) or env_profile_context()
    if runner is None:
        # Imported lazily: this pulls the whole agent graph (KB retriever, MCP
        # client, web search) into the worker process, and only this stage needs it.
        from app.api.dependencies import get_agent_runner

        runner = get_agent_runner()

    # The agent AS USERS GET IT: its published agent-editor version layered over
    # app_settings (exactly the legacy config for an agent the editor does not
    # manage) — including the planner switch and the tool allowlist.
    agent = await resolve_effective_agent(profile, "prod")
    profile = agent.profile
    runtime_cfg, prompts = agent.runtime, agent.prompts
    legal_year = int(profile.legal_year or 0) or None

    checks: list[FixCheck] = []
    for gt, _cluster_id in pairs:
        expected = _json_list(gt.expected_facts) or _json_list(gt.citations)
        forbidden = _json_list(gt.forbidden_facts)
        year = gt.legal_year or legal_year or 0
        language = Language.BG if _looks_bulgarian(gt.question) else Language.EN
        try:
            # Directives are loaded per check: the whole point is to test the agent
            # AS IT IS NOW, including a directive the reviewer just activated.
            directives = await load_active_directives(
                language, year, redis=None, profile_id=profile_id
            )
            answer, _prepared = await runner.answer_once(
                user_message=gt.question,
                language=language,
                legal_year=year,
                directives=directives,
                runtime=runtime_cfg,
                prompts=prompts,
                profile=profile,
                pipeline=agent.pipeline,
                allowed_tools=agent.allowed_tools,
            )
        except Exception as e:
            log.warning("loop_fix_check_failed", golden_test_id=gt.id, error=str(e))
            checks.append(FixCheck(gt.id, gt.question, passed=False, error=str(e)[:500]))
            continue

        lowered = answer.lower()
        missing = [f for f in expected if f.strip() and f.lower() not in lowered]
        hits = [f for f in forbidden if f.strip() and f.lower() in lowered]
        # A test with nothing to assert cannot pass — it would be a green light that
        # means nothing. `expected` prose is not assertable (it is redacted).
        passed = bool(expected) and not missing and not hits
        checks.append(FixCheck(gt.id, gt.question, passed, missing, hits, answer[:8000]))

    resolved = await _persist(checks, pairs, profile_id, run_id, dry_run=dry_run)
    report = _build_report(checks, resolved)
    log.info(
        "loop_fix_verification_done",
        profile_id=profile_id,
        checked=len(checks),
        passed=sum(1 for c in checks if c.passed),
        resolved_clusters=len(resolved),
    )
    return FixVerificationResult(checks, resolved, report)


def _looks_bulgarian(text: str) -> bool:
    """Cyrillic in the question → answer in Bulgarian, as the live guard would."""
    return any("Ѐ" <= ch <= "ӿ" for ch in text or "")


async def _persist(
    checks: list[FixCheck],
    pairs: list[tuple[GoldenTest, int | None]],
    profile_id: str,
    run_id: int | None,
    *,
    dry_run: bool,
) -> list[int]:
    """Record the checks; close any cluster whose every check now passes."""
    if dry_run or not checks:
        return []

    async with session_scope() as db:
        for c in checks:
            await db.execute(
                insert(LoopFixCheck).values(
                    profile_id=profile_id,
                    golden_test_id=c.golden_test_id,
                    run_id=run_id,
                    passed=c.passed,
                    answer=c.answer or None,
                    missing_facts=json.dumps(c.missing_facts, ensure_ascii=False)
                    if c.missing_facts
                    else None,
                    forbidden_hits=json.dumps(c.forbidden_hits, ensure_ascii=False)
                    if c.forbidden_hits
                    else None,
                    error=c.error or None,
                )
            )

    # A cluster closes only when EVERY re-checked test under it passes. One green
    # test out of five is not a fixed problem.
    by_cluster: dict[int, list[bool]] = {}
    passed_by_id = {c.golden_test_id: c.passed for c in checks}
    for gt, cluster_id in pairs:
        if cluster_id and gt.id in passed_by_id:
            by_cluster.setdefault(cluster_id, []).append(passed_by_id[gt.id])

    resolved = [cid for cid, results in by_cluster.items() if results and all(results)]
    if resolved:
        from sqlalchemy import update

        async with session_scope() as db:
            await db.execute(
                update(LoopCluster)
                .where(LoopCluster.id.in_(resolved), LoopCluster.status == "ticketed")
                .values(
                    status="resolved",
                    review_notes="auto-resolved: every re-asked question now answers correctly",
                )
            )
    return resolved


def _build_report(checks: list[FixCheck], resolved: list[int]) -> str:
    passed = sum(1 for c in checks if c.passed)
    lines = [
        "# Fix verification",
        "",
        f"Re-asked {len(checks)} question(s) behind claimed fixes · "
        f"**{passed} held**, {len(checks) - passed} did not.",
        "",
    ]
    for c in checks:
        mark = "✅" if c.passed else "❌"
        detail = c.error or ""
        if not detail and c.missing_facts:
            detail = f"missing: {c.missing_facts}"
        if not detail and c.forbidden_hits:
            detail = f"the old wrong value came back: {c.forbidden_hits}"
        if not detail and not c.passed:
            detail = "no assertable facts on this golden test"
        lines.append(
            f"- {mark} `{c.golden_test_id}` {c.question[:90]}" + (f" — {detail}" if detail else "")
        )
    if resolved:
        lines += ["", f"Clusters closed by this pass: {resolved}"]
    return "\n".join(lines)
