"""Per-project runner-resource columns (#386)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import Base, engine
from app.main import app


@pytest.mark.asyncio
async def test_project_runner_quota_columns(async_session_factory):
    from app.models import Project

    async with async_session_factory() as db:
        p = Project(
            repo="x/y",
            branch="main",
            runner_memory_limit=536870912,  # 512 MiB in bytes
            runner_cpu_limit=0.5,
        )
        db.add(p)
        await db.commit()

    async with async_session_factory() as db:
        row = (await db.execute(select(Project).where(Project.repo == "x/y"))).scalar_one()
        assert row.runner_memory_limit == 536870912
        assert row.runner_cpu_limit == 0.5


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
