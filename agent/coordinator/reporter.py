"""Post task-level events to the dashboard webhook."""

from __future__ import annotations

import json
import logging
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.coordinator.config import CoordinatorConfig
    from agent.coordinator.dag import Task

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def post_event(config: CoordinatorConfig, event: str, extra: dict | None = None) -> None:
    """POST an event to the dashboard webhook (best-effort, never throws)."""
    payload = {
        "event": event,
        "run_id": f"run-{config.run_id}",
        "timestamp": _utcnow_iso(),
        "concurrent_group_id": config.concurrent_group_id,
    }
    if extra:
        payload.update(extra)

    try:
        data = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        # Include auth token if a webhook secret is configured
        webhook_secret = os.environ.get("STATION_WEBHOOK_SECRET", "") or getattr(config, "webhook_secret", "")
        if webhook_secret:
            headers["X-Webhook-Token"] = webhook_secret
        req = urllib.request.Request(
            config.webhook_url,
            data=data,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3):
            pass
    except Exception as e:
        logger.debug("Webhook POST failed (non-fatal): %s", e)


def post_task_event(config: CoordinatorConfig, event: str, task: Task) -> None:
    """POST a task-level event to the dashboard."""
    post_event(config, event, {
        "task_id": task.id,
        "task_title": task.title,
        "project": task.project_repo,
        "employee_index": task.employee_index,
        "depends_on": json.dumps(task.depends_on),
    })


def post_conflict(config: CoordinatorConfig, file_path: str, employee_a: int, employee_b: int, project: str) -> None:
    """POST a conflict detection event."""
    post_event(config, "conflict_detected", {
        "project": project,
        "file_path": file_path,
        "employee_a": employee_a,
        "employee_b": employee_b,
    })


def post_guidance(config: CoordinatorConfig, employee_index: int, guidance_type: str, content: str, project: str) -> None:
    """POST a guidance-sent event."""
    post_event(config, "guidance_sent", {
        "project": project,
        "employee_index": employee_index,
        "guidance_type": guidance_type,
        "guidance_content": content[:200],
    })
