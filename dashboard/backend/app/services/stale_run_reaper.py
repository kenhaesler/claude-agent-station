from __future__ import annotations

"""Detect and reap runs stuck in 'running' after the agent process dies.

Postgres path: a ``_heartbeat_subscriber`` task resets per-run watchdog timers
via the ``heartbeat`` LISTEN/NOTIFY channel so the tick loop can be stretched
to 60 s without losing detection speed.  SQLite path: tick at 15 s, no
subscriber (``listen`` exhausts immediately).

When the agent is killed (hard stop, OOM, etc.) the run_complete webhook
never fires, leaving Run.status == 'running' forever.  This module checks
whether the agent service is actually alive (via ``service_control``, which
dispatches to systemd or the launcher's /status depending on
``STATION_DEPLOY_MODE``) and, if not, marks orphaned runs as 'interrupted'
so the UI reflects reality.

Also catches rows stuck in ``unknown`` (issue #268): when the orchestrator
exits before any teammate runs (no eligible issues, preflight failure, etc.)
``log_importer`` may insert a placeholder row whose status is ``unknown`` and
``finished_at`` is NULL.  If the agent service is dead and the run is older
than :data:`UNKNOWN_RUN_REAP_AGE_MINUTES`, we reap it the same way.
"""

import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CoordinatorTask, QueueItem, Run
from app.services.event_bus import publish as event_bus_publish
from app.services.notifier import send_notification
from app.services.service_control import deploy_mode, get_agent_status

logger = logging.getLogger(__name__)

# Conservative threshold: only reap ``unknown`` rows that have been in that
# state for at least this many minutes.  The ``unknown`` state is normal for
# the brief window between log_importer ingesting a freshly-started run's
# stream file and the launcher firing the ``finished`` webhook; we don't want
# to race-mark those.
UNKNOWN_RUN_REAP_AGE_MINUTES = 30

# Running rows whose last_event_at is older than this many seconds AND
# whose launcher reports no active run are reaped immediately. See
# issue #348.
ACTIVE_HEARTBEAT_TIMEOUT_SECONDS = 120

# Pending placeholder rows that never advanced to 'running' within this
# window — the bash never picked up the hint, or the launcher accepted
# the call but failed before run_start — are reaped as failed.
# See issue #346.
PENDING_REAP_AGE_SECONDS = 90

# Statuses we consider "still active" — rows in any of these states whose
# orchestrator is dead are candidates for reaping.  ``unknown`` is included
# only when the row is older than ``UNKNOWN_RUN_REAP_AGE_MINUTES``.
_ACTIVE_STATUSES = ("running", "reviewing")


def tick_interval_seconds() -> int:
    """Return the stale-run check interval based on the active DB dialect.

    On Postgres the ``heartbeat`` LISTEN/NOTIFY channel fans a heartbeat
    out to SSE clients in real time, so the periodic reaper tick is
    relaxed to 60 s (it's now the safety net, not the primary signal).
    On SQLite polling every 15 s is the only detection mechanism.
    """
    db_url = os.environ.get("STATION_DB_URL", "")
    if db_url and db_url.startswith("postgresql"):
        return 60
    return 15


async def _heartbeat_subscriber() -> None:
    """Subscribe to the Postgres ``heartbeat`` channel and rebroadcast on the
    in-process SSE event bus so dashboard clients see liveness without
    waiting for the next reaper tick.

    No-op on SQLite (``listen`` immediately exhausts).
    """
    from app.services.event_bus import publish as _bus_publish
    from app.services.pubsub import listen

    async for msg in listen("heartbeat"):
        run_id = msg.get("run_id")
        if not run_id:
            continue
        try:
            await _bus_publish({"type": "heartbeat", "data": {"run_id": run_id}})
        except Exception:  # noqa: BLE001
            logger.warning("heartbeat rebroadcast failed for %s", run_id)


def _is_orchestrator_process_alive() -> bool:
    """Check if a station_orchestrator process is running (manual/test runs)."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "station_orchestrator"],
            capture_output=True, timeout=3,
        )
        return result.returncode == 0
    except Exception:
        return False


async def _reap_by_heartbeat(db: AsyncSession) -> int:
    """Reap ``running`` rows whose ``last_event_at`` is older than the
    heartbeat timeout.

    This is the ONLY signal we have when the launcher is alive but the
    orchestrator died silently between events (e.g. SDK hung, OOM in a
    sub-process, segfault in a subagent). It must run regardless of the
    launcher's ``service_active`` state — the launcher-dead case is
    handled by the broader sweep in :func:`reap_stale_runs`.

    Returns the number of rows reaped. Does not commit; the caller is
    responsible for committing the session.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=ACTIVE_HEARTBEAT_TIMEOUT_SECONDS
    )
    result = await db.execute(
        select(Run).where(
            Run.status == "running",
            Run.last_event_at.isnot(None),
            Run.last_event_at < cutoff,
        )
    )
    stale = result.scalars().all()
    if not stale:
        return 0

    now = datetime.now(timezone.utc)
    for r in stale:
        r.status = "interrupted"
        r.finished_at = now
        logger.info(
            "Heartbeat-reaped stale run %s (last_event_at=%s)",
            r.run_id,
            r.last_event_at,
        )
        # Emit run_complete so SSE clients see the transition immediately,
        # matching the existing stale-run sweep below.
        await event_bus_publish({
            "type": "run_complete",
            "data": {
                "run_id": r.run_id,
                "event": "run_interrupted",
                "status": "interrupted",
                "project": None,
            },
        })

    return len(stale)


