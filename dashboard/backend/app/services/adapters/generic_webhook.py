"""Generic JSON webhook notification adapter."""

from __future__ import annotations

from typing import Any

from app.services.adapters.base import NotificationAdapter


class GenericWebhookAdapter(NotificationAdapter):
    """Formats notifications as a plain JSON payload."""

    @property
    def name(self) -> str:
        return "generic"

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
