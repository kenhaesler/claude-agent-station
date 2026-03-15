"""Slack Block Kit notification adapter."""

from __future__ import annotations

from typing import Any

from app.services.adapters.base import NotificationAdapter, STATUS_EMOJI


class SlackAdapter(NotificationAdapter):
    """Formats notifications as Slack Block Kit messages."""

    @property
    def name(self) -> str:
        return "slack"

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
        header = f"{emoji} {event_type} \u2014 {project}"

        fields: list[dict[str, Any]] = []
        if issue_number is not None:
            issue_text = f"#{issue_number}"
            if issue_title:
                issue_text += f" {issue_title}"
            fields.append({"type": "mrkdwn", "text": f"*Issue*: {issue_text}"})

        if tokens_total is not None:
            fields.append({"type": "mrkdwn", "text": f"*Tokens*: {tokens_total:,}"})

        blocks: list[dict[str, Any]] = [
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
