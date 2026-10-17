"""CLI entrypoint for the newsletter worker.

    python -m newsletter_worker worker                 # always-on queue drainer + scheduler
    python -m newsletter_worker collect [--profile ID] # run one collect now (standalone)
    python -m newsletter_worker send [--profile ID]    # run one send now (standalone)

Runs standalone — it only needs network reach to Postgres, the LLM gateway, and SMTP
(URLs from the same Settings/env as the app). It does NOT require the ``api``
container. In the stack it runs as the ``newsletter-worker`` container:
    docker compose up -d newsletter-worker
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.db_models import DEFAULT_PROFILE_ID
from app.core.logging import configure_logging, get_logger
from newsletter_worker.runner import run_collect, run_send
from newsletter_worker.worker import run_worker

log = get_logger(__name__)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m newsletter_worker")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("worker", help="Run the always-on worker: drain the queue + fire the schedules.")
    c = sub.add_parser("collect", help="Run one collect pass now for a profile.")
    c.add_argument("--profile", default=DEFAULT_PROFILE_ID, help="Profile id (default: 'default').")
    s = sub.add_parser("send", help="Compile + send one issue now for a profile.")
    s.add_argument("--profile", default=DEFAULT_PROFILE_ID, help="Profile id (default: 'default').")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.command == "worker":
        asyncio.run(run_worker())  # runs until the container stops
        return 0
    if args.command == "collect":
        stats = asyncio.run(run_collect(args.profile))
        print(stats)
        return 0
    if args.command == "send":
        stats = asyncio.run(run_send(args.profile))
        print(stats)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
