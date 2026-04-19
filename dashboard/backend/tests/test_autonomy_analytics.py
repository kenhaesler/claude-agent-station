"""Tests for the autonomy audit + analytics endpoints (P3.T8, P3.T9).

Covers both `auto_mode_decision` and `auto_mode_referral` event shapes,
filtering by run_id/tool/decision/event_type, and the aggregation shape
returned by `/api/analytics/autonomy`.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, async_session, engine
from app.main import app
from app.models import AgentEvent


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


async def _add_event(
    *,
    event_type: str,
    data: dict,
    run_id: str = "run-1",
    agent_id: str = "lead",
    created_at: datetime | None = None,
) -> None:
    async with async_session() as session:
        row = AgentEvent(
            workflow_id=run_id,
            run_id=run_id,
            agent_id=agent_id,
            event_type=event_type,
            team_name=None,
            event_data=json.dumps(data),
        )
        if created_at is not None:
            row.created_at = created_at
        session.add(row)
        await session.commit()


# --- Audit endpoint --------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_empty_ok(client):
    resp = await client.get("/api/analytics/autonomy-audit")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"total": 0, "limit": 200, "offset": 0, "items": []}


@pytest.mark.asyncio
async def test_audit_surfaces_both_event_types(client, setup_db):
    await _add_event(
        event_type="auto_mode_decision",
        data={"level": "assisted", "tool_name": "Read", "decision": "allow",
              "tool_input": {"file_path": "/etc/hosts"}, "reason": ""},
    )
    await _add_event(
        event_type="auto_mode_referral",
        data={"level": "manual", "tool_name": "Bash",
              "tool_input": {"command": "rm -rf build"},
              "request_id": "tray-abc", "final_status": "approved",
              "reason": "destructive"},
    )
    resp = await client.get("/api/analytics/autonomy-audit")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    types = {r["event_type"] for r in items}
    assert types == {"auto_mode_decision", "auto_mode_referral"}
    # Normalised decision field — approved → allow
    referral = next(r for r in items if r["event_type"] == "auto_mode_referral")
    assert referral["decision"] == "allow"
    assert referral["request_id"] == "tray-abc"


@pytest.mark.asyncio
async def test_audit_filter_by_run(client, setup_db):
    await _add_event(event_type="auto_mode_decision",
                     data={"level": "assisted", "tool_name": "Read", "decision": "allow"},
                     run_id="run-A")
    await _add_event(event_type="auto_mode_decision",
                     data={"level": "manual", "tool_name": "Bash", "decision": "deny"},
                     run_id="run-B")
    resp = await client.get("/api/analytics/autonomy-audit?run_id=run-B")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [r["run_id"] for r in items] == ["run-B"]


@pytest.mark.asyncio
async def test_audit_filter_by_tool_and_decision(client, setup_db):
    await _add_event(event_type="auto_mode_decision",
                     data={"level": "assisted", "tool_name": "Read", "decision": "allow"})
    await _add_event(event_type="auto_mode_decision",
                     data={"level": "manual", "tool_name": "Bash", "decision": "deny"})
    await _add_event(event_type="auto_mode_decision",
                     data={"level": "assisted", "tool_name": "Bash", "decision": "deny"})

    resp = await client.get("/api/analytics/autonomy-audit?tool_name=Bash&decision=deny")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert all(r["tool_name"] == "Bash" and r["decision"] == "deny" for r in body["items"])


@pytest.mark.asyncio
async def test_audit_filter_by_event_type(client, setup_db):
    await _add_event(event_type="auto_mode_decision",
                     data={"level": "assisted", "tool_name": "Read", "decision": "allow"})
    await _add_event(event_type="auto_mode_referral",
                     data={"level": "manual", "tool_name": "Bash",
                           "final_status": "denied"})
    resp = await client.get("/api/analytics/autonomy-audit?event_type=auto_mode_referral")
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["event_type"] == "auto_mode_referral"


@pytest.mark.asyncio
async def test_audit_ignores_other_event_types(client, setup_db):
    await _add_event(event_type="task.claimed", data={"irrelevant": True})
    resp = await client.get("/api/analytics/autonomy-audit")
    assert resp.json()["items"] == []


@pytest.mark.asyncio
async def test_audit_newest_first(client, setup_db):
    # Seed in mixed order; server must return newest first.
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    await _add_event(event_type="auto_mode_decision",
                     data={"level": "assisted", "tool_name": "Read", "decision": "allow"},
                     created_at=old)
    await _add_event(event_type="auto_mode_decision",
                     data={"level": "manual", "tool_name": "Bash", "decision": "deny"})
    resp = await client.get("/api/analytics/autonomy-audit")
    items = resp.json()["items"]
    assert items[0]["tool_name"] == "Bash"  # newer
    assert items[1]["tool_name"] == "Read"


# --- Summary endpoint ------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_empty(client):
    resp = await client.get("/api/analytics/autonomy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_decisions"] == 0
    assert body["by_level"] == {}
    assert body["by_decision"] == {}


@pytest.mark.asyncio
async def test_summary_crosstab(client, setup_db):
    # 2 manual denies + 1 assisted allow + 1 auto allow (bash destructive).
    await _add_event(event_type="auto_mode_decision",
                     data={"level": "manual", "tool_name": "Bash", "decision": "deny"})
    await _add_event(event_type="auto_mode_decision",
                     data={"level": "manual", "tool_name": "Edit", "decision": "deny"})
    await _add_event(event_type="auto_mode_decision",
                     data={"level": "assisted", "tool_name": "Read", "decision": "allow"})
    await _add_event(event_type="auto_mode_decision",
                     data={"level": "auto", "tool_name": "Bash", "decision": "allow"})
    # Referral — approve maps to allow.
    await _add_event(event_type="auto_mode_referral",
                     data={"level": "manual", "tool_name": "Bash",
                           "final_status": "approved"})

    resp = await client.get("/api/analytics/autonomy")
    body = resp.json()
    assert body["total_decisions"] == 5
    assert body["by_level"] == {"manual": 3, "assisted": 1, "auto": 1}
    assert body["by_decision"] == {"deny": 2, "allow": 3}
    assert body["by_level_decision"]["manual"] == {"deny": 2, "allow": 1}
    assert body["by_event_type"]["auto_mode_decision"] == 4
    assert body["by_event_type"]["auto_mode_referral"] == 1


@pytest.mark.asyncio
async def test_summary_windowed_to_days(client, setup_db):
    within = datetime.now(timezone.utc) - timedelta(days=2)
    outside = datetime.now(timezone.utc) - timedelta(days=40)
    await _add_event(event_type="auto_mode_decision",
                     data={"level": "auto", "tool_name": "Bash", "decision": "allow"},
                     created_at=within)
    await _add_event(event_type="auto_mode_decision",
                     data={"level": "auto", "tool_name": "Bash", "decision": "allow"},
                     created_at=outside)

    resp = await client.get("/api/analytics/autonomy?days=7")
    body = resp.json()
    assert body["total_decisions"] == 1
