"""Unit tests for the GitHub OAuth App service module."""

from __future__ import annotations

import os

import pytest


def test_oauth_path_defaults_to_home(monkeypatch):
    monkeypatch.delenv("STATION_GITHUB_OAUTH_PATH", raising=False)
    import importlib

    from app.services import github_oauth
    importlib.reload(github_oauth)

    expected = os.path.expanduser("~/.claude-agent-station/github_oauth.json")
    assert str(github_oauth.OAUTH_PATH) == expected


def test_oauth_path_honors_env(monkeypatch, tmp_path):
    custom = tmp_path / "oauth.json"
    monkeypatch.setenv("STATION_GITHUB_OAUTH_PATH", str(custom))
    import importlib

    from app.services import github_oauth
    importlib.reload(github_oauth)

    assert github_oauth.OAUTH_PATH == custom


def test_read_returns_none_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("STATION_GITHUB_OAUTH_PATH", str(tmp_path / "missing.json"))
    import importlib

    from app.services import github_oauth
    importlib.reload(github_oauth)

    assert github_oauth.read_oauth() is None


def test_write_then_read_round_trips_with_mode_0600(monkeypatch, tmp_path):
    target = tmp_path / "oauth.json"
    monkeypatch.setenv("STATION_GITHUB_OAUTH_PATH", str(target))
    import importlib

    from app.services import github_oauth
    importlib.reload(github_oauth)

    payload = {
        "client_id": "Iv1.abc",
        "client_secret": "secret",
        "access_token": "gho_xyz",
        "username": "octocat",
        "scope": "repo workflow",
    }
    github_oauth.write_oauth(payload)

    assert target.exists()
    assert (target.stat().st_mode & 0o777) == 0o600
    assert github_oauth.read_oauth() == payload


def test_delete_removes_file_and_is_idempotent(monkeypatch, tmp_path):
    target = tmp_path / "oauth.json"
    monkeypatch.setenv("STATION_GITHUB_OAUTH_PATH", str(target))
    import importlib

    from app.services import github_oauth
    importlib.reload(github_oauth)

    github_oauth.write_oauth({"client_id": "x", "client_secret": "y"})
    assert target.exists()
    github_oauth.delete_oauth()
    assert not target.exists()
    github_oauth.delete_oauth()  # idempotent


def test_clear_token_keeps_config_drops_session(monkeypatch, tmp_path):
    """clear_token() removes access_token + username + scope but preserves
    client_id + client_secret so the user can re-login without re-entering
    OAuth App credentials."""
    target = tmp_path / "oauth.json"
    monkeypatch.setenv("STATION_GITHUB_OAUTH_PATH", str(target))
    import importlib

    from app.services import github_oauth
    importlib.reload(github_oauth)

    github_oauth.write_oauth({
        "client_id": "Iv1.abc",
        "client_secret": "secret",
        "access_token": "gho_xyz",
        "username": "octocat",
        "scope": "repo workflow",
    })
    github_oauth.clear_token()

    after = github_oauth.read_oauth()
    assert after["client_id"] == "Iv1.abc"
    assert after["client_secret"] == "secret"
    assert after.get("access_token") is None
    assert after.get("username") is None
    assert after.get("scope") is None


def test_clear_token_noop_when_no_config(monkeypatch, tmp_path):
    """clear_token() on an unconfigured OAuth state is a no-op (no exception)."""
    monkeypatch.setenv("STATION_GITHUB_OAUTH_PATH", str(tmp_path / "missing.json"))
    import importlib

    from app.services import github_oauth
    importlib.reload(github_oauth)

    github_oauth.clear_token()  # should not raise
    assert github_oauth.read_oauth() is None
