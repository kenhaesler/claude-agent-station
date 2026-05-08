"""Tests for SSRF protection on webhook URLs (issue #193).

Covers:
- ``validate_webhook_url`` rejects non-http(s) schemes (file://, ftp://, gopher://, javascript:)
- ``validate_webhook_url`` rejects empty / missing netloc
- ``validate_webhook_url`` rejects literal private/loopback/link-local IPs
- ``validate_webhook_url`` accepts legitimate https hostnames
- Config router rejects PUT /api/config with malicious notifications.webhook_url
- Config router rejects PUT /api/config with malicious notifications.targets[*].webhook_url
- Config router rejects update if any single target entry is bad
- ``_send_to_target`` performs defense-in-depth re-validation and never makes the HTTP call
"""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, engine
from app.main import app
from app.services.notifier import (
    WebhookUrlValidationError,
    _send_to_target,
    validate_webhook_url,
)


# ---------------------------------------------------------------------------
# Unit tests: validate_webhook_url
# ---------------------------------------------------------------------------

class TestValidateWebhookUrlScheme:
    """Reject non-http(s) schemes."""

    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com/x",
        "javascript:alert(1)",
        "data:text/plain,hello",
        "ssh://example.com",
        "ldap://example.com",
        "://example.com",
        "example.com/no-scheme",
    ])
    def test_rejects_disallowed_scheme(self, url: str):
        with pytest.raises(WebhookUrlValidationError):
            validate_webhook_url(url)

    def test_accepts_http(self):
        # http allowed (some self-hosted webhooks); private-ip check still applies
        validate_webhook_url("http://hooks.example.com/x")

    def test_accepts_https(self):
        validate_webhook_url("https://hooks.slack.com/services/T000/B000/abc")


class TestValidateWebhookUrlNetloc:
    """Reject empty / missing netloc."""

    @pytest.mark.parametrize("url", [
        "",
        "http://",
        "https://",
        "http:///path",
    ])
    def test_rejects_empty_or_missing_netloc(self, url: str):
        with pytest.raises(WebhookUrlValidationError):
            validate_webhook_url(url)


class TestValidateWebhookUrlPrivateIp:
    """Reject literal IPs that resolve to private/loopback/link-local space."""

    @pytest.mark.parametrize("url", [
        "http://127.0.0.1/x",
        "http://127.1.2.3/x",
        "https://localhost/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/x",
        "http://10.255.255.254/x",
        "http://172.16.0.1/x",
        "http://172.31.255.254/x",
        "http://192.168.1.1/x",
        "http://0.0.0.0/x",
        "http://[::1]/x",
        "http://[fe80::1]/x",
        "http://[fc00::1]/x",
    ])
    def test_rejects_private_or_loopback(self, url: str):
        with pytest.raises(WebhookUrlValidationError):
            validate_webhook_url(url)

    @pytest.mark.parametrize("url", [
        "https://hooks.slack.com/services/T000/B000/abc",
        "https://discord.com/api/webhooks/123/abc",
        "https://api.telegram.org/bot123:abc/sendMessage",
        "http://webhook.site/uuid",
        "https://8.8.8.8/x",  # public IP literal is fine
    ])
    def test_accepts_public_url(self, url: str):
        validate_webhook_url(url)


# ---------------------------------------------------------------------------
# Integration tests: PUT /api/config rejects bad webhook URLs
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(setup_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_url", [
    "file:///etc/passwd",
    "ftp://example.com",
    "gopher://example.com",
    "http://127.0.0.1/x",
    "http://localhost/x",
    "http://169.254.169.254/x",
    "http://10.0.0.1/x",
    "http://192.168.1.1/x",
])
async def test_put_config_rejects_top_level_bad_webhook_url(client, bad_url: str):
    """PUT /api/config must reject malicious top-level webhook_url with HTTP 400."""
    current = {"projects": [], "notifications": {}}
    with patch("app.routers.config_router._read_config_json", return_value=current):
        with patch("app.routers.config_router._write_config_json") as mock_write:
            with patch("app.routers.config_router.sync_config_to_db", new_callable=AsyncMock):
                resp = await client.put("/api/config", json={
                    "notifications": {
                        "enabled": True,
                        "method": "webhook",
                        "webhook_url": bad_url,
                    },
                })
    assert resp.status_code == 400
    assert "webhook_url" in resp.json()["detail"].lower()
    mock_write.assert_not_called()


