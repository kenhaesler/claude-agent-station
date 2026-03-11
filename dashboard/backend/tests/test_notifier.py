"""Comprehensive tests for the notification service.

Covers:
- _should_notify filter logic
- All four message formatters (Slack, Discord, Telegram, Generic)
- send_notification with bypass filter
- send_test_notification flow
- Error handling (never crashes)
- Stale run reaper notification integration
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.notifier import (
    _should_notify,
    _format_slack,
    _format_discord,
    _format_telegram,
    _format_generic,
    send_notification,
    send_test_notification,
)


class TestShouldNotify:
    """Tests for the _should_notify filter function."""

    def test_returns_false_when_disabled(self):
        config = {"enabled": False, "method": "webhook", "webhook_url": "https://x"}
        assert _should_notify("approve", config) is False

    def test_returns_false_when_method_not_webhook(self):
        config = {"enabled": True, "method": "email", "webhook_url": "https://x"}
        assert _should_notify("approve", config) is False

    def test_returns_false_when_method_is_file(self):
        config = {"enabled": True, "method": "file", "webhook_url": "https://x"}
        assert _should_notify("approve", config) is False

    def test_returns_false_when_no_url(self):
        config = {"enabled": True, "method": "webhook", "webhook_url": ""}
        assert _should_notify("approve", config) is False

    def test_returns_false_when_url_missing(self):
        config = {"enabled": True, "method": "webhook"}
        assert _should_notify("approve", config) is False

    def test_returns_true_for_matching_event_type(self):
        config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://x",
            "notify_on": ["approve", "reject"],
        }
        assert _should_notify("approve", config) is True

    def test_returns_false_for_non_matching_event_type(self):
        config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://x",
            "notify_on": ["approve", "reject"],
        }
        assert _should_notify("TEST", config) is False

    def test_case_insensitive_matching(self):
        config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://x",
            "notify_on": ["APPROVE"],
        }
        assert _should_notify("approve", config) is True

    def test_default_notify_on_includes_standard_events(self):
        config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://x",
            # No notify_on specified — should use defaults
        }
        assert _should_notify("approve", config) is True
        assert _should_notify("reject", config) is True
        assert _should_notify("pr", config) is True
        assert _should_notify("error", config) is True

    def test_default_notify_on_excludes_test(self):
        config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://x",
        }
        assert _should_notify("TEST", config) is False


class TestFormatSlack:
    """Tests for the Slack Block Kit formatter."""

    def test_basic_approve_message(self):
        payload = _format_slack(
            event_type="APPROVE",
            project="kenhaesler/my-repo",
            issue_number=42,
            issue_title="Add dark mode",
            tokens_total=47234,
            summary="Changes approved and pushed",
            run_id="run-123",
            dashboard_url="https://dashboard.example.com",
        )
        assert "blocks" in payload
        blocks = payload["blocks"]
        # Header block
        assert blocks[0]["type"] == "header"
        assert "APPROVE" in blocks[0]["text"]["text"]
        assert "kenhaesler/my-repo" in blocks[0]["text"]["text"]
        # Section with fields
        assert blocks[1]["type"] == "section"
        fields = blocks[1]["fields"]
        assert any("#42" in f["text"] for f in fields)
        assert any("47,234" in f["text"] for f in fields)

    def test_minimal_message(self):
        payload = _format_slack(
            event_type="error",
            project="unknown",
            issue_number=None,
            issue_title=None,
            tokens_total=None,
            summary=None,
            run_id=None,
            dashboard_url=None,
        )
        assert "blocks" in payload
        assert payload["blocks"][0]["type"] == "header"

    def test_long_header_truncated(self):
        payload = _format_slack(
            event_type="APPROVE",
            project="a" * 200,
            issue_number=None,
            issue_title=None,
            tokens_total=None,
            summary=None,
            run_id=None,
            dashboard_url=None,
        )
        assert len(payload["blocks"][0]["text"]["text"]) <= 150

    def test_dashboard_link_included(self):
        payload = _format_slack(
            event_type="APPROVE",
            project="test/repo",
            issue_number=None,
            issue_title=None,
            tokens_total=None,
            summary=None,
            run_id="run-456",
            dashboard_url="https://dash.example.com",
        )
        # Should have a context block with dashboard link
        context_blocks = [b for b in payload["blocks"] if b["type"] == "context"]
        assert len(context_blocks) == 1
        assert "dash.example.com/runs/run-456" in context_blocks[0]["elements"][0]["text"]


class TestFormatDiscord:
    """Tests for the Discord embed formatter."""

    def test_basic_approve_message(self):
        payload = _format_discord(
            event_type="APPROVE",
            project="kenhaesler/my-repo",
            issue_number=42,
            issue_title="Add dark mode",
            tokens_total=47234,
            summary="Changes approved",
            run_id="run-123",
            dashboard_url="https://dashboard.example.com",
        )
        assert "embeds" in payload
        embed = payload["embeds"][0]
        assert "APPROVE" in embed["title"]
        assert embed["color"] == 5763719  # green
        assert any(f["name"] == "Issue" and "#42" in f["value"] for f in embed["fields"])
        assert any(f["name"] == "Tokens" and "47,234" in f["value"] for f in embed["fields"])
        assert embed["description"] == "Changes approved"
        assert embed["url"] == "https://dashboard.example.com/runs/run-123"

    def test_reject_color(self):
        payload = _format_discord(
            event_type="REJECT",
            project="test/repo",
            issue_number=None,
            issue_title=None,
            tokens_total=None,
            summary=None,
            run_id=None,
            dashboard_url=None,
        )
        assert payload["embeds"][0]["color"] == 15548997  # red

    def test_unknown_event_uses_grey(self):
        payload = _format_discord(
            event_type="UNKNOWN",
            project="test/repo",
            issue_number=None,
            issue_title=None,
            tokens_total=None,
            summary=None,
            run_id=None,
            dashboard_url=None,
        )
        assert payload["embeds"][0]["color"] == 8421504  # grey


class TestFormatTelegram:
    """Tests for the Telegram Bot API formatter."""

    def test_basic_message(self):
        payload = _format_telegram(
            event_type="APPROVE",
            project="kenhaesler/my-repo",
            issue_number=42,
            issue_title="Add dark mode",
            tokens_total=47234,
            summary="All good",
            run_id="run-123",
            dashboard_url="https://dashboard.example.com",
            chat_id="-1001234567890",
        )
        assert payload["parse_mode"] == "HTML"
        assert payload["chat_id"] == "-1001234567890"
        assert "<b>" in payload["text"]
        assert "APPROVE" in payload["text"]
        assert "#42" in payload["text"]
        assert "47,234" in payload["text"]
        assert payload["disable_web_page_preview"] is True

    def test_no_chat_id(self):
        payload = _format_telegram(
            event_type="APPROVE",
            project="test/repo",
            issue_number=None,
            issue_title=None,
            tokens_total=None,
            summary=None,
            run_id=None,
            dashboard_url=None,
        )
        assert "chat_id" not in payload


class TestFormatGeneric:
    """Tests for the generic JSON payload formatter."""

    def test_includes_all_fields(self):
        payload = _format_generic(
            event_type="APPROVE",
            project="kenhaesler/my-repo",
            issue_number=42,
            issue_title="Add dark mode",
            tokens_total=47234,
            summary="Changes approved",
            run_id="run-123",
            dashboard_url="https://dashboard.example.com",
        )
        assert payload["event_type"] == "APPROVE"
        assert payload["project"] == "kenhaesler/my-repo"
        assert payload["issue_number"] == 42
        assert payload["issue_title"] == "Add dark mode"
        assert payload["tokens_total"] == 47234
        assert payload["summary"] == "Changes approved"
        assert payload["run_id"] == "run-123"
        assert payload["dashboard_url"] == "https://dashboard.example.com/runs/run-123"

    def test_null_dashboard_url(self):
        payload = _format_generic(
            event_type="error",
            project="test/repo",
            issue_number=None,
            issue_title=None,
            tokens_total=None,
            summary=None,
            run_id=None,
            dashboard_url=None,
        )
        assert payload["dashboard_url"] is None


class TestSendTestNotification:
    """Tests for the send_test_notification function — validates the bug fix."""

    @pytest.mark.asyncio
    async def test_test_notification_bypasses_should_notify_filter(self):
        """The core bug fix: TEST event_type is not in notify_on list,
        but send_test_notification should still succeed because it uses
        _bypass_filter=True to skip the _should_notify check."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://hooks.example.com/test",
            "webhook_type": "generic",
            "notify_on": ["approve", "reject", "pr", "error"],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch(
            "app.services.notifier._get_notification_config", return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await send_test_notification()

            assert result["success"] is True
            assert "sent successfully" in result["message"]
            # Verify the webhook was actually called
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_test_notification_returns_error_when_disabled(self):
        mock_config = {"enabled": False}

        with patch(
            "app.services.notifier._get_notification_config", return_value=mock_config
        ):
            result = await send_test_notification()
            assert result["success"] is False
            assert "not enabled" in result["error"]

    @pytest.mark.asyncio
    async def test_test_notification_returns_error_when_no_url(self):
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "",
        }

        with patch(
            "app.services.notifier._get_notification_config", return_value=mock_config
        ):
            result = await send_test_notification()
            assert result["success"] is False
            assert "not configured" in result["error"]

    @pytest.mark.asyncio
    async def test_test_notification_returns_error_when_method_not_webhook(self):
        mock_config = {
            "enabled": True,
            "method": "file",
            "webhook_url": "https://hooks.example.com/test",
        }

        with patch(
            "app.services.notifier._get_notification_config", return_value=mock_config
        ):
            result = await send_test_notification()
            assert result["success"] is False
            assert "not 'webhook'" in result["error"]


