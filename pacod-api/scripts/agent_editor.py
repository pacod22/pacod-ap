"""Visual agent editor maintenance (see app/agent_editor).

    python -m scripts.agent_editor import        # draft + version 1 for every profile without one
    python -m scripts.agent_editor import --force <profile_id>   # rebuild one draft from current config
    python -m scripts.agent_editor adopt         # hand every unmanaged agent to the editor

Adopting is behaviour-neutral (the published version reproduces the current
config) but from then on the legacy config pages refuse writes to the settings
the version owns — edit those in the agent editor.
"""

from __future__ import annotations

import argparse
import asyncio

from app.agent_editor import service
from app.api.dependencies import get_redis


async def _main(args: argparse.Namespace) -> None:
    if args.command == "import":
        if args.profile_id:
            row = await service.import_agent(args.profile_id, force=args.force, actor="cli")
            print(f"imported {row['profile_id']} (draft revision {row['draft_revision']})")
        else:
            print(await service.ensure_agent_definitions(adopt=False, redis=get_redis()))
    elif args.command == "adopt":
        print(f"adopted {await service.adopt_all(redis=get_redis())} agent(s)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("profile_id", nargs="?")
    imp.add_argument("--force", action="store_true")
    sub.add_parser("adopt")
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
