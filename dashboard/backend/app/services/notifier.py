from __future__ import annotations

"""Webhook notification service.

Sends notifications when runs complete, verdicts are issued, or errors occur.
Failures are logged but never raise -- notifications must not crash the backend.

Message formatting is delegated to pluggable adapters in
:mod:`app.services.adapters`.  The public API (``send_notification`` and
``send_test_notification``) is unchanged.

Supports multi-target delivery: the ``notifications`` config section may
contain a ``targets`` list where each entry specifies its own
``webhook_url``, ``webhook_type``, and optional ``notify_on`` filter.
A single ``webhook_url`` at the top level is still supported for backward
compatibility and is treated as one implicit target.

Transient HTTP errors (5xx) are retried once after a 1-second delay.
"""

import asyncio
import ipaddress
import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from app.services.adapters import get_adapter
from app.services.config_sync import _read_config_json

logger = logging.getLogger(__name__)

# Retry settings for transient (5xx) failures
_MAX_RETRIES = 1
_RETRY_DELAY_SECONDS = 1.0


# ---------------------------------------------------------------------------
# SSRF protection: webhook URL validation
# ---------------------------------------------------------------------------

_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Hostnames that always resolve into reserved space and are therefore rejected
# without any DNS lookup.  Anything else is checked only if it parses as a
# literal IP address -- DNS resolution is intentionally skipped (we rely on
# outbound network egress controls for hostnames).
_BLOCKED_HOSTNAMES = frozenset({"localhost", "ip6-localhost", "ip6-loopback"})


class WebhookUrlValidationError(ValueError):
    """Raised when a webhook URL is rejected by SSRF protection."""


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True for IPs in private, loopback, link-local, multicast,
    reserved, or unspecified ranges (i.e. anything that should not be
    callable from a public webhook config)."""
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_webhook_url(url: str) -> None:
    """Validate *url* for use as a webhook target.

    Raises :class:`WebhookUrlValidationError` if the URL:

    - has a scheme other than ``http`` / ``https``
    - is missing a hostname / netloc
    - has a hostname literal that resolves into private, loopback,
      link-local, multicast, reserved, or unspecified address space
    - has a hostname in :data:`_BLOCKED_HOSTNAMES` (e.g. ``localhost``)

    DNS resolution is intentionally skipped for non-IP hostnames; the caller
    relies on outbound network egress controls for that defence layer.
    """
    if not isinstance(url, str) or not url:
        raise WebhookUrlValidationError("webhook_url must be a non-empty string")

    try:
        parsed = urlparse(url)
    except ValueError as e:
        raise WebhookUrlValidationError(f"webhook_url is not a valid URL: {e}") from e

    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise WebhookUrlValidationError(
            f"webhook_url scheme must be http or https (got '{scheme or 'missing'}')"
        )

    hostname = parsed.hostname
    if not hostname:
        raise WebhookUrlValidationError("webhook_url is missing a hostname")

    host_lc = hostname.lower()
    if host_lc in _BLOCKED_HOSTNAMES:
        raise WebhookUrlValidationError(
            f"webhook_url hostname '{hostname}' is not allowed"
        )

    # If the hostname is a literal IP address, reject any reserved range.
    # IPv6 hostnames come back from urlparse without surrounding brackets.
    try:
        ip = ipaddress.ip_address(host_lc)
    except ValueError:
        # Not an IP literal -- accept (rely on egress controls).
        return

    if _ip_is_blocked(ip):
        raise WebhookUrlValidationError(
            f"webhook_url points to a non-public IP address ({ip})"
        )


def validate_notifications_config(notifications: dict[str, Any]) -> None:
    """Validate every webhook URL inside a ``notifications`` config block.

    Validates both the legacy top-level ``webhook_url`` and every
    ``targets[*].webhook_url`` entry.  Empty / missing URLs are skipped
    (they're harmless -- the notifier simply won't send).

    Raises :class:`WebhookUrlValidationError` if any URL is invalid.
    """
    if not isinstance(notifications, dict):
        return

    top_url = notifications.get("webhook_url")
    if isinstance(top_url, str) and top_url:
        try:
            validate_webhook_url(top_url)
        except WebhookUrlValidationError as e:
            raise WebhookUrlValidationError(
                f"Invalid notifications.webhook_url: {e}"
            ) from e

    targets = notifications.get("targets")
    if isinstance(targets, list):
        for idx, target in enumerate(targets):
            if not isinstance(target, dict):
                continue
            t_url = target.get("webhook_url")
            if isinstance(t_url, str) and t_url:
                try:
                    validate_webhook_url(t_url)
                except WebhookUrlValidationError as e:
                    raise WebhookUrlValidationError(
                        f"Invalid notifications.targets[{idx}].webhook_url: {e}"
                    ) from e


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

async def _get_notification_config() -> dict[str, Any]:
    """Read notification config from manager-config.json."""
    config = await asyncio.to_thread(_read_config_json)
    return config.get("notifications", {})


def _resolve_targets(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a list of notification targets from config.

    If ``config`` contains a ``targets`` list, each entry is used as-is
    (inheriting top-level defaults where keys are missing).  Otherwise the
    top-level ``webhook_url`` / ``webhook_type`` are wrapped into a single
    implicit target for backward compatibility.
    """
    explicit_targets = config.get("targets")
    if explicit_targets:
        resolved: list[dict[str, Any]] = []
        for t in explicit_targets:
            # Merge top-level keys as defaults for any missing target keys
            merged: dict[str, Any] = {
                "webhook_url": t.get("webhook_url", config.get("webhook_url", "")),
                "webhook_type": t.get("webhook_type", config.get("webhook_type", "generic")),
                "notify_on": t.get("notify_on"),  # None means "use default"
                "dashboard_url": t.get("dashboard_url", config.get("dashboard_url", "")),
            }
            # Carry through any adapter-specific keys (e.g. telegram_chat_id)
            for key, value in t.items():
                if key not in merged:
                    merged[key] = value
            resolved.append(merged)
        return resolved

    # Backward-compatible: single target from top-level keys
    url = config.get("webhook_url", "")
    if not url:
        return []
    return [{
        "webhook_url": url,
        "webhook_type": config.get("webhook_type", "generic"),
        "notify_on": config.get("notify_on"),
        "dashboard_url": config.get("dashboard_url", ""),
    }]


