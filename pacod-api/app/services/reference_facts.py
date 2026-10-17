"""Reference facts — the agent's authoritative numbers, rates, amounts and dates.

One module for every reader and writer of ``reference_facts``: the MCP server's
tools (``get_fact`` / ``list_facts``), the admin CRUD + import routes and the
seed script. Deliberately light on imports (SQLAlchemy + the ORM only) because
the MCP image installs nothing else.

Rows are scoped by ``profile_id``, which every caller supplies explicitly — the
agent's planner never chooses it.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db_models import ReferenceFact

# What the agent sees of one fact (pasted verbatim into the synthesis context).
_TOOL_FIELDS = (
    "fact_key",
    "category",
    "variant",
    "legal_year",
    "value_type",
    "value_num",
    "value_max",
    "unit",
    "value_text",
    "valid_from",
    "valid_to",
    "label_bg",
    "label_en",
    "description_bg",
    "description_en",
    "source_url",
)

# The editable columns (the import/export and admin write format).
EDITABLE_FIELDS = (
    *_TOOL_FIELDS,
    "extra",
    "source_note",
)

_KEY = ("fact_key", "variant", "legal_year")

# The list tool's output is pasted into the prompt and paid for as input tokens;
# an unbounded result would silently blow the budget.
DEFAULT_LIST_LIMIT = 200
MAX_LIST_LIMIT = 1000


def encode_extra(value: dict | None) -> str | None:
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if value else None


def decode_extra(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def fact_to_tool_dict(row: ReferenceFact) -> dict[str, Any]:
    """The JSON shape the agent's tools return for one fact. Unset fields are
    dropped so the prompt only carries what the fact actually says."""
    out: dict[str, Any] = {}
    for name in _TOOL_FIELDS:
        value = getattr(row, name)
        if value is None or value == "":
            continue
        out[name] = value.isoformat() if isinstance(value, date) else value
    extra = decode_extra(row.extra)
    if extra:
        out["extra"] = extra
    return out


def _year_clause(legal_year: int | None):
    """Facts of the turn's reference year, plus the ones valid in every year."""
    if legal_year is None:
        return None
    return or_(ReferenceFact.legal_year == legal_year, ReferenceFact.legal_year.is_(None))


async def get_facts(
    session: AsyncSession,
    profile_id: str,
    fact_key: str,
    *,
    variant: str | None = None,
    legal_year: int | None = None,
) -> list[ReferenceFact]:
    """Every variant of one key (or just ``variant``) for the reference year.
    A year-specific row wins over a timeless row of the same variant."""
    stmt = select(ReferenceFact).where(
        ReferenceFact.profile_id == profile_id, ReferenceFact.fact_key == fact_key
    )
    if variant:
        stmt = stmt.where(ReferenceFact.variant == variant)
    year = _year_clause(legal_year)
    if year is not None:
        stmt = stmt.where(year)
    rows = (await session.execute(stmt.order_by(ReferenceFact.variant))).scalars().all()
    best: dict[str, ReferenceFact] = {}
    for row in rows:
        current = best.get(row.variant)
        if current is None or (current.legal_year is None and row.legal_year is not None):
            best[row.variant] = row
    return list(best.values())


async def list_facts(
    session: AsyncSession,
    profile_id: str,
    *,
    legal_year: int | None = None,
    category: str | None = None,
    query: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> tuple[list[ReferenceFact], bool]:
    """Facts of a profile for the reference year, optionally narrowed by category
    or a text query. Returns ``(rows, truncated)``."""
    limit = max(1, min(int(limit or DEFAULT_LIST_LIMIT), MAX_LIST_LIMIT))
    stmt = select(ReferenceFact).where(ReferenceFact.profile_id == profile_id)
    year = _year_clause(legal_year)
    if year is not None:
        stmt = stmt.where(year)
    if category:
        stmt = stmt.where(ReferenceFact.category == category)
    if query and query.strip():
        like = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                ReferenceFact.fact_key.ilike(like),
                ReferenceFact.category.ilike(like),
                ReferenceFact.label_en.ilike(like),
                ReferenceFact.label_bg.ilike(like),
                ReferenceFact.description_en.ilike(like),
                ReferenceFact.description_bg.ilike(like),
            )
        )
    stmt = stmt.order_by(
        ReferenceFact.category.nullslast(), ReferenceFact.fact_key, ReferenceFact.variant
    ).limit(limit + 1)
    rows = list((await session.execute(stmt)).scalars().all())
    return rows[:limit], len(rows) > limit


def _normalise(fact: dict[str, Any]) -> dict[str, Any]:
    values = {k: fact.get(k) for k in EDITABLE_FIELDS if k in fact}
    values["variant"] = values.get("variant") or "default"
    values.setdefault("value_type", "number")
    for d in ("valid_from", "valid_to"):
        if isinstance(values.get(d), str):
            values[d] = date.fromisoformat(values[d]) if values[d] else None
    if "extra" in values:
        values["extra"] = encode_extra(values["extra"])
    return values


async def upsert_facts(
    session: AsyncSession,
    profile_id: str,
    facts: list[dict[str, Any]],
    *,
    replace: bool = False,
    updated_by: str | None = None,
) -> tuple[int, int, int]:
    """Insert or update ``facts`` (keyed on fact_key / variant / reference year)
    within one profile. ``replace`` also deletes the profile's facts that are not
    in ``facts``. Returns ``(created, updated, deleted)``. The caller commits."""
    incoming: dict[tuple, dict[str, Any]] = {}
    for fact in facts:
        values = _normalise(fact)
        key = tuple(values.get(k) for k in _KEY)
        if key in incoming:
            raise ValueError(f"duplicate fact in import: {key}")
        incoming[key] = values

    existing = {
        (r.fact_key, r.variant, r.legal_year): r
        for r in (
            await session.execute(
                select(ReferenceFact).where(ReferenceFact.profile_id == profile_id)
            )
        )
        .scalars()
        .all()
    }
    created = updated = deleted = 0
    for key, values in incoming.items():
        row = existing.get(key)
        if row is None:
            session.add(ReferenceFact(profile_id=profile_id, updated_by=updated_by, **values))
            created += 1
            continue
        changed = False
        for name, value in values.items():
            if getattr(row, name) != value:
                setattr(row, name, value)
                changed = True
        if changed:
            row.updated_by = updated_by
            updated += 1
    if replace:
        stale = [r.id for k, r in existing.items() if k not in incoming]
        if stale:
            await session.execute(delete(ReferenceFact).where(ReferenceFact.id.in_(stale)))
            deleted = len(stale)
    await session.flush()
    return created, updated, deleted
