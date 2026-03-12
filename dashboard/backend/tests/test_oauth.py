"""Tests for OAuth PKCE state TTL and cleanup."""

import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.oauth import _pending, _cleanup_expired_states, STATE_TTL_SECONDS

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_pending():
    """Clear the pending store before each test."""
    _pending.clear()
    yield
    _pending.clear()


class TestStartOAuth:
    def test_start_creates_pending_entry_with_ttl(self):
        resp = client.post("/api/oauth/start")
        assert resp.status_code == 200
        data = resp.json()
        state = data["state"]
        assert state in _pending
        verifier, expires_at = _pending[state]
        assert isinstance(verifier, str)
        assert len(verifier) > 0
        # Should expire roughly STATE_TTL_SECONDS from now
        assert expires_at > time.time()
        assert expires_at <= time.time() + STATE_TTL_SECONDS + 1

    def test_start_triggers_cleanup_of_expired_states(self):
        # Insert an already-expired state
        _pending["old-state"] = ("old-verifier", time.time() - 100)
        assert "old-state" in _pending

        resp = client.post("/api/oauth/start")
        assert resp.status_code == 200

        # Expired state should have been cleaned up
        assert "old-state" not in _pending


class TestOAuthCallback:
    def test_callback_rejects_unknown_state(self):
        resp = client.post("/api/oauth/callback", json={
            "code": "test-code",
            "state": "nonexistent-state",
        })
        assert resp.status_code == 400
        assert "Invalid or expired" in resp.json()["detail"]

    def test_callback_rejects_expired_state(self):
        # Insert a state that expired 1 second ago
        _pending["expired-state"] = ("verifier123", time.time() - 1)

        resp = client.post("/api/oauth/callback", json={
            "code": "test-code",
            "state": "expired-state",
        })
        assert resp.status_code == 400
        assert "expired" in resp.json()["detail"].lower()
        # State should be removed from pending after rejection
        assert "expired-state" not in _pending

    @patch("app.routers.oauth.urlopen")
    @patch("app.routers.oauth._write_credentials")
    def test_callback_accepts_valid_state(self, mock_write, mock_urlopen):
        """Valid (non-expired) state should proceed to token exchange."""
        _pending["valid-state"] = ("verifier456", time.time() + 300)

        # Mock the token exchange response
        import io
        import json
        mock_resp = io.BytesIO(json.dumps({
            "access_token": "test-token",
            "refresh_token": "test-refresh",
            "expires_in": 3600,
        }).encode())
        mock_resp.status = 200
        mock_urlopen.return_value.__enter__ = lambda s: mock_resp
        mock_urlopen.return_value.__exit__ = lambda s, *a: None

        resp = client.post("/api/oauth/callback", json={
            "code": "test-code",
            "state": "valid-state",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        # State should be consumed
        assert "valid-state" not in _pending


class TestCleanupExpiredStates:
    def test_removes_only_expired_entries(self):
        now = time.time()
        _pending["expired1"] = ("v1", now - 100)
        _pending["expired2"] = ("v2", now - 1)
        _pending["valid1"] = ("v3", now + 300)
        _pending["valid2"] = ("v4", now + 600)

        _cleanup_expired_states()

        assert "expired1" not in _pending
        assert "expired2" not in _pending
        assert "valid1" in _pending
        assert "valid2" in _pending

    def test_handles_empty_pending(self):
        _cleanup_expired_states()
        assert len(_pending) == 0