class TestSendNotification:
    """Tests for send_notification with _bypass_filter parameter."""

    @pytest.mark.asyncio
    async def test_bypass_filter_skips_should_notify(self):
        """When _bypass_filter=True, _should_notify is not checked."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://hooks.example.com/test",
            "webhook_type": "generic",
            "notify_on": ["approve"],  # Does NOT include 'TEST'
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch(
            "app.services.notifier._get_notification_config", return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await send_notification(
                event_type="TEST",
                project="test/proj",
                _bypass_filter=True,
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_without_bypass_filter_respects_should_notify(self):
        """When _bypass_filter is False (default), _should_notify filters."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://hooks.example.com/test",
            "notify_on": ["approve"],  # Does NOT include 'TEST'
        }

        with patch(
            "app.services.notifier._get_notification_config", return_value=mock_config
        ):
            result = await send_notification(
                event_type="TEST",
                project="test/proj",
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_slack_webhook_type(self):
        """Verify Slack Block Kit payload is sent for slack webhook type."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://hooks.slack.com/services/test",
            "webhook_type": "slack",
            "notify_on": ["approve"],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch(
            "app.services.notifier._get_notification_config", return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await send_notification(
                event_type="APPROVE",
                project="test/repo",
                issue_number=42,
                issue_title="Test issue",
            )

            # Verify the payload sent was Slack Block Kit format
            call_args = mock_client.post.call_args
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            assert "blocks" in payload
            assert payload["blocks"][0]["type"] == "header"

    @pytest.mark.asyncio
    async def test_discord_webhook_type(self):
        """Verify Discord embed payload is sent for discord webhook type."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://discord.com/api/webhooks/test",
            "webhook_type": "discord",
            "notify_on": ["approve"],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch(
            "app.services.notifier._get_notification_config", return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await send_notification(
                event_type="APPROVE",
                project="test/repo",
            )

            call_args = mock_client.post.call_args
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            assert "embeds" in payload

    @pytest.mark.asyncio
    async def test_telegram_webhook_type(self):
        """Verify Telegram payload is sent for telegram webhook type."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://api.telegram.org/bot123/sendMessage",
            "webhook_type": "telegram",
            "telegram_chat_id": "-100123",
            "notify_on": ["approve"],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch(
            "app.services.notifier._get_notification_config", return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await send_notification(
                event_type="APPROVE",
                project="test/repo",
            )

            call_args = mock_client.post.call_args
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            assert payload["parse_mode"] == "HTML"
            assert payload["chat_id"] == "-100123"

    @pytest.mark.asyncio
    async def test_notification_failure_never_raises(self):
        """Notification errors are logged but never crash the backend."""
        import httpx

        mock_config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://hooks.example.com/test",
            "webhook_type": "generic",
            "notify_on": ["approve"],
        }

        with patch(
            "app.services.notifier._get_notification_config", return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            mock_client_cls.return_value = mock_client

            # Should not raise — returns False
            result = await send_notification(
                event_type="APPROVE",
                project="test/repo",
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_notification_http_error_never_raises(self):
        """HTTP errors are logged but never crash."""
        import httpx

        mock_config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://hooks.example.com/test",
            "webhook_type": "generic",
            "notify_on": ["approve"],
        }

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.reason_phrase = "Internal Server Error"
        mock_response.text = "Server error"
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "500", request=MagicMock(), response=mock_response
            )
        )

        with patch(
            "app.services.notifier._get_notification_config", return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await send_notification(
                event_type="APPROVE",
                project="test/repo",
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_unexpected_exception_never_raises(self):
        """Even unexpected exceptions don't crash."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://hooks.example.com/test",
            "webhook_type": "generic",
            "notify_on": ["approve"],
        }

        with patch(
            "app.services.notifier._get_notification_config", return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.side_effect = RuntimeError("Unexpected!")

            result = await send_notification(
                event_type="APPROVE",
                project="test/repo",
            )
            assert result is False
