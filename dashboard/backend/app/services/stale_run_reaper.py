from __future__ import annotations

"""Detect and reap runs stuck in 'running' after the agent process dies.

When the agent is killed (hard stop, OOM, etc.) the run_complete webhook
never fires, leaving Run.status == 'running' forever.  This module checks
whether the systemd service is actually alive and, if not, marks orphaned
runs as 'interrupted' so the UI reflects reality.
"""

import logging
import subprocess
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CoordinatorTask, QueueItem, Run
from app.services.event_bus import publish as event_bus_publish
from app.services.notifier import send_notification
from app.services.service_control import _mode, get_agent_status

logger = logging.getLogger(__name__)


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


async def reap_stale_runs(db: AsyncSession) -> int:
    """Mark orphaned 'running' runs as 'interrupted' if the agent service is dead.

    Returns the number of runs reaped.
    """
    # Check if the agent service is actually running
    svc = await get_agent_status()
    if svc.get("service_active"):
        return 0  # Agent is alive — nothing to reap

    # pgrep is a useful tie-breaker for manual orchestrator invocations
    # outside the systemd unit (developer testing on the host). It's
    # noise in compose mode — the orchestrator runs in a sibling container
    # so pgrep here finds nothing, and the subprocess + 3s timeout adds
    # latency to every reaper tick. Skip it in compose.
    if _mode() == "systemd" and _is_orchestrator_process_alive():
        return 0  # Orchestrator process is alive — nothing to reap

    # Service is inactive — find any runs still marked as 'running'
    result = await db.execute(
        select(Run).where(Run.status.in_(["running", "reviewing"]))
    )
    stale_runs = result.scalars().all()

    if not stale_runs:
        return 0

    now = datetime.now(timezone.utc)
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

    return len(stale_runs)
