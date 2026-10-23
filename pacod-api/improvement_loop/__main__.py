"""CLI entrypoint for the continuous-improvement loop.

    python -m improvement_loop run [--date YYYY-MM-DD] [--dry-run]

Runs standalone — it only needs network reach to Postgres, Qdrant, the LLM gateway, and
the embeddings service (URLs from the same Settings/env as the app). It does NOT
require the `api` container to be up. In the stack it runs as the `improvement-loop`
job container:  docker compose run --rm improvement-loop run
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.db_models import DEFAULT_PROFILE_ID
from app.core.logging import configure_logging, get_logger
from improvement_loop.meta_eval import run_meta_eval
from improvement_loop.runner import run_loop
from improvement_loop.worker import run_worker

log = get_logger(__name__)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m improvement_loop")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run one triage pass over the day's conversations.")
    run.add_argument(
        "--date",
        default=None,
        help="UTC day to analyse (YYYY-MM-DD); default = last lookback window.",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyse + print the report but write nothing to the DB.",
    )
    run.add_argument(
        "--turn-id",
        default=None,
        help="Analyse only this turn (ignores the window, skips triage, forces re-analysis).",
    )
    run.add_argument(
        "--profile",
        default=DEFAULT_PROFILE_ID,
        help="Profile (agent) to analyse; default = the default profile.",
    )
    meta = sub.add_parser(
        "meta-eval", help="Measure whether inserted curated entries helped (Stage 10)."
    )
    meta.add_argument(
        "--dry-run", action="store_true", help="Report only; never retire entries even if enabled."
    )
    meta.add_argument(
        "--profile",
        default=DEFAULT_PROFILE_ID,
        help="Profile (agent) to evaluate; every query is profile-scoped.",
    )
    sub.add_parser(
        "worker", help="Run the always-on worker: drains the run-request queue + daily schedule."
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    result = await run_loop(
        date=args.date,
        dry_run=args.dry_run,
        profile_id=args.profile,
        turn_id=args.turn_id,
        force=bool(args.turn_id),
    )
    # The report goes to stdout so an operator/cron captures it directly.
    print(result.report_md)
    return 0


async def _meta_eval(args: argparse.Namespace) -> int:
    result = await run_meta_eval(profile_id=args.profile, dry_run=args.dry_run)
    print(result.report_md)
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_logging("ap-improvement-loop")
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.command == "run":
        return asyncio.run(_run(args))
    if args.command == "meta-eval":
        return asyncio.run(_meta_eval(args))
    if args.command == "worker":
        asyncio.run(run_worker())  # runs until the container stops
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
