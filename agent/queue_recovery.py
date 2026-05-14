"""Queue purge / paused-resume / orphan recovery at run start.

Python port of agent/scripts/run-manager.sh queue_* functions (issue #383).
Talks to the dashboard's queue API via HTTP.

Error handling philosophy:
- ``ConnectError`` / ``ReadTimeout`` (dashboard unreachable): log + return
  empty. Operators expect this when starting a run before the dashboard
  is up; we cannot do anything useful, but the run can still proceed.
- ``HTTPStatusError`` (dashboard responded with 4xx/5xx): raise
  :class:`QueueRecoveryError`. The dashboard returning an error is a
  signal that something is wrong with the request shape or that the
  database is in a bad state; silently swallowing it would lose queue
  invariants (orphan items stay marked ``running`` forever; paused items
  never resume). The caller (``project_loop.iterate_projects``) decides
  whether to abort the run.
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)


class QueueRecoveryError(RuntimeError):
    """Raised when the dashboard responds with an error or the recovery
    operation cannot complete safely. Connection failures are NOT mapped
    to this exception — they degrade to a no-op so a run can still start
    when the dashboard is briefly unavailable.
    """


_QUEUE_BASE = os.environ.get("STATION_DASHBOARD_BASE", "http://localhost:8420").rstrip("/")


def _list_items_by_status(status: str) -> list[dict]:
    """Fetch queue items filtered by ``status``.

    Returns ``[]`` when the dashboard is unreachable (transport-level
    error). Raises :class:`QueueRecoveryError` when the dashboard responds
    with a 4xx/5xx — that indicates a real server-side problem that
    callers must surface, not silently absorb.
    """
    try:
        r = httpx.get(
            f"{_QUEUE_BASE}/api/queue/items",
            params={"status": status},
            timeout=10.0,
        )
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
        logger.warning(
            "queue_recovery: dashboard unreachable while listing %s: %s "
            "— skipping recovery for this status",
            status, exc,
        )
        return []
    if r.status_code >= 400:
        raise QueueRecoveryError(
            f"dashboard returned {r.status_code} listing {status}: {r.text[:200]}"
        )
    return r.json().get("items", [])


def _list_running_items() -> list[dict]:
    return _list_items_by_status("running")


def _list_paused_items() -> list[dict]:
    return _list_items_by_status("paused")


def _run_is_alive(run_id: str) -> bool:
    """Best-effort liveness check.

    Returns ``False`` on any error — including the dashboard responding
    with a non-200. Rationale: if we can't confirm a run is alive, the
    safe default is to treat it as dead so its orphaned items get
    reclaimed. The alternative (returning ``True`` on uncertainty) would
    leak orphans indefinitely.
    """
    try:
        r = httpx.get(f"{_QUEUE_BASE}/api/runs/{run_id}", timeout=5.0)
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout):
        return False
    if r.status_code != 200:
        return False
    return r.json().get("status") in {"running", "queued"}


def _mark_item(item_id: str, new_status: str, reason: str = "") -> None:
    """Mark a queue item with a new status.

    Connection errors degrade to a logged warning (the dashboard may be
    flapping during a busy run). A 4xx/5xx from the dashboard raises
    :class:`QueueRecoveryError` so the caller can abort the run rather
    than continue with a wrong queue state.
    """
    try:
        r = httpx.patch(
            f"{_QUEUE_BASE}/api/queue/items/{item_id}",
            json={"status": new_status, "reason": reason},
            timeout=5.0,
        )
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
        logger.warning(
            "queue_recovery: dashboard unreachable marking %s -> %s: %s",
            item_id, new_status, exc,
        )
        return
    if r.status_code >= 400:
        raise QueueRecoveryError(
            f"dashboard returned {r.status_code} marking {item_id} -> "
            f"{new_status}: {r.text[:200]}"
        )


def purge_and_recover(current_run_id: str) -> None:
    """Mark orphaned 'running' items from dead runs as failed; leave the current run alone.

    Raises :class:`QueueRecoveryError` on dashboard-side errors; the
    caller is expected to abort the run rather than proceed with a
    potentially inconsistent queue state.
    """
    for item in _list_running_items():
        rid = item.get("run_id", "")
        if rid == current_run_id:
            continue
        if _run_is_alive(rid):
            continue
        logger.info("queue_recovery: orphan item %s from dead run %s", item.get("id"), rid)
        _mark_item(item["id"], "failed", reason="orphaned: parent run died")


def resume_paused() -> None:
    """Flip 'paused' items back to 'pending' so the smart router will pick them up.

    Raises :class:`QueueRecoveryError` on dashboard-side errors.
    """
    for item in _list_paused_items():
        _mark_item(item["id"], "pending", reason="resumed at run start")
