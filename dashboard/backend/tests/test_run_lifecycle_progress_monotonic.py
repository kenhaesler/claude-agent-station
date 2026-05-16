"""Regression tests for issue #434 — telemetry oscillation.

Two distinct emitters write ``progress_update`` against the same ``runs`` row:

1. The orchestrator's outer counters, cumulative across all teammates.
2. ``teammate_progress`` events, scoped to a single ``task_id`` — these can
   legitimately carry 0 at the start of a new teammate cycle.

Both flow through ``handle_progress_update``. Before the fix, whichever event
fired last overwrote the row, so ``runs.turns`` (and the token fields) would
oscillate between a high value and 0 on every webhook tick.

The fix makes ``handle_progress_update`` monotonic — it only ever ratchets
counters upward, mirroring what ``coordinator_service`` does for
``CoordinatorTask`` rows.
"""

from __future__ import annotations

import pytest

from app.models import Run
from app.schemas import WebhookRunEvent
from app.services.run_lifecycle import handle_progress_update


def _make_event(**kwargs) -> WebhookRunEvent:
    """Build a minimal progress_update event with optional telemetry fields."""
    base = {
        "run_id": "run-monotonic-test",
        "event": "progress_update",
    }
    base.update(kwargs)
    return WebhookRunEvent(**base)


@pytest.mark.asyncio
async def test_progress_update_does_not_regress_turns() -> None:
    """A low turns value must not clobber an already-recorded high value."""
    run = Run(run_id="run-monotonic-test", status="running")

    await handle_progress_update(run, _make_event(turns=42))
    assert run.turns == 42

    # Per-teammate event arrives carrying turns=0 (fresh cycle start).
    await handle_progress_update(run, _make_event(turns=0))
    assert run.turns == 42, "turns regressed — telemetry should be monotonic"


@pytest.mark.asyncio
async def test_progress_update_does_not_regress_tokens() -> None:
    """All three token fields must also be monotonic."""
    run = Run(run_id="run-monotonic-test", status="running")

    await handle_progress_update(
        run,
        _make_event(
            tokens_input=1000,
            tokens_output=500,
            tokens_total=1500,
            turns=10,
        ),
    )
    assert run.tokens_input == 1000
    assert run.tokens_output == 500
    assert run.tokens_total == 1500
    assert run.turns == 10

    # Lower-valued progress_update (e.g. from a freshly-spawned teammate).
    await handle_progress_update(
        run,
        _make_event(
            tokens_input=0,
            tokens_output=0,
            tokens_total=0,
            turns=0,
        ),
    )
    assert run.tokens_input == 1000
    assert run.tokens_output == 500
    assert run.tokens_total == 1500
    assert run.turns == 10


@pytest.mark.asyncio
async def test_progress_update_advances_on_higher_values() -> None:
    """The monotonic guard must still let counters move forward."""
    run = Run(run_id="run-monotonic-test", status="running")

    await handle_progress_update(run, _make_event(turns=5, tokens_total=100))
    assert run.turns == 5
    assert run.tokens_total == 100

    await handle_progress_update(run, _make_event(turns=12, tokens_total=750))
    assert run.turns == 12
    assert run.tokens_total == 750


@pytest.mark.asyncio
async def test_progress_update_with_none_existing_handles_fresh_run() -> None:
    """First event against a brand-new run (all counters are NULL) populates them."""
    run = Run(run_id="run-monotonic-test", status="running")
    # Sanity: SQLAlchemy hasn't materialised defaults — fields are None.
    assert run.turns is None
    assert run.tokens_total is None

    await handle_progress_update(
        run,
        _make_event(turns=7, tokens_input=300, tokens_output=200, tokens_total=500),
    )
    assert run.turns == 7
    assert run.tokens_input == 300
    assert run.tokens_output == 200
    assert run.tokens_total == 500


@pytest.mark.asyncio
async def test_progress_update_ignores_absent_fields() -> None:
    """Events that omit a field entirely must not touch the existing value."""
    run = Run(
        run_id="run-monotonic-test",
        status="running",
        turns=20,
        tokens_total=999,
    )

    # Event only carries tokens_input — turns/tokens_total must stay put.
    await handle_progress_update(run, _make_event(tokens_input=50))
    assert run.turns == 20
    assert run.tokens_total == 999
    assert run.tokens_input == 50
