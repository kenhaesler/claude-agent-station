"""Base class for notification adapters.

Each adapter knows how to format a notification payload for a specific
webhook provider and (optionally) customise request headers or URL
construction.
"""

from __future__ import annotations

import abc
from typing import Any


# Shared emoji mapping used across adapters
STATUS_EMOJI = {
    "APPROVE": "\u2705",         # checkmark
    "PR": "\U0001F4E4",          # outbox tray
    "REJECT": "\u274C",          # cross mark
    "error": "\U0001F6A8",       # rotating light
    "interrupted": "\U0001F480", # skull
}


class NotificationAdapter(abc.ABC):
    """Abstract base class that every notification adapter must implement."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Short identifier for this adapter (e.g. ``"slack"``, ``"discord"``)."""

    @abc.abstractmethod
    def format_message(
        self,
        *,
        event_type: str,
        project: str,
        issue_number: int | None,
        issue_title: str | None,
        tokens_total: int | None,
        summary: str | None,
        run_id: str | None,
        dashboard_url: str | None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the JSON payload for this provider.

        Parameters
        ----------
        event_type:
            One of ``APPROVE``, ``PR``, ``REJECT``, ``error``, ``interrupted``,
            ``TEST``, etc.
        project:
            Repository slug such as ``owner/repo``.
        issue_number:
            GitHub issue number, if applicable.
        issue_title:
            GitHub issue title, if applicable.
        tokens_total:
            Total token usage for the run, if known.
        summary:
            Human-readable description of what happened.
        run_id:
            Unique run identifier for dashboard links.
        dashboard_url:
            Base URL of the dashboard (no trailing slash).
        config:
            Full notification config dict -- adapters may read
            provider-specific keys from here (e.g. ``telegram_chat_id``).
        """
