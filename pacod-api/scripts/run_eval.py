"""Run the agent against the golden set and report pass/fail per case.

Tests three properties:
  1. Topic relevance verdict matches expectation.
  2. Language guard accepts/rejects per expectation.
  3. For accepted on-topic cases, the rendered answer contains every
     `expected_facts` substring (case-insensitive) and none of `forbidden`.

This is a thin smoke-eval, not a Ragas-grade harness — but it catches the
most common regressions (wrong rate, wrong language, wrong topic verdict).

Every case is asked of ONE profile (agent), ``--profile`` (default: the default
profile); its id is sent with each chat request.

Two golden-case sources (select with --source):
  - yaml (default): the profile's curated data/profiles/<profile>/golden_qa.yaml.
  - db: the `golden_tests` table mined from production traffic by the improvement
    loop. Each DB case re-asks the question and checks the answer still cites the
    same primary-law references (the loop stores them in `citations`).
  - both: yaml + db.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

from app.core.db_models import DEFAULT_PROFILE_ID
from app.core.logging import configure_logging, get_logger

PROFILES_DATA_ROOT = Path(__file__).resolve().parent.parent / "data" / "profiles"
API_BASE = "http://localhost/api/v1"
TOKEN_TEMPLATE = "dev-token-test_user"


@dataclass
class CaseResult:
    case_id: str
    category: str
    passed: bool
    detail: str


def golden_path(profile_id: str) -> Path:
    return PROFILES_DATA_ROOT / profile_id / "golden_qa.yaml"


def load_cases(profile_id: str = DEFAULT_PROFILE_ID) -> list[dict]:
    path = golden_path(profile_id)
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw.get("cases", [])


def _json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(x) for x in parsed] if isinstance(parsed, list) else []


async def load_db_cases(profile_id: str | None = None, kind: str | None = None) -> list[dict]:
    """Build eval cases from the `golden_tests` table (mined by the loop).

    Asserts on `expected_facts` / `forbidden_facts` — the short, matchable fragments
    the remediation stage extracts. Previously this read only `citations`, so a
    golden test's entire assertion was "the answer still mentions these citation
    strings", and `expected` was written but never read at all.

    Note `expected` is deliberately NOT used as a substring assertion: it is
    reference prose — for a `good_answer` the analysis copy of the answer
    (identifiers masked as `[EMAIL_1]` …, amounts kept) — and would not match a new
    answer.

    Falls back to `citations` for rows written before the remediation stage existed,
    which preserves the old (weak) behaviour rather than silently passing them.
    """
    from sqlalchemy import select

    from app.core.db import session_scope
    from app.core.db_models import GoldenTest

    stmt = select(GoldenTest)
    if profile_id is not None:
        stmt = stmt.where(GoldenTest.profile_id == profile_id)
    if kind is not None:
        stmt = stmt.where(GoldenTest.kind == kind)
    async with session_scope() as db:
        rows = (await db.execute(stmt)).scalars().all()

    cases: list[dict] = []
    for r in rows:
        expected_facts = _json_list(r.expected_facts) or _json_list(r.citations)
        cases.append(
            {
                "id": f"golden-db-{r.id}",
                "category": r.kind,  # good_answer | fixed_error → on-topic evaluation
                "question": r.question,
                "expected_facts": expected_facts,
                "forbidden": _json_list(r.forbidden_facts),
            }
        )
    return cases


async def run_one(
    client: httpx.AsyncClient, case: dict, profile_id: str = DEFAULT_PROFILE_ID
) -> CaseResult:
    cid = case["id"]
    category = case["category"]
    payload = {
        "message": case["question"],
        "session_id": f"eval-{cid}",
        "profile_id": profile_id,
    }
    headers = {
        "Authorization": f"Bearer {TOKEN_TEMPLATE}",
        "Content-Type": "application/json",
    }

    # Use POST with stream so we receive the SSE events.
    full_text = ""
    error_code: str | None = None
    try:
        async with client.stream(
            "POST", f"{API_BASE}/chat", json=payload, headers=headers, timeout=90.0
        ) as resp:
            if resp.status_code != 200:
                # Read full body to learn the error code.
                body = await resp.aread()
                try:
                    data = json.loads(body)
                    detail = data.get("detail", {})
                    error_code = detail.get("code") if isinstance(detail, dict) else None
                except json.JSONDecodeError:
                    error_code = f"http_{resp.status_code}"
            else:
                async for raw_line in resp.aiter_lines():
                    if not raw_line or not raw_line.startswith("data:"):
                        continue
                    data_str = raw_line[len("data:") :].strip()
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    if "text" in data:
                        full_text += data["text"]
                    if "code" in data and "message_en" in data:
                        error_code = data["code"]
    except httpx.HTTPError as e:
        return CaseResult(cid, category, False, f"http_error:{e}")

    # Evaluate expectations.
    if category == "language_reject":
        if error_code == "unsupported_language":
            return CaseResult(cid, category, True, "rejected as expected")
        return CaseResult(
            cid, category, False, f"expected lang reject, got {error_code or 'success'}"
        )

    if category == "off_topic":
        if error_code == "off_topic":
            return CaseResult(cid, category, True, "rejected as expected")
        return CaseResult(
            cid, category, False, f"expected off_topic reject, got {error_code or 'success'}"
        )

    # On-topic categories.
    if error_code:
        return CaseResult(cid, category, False, f"unexpected error: {error_code}")

    text_lower = full_text.lower()
    missing = [f for f in case.get("expected_facts", []) if f.lower() not in text_lower]
    forbidden_hit = [f for f in case.get("forbidden", []) if f.lower() in text_lower]

    if missing:
        return CaseResult(cid, category, False, f"missing facts: {missing}")
    if forbidden_hit:
        return CaseResult(cid, category, False, f"forbidden present: {forbidden_hit}")
    return CaseResult(cid, category, True, "ok")


async def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m scripts.run_eval")
    parser.add_argument(
        "--source",
        choices=["yaml", "db", "both"],
        default="yaml",
        help="Golden-case source: curated yaml (default), the loop's golden_tests db, or both.",
    )
    parser.add_argument(
        "--profile",
        default=DEFAULT_PROFILE_ID,
        help=(
            "Profile (agent) to evaluate: its golden_qa.yaml, its db cases, and the "
            f"profile every question is asked of (default: {DEFAULT_PROFILE_ID})."
        ),
    )
    parser.add_argument(
        "--kind",
        choices=["good_answer", "fixed_error"],
        default=None,
        help="Scope db cases to one kind. `fixed_error` re-checks answers that were wrong.",
    )
    args = parser.parse_args()

    configure_logging()
    log = get_logger("scripts.run_eval")
    cases: list[dict] = []
    if args.source in ("yaml", "both"):
        yaml_cases = load_cases(args.profile)
        if not yaml_cases:
            log.warning("eval_no_yaml_cases", path=str(golden_path(args.profile)))
        cases += yaml_cases
    if args.source in ("db", "both"):
        cases += await load_db_cases(profile_id=args.profile, kind=args.kind)

    # A case with nothing to assert passes vacuously — that is how a regression set
    # rots into a green light that means nothing. Report it rather than hide it.
    vacuous = [
        c["id"]
        for c in cases
        if not c.get("expected_facts") and c["category"] in ("good_answer", "fixed_error")
    ]
    if vacuous:
        log.warning("eval_cases_without_assertions", count=len(vacuous), ids=vacuous[:10])
        print(
            f"⚠ {len(vacuous)} golden case(s) have NO assertable facts — they pass "
            f"vacuously. These predate the remediation stage or it produced none."
        )
    log.info("eval_starting", total=len(cases), source=args.source, profile_id=args.profile)

    results: list[CaseResult] = []
    async with httpx.AsyncClient() as client:
        # Run sequentially to keep load light; tests are fast.
        for case in cases:
            res = await run_one(client, case, args.profile)
            results.append(res)
            status = "PASS" if res.passed else "FAIL"
            print(f"[{status}] {res.case_id:35s} ({res.category}) — {res.detail}")

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    print(f"\nTotal: {len(results)}  Passed: {passed}  Failed: {failed}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
