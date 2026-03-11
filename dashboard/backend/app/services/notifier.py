"""Webhook notification service for Slack, Discord, Telegram, and generic webhooks.

Sends notifications when runs complete, verdicts are issued, or errors occur.
Failures are logged but never raise — notifications must not crash the backend.
"""

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.services.config_sync import _read_config_json

logger = logging.getLogger(__name__)

# Status emoji mapping
_STATUS_EMOJI = {
    "APPROVE": "\u2705",    # ✅
    "PR": "\U0001F4E4",     # 📤
    "REJECT": "\u274C",     # ❌
    "error": "\U0001F6A8",  # 🚨
    "interrupted": "\U0001F480",  # 💀
}

# Discord color mapping (decimal)
_DISCORD_COLORS = {
    "APPROVE": 5763719,    # green
    "PR": 3447003,         # blue
    "REJECT": 15548997,    # red
    "error": 16776960,     # yellow
    "interrupted": 10038562,  # dark red
}


def _get_notification_config() -> Dict[str, Any]:
    """Read notification config from manager-config.json."""
    config = _read_config_json()
    return config.get("notifications", {})


def _should_notify(event_type: str, config: Dict[str, Any]) -> bool:
    """Check if we should send a notification for this event type."""
    if not config.get("enabled", False):
        return False
    if config.get("method") != "webhook":
        return False
    if not config.get("webhook_url"):
        return False

    notify_on = config.get("notify_on", ["approve", "reject", "pr", "error"])
    return event_type.lower() in [n.lower() for n in notify_on]


def _format_slack(
    event_type: str,
    project: str,
    issue_number: Optional[int],
    issue_title: Optional[str],
    tokens_total: Optional[int],
    summary: Optional[str],
    run_id: Optional[str],
    dashboard_url: Optional[str],
) -> Dict[str, Any]:
    """Format a Slack Block Kit message."""
    emoji = _STATUS_EMOJI.get(event_type, "\u2139\uFE0F")
    header = f"{emoji} {event_type} \u2014 {project}"

    fields = []
    if issue_number is not None:
        issue_text = f"#{issue_number}"
        if issue_title:
            issue_text += f" {issue_title}"
        fields.append({"type": "mrkdwn", "text": f"*Issue*: {issue_text}"})

    if tokens_total is not None:
        fields.append({"type": "mrkdwn", "text": f"*Tokens*: {tokens_total:,}"})

    blocks: List[Dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": header[:150]}},
    ]

    if fields:
        blocks.append({"type": "section", "fields": fields})

    if summary:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": summary[:2000]},
        })

    if dashboard_url and run_id:
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"<{dashboard_url}/runs/{run_id}|View in Dashboard>"},
            ],
        })

    return {"blocks": blocks}


def _format_discord(
    event_type: str,
    project: str,
    issue_number: Optional[int],
    issue_title: Optional[str],
    tokens_total: Optional[int],
    summary: Optional[str],
    run_id: Optional[str],
    dashboard_url: Optional[str],
) -> Dict[str, Any]:
    """Format a Discord embed message."""
    emoji = _STATUS_EMOJI.get(event_type, "\u2139\uFE0F")
    color = _DISCORD_COLORS.get(event_type, 8421504)  # grey default

    fields = []
    if issue_number is not None:
        issue_text = f"#{issue_number}"
        if issue_title:
            issue_text += f" {issue_title}"
        fields.append({"name": "Issue", "value": issue_text, "inline": True})

    if tokens_total is not None:
        fields.append({"name": "Tokens", "value": f"{tokens_total:,}", "inline": True})

    embed: Dict[str, Any] = {
        "title": f"{emoji} {event_type} \u2014 {project}",
        "color": color,
        "fields": fields,
    }

    if summary:
        embed["description"] = summary[:2048]

    if dashboard_url and run_id:
        embed["url"] = f"{dashboard_url}/runs/{run_id}"

    return {"embeds": [embed]}


