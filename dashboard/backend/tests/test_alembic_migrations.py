"""Alembic baseline tests (#393).

NOTE: The plan originally targeted test_migrations.py, but that file already
existed (testing _migrate_add_columns). This file uses the name
test_alembic_migrations.py to avoid collision. See PR-1 drift notes.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


def test_alembic_config_exists():
    assert (Path(__file__).parent.parent / "alembic.ini").exists()


def test_alembic_history_runs():
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "history"],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


import importlib

from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base


@pytest.mark.asyncio
async def test_alembic_baseline_creates_full_schema():
    """A fresh `alembic upgrade head` produces a schema isomorphic to
    Base.metadata.create_all + everything _migrate_add_columns adds.

    NOTE: We avoid importlib.reload(app.database) here because it replaces the
    module-level engine and corrupts the shared in-process test DB used by
    subsequent tests. Instead, we pass STATION_DB_URL only to the subprocess.
    """
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    env = {**os.environ, "STATION_DB_URL": f"sqlite+aiosqlite:///{db_path}"}
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.connect() as conn:
        rows = await conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = {r[0] for r in rows.fetchall()}
    await engine.dispose()
    # Check a fixed set of key tables that must exist after the baseline revision.
    expected = {
        "runs", "projects", "agent_events", "audit_log", "coordinator_tasks",
        "task_queue", "task_outcomes", "config", "station_control",
    }
    assert expected <= names, f"Missing tables after alembic upgrade head: {expected - names}"
    os.unlink(db_path)


@pytest.mark.asyncio
async def test_init_db_runs_alembic_only():
    """init_db must invoke `alembic upgrade head`, not _migrate_add_columns."""
    import app.database as mod
    calls: list[str] = []

    def fake_check_call(cmd, *args, **kw):
        calls.append(" ".join(cmd))
        return 0

    import subprocess as sp
    orig = sp.check_call
    sp.check_call = fake_check_call
    try:
        await mod.init_db()
    finally:
        sp.check_call = orig
    assert any("alembic" in c and "upgrade" in c for c in calls)
