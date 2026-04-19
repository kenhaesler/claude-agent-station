"""Tests for the permission-tray endpoints — ADR-0001, P2.T10."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, async_session, engine
from app.main import app
from app.models import PermissionRequest


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


async def _seed_pending(
    request_id: str = "req-1",
    *,
    run_id: str = "run-100",
    created_at: datetime | None = None,
    status: str = "pending",
) -> PermissionRequest:
    async with async_session() as session:
        row = PermissionRequest(
            request_id=request_id,
            run_id=run_id,
            agent_id="lead",
            tool_name="Bash",
            tool_input=json.dumps({"command": "rm -rf node_modules"}),
            autonomy_level="assisted",
            reason="destructive bash",
            status=status,
        )
        if created_at is not None:
            row.created_at = created_at
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


# --- GET list ---

@pytest.mark.asyncio
async def test_list_returns_empty_when_no_requests(client):
    resp = await client.get("/api/permissions")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_returns_pending_only_by_default(client, setup_db):
    await _seed_pending("req-pending", status="pending")
    await _seed_pending("req-done", status="approved")

    resp = await client.get("/api/permissions")
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["request_id"] for r in rows] == ["req-pending"]
    assert rows[0]["tool_input"] == {"command": "rm -rf node_modules"}


@pytest.mark.asyncio
async def test_list_filters_by_run_id(client, setup_db):
    await _seed_pending("req-a", run_id="run-A")
    await _seed_pending("req-b", run_id="run-B")

    resp = await client.get("/api/permissions?run_id=run-B")
    assert resp.status_code == 200
    assert [r["request_id"] for r in resp.json()] == ["req-b"]


@pytest.mark.asyncio
async def test_list_status_all_returns_everything(client, setup_db):
    await _seed_pending("req-pending", status="pending")
    await _seed_pending("req-done", status="approved")

    resp = await client.get("/api/permissions?status=all")
    assert resp.status_code == 200
    assert {r["request_id"] for r in resp.json()} == {"req-pending", "req-done"}


# --- GET single ---

@pytest.mark.asyncio
async def test_get_single_request(client, setup_db):
    await _seed_pending("req-1")
    resp = await client.get("/api/permissions/req-1")
    assert resp.status_code == 200
    assert resp.json()["request_id"] == "req-1"


@pytest.mark.asyncio
async def test_get_single_request_404(client):
    resp = await client.get("/api/permissions/missing")
    assert resp.status_code == 404


# --- POST resolve ---

@pytest.mark.asyncio
async def test_approve_transitions_status(client, setup_db):
    await _seed_pending("req-1")
    resp = await client.post("/api/permissions/req-1", json={
        "decision": "approve",
        "note": "operator sign-off",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["resolution_note"] == "operator sign-off"
    assert body["resolved_at"] is not None


@pytest.mark.asyncio
async def test_deny_transitions_status(client, setup_db):
    await _seed_pending("req-1")
    resp = await client.post("/api/permissions/req-1", json={"decision": "deny"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "denied"


@pytest.mark.asyncio
async def test_resolve_twice_returns_409(client, setup_db):
    await _seed_pending("req-1")
    first = await client.post("/api/permissions/req-1", json={"decision": "approve"})
    assert first.status_code == 200

    # Second call is blocked — terminal state is immutable.
    second = await client.post("/api/permissions/req-1", json={"decision": "deny"})
    assert second.status_code == 409
    assert "already resolved" in second.json()["detail"]


@pytest.mark.asyncio
async def test_resolve_missing_returns_404(client):
    resp = await client.post("/api/permissions/missing", json={"decision": "approve"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_decision_must_be_approve_or_deny(client, setup_db):
    await _seed_pending("req-1")
    resp = await client.post("/api/permissions/req-1", json={"decision": "maybe"})
    assert resp.status_code == 422


# --- Timeout / auto-deny ---

@pytest.mark.asyncio
async def test_stale_pending_request_auto_times_out(client, setup_db, monkeypatch):
    """Rows older than the timeout are flipped to timed_out on read."""
    # Keep the timeout small for the test.
    monkeypatch.setenv("STATION_PERMISSION_TRAY_TIMEOUT_SECONDS", "60")
    long_ago = datetime.now(timezone.utc) - timedelta(seconds=120)
    await _seed_pending("req-stale", created_at=long_ago)

    # A plain list call triggers the sweep.
    resp = await client.get("/api/permissions?status=all")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["status"] == "timed_out"
    assert "auto-denied" in (rows[0]["resolution_note"] or "")


@pytest.mark.asyncio
async def test_cannot_resolve_after_timeout(client, setup_db, monkeypatch):
    """Once a row has timed out, approve/deny returns 409 so the operator
    can't retroactively allow a call the agent already moved past."""
    monkeypatch.setenv("STATION_PERMISSION_TRAY_TIMEOUT_SECONDS", "60")
    long_ago = datetime.now(timezone.utc) - timedelta(seconds=120)
    await _seed_pending("req-stale", created_at=long_ago)

    resp = await client.post("/api/permissions/req-stale", json={"decision": "approve"})
    assert resp.status_code == 409
    assert "timed_out" in resp.json()["detail"]


# --- Helper (agent side) ---

@pytest.mark.asyncio
async def test_create_permission_request_helper(setup_db):
    from app.routers.permissions import create_permission_request

    async with async_session() as session:
        row = await create_permission_request(
            session,
            request_id="req-agent",
            run_id="run-A",
            agent_id="teammate-issue-worker",
            tool_name="Bash",
            tool_input={"command": "git reset --hard HEAD~1"},
            autonomy_level="manual",
            reason="destructive at manual",
        )
    assert row.status == "pending"
    # Stored as JSON string; schema decodes on the way out.
    assert json.loads(row.tool_input) == {"command": "git reset --hard HEAD~1"}
