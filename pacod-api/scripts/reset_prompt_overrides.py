"""Reset stale per-profile prompt overrides back to the code default.

Why this exists: `PROMPT_SPECS` holds the code default for every editable phase
prompt, and only *overrides* are stored (`app_settings` row `prompt.<key>`, one per
profile). So when a default changes, the change reaches every profile that never
customised that prompt — and silently reaches NO profile that did. Their stored
text keeps whatever it said when it was written, indefinitely.

That is the situation after each review-process overhaul. Most recently every
`loop_*` prompt became a profile-neutral FORMAT template carrying the profile's
scope as `{topic_description}`; an older plain-text override has no placeholder
and unescaped JSON braces, so it fails validation at read time and the loop
silently uses the code default anyway — while the Prompts panel still shows the
stale text as "overridden". Resetting makes the panel tell the truth.

Safe by default: it PREVIEWS what it would delete and changes nothing unless you
pass `--yes`. Deleting an override is not destructive to the prompt itself — the
code default takes over immediately — but it does discard admin-authored text, so
the preview prints enough of each override to decide whether it's worth re-applying
on top of the new default.

    # see what would change (default — writes nothing)
    make reset-prompt-overrides
    docker compose exec api python -m scripts.reset_prompt_overrides

    # actually reset
    make reset-prompt-overrides ARGS=--yes
    docker compose exec api python -m scripts.reset_prompt_overrides --yes

(Run it in the `api` container: the loop worker image ships no `scripts/` and its
entrypoint is `python -m improvement_loop`, which would swallow the module name.)

    # one profile / other keys
    ... --profile taxes --key loop_analysis --key loop_judge
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.db import session_scope
from app.core.db_models import AppSetting
from app.core.logging import configure_logging, get_logger
from app.services.agent_prompts import PROMPT_SPECS, reset_prompt
from app.services.profiles import list_profiles

log = get_logger("scripts.reset_prompt_overrides")

# Every loop prompt: they all changed shape (format templates) in the same overhaul.
DEFAULT_KEYS = (
    "loop_triage",
    "loop_analysis",
    "loop_judge",
    "loop_critic",
    "loop_remediation",
    "loop_facts",
    "loop_cluster",
    "loop_entailment",
)
_SETTING_PREFIX = "prompt."
_PREVIEW_CHARS = 220


async def _existing_overrides(
    keys: list[str], profile_ids: list[str]
) -> list[tuple[str, str, str]]:
    """(profile_id, key, stored_text) for every override that actually exists."""
    setting_keys = [f"{_SETTING_PREFIX}{k}" for k in keys]
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(AppSetting.profile_id, AppSetting.key, AppSetting.value).where(
                    AppSetting.profile_id.in_(profile_ids),
                    AppSetting.key.in_(setting_keys),
                )
            )
        ).all()
    return [(pid, key[len(_SETTING_PREFIX) :], value or "") for pid, key, value in rows]


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.reset_prompt_overrides")
    parser.add_argument(
        "--key",
        action="append",
        default=None,
        help=f"Prompt key to reset (repeatable). Default: {', '.join(DEFAULT_KEYS)}.",
    )
    parser.add_argument(
        "--profile",
        action="append",
        default=None,
        help="Profile id to reset (repeatable). Default: every active profile.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually delete the overrides. Without this the run is a preview.",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    configure_logging()

    known = {s.key for s in PROMPT_SPECS}
    keys = list(args.key or DEFAULT_KEYS)
    unknown = [k for k in keys if k not in known]
    if unknown:
        print(f"Unknown prompt key(s): {unknown}\nKnown keys: {sorted(known)}")
        return 2

    if args.profile:
        profile_ids = list(args.profile)
    else:
        profile_ids = [p.id for p in await list_profiles(active_only=True)]
    if not profile_ids:
        print("No profiles found.")
        return 0

    overrides = await _existing_overrides(keys, profile_ids)
    if not overrides:
        print(
            f"No stored overrides for {keys} across {len(profile_ids)} profile(s) — "
            "every profile already uses the code default. Nothing to do."
        )
        return 0

    print(f"Found {len(overrides)} override(s) that would be reset to the code default:\n")
    for profile_id, key, text in overrides:
        preview = " ".join(text.split())[:_PREVIEW_CHARS]
        print(f"  · profile={profile_id}  prompt={key}  ({len(text)} chars)")
        print(f"      {preview}{'…' if len(text) > _PREVIEW_CHARS else ''}\n")

    if not args.yes:
        print(
            "PREVIEW ONLY — nothing was changed. Re-run with --yes to reset.\n"
            "The code default takes over immediately; if any of the text above is "
            "worth keeping, re-apply it on top of the new default afterwards."
        )
        return 0

    for profile_id, key, _text in overrides:
        await reset_prompt(key, redis=None, profile_id=profile_id)
        print(f"reset: profile={profile_id} prompt={key}")

    # The loop reads effective prompts fresh per run, and the Redis cache entry is
    # 60s TTL — but bust it explicitly anyway so the admin panel reflects the reset
    # without a wait. `reset_prompt(redis=None)` skips that, hence this note: if a
    # Redis client is available in your context, pass it instead.
    print(
        f"\nDone — {len(overrides)} override(s) reset. The Redis prompt cache "
        "(config:agent_prompts:<profile>, 60s TTL) expires on its own; the next loop "
        "run loads the effective prompts fresh regardless."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
