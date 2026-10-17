"""The worker's run lifecycle: the request row must tell the truth.

`run_loop` never raises — it records a failure on `loop_runs` and returns — so
the worker used to mark every finished request `done`. And a worker that died
mid-run left its request `running` forever with nothing to reap it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from improvement_loop import worker
from improvement_loop.runner import RunResult
from improvement_loop.worker import ClaimedRequest, _claim_day, _process_one, _reap_stale_requests

pytestmark = pytest.mark.asyncio


def _claimed(**over) -> ClaimedRequest:
    kw = {"id": 7, "dry_run": False, "force": False, "run_date": None, "profile_id": "p1"}
    kw.update(over)
    return ClaimedRequest(**kw)


async def _drive(monkeypatch, result: RunResult, claimed: ClaimedRequest):
    finished, postscripts = [], []

    async def claim():
        return claimed

    async def run_loop(**_kw):
        return result

    async def finish(request_id, *, status, run_id=None, error=None):
        finished.append((request_id, status, run_id, error))

    async def meta(profile_id, run_id):
        postscripts.append("meta")

    async def verify(profile_id, run_id):
        postscripts.append("verify")

    monkeypatch.setattr(worker, "_claim_next_request", claim)
    monkeypatch.setattr(worker, "run_loop", run_loop)
    monkeypatch.setattr(worker, "_finish_request", finish)
    monkeypatch.setattr(worker, "_maybe_run_meta_eval", meta)
    monkeypatch.setattr(worker, "_maybe_verify_fixes", verify)
    assert await _process_one() is True
    return finished, postscripts


async def test_a_failed_run_marks_the_request_failed(monkeypatch):
    result = RunResult(run_id=3, report_md="", stats={}, findings=[], status="failed", error="boom")
    finished, postscripts = await _drive(monkeypatch, result, _claimed())
    assert finished == [(7, "failed", 3, "boom")]
    assert postscripts == []  # nothing to meta-evaluate or verify


async def test_a_successful_full_run_marks_done_and_runs_the_postscripts(monkeypatch):
    result = RunResult(run_id=3, report_md="", stats={}, findings=[])
    finished, postscripts = await _drive(monkeypatch, result, _claimed())
    assert finished == [(7, "done", 3, None)]
    assert postscripts == ["meta", "verify"]


async def test_single_turn_and_dry_runs_skip_the_postscripts(monkeypatch):
    result = RunResult(run_id=3, report_md="", stats={}, findings=[])
    _, postscripts = await _drive(monkeypatch, result, _claimed(turn_id="t1"))
    assert postscripts == []
    _, postscripts = await _drive(monkeypatch, result, _claimed(dry_run=True))
    assert postscripts == []


async def test_an_exception_from_run_loop_still_fails_the_request(monkeypatch):
    finished = []

    async def claim():
        return _claimed()

    async def run_loop(**_kw):
        raise RuntimeError("db gone")

    async def finish(request_id, *, status, run_id=None, error=None):
        finished.append((status, error))

    monkeypatch.setattr(worker, "_claim_next_request", claim)
    monkeypatch.setattr(worker, "run_loop", run_loop)
    monkeypatch.setattr(worker, "_finish_request", finish)
    assert await _process_one() is True
    assert finished == [("failed", "db gone")]


# ---- manual stop -----------------------------------------------------


async def test_process_one_wires_a_cancel_watcher_and_finishes_cancelled(monkeypatch):
    finished, postscripts, seen_cancel = [], [], []

    async def claim():
        return _claimed()

    async def run_loop(**kw):
        # The run "notices" the stop the way the real one does: via the event.
        seen_cancel.append(kw["cancel"])
        await kw["cancel"].wait()
        return RunResult(
            run_id=3, report_md="", stats={}, findings=[], status="cancelled", error="stopped"
        )

    flag = {"v": False}

    async def cancel_requested(_id):
        return flag["v"]

    async def fast_sleep(_s):
        flag["v"] = True  # the admin flips the flag between polls

    async def finish(request_id, *, status, run_id=None, error=None):
        finished.append((status, run_id, error))

    async def meta(*_a):
        postscripts.append("meta")

    monkeypatch.setattr(worker, "_claim_next_request", claim)
    monkeypatch.setattr(worker, "run_loop", run_loop)
    monkeypatch.setattr(worker, "_cancel_requested", cancel_requested)
    monkeypatch.setattr(worker, "_sleep", fast_sleep)
    monkeypatch.setattr(worker, "_finish_request", finish)
    monkeypatch.setattr(worker, "_maybe_run_meta_eval", meta)
    monkeypatch.setattr(worker, "_maybe_verify_fixes", meta)
    assert await _process_one() is True
    assert seen_cancel and seen_cancel[0].is_set()
    assert finished == [("cancelled", 3, "stopped")]
    assert postscripts == []


async def test_analyse_one_skips_a_turn_once_stopped():
    import asyncio

    from improvement_loop.runner import CANCELLED, _analyse_one

    cancel = asyncio.Event()
    cancel.set()
    # Every other argument is irrelevant: the check happens before any work.
    out = await _analyse_one(
        None,
        None,
        None,
        None,
        [],
        None,
        asyncio.Semaphore(1),
        None,
        "",
        "",
        False,
        None,
        None,
        "",
        "",
        "",
        "",
        cancel,
    )
    assert out is CANCELLED


async def test_triage_stops_between_turns():
    import asyncio

    from improvement_loop.models import ConversationBundle
    from improvement_loop.triage import triage

    class _Cfg:
        triage_mode = "all"
        triage_max_tokens = 300
        thinking_budget_tokens = 0

        def models(self):
            return {"triage": "m"}

    def b(i):
        return ConversationBundle(
            turn_id=f"t{i}",
            session_id="s",
            user_id="u",
            occurred_at=None,
            user_message="q",
            response="a",
            content_hash="h",
        )

    cancel = asyncio.Event()
    cancel.set()
    selected, stats = await triage([b(1), b(2)], None, _Cfg(), "sys", cancel=cancel)
    assert selected == [] and stats["cancelled"] == 1


# ---- once-a-day guard ------------------------------------------------


async def test_claim_day_claims_before_the_work_and_only_once(monkeypatch):
    store: dict[str, str] = {}

    async def get(key):
        return store.get(key)

    async def set_(key, value):
        store[key] = value

    monkeypatch.setattr(worker, "_get_setting", get)
    monkeypatch.setattr(worker, "_set_setting", set_)
    assert await _claim_day("loop.last_fix_verify_date:p1") is True
    assert store["loop.last_fix_verify_date:p1"] == worker._now().strftime("%Y-%m-%d")
    assert await _claim_day("loop.last_fix_verify_date:p1") is False


# ---- stale-request reaper --------------------------------------------


class _Row:
    def __init__(self, id, started_at, run_id=None):
        self.id = id
        self.started_at = started_at
        self.run_id = run_id
        self.status = "running"
        self.finished_at = None
        self.error = None


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _Session:
    """Answers the reaper's SELECT with `rows`; records every other statement."""

    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    async def execute(self, stmt):
        from sqlalchemy.sql import Select

        if isinstance(stmt, Select):
            # The reaper's age predicate is applied in SQL; emulate it here from the
            # compiled WHERE so the test exercises the real statement.
            where = str(stmt.whereclause)
            if "started_at <" in where:
                return _Result([r for r in self.rows if r.started_at < _Session.cutoff])
            return _Result(self.rows)
        self.statements.append(stmt)
        return None

    cutoff = datetime.now(UTC)


