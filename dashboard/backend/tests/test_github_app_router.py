"""Endpoint tests for the GitHub App router."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, engine


@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(setup_db, monkeypatch, tmp_path):
    monkeypatch.setenv("STATION_GITHUB_APP_CREDENTIALS_PATH", str(tmp_path / "creds.json"))
    monkeypatch.setenv("STATION_DASHBOARD_BASE_URL", "http://localhost:8420")
    # Reload so module-level constants pick up the env vars.
    import importlib

    from app.services import github_app
    importlib.reload(github_app)

    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_manifest_start_returns_post_url_state_and_manifest(client):
    resp = await client.post("/api/github/app/manifest/start")
    assert resp.status_code == 200
    body = resp.json()

    assert body["post_url"].startswith("https://github.com/settings/apps/new")
    assert "state=" in body["post_url"]
    assert isinstance(body["state"], str) and len(body["state"]) >= 32
    assert "manifest" in body
    m = body["manifest"]
    assert m["name"]
    assert m["url"] == "http://localhost:8420"
    assert m["redirect_url"] == "http://localhost:8420/api/github/app/manifest/exchange"
    assert m["setup_url"] == "http://localhost:8420/api/github/app/install/callback"
    assert m["public"] is False
    assert m["default_permissions"]["contents"] == "write"
    assert m["default_permissions"]["issues"] == "write"
    assert m["default_permissions"]["pull_requests"] == "write"
    assert m["default_permissions"]["metadata"] == "read"


@pytest.mark.asyncio
async def test_manifest_start_state_is_random_per_call(client):
    a = (await client.post("/api/github/app/manifest/start")).json()["state"]
    b = (await client.post("/api/github/app/manifest/start")).json()["state"]
    assert a != b


import respx
from fastapi.responses import RedirectResponse


@pytest.mark.asyncio
async def test_exchange_rejects_unknown_state(client):
    resp = await client.get("/api/github/app/manifest/exchange?code=abc&state=not-issued")
    assert resp.status_code == 400
    assert "state" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_exchange_persists_credentials_and_redirects_to_install(client, tmp_path):
    # Issue a real state via the start endpoint
    start_resp = await client.post("/api/github/app/manifest/start")
    state = start_resp.json()["state"]

    fake_app = {
        "id": 12345,
        "slug": "claude-agent-station-laboef1900",
        "name": "Claude Agent Station",
        "owner": {"login": "laboef1900"},
        "client_id": "Iv1.testclient",
        "client_secret": "secret-shh",
        "webhook_secret": "webhook-shh",
        "pem": "-----BEGIN PRIVATE KEY-----\nFAKE\n-----END PRIVATE KEY-----\n",
        "html_url": "https://github.com/apps/claude-agent-station-laboef1900",
    }

    with respx.mock() as mock:
        mock.post("https://api.github.com/app-manifests/CODE123/conversions").respond(
            201, json=fake_app,
        )
        resp = await client.get(
            f"/api/github/app/manifest/exchange?code=CODE123&state={state}",
            follow_redirects=False,
        )

    assert resp.status_code == 302
    # Redirect points the operator at the installation page on github.com
    assert resp.headers["location"].startswith(
        "https://github.com/apps/claude-agent-station-laboef1900/installations/new"
    )

    # Credentials were persisted
    from app.services import github_app
    creds = github_app.read_credentials()
    assert creds["app_id"] == 12345
    assert creds["slug"] == "claude-agent-station-laboef1900"
    assert creds["owner"] == "laboef1900"
    assert creds["pem"].startswith("-----BEGIN PRIVATE KEY-----")


@pytest.mark.asyncio
async def test_exchange_returns_state_used_only_once(client):
    start_resp = await client.post("/api/github/app/manifest/start")
    state = start_resp.json()["state"]

    fake_app = {
        "id": 1, "slug": "x", "name": "x", "owner": {"login": "u"},
        "client_id": "c", "client_secret": "s", "webhook_secret": "w",
        "pem": "PEM", "html_url": "https://github.com/apps/x",
    }
    with respx.mock() as mock:
        mock.post("https://api.github.com/app-manifests/CODE/conversions").respond(201, json=fake_app)
        first = await client.get(
            f"/api/github/app/manifest/exchange?code=CODE&state={state}",
            follow_redirects=False,
        )
    assert first.status_code == 302

    # Replay with the same state — should be rejected
    second = await client.get(
        f"/api/github/app/manifest/exchange?code=CODE&state={state}",
        follow_redirects=False,
    )
    assert second.status_code == 400


@pytest.mark.asyncio
async def test_install_callback_stores_installation_id(client):
    # Seed an existing App-creation result (no installation_id yet)
    from app.services import github_app
    github_app.write_credentials({
        "app_id": 1, "slug": "x", "name": "x", "owner": "u",
        "client_id": "c", "client_secret": "s", "webhook_secret": "w",
        "pem": "PEM", "html_url": "https://github.com/apps/x",
        "installation_id": None,
    })

    resp = await client.get(
        "/api/github/app/install/callback?installation_id=99999&setup_action=install",
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/settings?tab=auth"

    creds = github_app.read_credentials()
    assert creds["installation_id"] == 99999


@pytest.mark.asyncio
async def test_install_callback_rejects_when_no_app_credentials(client):
    """If a malicious user calls the callback before manifest exchange has
    run, we shouldn't accept their installation_id (the installation isn't
    bound to *our* App)."""
    resp = await client.get(
        "/api/github/app/install/callback?installation_id=1&setup_action=install",
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "no app" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_status_not_created(client):
    resp = await client.get("/api/github/app/status")
    assert resp.status_code == 200
    assert resp.json() == {"state": "not_created"}


@pytest.mark.asyncio
async def test_status_created_not_installed(client):
    from app.services import github_app
    github_app.write_credentials({
        "app_id": 1, "slug": "test-app", "name": "Test", "owner": "u",
        "client_id": "c", "client_secret": "s", "webhook_secret": "w",
        "pem": "PEM", "html_url": "https://github.com/apps/test-app",
        "installation_id": None,
    })

    resp = await client.get("/api/github/app/status")
    body = resp.json()
    assert body["state"] == "created_not_installed"
    assert body["slug"] == "test-app"
    assert body["owner"] == "u"
    assert body["html_url"] == "https://github.com/apps/test-app"


@pytest.mark.asyncio
async def test_status_installed(client):
    from app.services import github_app
    github_app.write_credentials({
        "app_id": 1, "slug": "test-app", "name": "Test", "owner": "u",
        "client_id": "c", "client_secret": "s", "webhook_secret": "w",
        "pem": "PEM", "html_url": "https://github.com/apps/test-app",
        "installation_id": 999,
    })

    resp = await client.get("/api/github/app/status")
    body = resp.json()
    assert body["state"] == "installed"
    assert body["installation_id"] == 999


@pytest.mark.asyncio
async def test_disconnect_clears_credentials(client):
    from app.services import github_app
    github_app.write_credentials({"app_id": 1, "slug": "x"})

    resp = await client.delete("/api/github/app")
    assert resp.status_code == 200
    assert github_app.read_credentials() is None
