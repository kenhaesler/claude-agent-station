"""Pluggable notification adapter registry.

Adapters are registered by their ``name`` property and looked up at
send time via :func:`get_adapter`.  Third-party adapters can be added
by calling :func:`register_adapter`.

Usage::

    from app.services.adapters import get_adapter

    adapter = get_adapter("slack")
    payload = adapter.format_message(event_type="APPROVE", project="owner/repo", ...)
"""

from __future__ import annotations

from typing import Any

from app.services.adapters.base import NotificationAdapter
from app.services.adapters.discord import DiscordAdapter
from app.services.adapters.generic_webhook import GenericWebhookAdapter
from app.services.adapters.slack import SlackAdapter
from app.services.adapters.telegram import TelegramAdapter

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_registry: dict[str, NotificationAdapter] = {}


def register_adapter(adapter: NotificationAdapter) -> None:
    """Register an adapter instance under its ``name``."""
    _registry[adapter.name] = adapter


def get_adapter(webhook_type: str) -> NotificationAdapter:
    """Return the adapter for *webhook_type*, falling back to ``"generic"``."""
    return _registry.get(webhook_type, _registry["generic"])


def list_adapters() -> list[str]:
    """Return sorted list of registered adapter names."""
    return sorted(_registry)


# ---------------------------------------------------------------------------
# Auto-register built-in adapters
# ---------------------------------------------------------------------------

register_adapter(SlackAdapter())
register_adapter(DiscordAdapter())
register_adapter(TelegramAdapter())
register_adapter(GenericWebhookAdapter())

__all__ = [
    "NotificationAdapter",
    "get_adapter",
    "list_adapters",
    "register_adapter",
    # Concrete adapters
    "DiscordAdapter",
    "GenericWebhookAdapter",
    "SlackAdapter",
    "TelegramAdapter",
]
