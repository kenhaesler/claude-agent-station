"""Regression tests for issue #450 — incoherent finished state.

A live run row was observed flipping to ``status="completed"`` with
``finished_at`` set while the corresponding runner container was still alive
and still emitting heartbeats / narration / progress_update / Lead-agent tool
calls for 191 seconds afterwards.

Webhook tally for the offending run ``run-20260517T100903Z``:

    narration, heartbeat, progress_update, teammate_spawned, run_start,
    teammate_completed, orchestrator_start, employee_start

Conspicuously absent: ``run_complete``, ``orchestrator_complete``, ``finished``,
``manager_review``, ``manager_no_verdicts``, ``verdict_execute``,
``project_skipped_no_work``. None of the events that legitimately drive
``handle_finished`` ever fired.

Smoking gun: ``teammate_completed`` is NOT in the dispatcher's main
``_RUN_HANDLERS`` / ``_TASK_EVENTS`` / ``_MESSAGE_EVENTS`` / ``_DAG_EVENTS``
sets, so it falls through the if/elif chain to ``handle_unknown``. The
orchestrator emits ``teammate_completed`` with ``status="completed"``
(the *teammate's* terminal status — see
``agent/station_orchestrator.py::TaskNotificationMessage`` branch). And
``handle_unknown`` does ``run.status = event.status`` unconditionally,
which conflates the teammate's status with the *run's* status and flips
the parent run row terminal.

Once ``run.status == "completed"``, the log_importer's backfill loop
populates ``finished_at`` from ``started_at + duration_ms`` (see
``log_importer._update_run_from_logs`` line 120), producing the
``finished_at`` half of the incoherence.

These tests pin the contract: a ``teammate_completed`` webhook MUST NOT
mutate the parent ``runs.status`` to a terminal state. The only events
that may flip ``status`` to a terminal value are the explicit run-level
events handled by :mod:`app.services.run_lifecycle` (``finished``,
``plan_approved``, ``plan_rejected``) — not teammate-scoped events.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

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


async def _seed_running_run(run_id: str = "run-450-regression") -> None:
    """Insert a row mirroring an active run mid-flight: status=running, no
    finished_at, no terminal verdict — the state the offending run was in
    before the teammate_completed event flipped it."""
    from datetime import datetime, timezone
    async with async_session() as db:
        db.add(Run(
            run_id=run_id,
            status="running",
            started_at=datetime.now(timezone.utc),
        ))
        await db.commit()


async def _fetch_run(run_id: str) -> Run | None:
    async with async_session() as db:
        result = await db.execute(select(Run).where(Run.run_id == run_id))
        return result.scalar_one_or_none()


@pytest.mark.asyncio
async def test_teammate_completed_does_not_flip_run_status_to_completed(
    client: AsyncClient,
) -> None:
    """The smoking gun. A teammate finishing must not mark the parent run done."""
    run_id = "run-450-regression"
    await _seed_running_run(run_id)

    # Mirror the orchestrator's emit at station_orchestrator.py:1665 — the
    # teammate's own terminal status, NOT the run's.
    resp = await client.post("/api/webhook/run-event", json={
        "run_id": run_id,
        "event": "teammate_completed",
        "task_id": "task-1",
        "status": "completed",
        "agent_name": "backend",
        "tokens_total": 500,
        "turns": 10,
    })
    assert resp.status_code == 200

    run = await _fetch_run(run_id)
    assert run is not None
    assert run.status == "running", (
        f"teammate_completed event flipped run.status to {run.status!r} — "
        "this is issue #450. The dispatcher fell through to handle_unknown, "
        "which propagated the teammate's status to the run row."
    )
    assert run.finished_at is None, (
        f"teammate_completed event set finished_at={run.finished_at!r} — "
        "issue #450. No terminal-lifecycle handler should have fired here."
    )


@pytest.mark.asyncio
async def test_full_event_sequence_from_offending_run_keeps_status_running(
    client: AsyncClient,
) -> None:
    """Replay the actual event sequence observed for run-20260517T100903Z.

    The tally was: narration, heartbeat, progress_update, teammate_spawned,
    run_start, teammate_completed, orchestrator_start, employee_start.
    Notably absent: any event that legitimately drives ``handle_finished``.
    After replaying every one of these, status MUST still be ``running``.
    """
    run_id = "run-450-sequence"

    sequence = [
        {"event": "run_start", "run_id": run_id, "mode": "auto", "model": "sonnet-4-6"},
        {"event": "orchestrator_start", "run_id": run_id},
        {"event": "employee_start", "run_id": run_id, "employee_index": 0},
        {"event": "teammate_spawned", "run_id": run_id, "task_id": "t1",
         "agent_id": "a1", "agent_name": "backend", "team_name": "team-1"},
        {"event": "narration", "run_id": run_id,
         "narration": "Spawning teammate", "narration_kind": "system"},
        {"event": "heartbeat", "run_id": run_id},
        {"event": "progress_update", "run_id": run_id,
         "tokens_total": 200, "turns": 4},
        {"event": "teammate_completed", "run_id": run_id, "task_id": "t1",
         "status": "completed", "agent_name": "backend",
         "tokens_total": 500, "turns": 10},
        {"event": "narration", "run_id": run_id,
         "narration": "Finished (completed)", "narration_kind": "step"},
        {"event": "heartbeat", "run_id": run_id},
    ]

    for ev in sequence:
        resp = await client.post("/api/webhook/run-event", json=ev)
        assert resp.status_code == 200, (
            f"event {ev['event']!r} returned {resp.status_code}: {resp.text}"
        )
        run = await _fetch_run(run_id)
        assert run is not None
        assert run.status == "running", (
            f"After event {ev['event']!r}, run.status is {run.status!r} — "
            f"should still be 'running'. Issue #450."
        )
        assert run.finished_at is None, (
            f"After event {ev['event']!r}, run.finished_at={run.finished_at!r} "
            f"— should still be None. Issue #450."
        )


@pytest.mark.asyncio
async def test_teammate_completed_with_error_status_does_not_fail_run(
    client: AsyncClient,
) -> None:
    """A teammate failing must not propagate to the parent run row either —
    other teammates may still be running, and the run-level outcome is only
    determined by ``handle_finished``."""
    run_id = "run-450-teammate-error"
    await _seed_running_run(run_id)

    resp = await client.post("/api/webhook/run-event", json={
        "run_id": run_id,
        "event": "teammate_completed",
        "task_id": "task-2",
        "status": "error",
        "agent_name": "qa",
    })
    assert resp.status_code == 200

    run = await _fetch_run(run_id)
    assert run is not None
    assert run.status == "running"
    assert run.finished_at is None


@pytest.mark.asyncio
async def test_finished_webhook_still_marks_terminal(
    client: AsyncClient,
) -> None:
    """Sanity counter-test: the legitimate ``finished`` lifecycle event MUST
    still flip status and finished_at. Otherwise we've over-corrected."""
    run_id = "run-450-finished-counter"
    await _seed_running_run(run_id)

    resp = await client.post("/api/webhook/run-event", json={
        "run_id": run_id,
        "event": "run_complete",  # normalised to "finished"
        "status": "success",
    })
    assert resp.status_code == 200

    run = await _fetch_run(run_id)
    assert run is not None
    assert run.status == "completed"
    assert run.finished_at is not None
