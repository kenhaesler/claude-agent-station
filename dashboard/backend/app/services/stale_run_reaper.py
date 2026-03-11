"""Detect and reap runs stuck in 'running' after the agent process dies.

When the agent is killed (hard stop, OOM, etc.) the run_complete webhook
never fires, leaving Run.status == 'running' forever.  This module checks
whether the systemd service is actually alive and, if not, marks orphaned
runs as 'interrupted' so the UI reflects reality.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Run
from app.services.systemd import get_service_status
from app.services.event_bus import publish as event_bus_publish
from app.services.notifier import send_notification

logger = logging.getLogger(__name__)


async def reap_stale_runs(db: AsyncSession) -> int:
    """Mark orphaned 'running' runs as 'interrupted' if the agent service is dead.

    Returns the number of runs reaped.
    """
    # Check if the agent service is actually running
    svc = await get_service_status()
    if svc["service_active"]:
        return 0  # Service is alive — nothing to reap

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
            event_type="interrupted",
            project=project_name,
            run_id=run.run_id,
            summary=f"Stale run reaped: agent process died while run was in progress.",
        )

    return len(stale_runs)
