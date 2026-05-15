"""GET /api/runs/{run_id}/tree (#391).

The endpoint returns the parent run's identity plus a flat list of its
sub-runs, scanned via ``Run.parent_run_id``. The dashboard's tree view
(landed in PR-4) renders this payload directly.

Uses the ``app.database.engine`` + ``Base.metadata.create_all`` pattern
shared by the other router tests (see ``test_audit_log.py``); the
parametrized ``async_session_factory`` fixture targets the alembic-
migrated test DBs which the in-process app engine doesn't share.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, async_session, engine
from app.main import app
from app.models import Run


@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(setup_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_tree_endpoint_returns_parent_and_subs(client):
    async with async_session() as db:
        db.add(Run(run_id="run-pt-1", run_kind="primary",
                   started_at=datetime.now(timezone.utc)))
        db.add(Run(run_id="run-pt-1-a", run_kind="sub-of-27",
                   parent_run_id="run-pt-1",
                   started_at=datetime.now(timezone.utc),
                   verdict="APPROVE"))
        db.add(Run(run_id="run-pt-1-b", run_kind="sub-of-27",
                   parent_run_id="run-pt-1",
                   started_at=datetime.now(timezone.utc),
                   verdict="PR"))
        await db.commit()

    resp = await client.get("/api/runs/run-pt-1/tree")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == "run-pt-1"
    assert body["run_kind"] == "primary"
    sub_ids = {s["run_id"] for s in body["sub_runs"]}
    assert sub_ids == {"run-pt-1-a", "run-pt-1-b"}


@pytest.mark.asyncio
async def test_tree_endpoint_404_for_unknown(client):
    resp = await client.get("/api/runs/run-does-not-exist/tree")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_tree_endpoint_returns_empty_subs_for_non_split_run(client):
    """A regular non-split run still has a tree response, just empty."""
    async with async_session() as db:
        db.add(Run(run_id="run-solo", run_kind=None,
                   started_at=datetime.now(timezone.utc)))
        await db.commit()

    resp = await client.get("/api/runs/run-solo/tree")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sub_runs"] == []
    assert body["run_kind"] is None
