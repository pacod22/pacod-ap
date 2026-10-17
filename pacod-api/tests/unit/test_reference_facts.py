"""Reference facts: the shared service, the MCP tools and the admin routes.

SQLite-backed (no Postgres); the MCP server's session factory is swapped for the
test engine, so the tools run their real queries.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db_models import Base, ReferenceFact
from app.services import reference_facts as facts

DATA = Path(__file__).resolve().parents[2] / "data" / "profiles"


@pytest.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[ReferenceFact.__table__]))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


SAMPLE = [
    {
        "fact_key": "price",
        "variant": "plus",
        "legal_year": 2026,
        "value_type": "money",
        "value_num": 6,
        "unit": "EUR",
        "category": "pricing",
        "label_en": "Plus price",
        "source_url": "https://x/p",
    },
    {
        "fact_key": "price",
        "variant": "team",
        "legal_year": 2026,
        "value_type": "money",
        "value_num": 12,
        "unit": "EUR",
        "category": "pricing",
    },
    {
        "fact_key": "price",
        "variant": "team",
        "legal_year": 2025,
        "value_type": "money",
        "value_num": 10,
        "unit": "EUR",
        "category": "pricing",
    },
    # Timeless: valid in every year, overridden by a year-specific row of the variant.
    {
        "fact_key": "support_hours",
        "value_type": "text",
        "value_text": "Mon-Fri 8-18",
        "category": "support",
        "extra": {"tz": "CET"},
    },
]


async def _seed(maker, profile_id="p1", rows=SAMPLE, **kw):
    async with maker() as s:
        out = await facts.upsert_facts(s, profile_id, [dict(r) for r in rows], **kw)
        await s.commit()
    return out


# ---- service ------------------------------------------------------------------


async def test_upsert_is_idempotent_and_reports_changes(maker):
    assert await _seed(maker) == (4, 0, 0)
    assert await _seed(maker) == (0, 0, 0)  # nothing changed
    changed = [dict(SAMPLE[0], value_num=7), *SAMPLE[1:]]
    assert await _seed(maker, rows=changed) == (0, 1, 0)


async def test_replace_deletes_facts_not_in_the_set(maker):
    await _seed(maker)
    assert await _seed(maker, rows=SAMPLE[:1], replace=True) == (0, 0, 3)


async def test_duplicate_keys_in_one_import_are_refused(maker):
    with pytest.raises(ValueError):
        await _seed(maker, rows=[SAMPLE[0], SAMPLE[0]])


async def test_get_facts_returns_the_years_variants_and_timeless_rows(maker):
    await _seed(maker)
    async with maker() as s:
        rows = await facts.get_facts(s, "p1", "price", legal_year=2026)
        assert sorted((r.variant, r.value_num) for r in rows) == [("plus", 6), ("team", 12)]
        team = await facts.get_facts(s, "p1", "price", variant="team", legal_year=2025)
        assert [r.value_num for r in team] == [10]
        timeless = await facts.get_facts(s, "p1", "support_hours", legal_year=2026)
        assert [r.value_text for r in timeless] == ["Mon-Fri 8-18"]


async def test_facts_are_isolated_per_profile(maker):
    await _seed(maker, profile_id="p1")
    async with maker() as s:
        assert await facts.get_facts(s, "p2", "price", legal_year=2026) == []
        rows, _ = await facts.list_facts(s, "p2", legal_year=2026)
        assert rows == []


async def test_list_facts_filters_and_truncates(maker):
    await _seed(maker)
    async with maker() as s:
        rows, truncated = await facts.list_facts(s, "p1", legal_year=2026, category="pricing")
        assert {r.variant for r in rows} == {"plus", "team"} and not truncated
        rows, _ = await facts.list_facts(s, "p1", legal_year=2026, query="Plus")
        assert [r.variant for r in rows] == ["plus"]
        rows, truncated = await facts.list_facts(s, "p1", legal_year=2026, limit=1)
        assert len(rows) == 1 and truncated


def test_tool_dict_drops_empty_fields_and_decodes_extra():
    row = ReferenceFact(
        fact_key="k",
        variant="default",
        value_type="text",
        value_text="x",
        extra=facts.encode_extra({"a": 1}),
        label_en="",
        legal_year=None,
    )
    out = facts.fact_to_tool_dict(row)
    assert out == {
        "fact_key": "k",
        "variant": "default",
        "value_type": "text",
        "value_text": "x",
        "extra": {"a": 1},
    }


# ---- MCP tools ----------------------------------------------------------------


@pytest.fixture
def server(monkeypatch, maker):
    monkeypatch.setenv("POSTGRES_PASSWORD", "x")
    monkeypatch.setenv("MCP_POSTGRES_DSN", "postgresql+asyncpg://x:x@127.0.0.1:1/x")
    import mcp_server.server as srv

    monkeypatch.setattr(srv, "_SessionLocal", maker)
    return srv


async def test_get_fact_tool(server, maker):
    await _seed(maker)
    out = await server.get_fact(
        server.ToolCall(arguments={"fact_key": "price", "legal_year": 2026, "profile_id": "p1"})
    )
    assert out["found"] is True
    assert {f["variant"] for f in out["facts"]} == {"plus", "team"}
    missing = await server.get_fact(
        server.ToolCall(arguments={"fact_key": "nope", "legal_year": 2026, "profile_id": "p1"})
    )
    assert missing == {"found": False, "fact_key": "nope", "legal_year": 2026, "facts": []}
    with pytest.raises(HTTPException):
        await server.get_fact(server.ToolCall(arguments={"profile_id": "p1"}))


async def test_list_facts_tool(server, maker):
    await _seed(maker)
    out = await server.list_facts(
        server.ToolCall(arguments={"legal_year": 2026, "category": "support", "profile_id": "p1"})
    )
    assert [f["fact_key"] for f in out["facts"]] == ["support_hours"]
    assert out["truncated"] is False


# ---- admin routes ---------------------------------------------------------------


class _Admin:
    user_id = "admin"


async def test_admin_crud_import_export(maker):
    from admin_api.routes import reference_facts as routes
    from admin_api.schemas import (
        ReferenceFactCreate,
        ReferenceFactImport,
        ReferenceFactUpdate,
    )

    async with maker() as db:  # one session per request, as get_db gives the routes
        created = await routes.create_reference_fact(
            ReferenceFactCreate(profile_id="p1", **SAMPLE[0]), caller=_Admin(), db=db
        )
        assert created.value_num == 6 and created.updated_by == "admin"
        await db.commit()
    async with maker() as db:
        with pytest.raises(HTTPException) as e:
            await routes.create_reference_fact(
                ReferenceFactCreate(profile_id="p1", **SAMPLE[0]), caller=_Admin(), db=db
            )
        assert e.value.status_code == 409

    async with maker() as db:
        row = (await db.execute(select(ReferenceFact))).scalar_one()
        updated = await routes.update_reference_fact(
            row.id, ReferenceFactUpdate(value_num=7, extra={"note": "x"}), caller=_Admin(), db=db
        )
        assert updated.value_num == 7 and updated.extra == {"note": "x"}
        res = await routes.import_reference_facts(
            ReferenceFactImport(facts=SAMPLE[1:]), profile_id="p1", caller=_Admin(), db=db
        )
        assert (res.created, res.updated, res.deleted) == (3, 0, 0)
        await db.commit()

    async with maker() as db:
        listed = await routes.list_reference_facts(
            profile_id="p1",
            category="pricing",
            legal_year=None,
            fact_key=None,
            q=None,
            limit=200,
            offset=0,
            _=_Admin(),
            db=db,
        )
        assert len(listed) == 3
        assert await routes.list_categories(profile_id="p1", _=_Admin(), db=db) == [
            "pricing",
            "support",
        ]
        exported = await routes.export_reference_facts(profile_id="p1", _=_Admin(), db=db)
        assert len(exported) == 4
        await routes.delete_reference_fact(listed[0].id, caller=_Admin(), db=db)
        await db.commit()


# ---- shipped data -----------------------------------------------------------------


@pytest.mark.parametrize("profile_id", ["starter", "taxes"])
def test_shipped_fact_files_load_and_are_unique(profile_id):
    from admin_api.schemas import ReferenceFactFields

    rows = []
    for path in sorted((DATA / profile_id).glob("reference-facts-*.json")):
        rows.extend(json.loads(path.read_text(encoding="utf-8")))
    assert rows
    keys = [(r["fact_key"], r.get("variant", "default"), r.get("legal_year")) for r in rows]
    assert len(keys) == len(set(keys))
    for r in rows:
        ReferenceFactFields.model_validate(r)  # the import format accepts every row
