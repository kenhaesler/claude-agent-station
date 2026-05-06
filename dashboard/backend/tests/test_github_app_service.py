"""Unit tests for the GitHub App service module."""

from __future__ import annotations

import json
import os

import pytest


def test_credentials_path_defaults_to_home(monkeypatch):
    monkeypatch.delenv("STATION_GITHUB_APP_CREDENTIALS_PATH", raising=False)
    import importlib

    from app.services import github_app
    importlib.reload(github_app)

    expected = os.path.expanduser("~/.claude-agent-station/github_app.json")
    assert str(github_app.CREDENTIALS_PATH) == expected


def test_credentials_path_honors_env(monkeypatch, tmp_path):
    custom = tmp_path / "creds.json"
    monkeypatch.setenv("STATION_GITHUB_APP_CREDENTIALS_PATH", str(custom))
    import importlib

    from app.services import github_app
    importlib.reload(github_app)

    assert github_app.CREDENTIALS_PATH == custom


def test_read_returns_none_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("STATION_GITHUB_APP_CREDENTIALS_PATH", str(tmp_path / "missing.json"))
    import importlib

    from app.services import github_app
    importlib.reload(github_app)

    assert github_app.read_credentials() is None


def test_write_then_read_round_trips(monkeypatch, tmp_path):
    target = tmp_path / "creds.json"
    monkeypatch.setenv("STATION_GITHUB_APP_CREDENTIALS_PATH", str(target))
    import importlib

    from app.services import github_app
    importlib.reload(github_app)

    payload = {"app_id": 42, "slug": "test-app", "pem": "PEM-DATA"}
    github_app.write_credentials(payload)

    assert target.exists()
    assert (target.stat().st_mode & 0o777) == 0o600
    assert github_app.read_credentials() == payload


def test_delete_removes_file(monkeypatch, tmp_path):
    target = tmp_path / "creds.json"
    monkeypatch.setenv("STATION_GITHUB_APP_CREDENTIALS_PATH", str(target))
    import importlib

    from app.services import github_app
    importlib.reload(github_app)

    github_app.write_credentials({"app_id": 1})
    assert target.exists()
    github_app.delete_credentials()
    assert not target.exists()
    # Idempotent
    github_app.delete_credentials()


import time

import jwt as pyjwt


@pytest.fixture
def rsa_keypair():
    """Generate a throwaway RSA key for JWT signing tests."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return pem, public_pem


def test_make_jwt_signs_with_app_credentials(rsa_keypair):
    pem, public_pem = rsa_keypair
    from app.services import github_app

    token = github_app.make_jwt(app_id=12345, private_key_pem=pem)

    decoded = pyjwt.decode(token, public_pem, algorithms=["RS256"])
    # PyJWT >= 2.9 enforces iss as string (RFC 7519); GitHub accepts "12345".
    assert decoded["iss"] == "12345"
    # GitHub requires iat and exp; iat <= now, exp = iat + 600s max.
    now = int(time.time())
    assert decoded["iat"] <= now
    assert decoded["exp"] > now
    assert decoded["exp"] - decoded["iat"] <= 600


def test_make_jwt_iat_clock_skew_buffer(rsa_keypair):
    """GitHub rejects JWTs with iat in the future. Subtract 60s from iat
    to absorb minor clock drift between the dashboard and GitHub."""
    pem, public_pem = rsa_keypair
    from app.services import github_app

    token = github_app.make_jwt(app_id=1, private_key_pem=pem)
    decoded = pyjwt.decode(token, public_pem, algorithms=["RS256"])
    now = int(time.time())
    assert decoded["iat"] <= now - 30  # buffered behind real now


import respx


@pytest.mark.asyncio
async def test_mint_installation_token_calls_github(rsa_keypair, monkeypatch, tmp_path):
    pem, _ = rsa_keypair
    monkeypatch.setenv("STATION_GITHUB_APP_CREDENTIALS_PATH", str(tmp_path / "creds.json"))
    import importlib

    from app.services import github_app
    importlib.reload(github_app)

    github_app.write_credentials({
        "app_id": 12345,
        "slug": "test-app",
        "pem": pem,
        "installation_id": 67890,
    })
    github_app._token_cache.clear()

    with respx.mock() as mock:
        route = mock.post(
            "https://api.github.com/app/installations/67890/access_tokens"
        ).respond(
            201,
            json={
                "token": "ghs_abc123",
                "expires_at": "2026-05-06T23:00:00Z",
                "permissions": {"contents": "write"},
            },
        )
        token = await github_app.get_installation_token()

    assert token == "ghs_abc123"
    # JWT should appear in Authorization header on the request to GitHub
    assert route.calls[0].request.headers["Authorization"].startswith("Bearer eyJ")


@pytest.mark.asyncio
async def test_get_installation_token_returns_none_when_not_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("STATION_GITHUB_APP_CREDENTIALS_PATH", str(tmp_path / "missing.json"))
    import importlib

    from app.services import github_app
    importlib.reload(github_app)
    github_app._token_cache.clear()

    assert await github_app.get_installation_token() is None


@pytest.mark.asyncio
async def test_get_installation_token_caches_until_near_expiry(rsa_keypair, monkeypatch, tmp_path):
    """Two consecutive calls should hit GitHub once. Cache invalidates when
    less than 5 minutes remain on the token."""
    pem, _ = rsa_keypair
    monkeypatch.setenv("STATION_GITHUB_APP_CREDENTIALS_PATH", str(tmp_path / "creds.json"))
    import importlib

    from app.services import github_app
    importlib.reload(github_app)

    github_app.write_credentials({
        "app_id": 1, "slug": "x", "pem": pem, "installation_id": 99,
    })
    github_app._token_cache.clear()

    # Token "expires" in 1 hour
    future = time.gmtime(time.time() + 3600)
    expires_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", future)

    with respx.mock() as mock:
        route = mock.post(
            "https://api.github.com/app/installations/99/access_tokens"
        ).respond(201, json={"token": "ghs_first", "expires_at": expires_iso})

        first = await github_app.get_installation_token()
        second = await github_app.get_installation_token()

    assert first == "ghs_first"
    assert second == "ghs_first"
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_token_refreshes_when_close_to_expiry(rsa_keypair, monkeypatch, tmp_path):
    pem, _ = rsa_keypair
    monkeypatch.setenv("STATION_GITHUB_APP_CREDENTIALS_PATH", str(tmp_path / "creds.json"))
    import importlib

    from app.services import github_app
    importlib.reload(github_app)

    github_app.write_credentials({
        "app_id": 1, "slug": "x", "pem": pem, "installation_id": 99,
    })
    github_app._token_cache.clear()

    # Token "expires" in 60 seconds (well under the 5-minute refresh threshold)
    soon = time.gmtime(time.time() + 60)
    expires_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", soon)
    later = time.gmtime(time.time() + 3600)
    later_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", later)

    with respx.mock() as mock:
        route = mock.post(
            "https://api.github.com/app/installations/99/access_tokens"
        ).respond(201, json={"token": "ghs_first", "expires_at": expires_iso})

        first = await github_app.get_installation_token()

    with respx.mock() as mock:
        route2 = mock.post(
            "https://api.github.com/app/installations/99/access_tokens"
        ).respond(201, json={"token": "ghs_second", "expires_at": later_iso})

        second = await github_app.get_installation_token()

    assert first == "ghs_first"
    assert second == "ghs_second"
    assert route2.call_count == 1
