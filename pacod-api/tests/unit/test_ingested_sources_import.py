"""Unit tests for the admin CSV metadata-import route.

`POST /api/v1/ingested-sources/import` bulk-creates ingested_sources rows from a
CSV that strictly follows the table schema — metadata only, no Qdrant vectors.
The DB session is replaced with an in-memory fake (no Postgres needed). These
assert the behaviour that matters:

  * strict header validation rejects the whole file (422),
  * per-row bad values are reported and the rest still import,
  * duplicate source_ids (in the DB or within the file) are skipped + reported,
  * a clean row maps onto the schema columns.
"""

from __future__ import annotations

import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin_api.dependencies import require_admin
from admin_api.routes.ingested_sources import router as ingested_router
from app.core.db import get_db
from app.core.db_models import DEFAULT_PROFILE_ID
from app.guards.auth import AuthenticatedUser


class FakeResult:
    def __init__(self, ids):
        self._ids = ids

    def scalars(self):
        return self

    def all(self):
        return list(self._ids)


class FakeSession:
    """Minimal AsyncSession stand-in for the import route."""

    def __init__(self, existing_ids=()):
        self.existing_ids = set(existing_ids)
        self.added = []
        self.flushed = 0

    async def execute(self, _stmt):
        # The route only SELECTs existing source_ids; return the configured set.
        return FakeResult(self.existing_ids)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed += 1


@pytest.fixture
def make_client(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")

    def _factory(existing_ids=()):
        session = FakeSession(existing_ids)
        app = FastAPI()
        app.include_router(ingested_router)
        app.dependency_overrides[get_db] = lambda: session
        app.dependency_overrides[require_admin] = lambda: AuthenticatedUser(
            user_id="admin1", email="a@b.c", roles=("admin",)
        )
        c = TestClient(app)
        c.session = session  # type: ignore[attr-defined]
        return c

    return _factory


def _post_csv(client, text: str, profile_id: str | None = None):
    kwargs: dict = {
        "files": {"file": ("import.csv", io.BytesIO(text.encode("utf-8")), "text/csv")},
    }
    if profile_id is not None:
        kwargs["data"] = {"profile_id": profile_id}
    return client.post("/api/v1/ingested-sources/import", **kwargs)


def test_imports_clean_rows(make_client):
    client = make_client()
    csv_text = (
        "source_id,title,source_type,legal_year,language,url,status\n"
        "doc_a,Закон за ДДС,law,2026,bg,https://lex.bg/a,NEW\n"
        "doc_b,VAT guidance,guidance,2026,en,,NEW\n"
    )
    resp = _post_csv(client, csv_text)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_rows"] == 2
    assert body["created"] == 2
    assert body["skipped"] == 0
    assert body["errors"] == []
    assert set(body["created_source_ids"]) == {"doc_a", "doc_b"}

    # Two rows were added and flushed exactly once (single transaction).
    assert len(client.session.added) == 2
    assert client.session.flushed == 1
    row_a = next(s for s in client.session.added if s.source_id == "doc_a")
    assert row_a.legal_year == 2026
    assert row_a.url == "https://lex.bg/a"
    # Blank nullable cell becomes NULL, not "".
    row_b = next(s for s in client.session.added if s.source_id == "doc_b")
    assert row_b.url is None


def test_unknown_column_rejects_whole_file(make_client):
    client = make_client()
    csv_text = "source_id,title,source_type,legal_year,language,bogus\nx,t,law,2026,bg,1\n"
    resp = _post_csv(client, csv_text)
    assert resp.status_code == 422
    assert "bogus" in resp.json()["detail"]
    assert client.session.added == []


def test_id_column_rejected(make_client):
    client = make_client()
    csv_text = "id,source_id,title,source_type,legal_year,language\n1,x,t,law,2026,bg\n"
    resp = _post_csv(client, csv_text)
    assert resp.status_code == 422


def test_missing_required_column_rejects_whole_file(make_client):
    client = make_client()
    csv_text = "source_id,title,source_type,language\nx,t,law,bg\n"  # no legal_year
    resp = _post_csv(client, csv_text)
    assert resp.status_code == 422
    assert "legal_year" in resp.json()["detail"]


def test_bad_row_reported_others_imported(make_client):
    client = make_client()
    csv_text = (
        "source_id,title,source_type,legal_year,language\n"
        "good,T,law,2026,bg\n"
        "bad,T,law,not_a_year,bg\n"
        "missing_title,,law,2026,bg\n"
    )
    resp = _post_csv(client, csv_text)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 1
    assert body["created_source_ids"] == ["good"]
    errs = {e["row"]: e["error"] for e in body["errors"]}
    assert 2 in errs and "legal_year" in errs[2]
    assert 3 in errs and "title" in errs[3]


def test_duplicate_source_ids_skipped(make_client):
    # "exists" already in the DB; "dup" appears twice within the file.
    client = make_client(existing_ids={"exists"})
    csv_text = (
        "source_id,title,source_type,legal_year,language\n"
        "exists,T,law,2026,bg\n"
        "dup,T,law,2026,bg\n"
        "dup,T2,law,2026,bg\n"
    )
    resp = _post_csv(client, csv_text)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 1
    assert body["created_source_ids"] == ["dup"]
    assert body["skipped"] == 2
    assert all("already exists" in e["error"] for e in body["errors"])


def test_empty_file_rejected(make_client):
    client = make_client()
    resp = _post_csv(client, "")
    assert resp.status_code == 422


def test_row_stamped_with_profile():
    """The active profile is stamped on every built row (not read from the CSV)."""
    from admin_api.routes.ingested_sources import _row_to_source

    src = _row_to_source(
        {
            "source_id": "x",
            "title": "T",
            "source_type": "law",
            "legal_year": "2026",
            "language": "bg",
        },
        "profile_1",
    )
    assert src.profile_id == "profile_1"


def test_import_stamps_active_profile(make_client):
    client = make_client()
    csv_text = "source_id,title,source_type,legal_year,language\ndoc_a,T,law,2026,bg\n"
    resp = _post_csv(client, csv_text, profile_id="profile_1")
    assert resp.status_code == 200, resp.text
    assert client.session.added[0].profile_id == "profile_1"


def test_import_defaults_to_default_profile(make_client):
    """No profile_id form field → rows land in the default profile, never NULL."""
    client = make_client()
    csv_text = "source_id,title,source_type,legal_year,language\ndoc_a,T,law,2026,bg\n"
    resp = _post_csv(client, csv_text)
    assert resp.status_code == 200, resp.text
    assert client.session.added[0].profile_id == DEFAULT_PROFILE_ID


def test_import_row_is_stamped_authoritative_tier():
    """A NULL tier would make imported rows invisible to the Documents tier
    filter (exact equality), and the one-off backfill migration only ran once."""
    from admin_api.routes.ingested_sources import _row_to_source

    src = _row_to_source(
        {
            "source_id": "zdds-2026",
            "title": "ЗДДС",
            "source_type": "law",
            "legal_year": "2026",
            "language": "bg",
        },
        "default",
    )
    assert src.tier == "authoritative"
