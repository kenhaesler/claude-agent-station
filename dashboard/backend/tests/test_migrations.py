"""Tests for database migrations, especially _migrate_add_columns."""

import os
import sqlite3
import tempfile

import pytest

# Override DB path before any app imports
_fd, _tmp_db = tempfile.mkstemp(suffix=".db")
os.close(_fd)
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
async def test_runs_indexes_added_to_existing_database(tmp_path):
    """An older DB without indexes on runs filter columns should gain them after migration (issue #191)."""
    db_path = tmp_path / "indexes_test.db"

    # Create a runs table without any of the target indexes.
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY,
            run_id TEXT NOT NULL UNIQUE,
            project_id INTEGER,
            status TEXT,
            verdict TEXT,
            started_at DATETIME,
            concurrent_group_id TEXT
        )
    """)
    conn.commit()

    cursor = conn.execute("PRAGMA index_list(runs)")
    existing = {row[1] for row in cursor.fetchall()}
    expected = {
        "ix_runs_status",
        "ix_runs_project_id",
        "ix_runs_verdict",
        "ix_runs_started_at",
        "ix_runs_concurrent_group_id",
    }
    assert expected.isdisjoint(existing)
    conn.close()

    from sqlalchemy.ext.asyncio import create_async_engine

    test_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)

    # Run migration twice to confirm idempotency.
    async with test_engine.begin() as aconn:
        await _migrate_add_columns(aconn)
    async with test_engine.begin() as aconn:
        await _migrate_add_columns(aconn)

    await test_engine.dispose()

    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute("PRAGMA index_list(runs)")
    found = {row[1] for row in cursor.fetchall()}
    assert expected.issubset(found), f"missing indexes: {expected - found}"

    # EXPLAIN QUERY PLAN should report an index scan, not a full table scan,
    # for a status filter on the runs table.
    cursor = conn.execute("EXPLAIN QUERY PLAN SELECT * FROM runs WHERE status = 'running'")
    plan = " ".join(str(row) for row in cursor.fetchall())
    assert "ix_runs_status" in plan, f"expected index scan, got plan: {plan}"
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


@pytest.mark.asyncio
async def test_vision_bootstrap_columns_present(tmp_path):
    """The four vision-bootstrap columns must exist after migration."""
    db_path = tmp_path / "vision_bootstrap_test.db"

    # Create minimal runs and projects tables without the vision-bootstrap columns.
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY,
            run_id TEXT NOT NULL,
            project_id INTEGER,
            status TEXT,
            verdict TEXT,
            started_at DATETIME
        )
    """)
    conn.execute("""
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY,
            repo TEXT NOT NULL,
            name TEXT
        )
    """)
    conn.commit()
    conn.close()

    from sqlalchemy.ext.asyncio import create_async_engine

    test_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)

    async with test_engine.begin() as aconn:
        await _migrate_add_columns(aconn)

    await test_engine.dispose()

    conn = sqlite3.connect(str(db_path))
    runs_cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
    projects_cols = {row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
    conn.close()

    assert "skip_reason" in runs_cols
    assert "vision_bootstrap_count" in runs_cols
    assert "vision_bootstrap_proposals" in runs_cols
    assert "last_vision_analyzed_sha" in projects_cols
