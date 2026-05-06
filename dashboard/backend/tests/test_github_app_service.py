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
