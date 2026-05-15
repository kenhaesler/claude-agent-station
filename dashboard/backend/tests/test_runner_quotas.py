"""Per-project runner-resource columns (#386)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base, engine
from app.main import app


@pytest_asyncio.fixture(scope="module")
async def _quota_engine(tmp_path_factory):
    """Isolated SQLite engine for runner-quota column tests."""
    import subprocess
    import sys
    from pathlib import Path

    db_path = tmp_path_factory.mktemp("quota") / "test.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    backend_root = Path(__file__).resolve().parent.parent
    import os
    env = {
        **os.environ,
        "STATION_DB_URL": db_url,
        "PYTHONPATH": str(backend_root),
    }
    subprocess.check_call(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(backend_root),
        env=env,
    )
    eng = create_async_engine(db_url)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(scope="module")
async def _quota_session(_quota_engine):
    factory = async_sessionmaker(_quota_engine, expire_on_commit=False)
    yield factory


@pytest.mark.asyncio
async def test_project_runner_quota_columns(_quota_session):
    """runner_memory_limit (Integer) and runner_cpu_limit (Float) are persisted."""
    from app.models import Project

    async with _quota_session() as db:
        p = Project(
            repo="x/y",
            branch="main",
            runner_memory_limit=536870912,  # 512 MiB in bytes
            runner_cpu_limit=0.5,
        )
        db.add(p)
        await db.commit()

    async with _quota_session() as db:
        row = (await db.execute(select(Project).where(Project.repo == "x/y"))).scalar_one()
        assert row.runner_memory_limit == 536870912
        assert row.runner_cpu_limit == 0.5


@pytest.mark.asyncio
async def test_project_runner_quota_columns_null_by_default(_quota_session):
    """NULL is stored when no quotas are set (= use compose defaults)."""
    from app.models import Project

    async with _quota_session() as db:
        p = Project(repo="no/quota", branch="main")
        db.add(p)
        await db.commit()

    async with _quota_session() as db:
        row = (await db.execute(select(Project).where(Project.repo == "no/quota"))).scalar_one()
        assert row.runner_memory_limit is None
        assert row.runner_cpu_limit is None


# ---- API tests ----

@pytest_asyncio.fixture
async def _setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(_setup_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
@patch("app.routers.projects.sync_db_to_config", new_callable=AsyncMock)
async def test_project_edit_endpoint_accepts_quotas(mock_sync, client):
    resp = await client.post(
        "/api/projects",
        json={"repo": "edit/q", "branch": "main"},
    )
    assert resp.status_code in (200, 201)
    project_id = resp.json()["id"]

    resp = await client.patch(
        f"/api/projects/{project_id}",
        json={"runner_memory_limit": 1073741824, "runner_cpu_limit": 0.75},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["runner_memory_limit"] == 1073741824
    assert body["runner_cpu_limit"] == 0.75


@pytest.mark.asyncio
@patch("app.routers.projects.sync_db_to_config", new_callable=AsyncMock)
async def test_project_out_includes_quota_fields(mock_sync, client):
    """GET /api/projects/{id} includes runner_memory_limit and runner_cpu_limit."""
    resp = await client.post(
        "/api/projects",
        json={"repo": "out/q", "branch": "main"},
    )
    assert resp.status_code in (200, 201)
    body = resp.json()
    assert "runner_memory_limit" in body
    assert "runner_cpu_limit" in body
    assert body["runner_memory_limit"] is None
    assert body["runner_cpu_limit"] is None
