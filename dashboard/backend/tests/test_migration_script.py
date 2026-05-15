"""SQLite -> Postgres migration script (#393)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.asyncio
async def test_row_count_parity_per_table(postgres_url):
    # Seed a SQLite source with rows in every table.
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        sqlite_path = f.name
    sqlite_url = f"sqlite+aiosqlite:///{sqlite_path}"

    env = {**os.environ, "STATION_DB_URL": sqlite_url}
    subprocess.check_call(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(REPO_ROOT / "dashboard/backend"),
        env=env,
    )

    src_engine = create_async_engine(sqlite_url)
    from app.models import AgentEvent, Run

    async with async_sessionmaker(src_engine, expire_on_commit=False)() as db:
        db.add(Run(run_id="rmig-1", status="success",
                   started_at=datetime.now(timezone.utc),
                   employee_report=json.dumps({"k": "v"})))
        db.add(AgentEvent(workflow_id="w", run_id="rmig-1", agent_id="lead",
                          event_type="lifecycle.run_start",
                          event_data='{"foo":"bar"}',
                          created_at=datetime.now(timezone.utc)))
        await db.commit()
    await src_engine.dispose()

    # Prepare a fresh Postgres target with Alembic baseline applied.
    env_pg = {**os.environ, "STATION_DB_URL": postgres_url}
    subprocess.check_call(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(REPO_ROOT / "dashboard/backend"),
        env=env_pg,
    )

    # Test-isolation: the ephemeral Postgres container is session-scoped,
    # so prior parametrized tests may have left rows in it. The migrator
    # uses ON CONFLICT DO NOTHING and would silently skip rmig-1 if an
    # auto-increment ``id=1`` was already taken. Clear every model table
    # via SQLAlchemy's parametrized DELETE so the migrator sees a clean
    # target. Iterate ``sorted_tables`` in reverse (FK-dependent first)
    # to avoid foreign-key violations.
    from app.database import Base
    pg_engine_setup = create_async_engine(postgres_url)
    async with pg_engine_setup.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
    await pg_engine_setup.dispose()

    # Run the converter.
    proc = subprocess.run(
        [
            sys.executable, "-m", "scripts.migrate_sqlite_to_postgres",
            "--sqlite", sqlite_path,
            "--postgres", postgres_url,
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr

    # Parity check.
    pg_engine = create_async_engine(postgres_url)
    async with async_sessionmaker(pg_engine, expire_on_commit=False)() as db:
        run = (await db.execute(select(Run).where(Run.run_id == "rmig-1"))).scalar_one()
        assert run.status == "success"
        # JSONB roundtrip: stored as dict on Postgres regardless of source format.
        assert run.employee_report == {"k": "v"}
        evs = (await db.execute(select(AgentEvent).where(AgentEvent.run_id == "rmig-1"))).scalars().all()
        assert len(evs) == 1
        assert evs[0].event_data == {"foo": "bar"}
    await pg_engine.dispose()
    os.unlink(sqlite_path)
