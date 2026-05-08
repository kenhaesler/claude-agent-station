"""Retention/prune for the audit_log table (issue #73).

Bounded growth: delete rows whose ``started_at`` is older than the configured
retention window. Default 30 days; override via ``STATION_AUDIT_RETENTION_DAYS``.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEntry

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 30


def retention_days() -> int:
    raw = os.environ.get("STATION_AUDIT_RETENTION_DAYS")
    if not raw:
        return DEFAULT_RETENTION_DAYS
    try:
        n = int(raw)
        return n if n > 0 else DEFAULT_RETENTION_DAYS
    except ValueError:
        logger.warning(
            "Invalid STATION_AUDIT_RETENTION_DAYS=%r — falling back to %d",
            raw, DEFAULT_RETENTION_DAYS,
        )
        return DEFAULT_RETENTION_DAYS


async def prune_audit_log(db: AsyncSession, days: int | None = None) -> int:
    """Delete audit rows older than ``days``. Returns the number pruned."""
    n_days = days if days is not None else retention_days()
    cutoff = datetime.now(timezone.utc) - timedelta(days=n_days)
    result = await db.execute(delete(AuditEntry).where(AuditEntry.started_at < cutoff))
    await db.commit()
    return result.rowcount or 0
