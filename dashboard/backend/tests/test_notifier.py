"""Comprehensive tests for the notification service.

Covers:
- _should_notify filter logic (including multi-target)
- _resolve_targets backward compatibility and targets list
- _target_accepts_event per-target filtering
- All four adapter formatters (Slack, Discord, Telegram, Generic)
- Adapter registry (get_adapter, register_adapter, list_adapters)
- send_notification with bypass filter
- send_test_notification flow
- Multi-target delivery
- Per-target notify_on filtering
- Retry on transient 5xx failure
- Error handling (never crashes)
"""

from unittest.mock import AsyncMock, MagicMock, call, patch

import httpx
import pytest

from app.services.adapters import (
    GenericWebhookAdapter,
    get_adapter,
    list_adapters,
    register_adapter,
)
from app.services.adapters.base import NotificationAdapter
from app.services.adapters.discord import DiscordAdapter
from app.services.adapters.slack import SlackAdapter
from app.services.adapters.telegram import TelegramAdapter
from app.services.notifier import (
    _resolve_targets,
    _send_notification_detailed,
    _should_notify,
    _target_accepts_event,
    send_notification,
    send_test_notification,
)


# ---------------------------------------------------------------------------
# Adapter Registry
# ---------------------------------------------------------------------------

class TestAdapterRegistry:
    """Tests for the adapter registry in adapters/__init__.py."""

    def test_get_adapter_slack(self):
        adapter = get_adapter("slack")
        assert isinstance(adapter, SlackAdapter)
        assert adapter.name == "slack"

    def test_get_adapter_discord(self):
        adapter = get_adapter("discord")
        assert isinstance(adapter, DiscordAdapter)
        assert adapter.name == "discord"

    def test_get_adapter_telegram(self):
        adapter = get_adapter("telegram")
        assert isinstance(adapter, TelegramAdapter)
        assert adapter.name == "telegram"

    def test_get_adapter_generic(self):
        adapter = get_adapter("generic")
        assert isinstance(adapter, GenericWebhookAdapter)
        assert adapter.name == "generic"

    def test_get_adapter_unknown_falls_back_to_generic(self):
        adapter = get_adapter("unknown_provider")
        assert isinstance(adapter, GenericWebhookAdapter)

    def test_list_adapters_returns_all_builtins(self):
        names = list_adapters()
        assert "discord" in names
        assert "generic" in names
        assert "slack" in names
        assert "telegram" in names

    def test_register_custom_adapter(self):
        class CustomAdapter(NotificationAdapter):
            @property
            def name(self) -> str:
                return "custom_test"

            def format_message(self, **kwargs):
                return {"custom": True}

        register_adapter(CustomAdapter())
        adapter = get_adapter("custom_test")
        assert adapter.name == "custom_test"
        assert adapter.format_message() == {"custom": True}


# ---------------------------------------------------------------------------
# _resolve_targets
# ---------------------------------------------------------------------------

class TestResolveTargets:
    """Tests for the _resolve_targets config normalisation helper."""

    def test_single_webhook_url_becomes_one_target(self):
        config = {
            "webhook_url": "https://hooks.example.com/abc",
            "webhook_type": "slack",
        }
        targets = _resolve_targets(config)
        assert len(targets) == 1
        assert targets[0]["webhook_url"] == "https://hooks.example.com/abc"
        assert targets[0]["webhook_type"] == "slack"

    def test_empty_webhook_url_returns_empty(self):
        config = {"webhook_url": ""}
        assert _resolve_targets(config) == []

    def test_missing_webhook_url_returns_empty(self):
        config = {}
        assert _resolve_targets(config) == []

    def test_targets_list_used_as_is(self):
        config = {
            "targets": [
                {"webhook_url": "https://a.com", "webhook_type": "slack"},
                {"webhook_url": "https://b.com", "webhook_type": "discord"},
            ],
        }
        targets = _resolve_targets(config)
        assert len(targets) == 2
        assert targets[0]["webhook_url"] == "https://a.com"
        assert targets[0]["webhook_type"] == "slack"
        assert targets[1]["webhook_url"] == "https://b.com"
        assert targets[1]["webhook_type"] == "discord"

    def test_targets_inherit_top_level_defaults(self):
        config = {
            "webhook_url": "https://fallback.com",
            "webhook_type": "generic",
            "dashboard_url": "https://dash.example.com",
            "targets": [
                {"webhook_url": "https://custom.com"},
                {},  # entirely empty -- should get all defaults
            ],
        }
        targets = _resolve_targets(config)
        assert targets[0]["webhook_url"] == "https://custom.com"
        assert targets[0]["webhook_type"] == "generic"
        assert targets[1]["webhook_url"] == "https://fallback.com"
        assert targets[1]["dashboard_url"] == "https://dash.example.com"

    def test_target_specific_notify_on_preserved(self):
        config = {
            "notify_on": ["approve"],
            "targets": [
                {"webhook_url": "https://a.com", "notify_on": ["error"]},
            ],
        }
        targets = _resolve_targets(config)
        assert targets[0]["notify_on"] == ["error"]

    def test_adapter_specific_keys_carried_through(self):
        config = {
            "targets": [
                {
                    "webhook_url": "https://tg.example.com",
                    "webhook_type": "telegram",
                    "telegram_chat_id": "-100999",
                },
            ],
        }
        targets = _resolve_targets(config)
        assert targets[0]["telegram_chat_id"] == "-100999"


