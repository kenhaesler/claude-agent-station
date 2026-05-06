"""STATION_SERVICE_USER lets compose deployments override the hard-coded
'claude-agent' user name. Without it, shutil.chown raises LookupError
which is suppressed — but then the chown is silently a no-op."""

from __future__ import annotations

from unittest.mock import patch


def test_write_token_uses_station_service_user_env(monkeypatch, tmp_path):
    monkeypatch.setenv("STATION_SERVICE_USER", "myappuser")
    from app.routers import github_oauth

    target = tmp_path / "github_token"

    with patch("shutil.chown") as mock_chown:
        github_oauth._write_token(target, {"access_token": "x"})

    mock_chown.assert_called_once()
    _, kwargs = mock_chown.call_args
    assert kwargs.get("user") == "myappuser"


def test_write_token_defaults_to_claude_agent(monkeypatch, tmp_path):
    monkeypatch.delenv("STATION_SERVICE_USER", raising=False)
    from app.routers import github_oauth

    target = tmp_path / "github_token"

    with patch("shutil.chown") as mock_chown:
        github_oauth._write_token(target, {"access_token": "x"})

    _, kwargs = mock_chown.call_args
    assert kwargs.get("user") == "claude-agent"


def test_write_token_swallows_lookup_error_when_user_missing(tmp_path):
    """In containers neither claude-agent nor STATION_SERVICE_USER may exist
    — the chmod 600 must still happen so the token isn't world-readable."""
    from app.routers import github_oauth

    target = tmp_path / "github_token"

    def _raise_lookup(*args, **kwargs):
        raise LookupError("no such user")

    with patch("shutil.chown", side_effect=_raise_lookup):
        github_oauth._write_token(target, {"access_token": "x"})

    assert target.exists()
    # 0o600 = 384
    assert (target.stat().st_mode & 0o777) == 0o600
