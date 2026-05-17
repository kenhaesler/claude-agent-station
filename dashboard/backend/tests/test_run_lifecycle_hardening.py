"""Regression tests for issues #453 and #454 — run_lifecycle hardening.

Two defensive-programming follow-ups from PRs #438 (issue #434, telemetry
oscillation) and #452 (issue #450, teammate_completed flipping run.status):

* **#454 — non-monotonic telemetry in ``handle_finished``.** The terminal
  ``finished`` event used to unconditionally clobber ``run.turns`` /
  ``run.tokens_*`` / ``run.cost_usd`` with whatever the event carried, even
  if that was lower than the values already accumulated by repeated
  ``progress_update`` events. Live reproduction:
  ``run-20260517T144539Z`` ratcheted ``turns`` up to 31 over 19 minutes,
  then the terminal ``finished`` event reset it to 7.
  Fix: mirror ``handle_progress_update``'s monotonic ratchet —
  ``run.X = max(run.X or 0, event.X)`` for each cumulative field.

* **#453 — ``handle_unknown`` blind status mirror.** The fallback handler
  did ``if event.status: run.status = event.status`` for every unmapped
  event. PR #452 closed the only known offender (``teammate_completed``)
  by adding an explicit dispatcher entry, but the foot-gun stayed loaded:
  any future unmapped event carrying a ``status`` field would still
  mutate the run's status. Fix: remove the mirror entirely and require
  explicit ``_RUN_HANDLERS`` entries for events that should set run state.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from app.database import Base, async_session, engine
from app.models import Run
from app.schemas import WebhookRunEvent
from app.services.run_lifecycle import handle_finished, handle_unknown


@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


def _finished_event(**kwargs) -> WebhookRunEvent:
    base = {"event": "finished", "run_id": kwargs.pop("run_id", "run-454-test")}
    base.update(kwargs)
    return WebhookRunEvent(**base)


# ---------------------------------------------------------------------------
# Issue #454 — handle_finished must be monotonic on telemetry fields.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_finished_does_not_regress_turns(setup_db):
    """The smoking gun: turns=31 accumulated via handle_progress_update must
    not be clobbered by a terminal ``finished`` event carrying turns=7.

    Mirrors the live event sequence observed for run-20260517T144539Z.
    """
    run_id = "run-454-turns"
    async with async_session() as db:
        run = Run(run_id=run_id, status="running", turns=31,
                  tokens_total=12000)
        db.add(run)
        await db.commit()

        event = _finished_event(
            run_id=run_id, status="success", turns=7, tokens_total=4000,
        )
        await handle_finished(db, event, project_id=None, run=run)
        await db.commit()
        await db.refresh(run)

    assert run.turns == 31, (
        f"run.turns regressed from 31 to {run.turns!r} on handle_finished "
        "— issue #454. Terminal event clobbered the higher cumulative value."
    )
    assert run.tokens_total == 12000, (
        f"run.tokens_total regressed from 12000 to {run.tokens_total!r} "
        "— issue #454."
    )
    # And the status mapping still has to happen.
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_handle_finished_does_not_regress_all_token_fields(setup_db):
    """All four telemetry int fields (turns, tokens_input/output/total)
    must be guarded together."""
    run_id = "run-454-tokens"
    async with async_session() as db:
        run = Run(
            run_id=run_id, status="running",
            turns=50,
            tokens_input=8000,
            tokens_output=4000,
            tokens_total=12000,
        )
        db.add(run)
        await db.commit()

        event = _finished_event(
            run_id=run_id, status="success",
            turns=5,
            tokens_input=100,
            tokens_output=50,
            tokens_total=150,
        )
        await handle_finished(db, event, project_id=None, run=run)
        await db.commit()
        await db.refresh(run)

    assert run.turns == 50
    assert run.tokens_input == 8000
    assert run.tokens_output == 4000
    assert run.tokens_total == 12000


@pytest.mark.asyncio
async def test_handle_finished_does_not_regress_cost_usd(setup_db):
    """Cost is cumulative — terminal event with a lower value must not
    clobber a higher accumulated cost."""
    run_id = "run-454-cost"
    async with async_session() as db:
        run = Run(run_id=run_id, status="running", cost_usd=1.25)
        db.add(run)
        await db.commit()

        event = _finished_event(
            run_id=run_id, status="success", cost_usd=0.10,
        )
        await handle_finished(db, event, project_id=None, run=run)
        await db.commit()
        await db.refresh(run)

    assert run.cost_usd == 1.25, (
        f"cost_usd regressed from 1.25 to {run.cost_usd!r} on handle_finished "
        "— issue #454."
    )


@pytest.mark.asyncio
async def test_handle_finished_advances_on_higher_values(setup_db):
    """The monotonic guard must still let the terminal event ratchet
    counters upward when its values exceed what's been accumulated."""
    run_id = "run-454-advance"
    async with async_session() as db:
        run = Run(run_id=run_id, status="running", turns=5,
                  tokens_total=200, cost_usd=0.1)
        db.add(run)
        await db.commit()

        event = _finished_event(
            run_id=run_id, status="success",
            turns=12, tokens_total=999, cost_usd=0.75,
        )
        await handle_finished(db, event, project_id=None, run=run)
        await db.commit()
        await db.refresh(run)

    assert run.turns == 12
    assert run.tokens_total == 999
    assert run.cost_usd == 0.75


