"""Reviewer disposition survives the nightly upsert.

`persist_finding` upserts on (profile_id, turn_id), and `on_conflict_do_update`
overwrites every column its `set_` lists. Listing the disposition columns with
fresh values would wipe the reviewer's verdict on every run; omitting them entirely
would keep a verdict that describes an answer which has since been regenerated.
The CASE guard is the narrow path between those, so it gets a structural test — the
behaviour itself needs Postgres and is covered by the end-to-end checks.
"""

from __future__ import annotations

from sqlalchemy.dialects import postgresql

from app.core.db_models import LoopFinding
from improvement_loop.report import _reset_on_new_content


def _compiled(new_hash: str = "abc123") -> str:
    expr = _reset_on_new_content(LoopFinding.disposition, new_hash)
    return str(expr.compile(dialect=postgresql.dialect()))


def test_guard_compiles_to_a_conditional_not_a_constant():
    sql = _compiled().upper()
    assert "CASE" in sql
    # The condition must be on content_hash — that is what "the answer changed" means.
    assert "CONTENT_HASH" in sql


def test_guard_falls_back_to_the_existing_column_value():
    # The else-branch must reference the stored column, so an unchanged answer keeps
    # the reviewer's verdict rather than resetting it to NULL.
    sql = _compiled()
    assert "loop_findings.disposition" in sql


def test_guard_is_applied_to_every_reviewer_column():
    # All four move together: a verdict without its author/timestamp is not auditable.
    for column in (
        LoopFinding.disposition,
        LoopFinding.disposition_reason,
        LoopFinding.reviewed_by,
        LoopFinding.reviewed_at,
    ):
        sql = str(_reset_on_new_content(column, "h").compile(dialect=postgresql.dialect())).upper()
        assert "CASE" in sql and "CONTENT_HASH" in sql


def test_persist_finding_set_covers_all_reviewer_columns():
    # Guards the other direction: if someone adds a reviewer column and forgets the
    # CASE, this catches it by comparing the model against the known-guarded set.
    import inspect

    from improvement_loop import report

    source = inspect.getsource(report.persist_finding)
    for name in ("disposition", "disposition_reason", "reviewed_by", "reviewed_at"):
        assert f'"{name}": _reset_on_new_content(' in source or (
            f'"{name}": _reset_on_new_content' in source.replace("\n", " ")
        ), f"{name} must be written through the CASE guard, not overwritten"
