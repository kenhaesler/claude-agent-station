"""Regression tests for the run-autonomy badge bug.

The orchestrator resolves ``autonomy_level`` per ADR-0001 and passes it to
``can_use_tool``, but for a long time it never propagated that value into
the ``runs`` row, so every Run defaulted to ``"assisted"`` regardless of
project setting. This caused FULL-AUTO projects to render ASSIST badges in
the dashboard while the policy engine was actually running with AUTO.

The fix:

1. ``WebhookRunEvent`` carries an optional ``autonomy_level``.
2. ``handle_started`` snapshots it onto the ``Run`` row (insert and update).
3. The ``Run.autonomy_level`` column no longer has a misleading default.
"""

from __future__ import annotations

import pytest

from app.models import Run
from app.schemas import WebhookRunEvent
from app.services.run_lifecycle import handle_started


def _started_event(**kwargs) -> WebhookRunEvent:
    base = {
        "run_id": "run-autonomy-test",
        "event": "started",
        "mode": "full",
    }
    base.update(kwargs)
    return WebhookRunEvent(**base)


class _StubSession:
    """Minimal AsyncSession stand-in — handle_started only calls .add()."""

    def __init__(self) -> None:
        self.added: list[Run] = []

    def add(self, obj) -> None:
        self.added.append(obj)


@pytest.mark.asyncio
async def test_handle_started_snapshots_autonomy_on_insert() -> None:
    """A run_start carrying autonomy_level=auto must land on the new Run row."""
    db = _StubSession()
    event = _started_event(autonomy_level="auto")

    run = await handle_started(db, event, project_id=1, run=None)

    assert db.added == [run]
    assert run.autonomy_level == "auto"


@pytest.mark.asyncio
async def test_handle_started_upgrades_placeholder_autonomy() -> None:
    """The pre-trigger placeholder is later upgraded by the run_start webhook."""
    db = _StubSession()
    placeholder = Run(run_id="run-autonomy-test", status="pending")
    event = _started_event(autonomy_level="auto")

    result = await handle_started(db, event, project_id=1, run=placeholder)

    assert result is placeholder
    assert placeholder.status == "running"
    assert placeholder.autonomy_level == "auto"


@pytest.mark.asyncio
async def test_handle_started_preserves_existing_autonomy_when_missing() -> None:
    """A later event without autonomy_level must not wipe a prior snapshot."""
    db = _StubSession()
    existing = Run(run_id="run-autonomy-test", status="running", autonomy_level="auto")
    event = _started_event()  # no autonomy_level field

    result = await handle_started(db, event, project_id=1, run=existing)

    assert result.autonomy_level == "auto"


@pytest.mark.asyncio
async def test_handle_started_leaves_autonomy_null_without_snapshot() -> None:
    """Legacy webhooks without autonomy_level leave the column NULL (not 'assisted').

    A NULL value tells the dashboard to render ``—`` instead of falsely
    claiming the run was at the assisted level.
    """
    db = _StubSession()
    event = _started_event()

    run = await handle_started(db, event, project_id=1, run=None)

    assert run.autonomy_level is None
