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
    monkeypatch.setenv("STATION_GITHUB_PAT_PATH", str(tmp_path / "pat.json"))
    monkeypatch.setenv("STATION_GITHUB_OAUTH_PATH", str(tmp_path / "oauth.json"))
    monkeypatch.setenv("STATION_DASHBOARD_BASE_URL", "http://localhost:8420")
    # Reload so module-level constants pick up the env vars.
    import importlib

    from app.services import github_app, github_oauth, github_pat
    importlib.reload(github_app)
    importlib.reload(github_pat)
    importlib.reload(github_oauth)

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
    body = resp.json()
    assert body["state"] == "not_created"
    assert body["pat_set"] is False
    assert body["oauth"] == {"configured": False, "logged_in": False, "username": None}


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


@pytest.fixture
def rsa_keypair():
    """Throwaway RSA key for App-JWT tests in this module."""
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


@pytest.mark.asyncio
async def test_token_endpoint_requires_launcher_token_when_set(client, monkeypatch):
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "secret-launcher")

    resp = await client.get("/api/github/app/token")
    assert resp.status_code == 401

    resp = await client.get(
        "/api/github/app/token",
        headers={"X-Launcher-Token": "wrong"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_token_endpoint_accepts_anonymous_when_token_unset(client, monkeypatch, rsa_keypair):
    """Without STATION_LAUNCHER_TOKEN, the dashboard accepts anonymous calls
    so first-run on bare-metal systemd works without extra config."""
    monkeypatch.delenv("STATION_LAUNCHER_TOKEN", raising=False)
    pem, _ = rsa_keypair
    from app.services import github_app
    github_app.write_credentials({
        "app_id": 1, "slug": "x", "pem": pem, "installation_id": 99,
    })
    github_app._token_cache.clear()

    with respx.mock() as mock:
        mock.post(
            "https://api.github.com/app/installations/99/access_tokens"
        ).respond(201, json={
            "token": "ghs_anon",
            "expires_at": "2099-01-01T00:00:00Z",
        })
        resp = await client.get("/api/github/app/token")

    assert resp.status_code == 200
    assert resp.json()["token"] == "ghs_anon"


@pytest.mark.asyncio
async def test_token_endpoint_returns_404_when_app_not_installed(client):
    """If the App credentials don't include an installation_id, we can't
    mint a token. Surface as 404 so the caller can show a clear message."""
    from app.services import github_app
    github_app.write_credentials({
        "app_id": 1, "slug": "x", "pem": "PEM",
        "installation_id": None,
    })

    resp = await client.get("/api/github/app/token")
    assert resp.status_code == 404


# --- PAT (Personal Access Token) endpoints ---


@pytest.mark.asyncio
async def test_set_pat_persists_and_status_reports_pat_set(client):
    resp = await client.put(
        "/api/github/app/pat",
        json={"token": "ghp_user_pat_value"},
    )
    assert resp.status_code == 200

    from app.services import github_pat
    assert github_pat.read_pat() == "ghp_user_pat_value"

    status = (await client.get("/api/github/app/status")).json()
    assert status["pat_set"] is True


@pytest.mark.asyncio
async def test_set_pat_rejects_empty_value(client):
    resp = await client.put("/api/github/app/pat", json={"token": "   "})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_set_pat_strips_surrounding_whitespace(client):
    resp = await client.put(
        "/api/github/app/pat",
        json={"token": "  ghp_padded  \n"},
    )
    assert resp.status_code == 200

    from app.services import github_pat
    assert github_pat.read_pat() == "ghp_padded"


@pytest.mark.asyncio
async def test_clear_pat_removes_persisted_value(client):
    from app.services import github_pat
    github_pat.write_pat("ghp_to_be_removed")

    resp = await client.delete("/api/github/app/pat")
    assert resp.status_code == 200

    assert github_pat.read_pat() is None
    status = (await client.get("/api/github/app/status")).json()
    assert status["pat_set"] is False


@pytest.mark.asyncio
async def test_status_reports_pat_set_false_when_no_pat(client):
    status = (await client.get("/api/github/app/status")).json()
    assert status["pat_set"] is False


@pytest.mark.asyncio
async def test_token_endpoint_returns_pat_when_set_overrides_app(client, monkeypatch):
    """When a PAT is configured, the /token endpoint returns it regardless of
    App-installation state. Treats explicit PAT as the user's override."""
    monkeypatch.delenv("STATION_LAUNCHER_TOKEN", raising=False)
    from app.services import github_app, github_pat
    github_pat.write_pat("ghp_override")
    # Even if an App is installed, PAT wins
    github_app.write_credentials({
        "app_id": 1, "slug": "x", "pem": "PEM", "installation_id": 99,
    })

    resp = await client.get("/api/github/app/token")
    assert resp.status_code == 200
    body = resp.json()
    assert body["token"] == "ghp_override"
    assert body["source"] == "pat"


@pytest.mark.asyncio
async def test_token_endpoint_falls_through_to_app_when_no_pat(client, monkeypatch, rsa_keypair):
    """Without a PAT but with an installed App, /token mints from GitHub as before."""
    monkeypatch.delenv("STATION_LAUNCHER_TOKEN", raising=False)
    pem, _ = rsa_keypair
    from app.services import github_app
    github_app.write_credentials({
        "app_id": 1, "slug": "x", "pem": pem, "installation_id": 99,
    })
    github_app._token_cache.clear()

    with respx.mock() as mock:
        mock.post(
            "https://api.github.com/app/installations/99/access_tokens"
        ).respond(201, json={
            "token": "ghs_app_minted",
            "expires_at": "2099-01-01T00:00:00Z",
        })
        resp = await client.get("/api/github/app/token")

    assert resp.status_code == 200
    body = resp.json()
    assert body["token"] == "ghs_app_minted"
    assert body["source"] == "app"


@pytest.mark.asyncio
async def test_token_endpoint_404_when_neither_pat_nor_app_installed(client, monkeypatch):
    """No PAT, no App → 404 with a message that mentions both options."""
    monkeypatch.delenv("STATION_LAUNCHER_TOKEN", raising=False)
    resp = await client.get("/api/github/app/token")
    assert resp.status_code == 404
    detail = resp.json()["detail"].lower()
    assert "pat" in detail or "github app" in detail


@pytest.mark.asyncio
async def test_pat_set_requires_no_launcher_token_for_now(client, monkeypatch):
    """The PUT /pat and DELETE /pat endpoints aren't launcher-token gated —
    they're called from the dashboard UI, not the agent. Confirm a launcher
    token mismatch doesn't block them (auth is a separate concern handled
    at a higher layer when the dashboard becomes internet-exposed)."""
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "secret-only-affects-token-endpoint")
    resp = await client.put("/api/github/app/pat", json={"token": "ghp_x"})
    assert resp.status_code == 200


# --- OAuth App login ---


@pytest.mark.asyncio
async def test_set_oauth_config_persists(client):
    resp = await client.put(
        "/api/github/app/oauth/config",
        json={"client_id": "Iv1.test", "client_secret": "shh"},
    )
    assert resp.status_code == 200

    from app.services import github_oauth
    state = github_oauth.read_oauth()
    assert state["client_id"] == "Iv1.test"
    assert state["client_secret"] == "shh"
    assert state.get("access_token") is None


@pytest.mark.asyncio
async def test_set_oauth_config_rejects_empty(client):
    resp = await client.put(
        "/api/github/app/oauth/config",
        json={"client_id": "", "client_secret": "shh"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_set_oauth_config_preserves_existing_token(client):
    """Updating client credentials shouldn't log the user out."""
    from app.services import github_oauth
    github_oauth.write_oauth({
        "client_id": "old", "client_secret": "old-secret",
        "access_token": "gho_existing", "username": "octocat", "scope": "repo",
    })

    resp = await client.put(
        "/api/github/app/oauth/config",
        json={"client_id": "new", "client_secret": "new-secret"},
    )
    assert resp.status_code == 200

    state = github_oauth.read_oauth()
    assert state["client_id"] == "new"
    assert state["access_token"] == "gho_existing"
    assert state["username"] == "octocat"


@pytest.mark.asyncio
async def test_clear_oauth_removes_everything(client):
    from app.services import github_oauth
    github_oauth.write_oauth({
        "client_id": "x", "client_secret": "y",
        "access_token": "gho_z", "username": "u", "scope": "repo",
    })

    resp = await client.delete("/api/github/app/oauth")
    assert resp.status_code == 200
    assert github_oauth.read_oauth() is None


@pytest.mark.asyncio
async def test_oauth_logout_keeps_config(client):
    from app.services import github_oauth
    github_oauth.write_oauth({
        "client_id": "x", "client_secret": "y",
        "access_token": "gho_z", "username": "u", "scope": "repo",
    })

    resp = await client.delete("/api/github/app/oauth/token")
    assert resp.status_code == 200

    after = github_oauth.read_oauth()
    assert after["client_id"] == "x"
    assert after["client_secret"] == "y"
    assert after.get("access_token") is None


@pytest.mark.asyncio
async def test_oauth_login_redirects_to_github_authorize(client):
    from app.services import github_oauth
    github_oauth.write_oauth({"client_id": "Iv1.abc", "client_secret": "shh"})

    resp = await client.get("/api/github/app/oauth/login", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("https://github.com/login/oauth/authorize?")
    assert "client_id=Iv1.abc" in location
    assert "redirect_uri=" in location
    assert "state=" in location
    assert "scope=" in location


@pytest.mark.asyncio
async def test_oauth_login_400_when_not_configured(client):
    resp = await client.get("/api/github/app/oauth/login", follow_redirects=False)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_oauth_callback_rejects_unknown_state(client):
    resp = await client.get(
        "/api/github/app/oauth/callback?code=abc&state=not-issued",
        follow_redirects=False,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_oauth_callback_exchanges_code_persists_token_and_redirects(client):
    from app.services import github_oauth
    github_oauth.write_oauth({"client_id": "Iv1.abc", "client_secret": "shh"})

    # Issue a state via /login (mock follow on github.com URL not needed since we
    # only care about extracting the state param)
    login_resp = await client.get("/api/github/app/oauth/login", follow_redirects=False)
    location = login_resp.headers["location"]
    state = location.split("state=")[1].split("&")[0]

    with respx.mock() as mock:
        mock.post("https://github.com/login/oauth/access_token").respond(
            200,
            json={"access_token": "gho_user_token", "scope": "repo,workflow", "token_type": "bearer"},
        )
        mock.get("https://api.github.com/user").respond(
            200, json={"login": "octocat", "id": 1},
        )
        resp = await client.get(
            f"/api/github/app/oauth/callback?code=CODE&state={state}",
            follow_redirects=False,
        )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/settings?tab=auth"

    state_after = github_oauth.read_oauth()
    assert state_after["access_token"] == "gho_user_token"
    assert state_after["username"] == "octocat"
    assert state_after["scope"] == "repo,workflow"


@pytest.mark.asyncio
async def test_oauth_callback_502_when_github_returns_no_token(client):
    from app.services import github_oauth
    github_oauth.write_oauth({"client_id": "Iv1.abc", "client_secret": "shh"})

    login_resp = await client.get("/api/github/app/oauth/login", follow_redirects=False)
    state = login_resp.headers["location"].split("state=")[1].split("&")[0]

    with respx.mock() as mock:
        # GitHub returns 200 with an error body (e.g. bad_verification_code)
        mock.post("https://github.com/login/oauth/access_token").respond(
            200, json={"error": "bad_verification_code"},
        )
        resp = await client.get(
            f"/api/github/app/oauth/callback?code=BAD&state={state}",
            follow_redirects=False,
        )

    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_status_reflects_oauth_states(client):
    from app.services import github_oauth

    # Configured but not signed in
    github_oauth.write_oauth({"client_id": "Iv1.abc", "client_secret": "shh"})
    body = (await client.get("/api/github/app/status")).json()
    assert body["oauth"]["configured"] is True
    assert body["oauth"]["logged_in"] is False
    assert body["oauth"]["username"] is None

    # Signed in
    github_oauth.write_oauth({
        "client_id": "Iv1.abc", "client_secret": "shh",
        "access_token": "gho_t", "username": "octocat", "scope": "repo",
    })
    body = (await client.get("/api/github/app/status")).json()
    assert body["oauth"]["configured"] is True
    assert body["oauth"]["logged_in"] is True
    assert body["oauth"]["username"] == "octocat"


@pytest.mark.asyncio
async def test_token_endpoint_resolves_oauth_when_no_pat(client, monkeypatch):
    """OAuth token sits between PAT (highest priority) and App (lowest)."""
    monkeypatch.delenv("STATION_LAUNCHER_TOKEN", raising=False)
    from app.services import github_oauth
    github_oauth.write_oauth({
        "client_id": "x", "client_secret": "y",
        "access_token": "gho_resolved", "username": "u", "scope": "repo",
    })

    resp = await client.get("/api/github/app/token")
    assert resp.status_code == 200
    body = resp.json()
    assert body["token"] == "gho_resolved"
    assert body["source"] == "oauth"


@pytest.mark.asyncio
async def test_token_endpoint_pat_beats_oauth(client, monkeypatch):
    """PAT remains highest precedence (explicit override)."""
    monkeypatch.delenv("STATION_LAUNCHER_TOKEN", raising=False)
    from app.services import github_oauth, github_pat
    github_pat.write_pat("ghp_pat_wins")
    github_oauth.write_oauth({
        "client_id": "x", "client_secret": "y",
        "access_token": "gho_loses", "username": "u", "scope": "repo",
    })

    body = (await client.get("/api/github/app/token")).json()
    assert body["token"] == "ghp_pat_wins"
    assert body["source"] == "pat"
