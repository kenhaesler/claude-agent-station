"""Discord embed notification adapter."""

from __future__ import annotations

from typing import Any

from app.services.adapters.base import NotificationAdapter, STATUS_EMOJI

# Discord color mapping (decimal)
DISCORD_COLORS = {
    "APPROVE": 5763719,       # green
    "PR": 3447003,            # blue
    "REJECT": 15548997,       # red
    "error": 16776960,        # yellow
    "interrupted": 10038562,  # dark red
}


class DiscordAdapter(NotificationAdapter):
    """Formats notifications as Discord embed messages."""

    @property
    def name(self) -> str:
        return "discord"

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
        emoji = STATUS_EMOJI.get(event_type, "\u2139\uFE0F")
        color = DISCORD_COLORS.get(event_type, 8421504)  # grey default

        fields: list[dict[str, Any]] = []
        if issue_number is not None:
            issue_text = f"#{issue_number}"
            if issue_title:
                issue_text += f" {issue_title}"
            fields.append({"name": "Issue", "value": issue_text, "inline": True})

        if tokens_total is not None:
            fields.append({"name": "Tokens", "value": f"{tokens_total:,}", "inline": True})

        embed: dict[str, Any] = {
            "title": f"{emoji} {event_type} \u2014 {project}",
            "color": color,
            "fields": fields,
        }

        if summary:
            embed["description"] = summary[:2048]

        if dashboard_url and run_id:
            embed["url"] = f"{dashboard_url}/runs/{run_id}"

        return {"embeds": [embed]}