# ---------------------------------------------------------------------------
# _target_accepts_event
# ---------------------------------------------------------------------------

class TestTargetAcceptsEvent:
    """Tests for per-target event filtering."""

    def test_accepts_when_event_in_target_notify_on(self):
        target = {"notify_on": ["approve", "error"]}
        assert _target_accepts_event("approve", target, []) is True

    def test_rejects_when_event_not_in_target_notify_on(self):
        target = {"notify_on": ["approve"]}
        assert _target_accepts_event("error", target, []) is False

    def test_falls_back_to_default_when_target_has_no_notify_on(self):
        target = {"notify_on": None}
        assert _target_accepts_event("pr", target, ["pr", "error"]) is True
        assert _target_accepts_event("approve", target, ["pr", "error"]) is False

    def test_case_insensitive(self):
        target = {"notify_on": ["APPROVE"]}
        assert _target_accepts_event("approve", target, []) is True

    def test_missing_notify_on_key_uses_default(self):
        target = {}
        assert _target_accepts_event("approve", target, ["approve"]) is True
        assert _target_accepts_event("error", target, ["approve"]) is False


# ---------------------------------------------------------------------------
# _should_notify
# ---------------------------------------------------------------------------

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
            # No notify_on specified -- should use defaults
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

    def test_multi_target_accepts_if_any_target_matches(self):
        """With targets list, _should_notify returns True if at least one
        target accepts the event type."""
        config = {
            "enabled": True,
            "method": "webhook",
            "targets": [
                {"webhook_url": "https://a.com", "notify_on": ["approve"]},
                {"webhook_url": "https://b.com", "notify_on": ["error"]},
            ],
        }
        assert _should_notify("approve", config) is True
        assert _should_notify("error", config) is True
        assert _should_notify("TEST", config) is False

    def test_multi_target_rejects_if_no_target_matches(self):
        config = {
            "enabled": True,
            "method": "webhook",
            "targets": [
                {"webhook_url": "https://a.com", "notify_on": ["approve"]},
            ],
        }
        assert _should_notify("error", config) is False


# ---------------------------------------------------------------------------
# Slack adapter
# ---------------------------------------------------------------------------

