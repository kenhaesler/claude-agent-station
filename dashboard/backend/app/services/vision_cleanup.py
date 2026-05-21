"""Periodic cleanup of stale vision chat sessions.

Same surface as app/services/stale_run_reaper.py — startup hook in main.py
launches an asyncio task that calls sweep_stale_sessions() every 30 min.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models import VisionChatSession
from app.services.vision_attachments import _upload_root

logger = logging.getLogger(__name__)

ACTIVE_TTL = timedelta(hours=24)
COMPLETED_TTL = timedelta(days=30)
SWEEP_INTERVAL_SECONDS = 30 * 60


async def _sweep_orphan_upload_dirs(db: AsyncSession) -> int:
    """Remove upload dirs for sessions that don't exist OR are not active."""
    import shutil

    root = _upload_root()
    if not root.is_dir():
        return 0

    result = await db.execute(select(VisionChatSession.id, VisionChatSession.state))
    rows = {r[0]: r[1] for r in result.all()}

    removed = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        state = rows.get(child.name)
        if state is None or state != "active":
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed


async def sweep_stale_sessions(db: AsyncSession) -> tuple[int, int]:
    """Cancel active>24h, delete approved/cancelled>30d. Returns (cancelled, deleted).

    SQLite stores naive UTC datetimes — compare against a naive ``now``.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC, matches SQLite storage
    active_cutoff = now - ACTIVE_TTL
    completed_cutoff = now - COMPLETED_TTL

    # Cancel stale active sessions
    result = await db.execute(
        select(VisionChatSession).where(
            VisionChatSession.state == "active",
            VisionChatSession.updated_at < active_cutoff,
        )
    )
    stale_active = result.scalars().all()
    for s in stale_active:
        s.state = "cancelled"
        s.updated_at = now

    # Delete old completed/cancelled sessions
    delete_result = await db.execute(
        delete(VisionChatSession).where(
            VisionChatSession.state.in_(["approved", "cancelled"]),
            VisionChatSession.updated_at < completed_cutoff,
        )
    )

    await db.commit()

    # Orphan upload-dir cleanup runs after commit so newly-cancelled/deleted
    # sessions are correctly reflected in the dir sweep.
    try:
        orphans_removed = await _sweep_orphan_upload_dirs(db)
        if orphans_removed:
            logger.info("vision_cleanup: removed %d orphan upload dirs", orphans_removed)
    except OSError:
        logger.warning("vision_cleanup: could not sweep orphan upload dirs", exc_info=True)

    return len(stale_active), delete_result.rowcount or 0


async def run_cleanup_loop() -> None:
    """Background task: sweep every 30 min, log results, never crash."""
    while True:
        try:
            async with async_session() as db:
                cancelled, deleted = await sweep_stale_sessions(db)
                if cancelled or deleted:
                    logger.info(
                        "vision_cleanup: cancelled=%d deleted=%d", cancelled, deleted,
                    )
        except Exception:
            logger.exception("vision_cleanup sweep failed")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