def _install(monkeypatch, rows):
    session = _Session(rows)

    class _Scope:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(worker, "session_scope", lambda: _Scope())
    return session


async def test_startup_reap_fails_every_running_row(monkeypatch):
    now = datetime.now(UTC)
    rows = [_Row(1, now - timedelta(minutes=1), run_id=10), _Row(2, now - timedelta(days=1))]
    session = _install(monkeypatch, rows)
    assert await _reap_stale_requests(None) == 2
    assert all(r.status == "failed" and "worker restarted" in r.error for r in rows)
    assert all(r.finished_at is not None for r in rows)
    # The matching loop_runs row is failed too (one UPDATE, for the run that has one).
    assert len(session.statements) == 1
    assert "loop_runs" in str(session.statements[0])


async def test_periodic_reap_only_touches_rows_older_than_the_threshold(monkeypatch):
    now = datetime.now(UTC)
    _Session.cutoff = now - timedelta(hours=6)
    fresh, old = _Row(1, now - timedelta(minutes=5)), _Row(2, now - timedelta(hours=7))
    _install(monkeypatch, [fresh, old])
    assert await _reap_stale_requests(timedelta(hours=6)) == 1
    assert fresh.status == "running"
    assert old.status == "failed"


async def test_reap_with_nothing_running_is_a_noop(monkeypatch):
    session = _install(monkeypatch, [])
    assert await _reap_stale_requests(None) == 0
    assert session.statements == []
