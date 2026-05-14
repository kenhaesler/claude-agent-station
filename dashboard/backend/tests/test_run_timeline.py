"""Run timeline API tests (issue #387)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, async_session, engine
from app.main import app
from app.schemas import RunTimelineEvent, RunTimelinePage


@pytest_asyncio.fixture(scope="module")
async def setup_db():
    """Create tables once for all tests in this module."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(setup_db):
    """Provide an async HTTP client for testing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def test_timeline_event_shape():
    ev = RunTimelineEvent(
        t=datetime(2026, 5, 13, 15, 14, 8, tzinfo=timezone.utc),
        kind="lifecycle",
        event="run_start",
        source="runs",
        source_id="run-20260513T151408Z",
        agent=None,
        data={"status": "started"},
    )
    assert ev.kind == "lifecycle"
    assert ev.data == {"status": "started"}


def test_timeline_page_default_empty():
    page = RunTimelinePage(run_id="run-x", events=[], next_cursor=None, has_more=False)
    assert page.events == []
    assert page.has_more is False


from app.services.run_timeline import TimelineCursor


def test_cursor_roundtrip():
    c = TimelineCursor(
        t=datetime(2026, 5, 13, 15, 14, 8, tzinfo=timezone.utc),
        source="audit_log",
        source_id="12345",
    )
    encoded = c.encode()
    assert isinstance(encoded, str)
    decoded = TimelineCursor.decode(encoded)
    assert decoded.t == c.t
    assert decoded.source == c.source
    assert decoded.source_id == c.source_id


def test_cursor_decode_rejects_garbage():
    with pytest.raises(ValueError):
        TimelineCursor.decode("not-base64!!!")


from app.models import AgentEvent, AuditEntry, Run
from app.services.run_timeline import _lifecycle_events


@pytest.mark.asyncio
async def test_lifecycle_events_emits_run_start_and_complete(setup_db):
    run_id = "run-tl-lifecycle-1"
    async with async_session() as db:
        db.add(
            Run(
                run_id=run_id,
                status="success",
                started_at=datetime(2026, 5, 13, 15, 14, 8, tzinfo=timezone.utc),
                finished_at=datetime(2026, 5, 13, 15, 30, 0, tzinfo=timezone.utc),
                verdict="APPROVE",
            )
        )
        db.add(
            AgentEvent(
                workflow_id="wf-1",
                run_id=run_id,
                agent_id="lead",
                event_type="lifecycle.orchestrator_complete",
                event_data="{}",
                created_at=datetime(2026, 5, 13, 15, 29, 50, tzinfo=timezone.utc),
            )
        )
        await db.commit()

    async with async_session() as db:
        events = await _lifecycle_events(db, run_id, since=None, until=None)

    kinds = [(e.event, e.source) for e in events]
    assert ("run_start", "runs") in kinds
    assert ("run_complete", "runs") in kinds
    assert ("lifecycle.orchestrator_complete", "agent_events") in kinds
    # Ordered ascending by t.
    assert [e.t for e in events] == sorted(e.t for e in events)


from app.models import ConflictResolution, CoordinatorTask
from app.services.run_timeline import (
    TimelineCursor,
    _audit_events,
    _conflict_events,
    _teammate_events,
    _verdict_events,
    build_timeline,
)


@pytest.mark.asyncio
async def test_audit_events_emits_ok_and_error(setup_db):
    run_id = "run-tl-audit-1"
    async with async_session() as db:
        db.add(
            AuditEntry(
                idempotency_key="k1",
                run_id=run_id,
                actor="teammate-backend",
                action_kind="tool.bash",
                action_detail='{"command":"ls"}',
                status="ok",
                exit_code=0,
                stdout_tail="ok",
                started_at=datetime(2026, 5, 13, 15, 15, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 5, 13, 15, 15, 1, tzinfo=timezone.utc),
            )
        )
        db.add(
            AuditEntry(
                idempotency_key="k2",
                run_id=run_id,
                actor="teammate-qa",
                action_kind="tool.edit",
                action_detail=None,
                status="error",
                exit_code=1,
                stdout_tail=None,
                stderr_tail="boom",
                started_at=datetime(2026, 5, 13, 15, 16, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 5, 13, 15, 16, 2, tzinfo=timezone.utc),
            )
        )
        await db.commit()

    async with async_session() as db:
        events = await _audit_events(db, run_id, since=None, until=None)

    assert [e.event for e in events] == ["tool.bash.ok", "tool.edit.error"]
    assert events[0].agent == "teammate-backend"
    assert events[0].data["exit_code"] == 0
    assert events[1].data["truncated"] is False


@pytest.mark.asyncio
async def test_teammate_verdict_conflict_events(setup_db):
    run_id = "run-tl-mixed-1"
    async with async_session() as db:
        db.add(
            CoordinatorTask(
                id="t-1",
                run_id=run_id,
                project_repo="x/y",
                status="completed",
                started_at=datetime(2026, 5, 13, 15, 20, 0, tzinfo=timezone.utc),
                claimed_at=datetime(2026, 5, 13, 15, 20, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 5, 13, 15, 22, 0, tzinfo=timezone.utc),
                teammate_agent_id="backend",
                title="login api",
            )
        )
        db.add(
            AgentEvent(
                workflow_id="wf-2",
                run_id=run_id,
                agent_id="manager",
                event_type="verdict_execute",
                event_data='{"verdict":"PR"}',
                created_at=datetime(2026, 5, 13, 15, 25, 0, tzinfo=timezone.utc),
            )
        )
        db.add(
            ConflictResolution(
                branch="feature/x",
                repo="x/y",
                phase_reached="llm",
                outcome="resolved",
                triggered_by="pre_pr",
                started_at=datetime(2026, 5, 13, 15, 23, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 5, 13, 15, 23, 30, tzinfo=timezone.utc),
            )
        )
        # ConflictResolution doesn't have run_id directly; tag via branch / runs.
        # For this test we simulate by giving the run a matching branch.
        db.add(
            Run(
                run_id=run_id,
                status="running",
                started_at=datetime(2026, 5, 13, 15, 14, 8, tzinfo=timezone.utc),
                branch="feature/x",
            )
        )
        await db.commit()

    async with async_session() as db:
        t_events = await _teammate_events(db, run_id, since=None, until=None)
        v_events = await _verdict_events(db, run_id, since=None, until=None)
        c_events = await _conflict_events(db, run_id, since=None, until=None)

    assert {e.event for e in t_events} == {"teammate.spawned", "teammate.completed"}
    assert t_events[0].agent == "backend"
    assert [e.event for e in v_events] == ["verdict_execute"]
    assert {e.event for e in c_events} == {"conflict.started", "conflict.resolved"}


@pytest.mark.asyncio
async def test_build_timeline_merges_all_sources_in_order(setup_db):
    run_id = "run-tl-merge-1"
    async with async_session() as db:
        db.add(
            Run(
                run_id=run_id,
                status="success",
                started_at=datetime(2026, 5, 13, 15, 0, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 5, 13, 15, 30, 0, tzinfo=timezone.utc),
                branch="feature/m",
                verdict="APPROVE",
            )
        )
        db.add(
            AuditEntry(
                idempotency_key="m-k1",
                run_id=run_id,
                actor="lead",
                action_kind="tool.bash",
                status="ok",
                started_at=datetime(2026, 5, 13, 15, 10, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 5, 13, 15, 10, 1, tzinfo=timezone.utc),
            )
        )
        db.add(
            CoordinatorTask(
                id="m-t1",
                run_id=run_id,
                project_repo="x/y",
                status="completed",
                started_at=datetime(2026, 5, 13, 15, 5, 0, tzinfo=timezone.utc),
                claimed_at=datetime(2026, 5, 13, 15, 5, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 5, 13, 15, 20, 0, tzinfo=timezone.utc),
                teammate_agent_id="qa",
                title="qa task",
            )
        )
        await db.commit()

    async with async_session() as db:
        page = await build_timeline(db, run_id, kinds=None, since=None, until=None, limit=500, cursor=None)

    times = [e.t for e in page.events]
    assert times == sorted(times)
    assert page.has_more is False
    assert page.next_cursor is None
    kinds = {e.kind for e in page.events}
    assert {"lifecycle", "tool", "teammate"} <= kinds


@pytest.mark.asyncio
async def test_build_timeline_filters_by_kind(setup_db):
    async with async_session() as db:
        page = await build_timeline(
            db, "run-tl-merge-1", kinds={"tool"}, since=None, until=None, limit=500, cursor=None
        )
    assert page.events
    assert {e.kind for e in page.events} == {"tool"}


@pytest.mark.asyncio
async def test_build_timeline_paginates_with_cursor(setup_db):
    run_id = "run-tl-paginate-1"
    async with async_session() as db:
        db.add(Run(run_id=run_id, status="running",
                   started_at=datetime(2026, 5, 13, 15, 0, 0, tzinfo=timezone.utc)))
        for i in range(1200):
            db.add(
                AuditEntry(
                    idempotency_key=f"p-{i}",
                    run_id=run_id,
                    actor="lead",
                    action_kind="tool.bash",
                    status="ok",
                    started_at=datetime(2026, 5, 13, 15, 0, 0, tzinfo=timezone.utc).replace(microsecond=i),
                )
            )
        await db.commit()

    seen_ids: set[tuple[str, str]] = set()
    cursor = None
    pages = 0
    async with async_session() as db:
        while True:
            page = await build_timeline(
                db, run_id, kinds={"tool"}, since=None, until=None, limit=500, cursor=cursor
            )
            pages += 1
            for ev in page.events:
                key = (ev.source, ev.source_id)
                assert key not in seen_ids
                seen_ids.add(key)
            if not page.has_more:
                break
            cursor = TimelineCursor.decode(page.next_cursor)
    assert pages == 3
    assert len(seen_ids) == 1200


from httpx import AsyncClient


@pytest.mark.asyncio
async def test_timeline_endpoint_returns_page(client: AsyncClient):
    # Reuse `run-tl-merge-1` seeded earlier — tests run in order on shared db.
    resp = await client.get("/api/runs/run-tl-merge-1/timeline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == "run-tl-merge-1"
    assert isinstance(body["events"], list)
    assert body["events"], "expected at least one event"
    assert body["has_more"] is False


@pytest.mark.asyncio
async def test_timeline_endpoint_rejects_unknown_kind(client: AsyncClient):
    resp = await client.get("/api/runs/run-tl-merge-1/timeline?kinds=lifecycle,bogus")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_timeline_endpoint_404_for_unknown_run(client: AsyncClient):
    resp = await client.get("/api/runs/run-does-not-exist/timeline")
    assert resp.status_code == 404
