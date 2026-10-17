"""Guard: every ORM table that carries a `profile_id` column must be listed in
PROFILE_SCOPED_TABLES, because profile deletion cascades by iterating that tuple.

Three tables (loop_clusters, loop_fix_checks, turn_evidence) were once missing —
deleting a profile and re-creating one with the same id resurrected the old
profile's recurring-problem clusters under the new one.
"""

from __future__ import annotations

from app.core.db_models import Base
from app.services.profiles import PROFILE_SCOPED_TABLES

# Tables that carry `profile_id` as a plain attribute but are deliberately global.
# user_activity: a per-account security log; its profile_id only says which agent
# a chat turn used, and deleting an agent must not erase the user's log.
_EXEMPT: set[str] = {"user_activity"}


def _tables_with_profile_id() -> set[str]:
    return {table.name for table in Base.metadata.sorted_tables if "profile_id" in table.columns}


def test_every_scoped_table_is_cascaded():
    scoped = _tables_with_profile_id() - _EXEMPT
    missing = scoped - set(PROFILE_SCOPED_TABLES)
    assert (
        not missing
    ), f"tables with profile_id missing from PROFILE_SCOPED_TABLES: {sorted(missing)}"


def test_no_phantom_tables_listed():
    known = {t.name for t in Base.metadata.sorted_tables}
    phantom = set(PROFILE_SCOPED_TABLES) - known
    assert not phantom, f"PROFILE_SCOPED_TABLES names tables that do not exist: {sorted(phantom)}"