def _format_telegram(
    event_type: str,
    project: str,
    issue_number: Optional[int],
    issue_title: Optional[str],
    tokens_total: Optional[int],
    summary: Optional[str],
    run_id: Optional[str],
    dashboard_url: Optional[str],
    chat_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Format a Telegram Bot API message.

    Expects webhook_url to be: https://api.telegram.org/bot<TOKEN>/sendMessage
    The chat_id must be configured in notifications.telegram_chat_id.
    """
    emoji = _STATUS_EMOJI.get(event_type, "\u2139\uFE0F")

    lines = [f"<b>{emoji} {event_type} \u2014 {project}</b>"]

    if issue_number is not None:
        issue_text = f"#{issue_number}"
        if issue_title:
            issue_text += f" {issue_title}"
        lines.append(f"<b>Issue:</b> {issue_text}")

    if tokens_total is not None:
        lines.append(f"<b>Tokens:</b> {tokens_total:,}")

    if summary:
        lines.append(f"\n{summary[:1000]}")

    if dashboard_url and run_id:
        lines.append(f'\n<a href="{dashboard_url}/runs/{run_id}">View in Dashboard</a>')

    text = "\n".join(lines)

    payload: Dict[str, Any] = {
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if chat_id:
        payload["chat_id"] = chat_id

    return payload


def _format_generic(
    event_type: str,
    project: str,
    issue_number: Optional[int],
    issue_title: Optional[str],
    tokens_total: Optional[int],
    summary: Optional[str],
    run_id: Optional[str],
    dashboard_url: Optional[str],
) -> Dict[str, Any]:
    """Format a simple JSON payload for generic webhooks."""
    return {
        "event_type": event_type,
        "project": project,
        "issue_number": issue_number,
        "issue_title": issue_title,
        "tokens_total": tokens_total,
        "summary": summary,
        "run_id": run_id,
        "dashboard_url": f"{dashboard_url}/runs/{run_id}" if dashboard_url and run_id else None,
    }


async def send_notification(
    event_type: str,
    project: str,
    issue_number: Optional[int] = None,
    issue_title: Optional[str] = None,
    tokens_total: Optional[int] = None,
    summary: Optional[str] = None,
    run_id: Optional[str] = None,
) -> bool:
    """Send a webhook notification for a run event.

    Returns True if notification was sent successfully, False otherwise.
    Never raises — failures are logged.
    """
    try:
        config = _get_notification_config()

        if not _should_notify(event_type, config):
            return False

        webhook_url = config["webhook_url"]
        webhook_type = config.get("webhook_type", "generic").lower()
        dashboard_url = config.get("dashboard_url", "").rstrip("/") or None

        # Build payload based on webhook type
        kwargs = dict(
            event_type=event_type,
            project=project,
            issue_number=issue_number,
            issue_title=issue_title,
            tokens_total=tokens_total,
            summary=summary,
            run_id=run_id,
            dashboard_url=dashboard_url,
        )

        if webhook_type == "slack":
            payload = _format_slack(**kwargs)
        elif webhook_type == "discord":
            payload = _format_discord(**kwargs)
        elif webhook_type == "telegram":
            chat_id = config.get("telegram_chat_id")
            payload = _format_telegram(**kwargs, chat_id=chat_id)
        else:
            payload = _format_generic(**kwargs)

        # Send the webhook
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()

        logger.info(
            "Notification sent: %s for %s (type=%s, status=%d)",
            event_type, project, webhook_type, response.status_code,
        )
        return True

    except httpx.HTTPStatusError as e:
        logger.warning(
            "Notification webhook returned error: %s %s (body: %s)",
            e.response.status_code, e.response.reason_phrase,
            e.response.text[:200] if e.response.text else "",
        )
        return False
    except httpx.RequestError as e:
        logger.warning("Notification webhook request failed: %s", e)
        return False
    except Exception:
        logger.exception("Unexpected error sending notification")
        return False


async def send_test_notification() -> Dict[str, Any]:
    """Send a test notification to verify webhook configuration.

    Returns a dict with status and details.
    """
    config = _get_notification_config()

    if not config.get("enabled"):
        return {"success": False, "error": "Notifications are not enabled"}
    if config.get("method") != "webhook":
        return {"success": False, "error": "Method is not 'webhook'"}
    if not config.get("webhook_url"):
        return {"success": False, "error": "Webhook URL is not configured"}

    result = await send_notification(
        event_type="TEST",
        project="test/notification-check",
        issue_number=0,
        issue_title="Test Notification",
        tokens_total=12345,
        summary="This is a test notification from Claude Agent Station. If you see this, your webhook is configured correctly!",
        run_id="test-notification",
    )

    if result:
        return {"success": True, "message": "Test notification sent successfully"}
    else:
        return {"success": False, "error": "Failed to send notification. Check logs for details."}