@pytest.mark.asyncio
async def test_handle_finished_handles_none_existing_telemetry(setup_db):
    """Fresh runs whose telemetry columns are NULL must still accept the
    terminal event's values (no TypeError from max(None, int))."""
    run_id = "run-454-fresh"
    async with async_session() as db:
        run = Run(run_id=run_id, status="running")  # all telemetry NULL
        db.add(run)
        await db.commit()

        event = _finished_event(
            run_id=run_id, status="success",
            turns=8, tokens_input=300, tokens_output=200,
            tokens_total=500, cost_usd=0.42,
        )
        await handle_finished(db, event, project_id=None, run=run)
        await db.commit()
        await db.refresh(run)

    assert run.turns == 8
    assert run.tokens_input == 300
    assert run.tokens_output == 200
    assert run.tokens_total == 500
    assert run.cost_usd == 0.42


# ---------------------------------------------------------------------------
# Issue #453 — handle_unknown must not mirror event.status onto run.status.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_unknown_does_not_mirror_status(setup_db):
    """Synthetic unmapped event carrying status='completed' must NOT flip
    the run's status. Future unmapped events should require an explicit
    handler in ``_RUN_HANDLERS`` to mutate run-level state — see #453.
    """
    run_id = "run-453-unknown"
    async with async_session() as db:
        run = Run(run_id=run_id, status="running")
        db.add(run)
        await db.commit()

        # Pretend this is some new event type the dashboard doesn't know
        # about yet, but which carries a teammate-scoped or task-scoped
        # status that should NOT propagate to the run row.
        event = WebhookRunEvent(
            event="some_future_unmapped_event",
            run_id=run_id,
            status="completed",
        )
        await handle_unknown(db, event, project_id=None, run=run)
        await db.commit()
        await db.refresh(run)

    assert run.status == "running", (
        f"handle_unknown blindly mirrored event.status='completed' onto "
        f"run.status (got {run.status!r}) — issue #453. The reviewer's "
        "recommended fix is to remove the mirror entirely so future "
        "unmapped events cannot quietly mutate run-level state."
    )


@pytest.mark.asyncio
async def test_handle_unknown_does_not_mirror_error_status(setup_db):
    """Same contract for status='error' — a teammate failing must not
    cascade to the parent run via the unknown-event fallback."""
    run_id = "run-453-unknown-error"
    async with async_session() as db:
        run = Run(run_id=run_id, status="running")
        db.add(run)
        await db.commit()

        event = WebhookRunEvent(
            event="hypothetical_new_event",
            run_id=run_id,
            status="error",
        )
        await handle_unknown(db, event, project_id=None, run=run)
        await db.commit()
        await db.refresh(run)

    assert run.status == "running"


@pytest.mark.asyncio
async def test_handle_unknown_still_updates_other_fields(setup_db):
    """Removing the status mirror must NOT regress the other side-effects:
    mode/model/project_id/trace_id assignments still apply."""
    run_id = "run-453-unknown-other"
    async with async_session() as db:
        run = Run(run_id=run_id, status="running")
        db.add(run)
        await db.commit()

        event = WebhookRunEvent(
            event="hypothetical_event_with_model",
            run_id=run_id,
            mode="auto",
            model="sonnet-4-6",
            trace_id="trace-xyz",
            status="completed",  # must be IGNORED for run.status
        )
        await handle_unknown(db, event, project_id=None, run=run)
        await db.commit()
        await db.refresh(run)

    assert run.status == "running"
    assert run.mode == "auto"
    assert run.model == "sonnet-4-6"
    assert run.trace_id == "trace-xyz"


@pytest.mark.asyncio
async def test_handle_unknown_creating_new_run_defaults_to_running(setup_db):
    """If handle_unknown has to materialise a brand-new run row (no
    pre-existing row), it must default to status='running' — never adopt
    the event's status as the initial run status."""
    run_id = "run-453-unknown-fresh"
    async with async_session() as db:
        event = WebhookRunEvent(
            event="hypothetical_first_sighting",
            run_id=run_id,
            status="completed",  # must be IGNORED for run.status
        )
        run = await handle_unknown(db, event, project_id=None, run=None)
        await db.commit()
        await db.refresh(run)

    assert run.status == "running", (
        f"New run materialised by handle_unknown adopted event.status="
        f"'completed' (got {run.status!r}) — issue #453. The fallback "
        "must default to 'running' regardless of the event payload."
    )
