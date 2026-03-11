"""Tests for the notification service, including the test-notification bug fix."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.notifier import (
    _should_notify,
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

    def test_returns_false_when_no_url(self):
        config = {"enabled": True, "method": "webhook", "webhook_url": ""}
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


class TestSendTestNotification:
    """Tests for the send_test_notification function - validates the bug fix."""

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
