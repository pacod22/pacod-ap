"""The per-request analytics context: site resolution, the middleware's
web_requests row, and user/profile capture from deeper in the request."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse

import app.core.request_context as rc
from app.services.debug_audit import debug_scope


class _Sink:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def push(self, row: dict) -> None:
        self.rows.append(row)


@pytest.fixture
def sink(monkeypatch) -> _Sink:
    s = _Sink()
    import app.services.web_request_sink as wrs

    monkeypatch.setattr(wrs, "get_web_request_sink", lambda: s)
    return s


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/v1/things/{thing_id}")
    async def thing(thing_id: str):
        rc.note_user("u-42")
        with debug_scope("t", "u-42", "s", False, profile_id="traffic"):
            pass
        return {"site": rc.current_site()}

    @app.get("/api/v1/stream")
    async def stream():
        async def gen():
            rc.note_user("u-stream")
            yield b"a"
            yield b"b"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/v1/models")
    async def models():
        return {"data": []}

    @app.get("/v1/chat/completions")
    async def completions():
        return {"site": rc.current_site()}

    @app.get("/health")
    async def health():
        return {"ok": True}

    app.add_middleware(rc.RequestContextMiddleware)
    return app


class TestResolveSite:
    def test_trusted_header(self):
        assert rc.resolve_site({"x-pacod-site": "pacod"}) == "pacod"
        assert rc.resolve_site({"x-pacod-site": "PACOD"}) == "pacod"

    def test_untrusted_header_ignored(self):
        assert rc.resolve_site({"x-pacod-site": "evil"}) == "unknown"
        # A retired site is no longer accepted from the header either.
        assert rc.resolve_site({"x-pacod-site": "openwebui"}) == "unknown"

    def test_no_header_is_unknown(self):
        assert rc.resolve_site({}) == "unknown"

    def test_no_context_is_internal(self):
        assert rc.current_site() == "internal"
        with rc.site_scope("pacod"):
            assert rc.current_site() == "pacod"
        assert rc.current_site() == "internal"


class TestMiddleware:
    def test_records_route_template_user_and_site(self, sink):
        r = TestClient(_app()).get("/api/v1/things/abc?q=secret", headers={"X-Pacod-Site": "pacod"})
        assert r.json() == {"site": "pacod"}
        assert len(sink.rows) == 1
        row = sink.rows[0]
        assert row["route"] == "/api/v1/things/{thing_id}"  # template, never the raw path
        assert row["site"] == "pacod"
        assert row["kind"] == "api"
        assert row["user_id"] == "u-42"
        assert row["user_type"] == "registered"
        assert row["status"] == 200

    def test_profile_captured_from_debug_scope(self, monkeypatch, sink):
        seen: list = []
        orig = rc._enqueue_row

        def spy(ctx, **kw):
            seen.append(ctx.profile_id)
            orig(ctx, **kw)

        monkeypatch.setattr(rc, "_enqueue_row", spy)
        TestClient(_app()).get("/api/v1/things/1")
        assert seen == ["traffic"]

    def test_streaming_passthrough_and_late_user(self, sink):
        r = TestClient(_app()).get("/api/v1/stream")
        assert r.text == "ab"
        assert sink.rows[0]["user_id"] == "u-stream"

    def test_internal_header_suppresses_row(self, sink):
        TestClient(_app()).get("/api/v1/things/1", headers={"X-Pacod-Internal": "1"})
        assert sink.rows == []

    def test_skip_list(self, sink):
        c = TestClient(_app())
        c.get("/health")
        c.get("/v1/models")
        assert sink.rows == []

    def test_openai_compat_call_is_a_direct_call(self, sink):
        # /v1/* is recorded like any other API call; without a trusted site header it is
        # a direct call (an OpenAI client), not a site of its own.
        r = TestClient(_app()).get("/v1/chat/completions")
        assert r.json() == {"site": "unknown"}
        assert sink.rows[0]["site"] == "unknown"
        assert sink.rows[0]["user_type"] == "anonymous"

    def test_kill_switch(self, monkeypatch, sink):
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings(), "analytics_api_requests_enabled", False)
        TestClient(_app()).get("/api/v1/things/1")
        assert sink.rows == []
