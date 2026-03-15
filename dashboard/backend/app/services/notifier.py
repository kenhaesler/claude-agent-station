from __future__ import annotations

"""Webhook notification service.

Sends notifications when runs complete, verdicts are issued, or errors occur.
Failures are logged but never raise -- notifications must not crash the backend.

Message formatting is delegated to pluggable adapters in
:mod:`app.services.adapters`.  The public API (``send_notification`` and
``send_test_notification``) is unchanged.
"""

import asyncio
import logging
from typing import Any

import httpx

from app.services.adapters import get_adapter
from app.services.config_sync import _read_config_json

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

async def _get_notification_config() -> dict[str, Any]:
    """Read notification config from manager-config.json."""
    config = await asyncio.to_thread(_read_config_json)
    return config.get("notifications", {})


def _should_notify(event_type: str, config: dict[str, Any]) -> bool:
    """Check if we should send a notification for this event type."""
    if not config.get("enabled", False):
        return False
    if config.get("method") != "webhook":
        return False
    if not config.get("webhook_url"):
        return False

    notify_on = config.get("notify_on", ["approve", "reject", "pr", "error"])
    return event_type.lower() in [n.lower() for n in notify_on]


# ---------------------------------------------------------------------------
# Core send logic
# ---------------------------------------------------------------------------

async def _send_notification_detailed(
    event_type: str,
    project: str,
    issue_number: int | None = None,
    issue_title: str | None = None,
    tokens_total: int | None = None,
    summary: str | None = None,
    run_id: str | None = None,
    _bypass_filter: bool = False,
) -> tuple[bool, str | None]:
    """Send a webhook notification, returning (success, error_detail).

    Returns (True, None) on success, (False, error_message) on failure.
    Never raises -- failures are logged.
    """
    try:
        config = await _get_notification_config()

        if not _bypass_filter and not _should_notify(event_type, config):
            return (False, None)

        webhook_url = config["webhook_url"]
        webhook_type = config.get("webhook_type", "generic").lower()
        dashboard_url = config.get("dashboard_url", "").rstrip("/") or None

        # Look up the adapter for this webhook type
        adapter = get_adapter(webhook_type)

        payload = adapter.format_message(
            event_type=event_type,
            project=project,
            issue_number=issue_number,
            issue_title=issue_title,
            tokens_total=tokens_total,
            summary=summary,
            run_id=run_id,
            dashboard_url=dashboard_url,
            config=config,
        )

        # Send the webhook
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()

        logger.info(
            "Notification sent: %s for %s (adapter=%s, status=%d)",
            event_type, project, adapter.name, response.status_code,
        )
        return (True, None)

    except httpx.HTTPStatusError as e:
        body = e.response.text[:200] if e.response.text else ""
        detail = f"Webhook returned HTTP {e.response.status_code} {e.response.reason_phrase}: {body}"
        logger.warning("Notification webhook returned error: %s", detail)
        return (False, detail)
    except httpx.RequestError as e:
        detail = f"Webhook request failed: {e}"
        logger.warning("Notification webhook request failed: %s", e)
        return (False, detail)
    except Exception as e:
        detail = f"Unexpected error: {type(e).__name__}: {e}"
        logger.exception("Unexpected error sending notification")
        return (False, detail)


async def send_notification(
    event_type: str,
    project: str,
    issue_number: int | None = None,
    issue_title: str | None = None,
    tokens_total: int | None = None,
    summary: str | None = None,
    run_id: str | None = None,
    _bypass_filter: bool = False,
) -> bool:
    """Send a webhook notification for a run event.

    Returns True if notification was sent successfully, False otherwise.
    Never raises -- failures are logged.

    Args:
        _bypass_filter: If True, skip the _should_notify check. Used by
            send_test_notification which validates config independently.
    """
    success, _ = await _send_notification_detailed(
        event_type=event_type,
        project=project,
        issue_number=issue_number,
        issue_title=issue_title,
        tokens_total=tokens_total,
        summary=summary,
        run_id=run_id,
        _bypass_filter=_bypass_filter,
    )
    return success


async def send_test_notification() -> dict[str, Any]:
    """Send a test notification to verify webhook configuration.

    Returns a dict with status and details.
    """
    config = await _get_notification_config()

    if not config.get("enabled"):
        return {"success": False, "error": "Notifications are not enabled"}
    if config.get("method") != "webhook":
        return {"success": False, "error": "Method is not 'webhook'"}
    if not config.get("webhook_url"):
        return {"success": False, "error": "Webhook URL is not configured"}

    success, error_detail = await _send_notification_detailed(
        event_type="TEST",
        project="test/notification-check",
        issue_number=0,
        issue_title="Test Notification",
        tokens_total=12345,
        summary="This is a test notification from Claude Agent Station. If you see this, your webhook is configured correctly!",
        run_id="test-notification",
        _bypass_filter=True,
    )

    if success:
        return {"success": True, "message": "Test notification sent successfully"}
    else:
        return {"success": False, "error": error_detail or "Failed to send notification"}
