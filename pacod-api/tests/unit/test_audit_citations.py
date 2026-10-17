"""Chat history must carry the Sources list.

The live UI gets citations from SSE events; a reloaded conversation gets them
from ``audit_log.citations``. Two properties are pinned here: the writer stores
the list as JSON-as-TEXT in its own column (never inside the 8000-char ``detail``
blob), and the turns endpoint's decoder tolerates NULL / corrupt / non-list
values from legacy rows instead of failing the whole history load.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest

import app.services.audit as audit_mod
from app.api.routes_chat import _decode_citations
from app.services.audit import record_audit

CITATION = {
    "source_id": "zdds_2026",
    "source_type": "law",
    "title": "ЗДДС",
    "url": None,
    "snippet": "Чл. 96.",
    "legal_year": 2026,
    "chunk_id": "c1",
}


class _CapturingSession:
    def __init__(self):
        self.values: dict | None = None

    async def execute(self, stmt):
        # SQLAlchemy Insert exposes the bound values via compile(); keep it simple.
        self.values = stmt.compile().params


@pytest.mark.asyncio
async def test_record_audit_stores_citations_in_own_column(monkeypatch):
    captured = _CapturingSession()

    @asynccontextmanager
    async def _scope():
        yield captured

    monkeypatch.setattr(audit_mod, "session_scope", _scope)
    await record_audit(
        user_id="u1",
        session_id="s1",
        event_type="request_completed",
        detail=json.dumps({"tokens": {}}),
        citations=[CITATION],
    )
    assert captured.values is not None
    assert json.loads(captured.values["citations"]) == [CITATION]
    assert "citations" not in captured.values["detail"]


@pytest.mark.asyncio
async def test_record_audit_omits_citations_when_not_given(monkeypatch):
    captured = _CapturingSession()

    @asynccontextmanager
    async def _scope():
        yield captured

    monkeypatch.setattr(audit_mod, "session_scope", _scope)
    await record_audit(user_id="u1", session_id="s1", event_type="x", detail="{}")
    assert captured.values["citations"] is None


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, []),
        ("", []),
        ("not json", []),
        ('{"a": 1}', []),
        ('[1, "x", {"source_id": "s"}]', [{"source_id": "s"}]),
        (json.dumps([CITATION]), [CITATION]),
    ],
)
def test_decode_citations_is_lenient(raw, expected):
    assert _decode_citations(raw) == expected