async def _reap_pending_placeholders(db: AsyncSession) -> int:
    """Reap ``pending`` placeholder rows that never advanced to ``running``
    within :data:`PENDING_REAP_AGE_SECONDS`.

    Runs unconditionally — pending rows are stale regardless of whether the
    service is active, because the bash may have adopted a different run_id
    or failed silently after the launcher accepted the POST.

    Returns the number of rows reaped. Does not commit; caller is responsible.
    """
    cutoff_pending = datetime.now(timezone.utc) - timedelta(
        seconds=PENDING_REAP_AGE_SECONDS
    )
    pending_result = await db.execute(
        select(Run).where(
            Run.status == "pending",
            Run.started_at < cutoff_pending,
        )
    )
    pending_rows = pending_result.scalars().all()
    for r in pending_rows:
        r.status = "failed"
        r.finished_at = datetime.now(timezone.utc)
        logger.info(
            "Reaped expired pending placeholder %s (started %s)",
            r.run_id,
            r.started_at,
        )
        await event_bus_publish({
            "type": "run_complete",
            "data": {
                "run_id": r.run_id,
                "status": "failed",
                "error": "pending placeholder expired",
            },
        })
    return len(pending_rows)


async def reap_stale_runs(db: AsyncSession) -> int:
    """Mark orphaned 'running' runs as 'interrupted' if the agent service is dead.

    Returns the number of runs reaped.
    """
    # Heartbeat reap runs unconditionally — it's the only signal we have
    # when the launcher is alive but the orchestrator died mid-run. The
    # existing service-inactive sweep below catches the dead-launcher case.
    heartbeat_reaped = await _reap_by_heartbeat(db)

    # Pending placeholder reap runs unconditionally — the placeholder row
    # may expire regardless of whether the service later starts or not.
    pending_reaped = await _reap_pending_placeholders(db)

    # Check if the agent service is actually running
    svc = await get_agent_status()
    if svc.get("service_active"):
        # Launcher reports a run alive — heartbeat and pending sweeps may
        # still have reaped rows. Commit and return.
        await db.commit()
        return heartbeat_reaped + pending_reaped

    # pgrep is a useful tie-breaker for manual orchestrator invocations
    # outside the systemd unit (developer testing on the host). It's
    # noise in compose mode — the orchestrator runs in a sibling container
    # so pgrep here finds nothing, and the subprocess + 3s timeout adds
    # latency to every reaper tick. Skip it in compose.
    if deploy_mode() == "systemd" and _is_orchestrator_process_alive():
        await db.commit()
        return heartbeat_reaped + pending_reaped  # Orchestrator process is alive — nothing more to reap

    # Service is inactive — find any runs still marked as 'running' /
    # 'reviewing', or stuck in 'unknown' for too long.  ``unknown`` rows are
    # only reaped when they have aged past ``UNKNOWN_RUN_REAP_AGE_MINUTES``
    # so we don't race the launcher's normal ``finished`` webhook for a
    # freshly-started run whose stream file was just imported.
    now = datetime.now(timezone.utc)
    unknown_cutoff = now - timedelta(minutes=UNKNOWN_RUN_REAP_AGE_MINUTES)
    result = await db.execute(
        select(Run).where(
            Run.finished_at.is_(None),
            or_(
                Run.status.in_(_ACTIVE_STATUSES),
                # ``started_at`` is generally non-null for rows the importer
                # creates from log timestamps, but we tolerate a NULL by
                # treating the row as old enough to reap (it's almost
                # certainly leftover from a long-dead run if status is
                # ``unknown`` and started_at was never set).
                (
                    (Run.status == "unknown")
                    & (
                        Run.started_at.is_(None)
                        | (Run.started_at < unknown_cutoff)
                    )
                ),
            ),
        )
    )
    stale_runs = result.scalars().all()

    for run in stale_runs:
        old_status = run.status
        run.status = "interrupted"
        run.finished_at = now
        logger.info(
            "Reaped stale run %s (was %s, started %s)",
            run.run_id,
            old_status,
            run.started_at,
        )
        # Notify SSE subscribers so frontend updates immediately
        await event_bus_publish({
            "type": "run_complete",
            "data": {
                "run_id": run.run_id,
                "event": "run_interrupted",
                "status": "interrupted",
                "project": None,
            },
        })

    # Reap stale CoordinatorTask records (same root cause — agent died)
    ct_result = await db.execute(
        select(CoordinatorTask).where(
            CoordinatorTask.status.in_(["running", "ready", "blocked"])
        )
    )
    stale_tasks = ct_result.scalars().all()
    for ct in stale_tasks:
        logger.info(
            "Reaped stale coordinator task %s (was %s)", ct.id, ct.status
        )
        ct.status = "failed"
        ct.finished_at = now

    # Recover orphaned queue items from reaped runs
    reaped_run_ids = [r.run_id for r in stale_runs]
    orphan_result = await db.execute(
        select(QueueItem).where(
            QueueItem.run_id.in_(reaped_run_ids),
            QueueItem.state.in_(["assigned", "in_progress", "review"]),
        )
    )
    orphaned_items = orphan_result.scalars().all()
    for item in orphaned_items:
        from app.services.queue_service import reset_orphaned_item
        await reset_orphaned_item(item, reason="stale run recovery")

    await db.commit()

    # Send webhook notification for reaped runs
    for run in stale_runs:
        # Resolve project name if possible
        project_name = "unknown"
        if run.project_id:
            from app.models import Project
            proj_result = await db.execute(
                select(Project).where(Project.id == run.project_id)
            )
            proj = proj_result.scalar_one_or_none()
            if proj:
                project_name = proj.repo

        await send_notification(
            event_type="error",
            project=project_name,
            run_id=run.run_id,
            summary="Stale run reaped: agent process died while run was in progress.",
            _bypass_filter=True,
        )

    return heartbeat_reaped + pending_reaped + len(stale_runs)
