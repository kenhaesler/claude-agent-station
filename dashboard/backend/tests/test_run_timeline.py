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


from app.models import AgentEvent, Run
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
