"""Tests for audit_log feature (issue #73): model, router, retention.

Covers:
- AuditEntry insert + idempotency_key UNIQUE constraint
- GET /api/audit?run_id=X returns ordered timeline
- GET /api/audit?trace_id=X scopes by trace
- GET /api/audit returns 400 without run_id or trace_id
- GET /api/audit/stats returns by_kind/error_rate/avg_duration_ms
- prune_audit_log deletes rows older than the retention window
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import Base, async_session, engine
from app.main import app
from app.models import AuditEntry
from app.services.audit_retention import prune_audit_log


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


# Sentinel marker so the factory can distinguish "caller passed None
# explicitly" from "caller did not pass anything"; the defaults for
# finished_at and exit_code depend on whether status is terminal.
_UNSET: object = object()


def _entry(
    *,
    key: str,
    run_id: str = "run-001",
    trace_id: str | None = "trace-001",
    actor: str = "lead",
    action_kind: str = "tool.bash",
    status: str = "ok",
    started_at: datetime | None = None,
    finished_at: object = _UNSET,
    exit_code: object = _UNSET,
) -> AuditEntry:
    started = started_at or datetime.now(timezone.utc)
    # 'started' rows have not finished yet — finished_at and exit_code
    # are NULL until PostToolUse fires.
    is_terminal = status != "started"
    if finished_at is _UNSET:
        finished_at = started + timedelta(milliseconds=120) if is_terminal else None
    if exit_code is _UNSET:
        exit_code = 0 if is_terminal else None
    return AuditEntry(
        idempotency_key=key,
        trace_id=trace_id,
        run_id=run_id,
        actor=actor,
        action_kind=action_kind,
        action_detail='{"tool_name": "Bash"}',
        status=status,
        exit_code=exit_code,
        stdout_tail="ok" if is_terminal else None,
        stderr_tail=None,
        started_at=started,
        finished_at=finished_at,
    )


# --- model -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_and_round_trip(setup_db):
    async with async_session() as db:
        db.add(_entry(key="k-1"))
        await db.commit()

        rows = (await db.execute(select(AuditEntry))).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.idempotency_key == "k-1"
        assert row.action_kind == "tool.bash"
        assert row.status == "ok"


@pytest.mark.asyncio
async def test_idempotency_key_unique_constraint(setup_db):
    async with async_session() as db:
        db.add(_entry(key="dup"))
        await db.commit()

    with pytest.raises(IntegrityError):
        async with async_session() as db:
            db.add(_entry(key="dup"))
            await db.commit()


# --- GET /api/audit --------------------------------------------------------


@pytest.mark.asyncio
async def test_list_requires_run_or_trace(client):
    resp = await client.get("/api/audit")
    assert resp.status_code == 400
    assert "run_id" in resp.json()["detail"] or "trace_id" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_list_by_run_id_returns_ordered_timeline(client):
    base = datetime.now(timezone.utc)
    async with async_session() as db:
        db.add(_entry(key="a", run_id="run-A", started_at=base + timedelta(seconds=2)))
        db.add(_entry(key="b", run_id="run-A", started_at=base + timedelta(seconds=1)))
        db.add(_entry(key="c", run_id="run-A", started_at=base + timedelta(seconds=3)))
        db.add(_entry(key="other", run_id="run-B"))
        await db.commit()

    resp = await client.get("/api/audit", params={"run_id": "run-A"})
    assert resp.status_code == 200
    body = resp.json()
    keys = [r["idempotency_key"] for r in body]
    assert keys == ["b", "a", "c"]


@pytest.mark.asyncio
async def test_list_by_trace_id_scopes_results(client):
    async with async_session() as db:
        db.add(_entry(key="t1", trace_id="trace-X", run_id="run-1"))
        db.add(_entry(key="t2", trace_id="trace-X", run_id="run-2"))
        db.add(_entry(key="other", trace_id="trace-Y", run_id="run-3"))
        await db.commit()

    resp = await client.get("/api/audit", params={"trace_id": "trace-X"})
    assert resp.status_code == 200
    keys = sorted(r["idempotency_key"] for r in resp.json())
    assert keys == ["t1", "t2"]


@pytest.mark.asyncio
async def test_list_filters_by_action_kind_and_status(client):
    async with async_session() as db:
        db.add(_entry(key="k1", run_id="run-Z", action_kind="tool.bash", status="ok"))
        db.add(_entry(key="k2", run_id="run-Z", action_kind="tool.edit", status="ok"))
        db.add(_entry(key="k3", run_id="run-Z", action_kind="tool.bash", status="error"))
        await db.commit()

    resp = await client.get(
        "/api/audit",
        params={"run_id": "run-Z", "action_kind": "tool.bash", "status": "ok"},
    )
    assert resp.status_code == 200
    keys = [r["idempotency_key"] for r in resp.json()]
    assert keys == ["k1"]


# --- GET /api/audit/stats --------------------------------------------------


@pytest.mark.asyncio
async def test_stats_returns_kind_distribution_and_error_rate(client):
    base = datetime.now(timezone.utc) - timedelta(hours=1)
    async with async_session() as db:
        db.add(_entry(key="s1", action_kind="tool.bash", status="ok",
                      started_at=base, finished_at=base + timedelta(milliseconds=100)))
        db.add(_entry(key="s2", action_kind="tool.bash", status="error",
                      started_at=base, finished_at=base + timedelta(milliseconds=300)))
        db.add(_entry(key="s3", action_kind="tool.edit", status="ok",
                      started_at=base, finished_at=base + timedelta(milliseconds=200)))
        # 'started' rows are excluded from the error rate denominator.
        db.add(_entry(key="s4", action_kind="tool.read", status="started",
                      started_at=base, finished_at=None))
        await db.commit()

    resp = await client.get("/api/audit/stats", params={"days": 7})
    assert resp.status_code == 200
    body = resp.json()
    assert body["days"] == 7
    assert body["total"] == 4
    assert body["by_kind"]["tool.bash"] == 2
    assert body["by_kind"]["tool.edit"] == 1
    # 1 error / 3 finished rows = 0.3333…
    assert abs(body["error_rate"] - (1 / 3)) < 0.01
    assert body["avg_duration_ms"] is not None
    assert body["avg_duration_ms"] > 0


# --- retention -------------------------------------------------------------


@pytest.mark.asyncio
async def test_prune_audit_log_deletes_old_rows_only(setup_db):
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=45)
    recent = now - timedelta(days=2)

    async with async_session() as db:
        db.add(_entry(key="old-1", started_at=old, finished_at=old + timedelta(seconds=1)))
        db.add(_entry(key="old-2", started_at=old, finished_at=old + timedelta(seconds=1)))
        db.add(_entry(key="recent", started_at=recent, finished_at=recent + timedelta(seconds=1)))
        await db.commit()

    async with async_session() as db:
        pruned = await prune_audit_log(db, days=30)
    assert pruned == 2

    async with async_session() as db:
        rows = (await db.execute(select(AuditEntry))).scalars().all()
        assert [r.idempotency_key for r in rows] == ["recent"]