@pytest.mark.asyncio
async def test_put_config_accepts_legitimate_webhook_url(client):
    """PUT /api/config must accept a legitimate https webhook URL."""
    current = {"projects": [], "notifications": {}}
    with patch("app.routers.config_router._read_config_json", return_value=current):
        with patch("app.routers.config_router._write_config_json") as mock_write:
            with patch("app.routers.config_router.sync_config_to_db", new_callable=AsyncMock):
                resp = await client.put("/api/config", json={
                    "notifications": {
                        "enabled": True,
                        "method": "webhook",
                        "webhook_url": "https://hooks.slack.com/services/T000/B000/abc",
                    },
                })
    assert resp.status_code == 200
    mock_write.assert_called_once()


@pytest.mark.asyncio
async def test_put_config_rejects_bad_target_url(client):
    """PUT /api/config must reject when any targets[*].webhook_url is bad."""
    current = {"projects": [], "notifications": {}}
    bad_payload = {
        "notifications": {
            "enabled": True,
            "method": "webhook",
            "targets": [
                {"webhook_url": "https://hooks.slack.com/services/T000/B000/abc",
                 "webhook_type": "slack"},
                {"webhook_url": "http://169.254.169.254/latest/meta-data/",
                 "webhook_type": "generic"},
            ],
        },
    }
    with patch("app.routers.config_router._read_config_json", return_value=current):
        with patch("app.routers.config_router._write_config_json") as mock_write:
            with patch("app.routers.config_router.sync_config_to_db", new_callable=AsyncMock):
                resp = await client.put("/api/config", json=bad_payload)
    assert resp.status_code == 400
    assert "webhook_url" in resp.json()["detail"].lower()
    mock_write.assert_not_called()


@pytest.mark.asyncio
async def test_put_config_accepts_all_good_targets(client):
    """PUT /api/config must accept multiple good target URLs."""
    current = {"projects": [], "notifications": {}}
    payload = {
        "notifications": {
            "enabled": True,
            "method": "webhook",
            "targets": [
                {"webhook_url": "https://hooks.slack.com/services/T000/B000/abc",
                 "webhook_type": "slack"},
                {"webhook_url": "https://discord.com/api/webhooks/123/abc",
                 "webhook_type": "discord"},
            ],
        },
    }
    with patch("app.routers.config_router._read_config_json", return_value=current):
        with patch("app.routers.config_router._write_config_json") as mock_write:
            with patch("app.routers.config_router.sync_config_to_db", new_callable=AsyncMock):
                resp = await client.put("/api/config", json=payload)
    assert resp.status_code == 200
    mock_write.assert_called_once()


@pytest.mark.asyncio
async def test_put_config_allows_disabled_notifications_with_empty_url(client):
    """PUT /api/config must allow saving notifications with empty/missing webhook_url
    (e.g. user clearing the field while keeping ``enabled=False``)."""
    current = {"projects": [], "notifications": {}}
    with patch("app.routers.config_router._read_config_json", return_value=current):
        with patch("app.routers.config_router._write_config_json") as mock_write:
            with patch("app.routers.config_router.sync_config_to_db", new_callable=AsyncMock):
                resp = await client.put("/api/config", json={
                    "notifications": {"enabled": False, "webhook_url": ""},
                })
    assert resp.status_code == 200
    mock_write.assert_called_once()


# ---------------------------------------------------------------------------
# Defense-in-depth: _send_to_target re-validates before HTTP call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_to_target_blocks_malicious_url_without_http_call():
    """If a malicious URL slipped past write-time validation (e.g. direct file
    edit), ``_send_to_target`` must refuse to make the HTTP request."""
    target = {
        "webhook_url": "http://169.254.169.254/latest/meta-data/",
        "webhook_type": "generic",
    }
    with patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls:
        success, error = await _send_to_target(
            target=target,
            event_type="TEST",
            project="x/y",
            issue_number=1,
            issue_title="t",
            tokens_total=0,
            summary="s",
            run_id="r",
            config={},
        )
    assert success is False
    assert error is not None and "webhook_url" in error.lower()
    # No httpx client was instantiated -- no outbound request was made
    mock_client_cls.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_url", [
    "file:///etc/passwd",
    "http://127.0.0.1/x",
    "http://localhost/x",
])
async def test_send_to_target_blocks_various_malicious_urls(bad_url: str):
    target = {"webhook_url": bad_url, "webhook_type": "generic"}
    with patch("app.services.notifier.httpx.AsyncClient") as mock_client_cls:
        success, error = await _send_to_target(
            target=target,
            event_type="TEST",
            project="x/y",
            issue_number=None,
            issue_title=None,
            tokens_total=None,
            summary=None,
            run_id=None,
            config={},
        )
    assert success is False
    assert error is not None
    mock_client_cls.assert_not_called()
