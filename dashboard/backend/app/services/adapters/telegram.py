"""Telegram Bot API notification adapter."""

from __future__ import annotations

from typing import Any

from app.services.adapters.base import NotificationAdapter, STATUS_EMOJI


class TelegramAdapter(NotificationAdapter):
    """Formats notifications for the Telegram Bot API.

    Expects ``webhook_url`` to be ``https://api.telegram.org/bot<TOKEN>/sendMessage``.
    The ``chat_id`` is read from ``config["telegram_chat_id"]``.
    """

    @property
    def name(self) -> str:
        return "telegram"

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

        payload: dict[str, Any] = {
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        chat_id = (config or {}).get("telegram_chat_id")
        if chat_id:
            payload["chat_id"] = chat_id

        return payload