class TestSlackAdapter:
    """Tests for the Slack Block Kit adapter."""

    def setup_method(self):
        self.adapter = SlackAdapter()

    def test_basic_approve_message(self):
        payload = self.adapter.format_message(
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
        payload = self.adapter.format_message(
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
        payload = self.adapter.format_message(
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
        payload = self.adapter.format_message(
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


# ---------------------------------------------------------------------------
# Discord adapter
# ---------------------------------------------------------------------------

class TestDiscordAdapter:
    """Tests for the Discord embed adapter."""

    def setup_method(self):
        self.adapter = DiscordAdapter()

    def test_basic_approve_message(self):
        payload = self.adapter.format_message(
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
        payload = self.adapter.format_message(
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
        payload = self.adapter.format_message(
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


# ---------------------------------------------------------------------------
# Telegram adapter
# ---------------------------------------------------------------------------

class TestTelegramAdapter:
    """Tests for the Telegram Bot API adapter."""

    def setup_method(self):
        self.adapter = TelegramAdapter()

    def test_basic_message(self):
        payload = self.adapter.format_message(
            event_type="APPROVE",
            project="kenhaesler/my-repo",
            issue_number=42,
            issue_title="Add dark mode",
            tokens_total=47234,
            summary="All good",
            run_id="run-123",
            dashboard_url="https://dashboard.example.com",
            config={"telegram_chat_id": "-1001234567890"},
        )
        assert payload["parse_mode"] == "HTML"
        assert payload["chat_id"] == "-1001234567890"
        assert "<b>" in payload["text"]
        assert "APPROVE" in payload["text"]
        assert "#42" in payload["text"]
        assert "47,234" in payload["text"]
        assert payload["disable_web_page_preview"] is True

    def test_no_chat_id(self):
        payload = self.adapter.format_message(
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


# ---------------------------------------------------------------------------
# Generic webhook adapter
# ---------------------------------------------------------------------------

class TestGenericWebhookAdapter:
    """Tests for the generic JSON payload adapter."""

    def setup_method(self):
        self.adapter = GenericWebhookAdapter()

    def test_includes_all_fields(self):
        payload = self.adapter.format_message(
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
        payload = self.adapter.format_message(
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


# ---------------------------------------------------------------------------
# send_test_notification
# ---------------------------------------------------------------------------

class TestSendTestNotification:
    """Tests for the send_test_notification function -- validates the bug fix."""

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
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
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
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
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
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
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
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
        ):
            result = await send_test_notification()
            assert result["success"] is False
            assert "not 'webhook'" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_specific_http_error(self):
        """Test notification should return specific HTTP error details."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://hooks.example.com/test",
            "webhook_type": "generic",
        }

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.reason_phrase = "Forbidden"
        mock_response.text = "Access denied"
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "403", request=MagicMock(), response=mock_response
            )
        )

        with patch(
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await send_test_notification()
            assert result["success"] is False
            assert "403" in result["error"]
            assert "Forbidden" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_specific_connection_error(self):
        """Test notification should return specific connection error details."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://hooks.example.com/test",
            "webhook_type": "generic",
        }

        with patch(
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            mock_client_cls.return_value = mock_client

            result = await send_test_notification()
            assert result["success"] is False
            assert "Connection refused" in result["error"]


# ---------------------------------------------------------------------------
# _send_notification_detailed
# ---------------------------------------------------------------------------

class TestSendNotificationDetailed:
    """Tests for _send_notification_detailed return values."""

    @pytest.mark.asyncio
    async def test_detailed_returns_tuple_on_failure(self):
        """Verify (False, error_string) on HTTP error (5xx triggers retry)."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://hooks.example.com/test",
            "webhook_type": "generic",
            "notify_on": ["approve"],
        }

        mock_response = MagicMock()
        mock_response.status_code = 502
        mock_response.reason_phrase = "Bad Gateway"
        mock_response.text = "upstream error"
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "502", request=MagicMock(), response=mock_response
            )
        )

        with patch(
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls, \
             patch("app.services.notifier.asyncio.sleep", new_callable=AsyncMock):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            success, detail = await _send_notification_detailed(
                event_type="APPROVE",
                project="test/repo",
            )
            assert success is False
            assert "502" in detail
            assert "Bad Gateway" in detail

    @pytest.mark.asyncio
    async def test_detailed_returns_tuple_on_success(self):
        """Verify (True, None) on success."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://hooks.example.com/test",
            "webhook_type": "generic",
            "notify_on": ["approve"],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch(
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            success, detail = await _send_notification_detailed(
                event_type="APPROVE",
                project="test/repo",
            )
            assert success is True
            assert detail is None


# ---------------------------------------------------------------------------
# send_notification
# ---------------------------------------------------------------------------

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
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
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
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
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
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
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
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
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
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
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
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://hooks.example.com/test",
            "webhook_type": "generic",
            "notify_on": ["approve"],
        }

        with patch(
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            mock_client_cls.return_value = mock_client

            # Should not raise -- returns False
            result = await send_notification(
                event_type="APPROVE",
                project="test/repo",
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_notification_http_error_never_raises(self):
        """HTTP errors are logged but never crash."""
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
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls, \
             patch("app.services.notifier.asyncio.sleep", new_callable=AsyncMock):
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
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.side_effect = RuntimeError("Unexpected!")

            result = await send_notification(
                event_type="APPROVE",
                project="test/repo",
            )
            assert result is False


# ---------------------------------------------------------------------------
# Multi-target delivery
# ---------------------------------------------------------------------------

class TestMultiTargetDelivery:
    """Tests for sending notifications to multiple targets."""

    @pytest.mark.asyncio
    async def test_sends_to_all_targets(self):
        """send_notification should POST to every target in the targets list."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "targets": [
                {"webhook_url": "https://slack.example.com", "webhook_type": "slack"},
                {"webhook_url": "https://discord.example.com", "webhook_type": "discord"},
            ],
            "notify_on": ["approve"],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch(
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
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

            assert result is True
            # Two targets, each gets its own AsyncClient context, so
            # post is called once per target (2 total).
            assert mock_client.post.call_count == 2

            # Verify both URLs were called
            urls_called = [c.args[0] for c in mock_client.post.call_args_list]
            assert "https://slack.example.com" in urls_called
            assert "https://discord.example.com" in urls_called

    @pytest.mark.asyncio
    async def test_partial_failure_returns_true_if_any_succeed(self):
        """If one target fails but another succeeds, return True."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "targets": [
                {"webhook_url": "https://good.example.com", "webhook_type": "generic"},
                {"webhook_url": "https://bad.example.com", "webhook_type": "generic"},
            ],
            "notify_on": ["approve"],
        }

        good_response = MagicMock()
        good_response.status_code = 200
        good_response.raise_for_status = MagicMock()

        bad_response = MagicMock()
        bad_response.status_code = 403
        bad_response.reason_phrase = "Forbidden"
        bad_response.text = "Access denied"
        bad_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "403", request=MagicMock(), response=bad_response
            )
        )

        call_count = 0

        async def pick_response(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            url = args[0]
            if "good" in url:
                return good_response
            return bad_response

        with patch(
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=pick_response)
            mock_client_cls.return_value = mock_client

            result = await send_notification(
                event_type="APPROVE",
                project="test/repo",
            )

            assert result is True

    @pytest.mark.asyncio
    async def test_all_targets_fail_returns_false(self):
        """If every target fails, return False."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "targets": [
                {"webhook_url": "https://a.example.com", "webhook_type": "generic"},
                {"webhook_url": "https://b.example.com", "webhook_type": "generic"},
            ],
            "notify_on": ["approve"],
        }

        with patch(
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(
                side_effect=httpx.ConnectError("refused")
            )
            mock_client_cls.return_value = mock_client

            result = await send_notification(
                event_type="APPROVE",
                project="test/repo",
            )

            assert result is False

    @pytest.mark.asyncio
    async def test_backward_compat_single_url(self):
        """A config with a single webhook_url (no targets) still works."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://single.example.com",
            "webhook_type": "generic",
            "notify_on": ["approve"],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch(
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
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
            assert result is True
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_adapter_specific_keys_from_target(self):
        """Target-level adapter keys (e.g. telegram_chat_id) are merged
        into the config dict passed to the adapter."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "targets": [
                {
                    "webhook_url": "https://tg.example.com",
                    "webhook_type": "telegram",
                    "telegram_chat_id": "-100999",
                    "notify_on": ["approve"],
                },
            ],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch(
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
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
            # Telegram adapter should have received the chat_id
            assert payload["chat_id"] == "-100999"


# ---------------------------------------------------------------------------
# Per-target notify_on filtering
# ---------------------------------------------------------------------------

class TestPerTargetNotifyOn:
    """Tests for per-target notify_on filtering during send."""

    @pytest.mark.asyncio
    async def test_target_only_receives_matching_events(self):
        """A target with notify_on=["error"] should NOT receive 'approve'."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "targets": [
                {
                    "webhook_url": "https://errors-only.example.com",
                    "webhook_type": "generic",
                    "notify_on": ["error"],
                },
                {
                    "webhook_url": "https://all-events.example.com",
                    "webhook_type": "generic",
                    "notify_on": ["approve", "error"],
                },
            ],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch(
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
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

            assert result is True
            # Only the second target should receive the approve event
            assert mock_client.post.call_count == 1
            url_called = mock_client.post.call_args.args[0]
            assert url_called == "https://all-events.example.com"

    @pytest.mark.asyncio
    async def test_error_event_goes_to_errors_only_target(self):
        """An error event should reach the errors-only target."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "targets": [
                {
                    "webhook_url": "https://errors-only.example.com",
                    "webhook_type": "generic",
                    "notify_on": ["error"],
                },
                {
                    "webhook_url": "https://approves-only.example.com",
                    "webhook_type": "generic",
                    "notify_on": ["approve"],
                },
            ],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch(
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await send_notification(
                event_type="error",
                project="test/repo",
            )

            assert result is True
            assert mock_client.post.call_count == 1
            url_called = mock_client.post.call_args.args[0]
            assert url_called == "https://errors-only.example.com"

    @pytest.mark.asyncio
    async def test_target_without_notify_on_uses_default(self):
        """Targets without notify_on inherit the top-level default."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "notify_on": ["approve", "reject"],
            "targets": [
                {
                    "webhook_url": "https://default-filter.example.com",
                    "webhook_type": "generic",
                    # no notify_on -- inherits top-level ["approve", "reject"]
                },
            ],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch(
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            # "approve" matches
            result = await send_notification(
                event_type="APPROVE",
                project="test/repo",
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_bypass_filter_skips_per_target_filtering(self):
        """_bypass_filter=True should send to all targets regardless of notify_on."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "targets": [
                {
                    "webhook_url": "https://a.example.com",
                    "webhook_type": "generic",
                    "notify_on": ["approve"],  # Does not include TEST
                },
                {
                    "webhook_url": "https://b.example.com",
                    "webhook_type": "generic",
                    "notify_on": ["error"],  # Does not include TEST
                },
            ],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch(
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
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
            # Both targets should receive the notification
            assert mock_client.post.call_count == 2


# ---------------------------------------------------------------------------
# Retry on 5xx
# ---------------------------------------------------------------------------

class TestRetryOnTransientFailure:
    """Tests for automatic retry on HTTP 5xx errors."""

    @pytest.mark.asyncio
    async def test_retries_once_on_5xx_then_succeeds(self):
        """If first POST returns 5xx, retry once. If retry succeeds, return True."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://flaky.example.com",
            "webhook_type": "generic",
            "notify_on": ["approve"],
        }

        first_response = MagicMock()
        first_response.status_code = 502

        second_response = MagicMock()
        second_response.status_code = 200
        second_response.raise_for_status = MagicMock()

        with patch(
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls, \
             patch("app.services.notifier.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=[first_response, second_response])
            mock_client_cls.return_value = mock_client

            result = await send_notification(
                event_type="APPROVE",
                project="test/repo",
            )

            assert result is True
            assert mock_client.post.call_count == 2
            mock_sleep.assert_called_once_with(1.0)

    @pytest.mark.asyncio
    async def test_retries_once_on_5xx_still_fails(self):
        """If both attempts return 5xx, return False."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://down.example.com",
            "webhook_type": "generic",
            "notify_on": ["approve"],
        }

        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.reason_phrase = "Service Unavailable"
        mock_response.text = "try later"
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "503", request=MagicMock(), response=mock_response
            )
        )

        with patch(
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls, \
             patch("app.services.notifier.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
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
            # Initial + 1 retry = 2 POST calls
            assert mock_client.post.call_count == 2
            mock_sleep.assert_called_once_with(1.0)

    @pytest.mark.asyncio
    async def test_no_retry_on_4xx(self):
        """4xx errors should NOT be retried."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://auth.example.com",
            "webhook_type": "generic",
            "notify_on": ["approve"],
        }

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.reason_phrase = "Unauthorized"
        mock_response.text = "bad token"
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "401", request=MagicMock(), response=mock_response
            )
        )

        with patch(
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls, \
             patch("app.services.notifier.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
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
            # Only one attempt, no retry
            mock_client.post.assert_called_once()
            mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_retry_on_success(self):
        """A 200 response should not trigger any retry."""
        mock_config = {
            "enabled": True,
            "method": "webhook",
            "webhook_url": "https://ok.example.com",
            "webhook_type": "generic",
            "notify_on": ["approve"],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch(
            "app.services.notifier._get_notification_config", new_callable=AsyncMock, return_value=mock_config
        ), patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls, \
             patch("app.services.notifier.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await send_notification(
                event_type="APPROVE",
                project="test/repo",
            )

            assert result is True
            mock_client.post.assert_called_once()
            mock_sleep.assert_not_called()
