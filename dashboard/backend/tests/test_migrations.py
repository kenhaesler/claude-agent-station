"""Tests for database migrations, especially _migrate_add_columns."""

import os
import sqlite3
import tempfile

import pytest

# Override DB path before any app imports
_tmp_db = tempfile.mktemp(suffix=".db")
os.environ["STATION_DB_PATH"] = _tmp_db


from sqlalchemy import text

from app.database import _migrate_add_columns
from app.models import CoordinatorTask  # noqa: F401


@pytest.fixture(autouse=True)
def _clean_db(tmp_path):
    """Use a fresh temp DB for each test."""
    db_file = str(tmp_path / "test.db")
    os.environ["STATION_DB_PATH"] = db_file
    yield
    if os.path.exists(db_file):
        os.unlink(db_file)


@pytest.mark.asyncio
async def test_dag_json_migration_adds_missing_column(tmp_path):
    """Simulate an older DB missing dag_json and verify migration adds it."""
    db_path = tmp_path / "migration_test.db"

    # 1. Create a minimal coordinator_tasks table WITHOUT dag_json
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE coordinator_tasks (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            project_repo TEXT NOT NULL,
            issue_number INTEGER,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'pending',
            employee_index INTEGER,
            depends_on TEXT,
            workspace TEXT,
            expected_files TEXT,
            touched_files TEXT,
            exit_code INTEGER,
            error_message TEXT,
            result_summary TEXT,
            log_path TEXT,
            branch TEXT,
            created_at DATETIME,
            started_at DATETIME,
            finished_at DATETIME
        )
    """)
    conn.commit()

    # Verify dag_json does NOT exist
    cursor = conn.execute("PRAGMA table_info(coordinator_tasks)")
    columns = [row[1] for row in cursor.fetchall()]
    assert "dag_json" not in columns
    conn.close()

    # 2. Run the migration function via a real async engine
    from sqlalchemy.ext.asyncio import create_async_engine

    test_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    async with test_engine.begin() as aconn:
        await _migrate_add_columns(aconn)

    await test_engine.dispose()

    # 3. Verify dag_json now exists
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute("PRAGMA table_info(coordinator_tasks)")
    columns = [row[1] for row in cursor.fetchall()]
    assert "dag_json" in columns
    conn.close()


@pytest.mark.asyncio
async def test_migration_is_idempotent(tmp_path):
    """Running _migrate_add_columns twice should not error."""
    db_path = tmp_path / "idempotent_test.db"

    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE coordinator_tasks (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            project_repo TEXT NOT NULL,
            title TEXT NOT NULL,
            result_summary TEXT,
            log_path TEXT,
            branch TEXT
        )
    """)
    conn.commit()
    conn.close()

    from sqlalchemy.ext.asyncio import create_async_engine

    test_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)

    # Run twice - should not raise
    async with test_engine.begin() as aconn:
        await _migrate_add_columns(aconn)
    async with test_engine.begin() as aconn:
        await _migrate_add_columns(aconn)

    await test_engine.dispose()

    # Verify dag_json exists (only one copy)
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute("PRAGMA table_info(coordinator_tasks)")
    columns = [row[1] for row in cursor.fetchall()]
    assert columns.count("dag_json") == 1
    conn.close()


@pytest.mark.asyncio
async def test_dag_json_read_write_after_migration(tmp_path):
    """After migration, CoordinatorTask.dag_json should be readable/writable."""
    db_path = tmp_path / "rw_test.db"

    # Create table without dag_json
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE coordinator_tasks (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            project_repo TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            result_summary TEXT,
            log_path TEXT,
            branch TEXT,
            created_at DATETIME,
            started_at DATETIME,
            finished_at DATETIME
        )
    """)
    conn.commit()
    conn.close()

    from sqlalchemy.ext.asyncio import create_async_engine

    test_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)

    # Run migration
    async with test_engine.begin() as aconn:
        await _migrate_add_columns(aconn)

    # Write and read dag_json via raw SQL (to avoid full ORM setup)
    async with test_engine.begin() as aconn:
        await aconn.execute(text(
            "INSERT INTO coordinator_tasks (id, run_id, project_repo, title, dag_json) "
            "VALUES ('t1', 'run-1', 'owner/repo', 'Test task', '{\"nodes\": []}')"
        ))

    async with test_engine.connect() as aconn:
        result = await aconn.execute(text(
            "SELECT dag_json FROM coordinator_tasks WHERE id = 't1'"
        ))
        row = result.fetchone()
        assert row is not None
        assert row[0] == '{"nodes": []}'

    await test_engine.dispose()
