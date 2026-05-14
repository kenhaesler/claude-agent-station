"""Queue purge / paused-resume / orphan recovery at run start.

Python port of agent/scripts/run-manager.sh queue_* functions (issue #383).
Talks to the dashboard's queue API via HTTP.
"""
from __future__ import annotations

import logging
import os
from typing import Iterable

import httpx

logger = logging.getLogger(__name__)


_QUEUE_BASE = os.environ.get("STATION_DASHBOARD_BASE", "http://localhost:8420").rstrip("/")


def _list_running_items() -> list[dict]:
    try:
        r = httpx.get(f"{_QUEUE_BASE}/api/queue/items", params={"status": "running"}, timeout=10.0)
        r.raise_for_status()
        return r.json().get("items", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("queue_recovery: list running failed: %s", exc)
        return []


def _list_paused_items() -> list[dict]:
    try:
        r = httpx.get(f"{_QUEUE_BASE}/api/queue/items", params={"status": "paused"}, timeout=10.0)
        r.raise_for_status()
        return r.json().get("items", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("queue_recovery: list paused failed: %s", exc)
        return []


def _run_is_alive(run_id: str) -> bool:
    try:
        r = httpx.get(f"{_QUEUE_BASE}/api/runs/{run_id}", timeout=5.0)
        return r.status_code == 200 and r.json().get("status") in {"running", "queued"}
    except Exception:  # noqa: BLE001
        return False


def _mark_item(item_id: str, new_status: str, reason: str = "") -> None:
    try:
        httpx.patch(
            f"{_QUEUE_BASE}/api/queue/items/{item_id}",
            json={"status": new_status, "reason": reason},
            timeout=5.0,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("queue_recovery: mark %s -> %s failed: %s", item_id, new_status, exc)


def purge_and_recover(current_run_id: str) -> None:
    """Mark orphaned 'running' items from dead runs as failed; leave the current run alone."""
    for item in _list_running_items():
        rid = item.get("run_id", "")
        if rid == current_run_id:
            continue
        if _run_is_alive(rid):
            continue
        logger.info("queue_recovery: orphan item %s from dead run %s", item.get("id"), rid)
        _mark_item(item["id"], "failed", reason="orphaned: parent run died")


def resume_paused() -> None:
    """Flip 'paused' items back to 'pending' so the smart router will pick them up."""
    for item in _list_paused_items():
        _mark_item(item["id"], "pending", reason="resumed at run start")