def _should_notify(event_type: str, config: dict[str, Any]) -> bool:
    """Check if notifications are globally enabled for this event type.

    This validates top-level switches (``enabled``, ``method``).  It does
    **not** filter by ``webhook_url`` or per-target ``notify_on`` -- that
    happens inside ``_send_to_target``.
    """
    if not config.get("enabled", False):
        return False
    if config.get("method") != "webhook":
        return False
    # Must have at least one target URL (top-level or in targets list)
    targets = _resolve_targets(config)
    if not targets:
        return False

    # Global notify_on acts as a pre-filter when no targets list is used
    default_notify_on = config.get("notify_on", ["approve", "reject", "pr", "error"])
    has_targets_list = bool(config.get("targets"))
    if has_targets_list:
        # With explicit targets, at least one target must accept this event
        for t in targets:
            target_notify_on = t.get("notify_on") or default_notify_on
            if event_type.lower() in [n.lower() for n in target_notify_on]:
                return True
        return False
    else:
        return event_type.lower() in [n.lower() for n in default_notify_on]


def _target_accepts_event(
    event_type: str,
    target: dict[str, Any],
    default_notify_on: list[str],
) -> bool:
    """Return True if *target* should receive *event_type*."""
    notify_on = target.get("notify_on") or default_notify_on
    return event_type.lower() in [n.lower() for n in notify_on]


# ---------------------------------------------------------------------------
# Core send logic
# ---------------------------------------------------------------------------

async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
) -> httpx.Response:
    """POST *payload* to *url*, retrying once on 5xx errors."""
    response = await client.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json"},
    )
    if response.status_code >= 500 and _MAX_RETRIES > 0:
        logger.info(
            "Webhook returned %d, retrying in %.1fs ...",
            response.status_code, _RETRY_DELAY_SECONDS,
        )
        await asyncio.sleep(_RETRY_DELAY_SECONDS)
        response = await client.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
    response.raise_for_status()
    return response


async def _send_to_target(
    target: dict[str, Any],
    event_type: str,
    project: str,
    issue_number: int | None,
    issue_title: str | None,
    tokens_total: int | None,
    summary: str | None,
    run_id: str | None,
    config: dict[str, Any],
) -> tuple[bool, str | None]:
    """Send one notification to a single target.

    Returns (True, None) on success, (False, error_message) on failure.
    Never raises.
    """
    try:
        webhook_url = target["webhook_url"]
        webhook_type = target.get("webhook_type", "generic").lower()
        dashboard_url = target.get("dashboard_url", "").rstrip("/") or None

        # Defense-in-depth: re-validate the URL before issuing the request.
        # If a malicious config slipped past write-time validation (e.g.
        # via direct file edit), refuse to make the outbound call.
        try:
            validate_webhook_url(webhook_url)
        except WebhookUrlValidationError as e:
            detail = f"Refusing to send to invalid webhook_url: {e}"
            logger.warning(
                "Notification blocked by SSRF guard (url=%s): %s",
                webhook_url, e,
            )
            return (False, detail)

        adapter = get_adapter(webhook_type)

        # Build adapter config by merging target-level keys into the
        # top-level config so adapters can read provider-specific keys
        # (e.g. telegram_chat_id) from either level.
        adapter_config = {**config, **target}

        payload = adapter.format_message(
            event_type=event_type,
            project=project,
            issue_number=issue_number,
            issue_title=issue_title,
            tokens_total=tokens_total,
            summary=summary,
            run_id=run_id,
            dashboard_url=dashboard_url,
            config=adapter_config,
        )

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await _post_with_retry(client, webhook_url, payload)

        logger.info(
            "Notification sent: %s for %s (adapter=%s, url=%s, status=%d)",
            event_type, project, adapter.name, webhook_url, response.status_code,
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
    """Send a webhook notification to all matching targets.

    Returns (True, None) if **at least one** target succeeded,
    (False, error_message) if all failed or none matched.
    Never raises -- failures are logged.
    """
    try:
        config = await _get_notification_config()

        if not _bypass_filter and not _should_notify(event_type, config):
            return (False, None)

        targets = _resolve_targets(config)
        if not targets:
            return (False, None)

        default_notify_on = config.get("notify_on", ["approve", "reject", "pr", "error"])

        any_success = False
        last_error: str | None = None

        for target in targets:
            # Per-target event filtering (skip if _bypass_filter is set)
            if not _bypass_filter and not _target_accepts_event(
                event_type, target, default_notify_on
            ):
                continue

            success, error = await _send_to_target(
                target=target,
                event_type=event_type,
                project=project,
                issue_number=issue_number,
                issue_title=issue_title,
                tokens_total=tokens_total,
                summary=summary,
                run_id=run_id,
                config=config,
            )
            if success:
                any_success = True
            else:
                last_error = error

        if any_success:
            return (True, None)
        return (False, last_error)

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

    targets = _resolve_targets(config)
    if not targets:
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
