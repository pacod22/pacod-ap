"""Per-user IP addresses + activity log: IP extraction, the middleware's IP
counter, the aggregating sink, ``record_activity``, the monthly prune and the
admin queries/routes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.core.request_context as rc
import app.services.user_activity as ua
import improvement_loop.worker as worker
from admin_api.routes import analytics as routes
from app.core.client_ip import client_ip_from_headers
from app.core.config import get_settings
from app.core.db_models import Base, Profile, User, UserActivity, UserIpAddress
from app.services import analytics_queries as aq
from app.services.user_ip_sink import UserIpSink

_TABLES = [t.__table__ for t in (User, Profile, UserActivity, UserIpAddress)]


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=_TABLES))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _scope_for(maker):
    class _Scope:
        async def __aenter__(self):
            self.s = maker()
            return await self.s.__aenter__()

        async def __aexit__(self, exc_type, *a):
            if exc_type is None:
                await self.s.commit()
            return await self.s.__aexit__(exc_type, *a)

    return lambda: _Scope()


@pytest.fixture
def settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "user_activity_enabled", True)
    monkeypatch.setattr(s, "activity_log_retention_months", 6)
    return s


# ---- client IP -------------------------------------------------------


class TestClientIp:
    def test_first_forwarded_hop(self):
        assert client_ip_from_headers({"x-forwarded-for": "203.0.113.7, 10.0.0.1"}) == "203.0.113.7"

    def test_real_ip_fallback(self):
        assert client_ip_from_headers({"x-real-ip": " 198.51.100.2 "}) == "198.51.100.2"

    def test_none_without_headers(self):
        # Never the peer: without a forwarding header the peer is a container.
        assert client_ip_from_headers({}) is None
        assert client_ip_from_headers({"x-forwarded-for": " , "}) is None


# ---- middleware IP counter -------------------------------------------


class _FakeIpSink:
    def __init__(self) -> None:
        self.hits: list[tuple[str, str]] = []

    def hit(self, user_id: str, ip: str) -> None:
        self.hits.append((user_id, ip))


@pytest.fixture
def ip_sink(monkeypatch, settings) -> _FakeIpSink:
    s = _FakeIpSink()
    import app.services.user_ip_sink as uis
    import app.services.web_request_sink as wrs

    monkeypatch.setattr(uis, "get_user_ip_sink", lambda: s)

    class _Null:
        def push(self, row):
            pass

    monkeypatch.setattr(wrs, "get_web_request_sink", lambda: _Null())
    return s


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/v1/me")
    async def me():
        rc.note_user("u1")
        return {"ip": rc.current_request_context().ip}

    @app.get("/api/v1/public")
    async def public():
        return {}

    @app.get("/v1/chat/completions")
    async def completions():
        rc.note_user("u1")
        return {}

    app.add_middleware(rc.RequestContextMiddleware)
    return app


class TestMiddlewareIpCounter:
    def test_authenticated_request_is_counted(self, ip_sink):
        c = TestClient(_app())
        r = c.get("/api/v1/me", headers={"X-Forwarded-For": "203.0.113.7"})
        assert r.json() == {"ip": "203.0.113.7"}
        c.get("/api/v1/me", headers={"X-Forwarded-For": "203.0.113.7"})
        assert ip_sink.hits == [("u1", "203.0.113.7"), ("u1", "203.0.113.7")]

    def test_not_counted_without_ip_or_user(self, ip_sink):
        c = TestClient(_app())
        c.get("/api/v1/me")  # no forwarding header → the peer is not recorded
        c.get("/api/v1/public", headers={"X-Forwarded-For": "203.0.113.7"})  # anonymous
        assert ip_sink.hits == []

    def test_ssr_not_counted(self, ip_sink):
        c = TestClient(_app())
        c.get("/api/v1/me", headers={"X-Forwarded-For": "203.0.113.7", "X-Pacod-Internal": "1"})
        assert ip_sink.hits == []

    def test_openai_compat_call_is_counted_like_any_other(self, ip_sink):
        TestClient(_app()).get("/v1/chat/completions", headers={"X-Forwarded-For": "203.0.113.8"})
        assert ip_sink.hits == [("u1", "203.0.113.8")]

    def test_kill_switch(self, ip_sink, settings, monkeypatch):
        monkeypatch.setattr(settings, "user_activity_enabled", False)
        TestClient(_app()).get("/api/v1/me", headers={"X-Forwarded-For": "203.0.113.7"})
        assert ip_sink.hits == []


# ---- aggregating sink ------------------------------------------------


class TestUserIpSink:
    def test_hits_aggregate_per_user_and_ip(self):
        sink = UserIpSink()
        t0 = datetime(2026, 9, 1, tzinfo=UTC)
        sink.hit("u1", "1.1.1.1", t0 + timedelta(minutes=5))
        sink.hit("u1", "1.1.1.1", t0)
        sink.hit("u1", "1.1.1.1", t0 + timedelta(minutes=9))
        sink.hit("u1", "2.2.2.2", t0)
        rows = {(r["user_id"], r["ip"]): r for r in sink.rows()}
        a = rows[("u1", "1.1.1.1")]
        assert a["use_count"] == 3
        assert a["first_seen_at"] == t0 + timedelta(minutes=5)  # first *seen* by the sink
        assert a["last_used_at"] == t0 + timedelta(minutes=9)
        assert rows[("u1", "2.2.2.2")]["use_count"] == 1

    async def test_flush_upserts_and_adds_counts(self, monkeypatch):
        captured = []

        class _Result:
            def scalars(self):
                return iter(["u1"])  # u-gone was deleted since its hit

        class _Session:
            async def execute(self, stmt):
                if stmt.is_select:
                    return _Result()
                captured.append(stmt)

        class _Scope:
            async def __aenter__(self):
                return _Session()

            async def __aexit__(self, *a):
                return False

        import app.core.db as db

        monkeypatch.setattr(db, "session_scope", lambda: _Scope())
        sink = UserIpSink()
        sink.hit("u1", "1.1.1.1")
        sink.hit("u-gone", "9.9.9.9")
        assert await sink.flush() == 1  # the deleted user's row is skipped, not the batch
        assert sink.pending() == 0
        (stmt,) = captured
        params = stmt.compile(dialect=postgresql.dialect()).params
        assert "u1" in params.values() and "u-gone" not in params.values()
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "ON CONFLICT (user_id, ip) DO UPDATE" in sql
        assert "use_count = (user_ip_addresses.use_count + excluded.use_count)" in sql
        assert "greatest(" in sql

    async def test_flush_failure_is_swallowed(self, monkeypatch):
        import app.core.db as db

        def boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(db, "session_scope", boom)
        sink = UserIpSink()
        sink.hit("u1", "1.1.1.1")
        assert await sink.flush() == 0


# ---- record_activity -------------------------------------------------


class TestRecordActivity:
    async def test_stamps_ip_and_site_from_the_request(self, maker, monkeypatch, settings):
        monkeypatch.setattr(ua, "session_scope", _scope_for(maker))
        async with maker() as s:
            s.add(User(id="u1", email="a@example.com", display_name="A"))
            await s.commit()
        with rc.site_scope("pacod") as ctx:
            ctx.ip = "203.0.113.7"
            await ua.record_activity(
                "u1", "chat_turn", session_id="chat-1", turn_id="t1", profile_id="taxes"
            )
            await ua.record_activity("u1", "login", detail={"method": "google", "device": None})
        async with maker() as s:
            rows = (await s.execute(select(UserActivity).order_by(UserActivity.id))).scalars().all()
        assert [(r.action, r.ip, r.site, r.session_id) for r in rows] == [
            ("chat_turn", "203.0.113.7", "pacod", "chat-1"),
            ("login", "203.0.113.7", "pacod", None),
        ]
        assert rows[1].detail == '{"method": "google"}'  # empty values dropped

    async def test_unknown_action_and_kill_switch_write_nothing(self, maker, monkeypatch, settings):
        monkeypatch.setattr(ua, "session_scope", _scope_for(maker))
        await ua.record_activity("u1", "made_up")
        monkeypatch.setattr(settings, "user_activity_enabled", False)
        await ua.record_activity("u1", "login")
        async with maker() as s:
            assert (await s.execute(select(UserActivity))).first() is None

    async def test_db_failure_is_swallowed(self, monkeypatch, settings):
        def boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(ua, "session_scope", boom)
        await ua.record_activity("u1", "login")  # must not raise


# ---- retention -------------------------------------------------------


class TestMonthsAgo:
    def test_clamps_to_month_end(self):
        assert ua.months_ago(datetime(2026, 3, 31, tzinfo=UTC), 1) == datetime(
            2026, 2, 28, tzinfo=UTC
        )
        assert ua.months_ago(datetime(2028, 3, 31, tzinfo=UTC), 1).day == 29

    def test_crosses_years(self):
        assert ua.months_ago(datetime(2026, 2, 15, 10, tzinfo=UTC), 6) == datetime(
            2025, 8, 15, 10, tzinfo=UTC
        )
        assert ua.months_ago(datetime(2026, 1, 1, tzinfo=UTC), 0) == datetime(
            2026, 1, 1, tzinfo=UTC
        )


class TestPrune:
    async def test_prunes_old_rows_once_a_day(self, maker, monkeypatch, settings):
        store: dict[str, str] = {}

        async def get(key):
            return store.get(key)

        async def set_(key, value):
            store[key] = value

        monkeypatch.setattr(worker, "_get_setting", get)
        monkeypatch.setattr(worker, "_set_setting", set_)
        monkeypatch.setattr(worker, "session_scope", _scope_for(maker))
        now = datetime.now(UTC)
        old, recent = now - timedelta(days=200), now - timedelta(days=10)
        async with maker() as s:
            s.add(User(id="u1", email="a@example.com", display_name="A"))
            s.add_all(
                [
                    UserActivity(id=1, user_id="u1", occurred_at=old, action="login"),
                    UserActivity(id=2, user_id="u1", occurred_at=recent, action="login"),
                    UserIpAddress(id=1, user_id="u1", ip="1.1.1.1", last_used_at=old, use_count=3),
                    UserIpAddress(
                        id=2, user_id="u1", ip="2.2.2.2", last_used_at=recent, use_count=1
                    ),
                ]
            )
            await s.commit()
        await worker._maybe_prune_user_activity()
        async with maker() as s:
            assert [r.id for r in (await s.execute(select(UserActivity))).scalars()] == [2]
            assert [r.ip for r in (await s.execute(select(UserIpAddress))).scalars()] == ["2.2.2.2"]
            s.add(UserActivity(id=3, user_id="u1", occurred_at=old, action="login"))
            await s.commit()
        await worker._maybe_prune_user_activity()  # same day → guarded
        async with maker() as s:
            assert len((await s.execute(select(UserActivity))).scalars().all()) == 2

    async def test_zero_keeps_forever(self, monkeypatch, settings):
        monkeypatch.setattr(settings, "activity_log_retention_months", 0)

        async def fail(key):
            raise AssertionError("must not touch the guard")

        monkeypatch.setattr(worker, "_get_setting", fail)
        await worker._maybe_prune_user_activity()


# ---- admin queries + routes ------------------------------------------


async def _seed(maker) -> None:
    t = datetime(2026, 9, 20, 12, tzinfo=UTC)
    async with maker() as s:
        s.add_all(
            [
                User(id="u1", email="a@example.com", display_name="A"),
                Profile(
                    id="taxes",
                    slug="taxes",
                    name="Taxes",
                    base_collection="pacod_taxes_kb",
                    curated_collection="pacod_taxes_curated",
                ),
                UserIpAddress(id=1, user_id="u1", ip="1.1.1.1", last_used_at=t, use_count=4),
                UserIpAddress(
                    id=2, user_id="u1", ip="2.2.2.2", last_used_at=t + timedelta(hours=1)
                ),
                UserActivity(id=1, user_id="u1", occurred_at=t, action="login", detail="{bad"),
                UserActivity(
                    id=2,
                    user_id="u1",
                    occurred_at=t + timedelta(minutes=5),
                    action="chat_turn",
                    session_id="chat-1",
                    profile_id="taxes",
                    ip="1.1.1.1",
                ),
                UserActivity(
                    id=3,
                    user_id="u1",
                    occurred_at=t + timedelta(minutes=9),
                    action="reading",
                    detail='{"method": "google", "device": "phone"}',
                ),
            ]
        )
        await s.commit()


class TestAdminQueries:
    async def test_ips_most_recent_first(self, maker):
        await _seed(maker)
        async with maker() as s:
            ips = await aq.user_ip_addresses(s, "u1")
        assert [r["ip"] for r in ips] == ["2.2.2.2", "1.1.1.1"]
        assert ips[1]["use_count"] == 4

    async def test_activity_paging_filter_and_profile_name(self, maker):
        await _seed(maker)
        async with maker() as s:
            total, items = await aq.user_activity(s, "u1", limit=2)
            assert total == 3
            assert [i["action"] for i in items] == ["reading", "chat_turn"]
            assert items[0]["detail"] == {"method": "google", "device": "phone"}
            assert items[1]["profile_name"] == "Taxes"
            _, rest = await aq.user_activity(s, "u1", limit=2, offset=2)
            assert rest[0]["detail"] == {"text": "{bad"}
            total, only = await aq.user_activity(s, "u1", action="chat_turn")
            assert total == 1 and only[0]["session_id"] == "chat-1"

    async def test_route_rejects_unknown_action(self, maker, settings):
        async with maker() as s:
            with pytest.raises(HTTPException) as e:
                await routes.analytics_user_activity(
                    "u1", action="nope", limit=50, offset=0, _=None, db=s
                )
        assert e.value.status_code == 422

    async def test_route_returns_retention(self, maker, settings):
        await _seed(maker)
        async with maker() as s:
            res = await routes.analytics_user_activity(
                "u1", action=None, limit=50, offset=0, _=None, db=s
            )
        assert res["retention_months"] == 6 and res["total"] == 3
