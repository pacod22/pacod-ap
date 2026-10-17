"""The ORM is the schema baseline: ``create_all`` alone builds the whole database.

``app.main._MIGRATIONS`` holds only changes to deployed databases (today the identity
move: the users email constraint, the app's former sign-in tables and columns), so
every index and constraint a fresh deployment needs must be declared on the models.
These checks run the same ``create_all`` the api runs at boot (on SQLite) and pin the
pieces that used to exist only as migration statements.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.db_models import Base
from app.main import _EVERY_BOOT, _MIGRATIONS


@pytest.fixture
async def inspector_data():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        def collect(sync_conn):
            insp = inspect(sync_conn)
            return {t: {i["name"] for i in insp.get_indexes(t)} for t in insp.get_table_names()}

        data = await conn.run_sync(collect)
    await engine.dispose()
    return data


def test_migrations_after_the_baseline_are_idempotent():
    # The baseline is create_all; the ledger carries only changes made since, each of which
    # must be safe to run on a database that already has it (a fresh one built by create_all).
    for stmt in _MIGRATIONS:
        assert "IF EXISTS" in stmt or "IF NOT EXISTS" in stmt, stmt
    # Nothing runs on every boot any more (the role seeds went with the roles table).
    assert _EVERY_BOOT == []
    # The app's former sign-in tables and columns are dropped from deployed databases.
    assert "DROP TABLE IF EXISTS device_sessions" in _MIGRATIONS


async def test_create_all_builds_the_core_tables(inspector_data):
    for table in ("profiles", "users", "audit_log", "usage_events", "app_settings"):
        assert table in inspector_data, table


async def test_former_migration_indexes_are_on_the_models(inspector_data):
    expected = {
        "usage_events": {"ix_usage_events_site_occurred", "ix_usage_events_user_occurred"},
        "audit_log": {"ix_audit_log_site_occurred", "ix_audit_log_user_session"},
        "golden_tests": {"ix_golden_tests_kind"},
        "loop_clusters": {"ix_loop_clusters_profile_status"},
        "loop_findings": {"ix_loop_findings_disposition"},
    }
    for table, names in expected.items():
        assert names <= inspector_data[table], (table, inspector_data[table])


async def test_no_removed_product_tables(inspector_data):
    for table in ("readings", "reading_photos", "app_releases"):
        assert table not in inspector_data
