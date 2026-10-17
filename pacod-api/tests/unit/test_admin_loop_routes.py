"""Pure pieces of the loop admin routes, compiled without a database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from admin_api.routes.kb_candidates import golden_upsert_statement
from admin_api.routes.loop_config import disposition_predicate, queue_status_from


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


# ---- hand-added golden tests ----------------------------------------


def test_hand_added_golden_test_writes_facts_and_marks_itself_human_owned():
    stmt = golden_upsert_statement(
        profile_id="p1",
        source_turn_id="t1",
        question="Q",
        expected="A",
        expected_facts=["166 000"],
        forbidden_facts=["50 000"],
        citations=["чл. 96"],
        kind="fixed_error",
        legal_year=2026,
        edited_by="admin-1",
    )
    sql = _sql(stmt)
    on_conflict = sql.split("ON CONFLICT")[1]
    # Every human-supplied column is replaced on conflict — including the facts
    # the eval asserts (they used to be dropped) and the ownership stamp.
    for col in ("expected_facts", "forbidden_facts", "edited_by", "expected", "question"):
        assert f"{col} = " in on_conflict, col
    # No `WHERE edited_by IS NULL` on the ADMIN route: a second edit must apply.
    assert "WHERE" not in on_conflict.split("RETURNING")[0]


# ---- manual stop ----------------------------------------------------


def test_stop_cancels_a_queued_request_and_only_flags_a_running_one():
    from admin_api.routes.loop_config import cancel_statements

    cancel_queued, flag_running = cancel_statements(7, datetime.now(UTC))
    q = _sql(cancel_queued)
    assert "loop_run_requests.status = " in q  # conditional on `requested`
    assert "SET status=" in q and "finished_at=" in q
    r = _sql(flag_running)
    assert "SET cancel_requested=" in r
    assert "finished_at" not in r  # the WORKER finishes a running request


# ---- findings filters -----------------------------------------------


def test_unreviewed_means_null_disposition():
    assert "IS NULL" in _sql(disposition_predicate("unreviewed"))
    assert "IS NULL" not in _sql(disposition_predicate("dismissed"))


# ---- queue status ---------------------------------------------------


def _settings(**over):
    base = {"loop_run_stale_hours": 6, "loop_worker_poll_seconds": 30}
    base.update(over)
    return SimpleNamespace(**base)


def test_queue_status_empty():
    st = queue_status_from([], [], now=datetime.now(UTC), settings=_settings())
    assert st.requested == 0 and st.running == 0
    assert not st.stale and not st.waiting_too_long
    assert st.oldest_requested_age_seconds is None


def test_queue_status_flags_a_request_nobody_claims():
    now = datetime.now(UTC)
    req = SimpleNamespace(requested_at=now - timedelta(minutes=10))
    st = queue_status_from([req], [], now=now, settings=_settings())
    assert st.requested == 1
    assert st.oldest_requested_age_seconds == 600
    assert st.waiting_too_long
    fresh = SimpleNamespace(requested_at=now - timedelta(seconds=20))
    assert not queue_status_from([fresh], [], now=now, settings=_settings()).waiting_too_long


def test_queue_status_flags_a_run_older_than_the_stale_threshold():
    now = datetime.now(UTC)
    running = SimpleNamespace(started_at=now - timedelta(hours=7))
    st = queue_status_from([], [running], now=now, settings=_settings())
    assert st.running == 1 and st.stale
    live = SimpleNamespace(started_at=now - timedelta(minutes=3))
    assert not queue_status_from([], [live], now=now, settings=_settings()).stale
