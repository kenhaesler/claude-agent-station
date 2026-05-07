"""HTTP surface for GitHub auth: App lifecycle + PAT fallback.

Token resolution at ``/token``: PAT > App > 404.

Endpoints:
  POST   /api/github/app/manifest/start    — issue a fresh state token, return manifest
  GET    /api/github/app/manifest/exchange — receive code from GitHub, persist credentials
  GET    /api/github/app/install/callback  — receive installation_id, persist
  GET    /api/github/app/status            — App state + pat_set
  DELETE /api/github/app                   — clear App credentials (App stays in GitHub)
  GET    /api/github/app/token             — return a usable token (resolution above)
  GET    /api/github/app/repos             — list repos accessible to the configured auth
  PUT    /api/github/app/pat               — store a Personal Access Token
  DELETE /api/github/app/pat               — clear the stored PAT
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import github_app, github_pat

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/github/app", tags=["github-app"])


def _dashboard_base_url() -> str:
    """Public URL the operator's browser uses to reach the dashboard.

    Set via ``STATION_DASHBOARD_BASE_URL``; defaults to ``http://localhost:8420``.
    Compose deployments and remote installs override this so GitHub's
    redirect-after-create lands on the right host.
    """
    return os.environ.get("STATION_DASHBOARD_BASE_URL", "http://localhost:8420").rstrip("/")


def _build_manifest() -> dict:
    base = _dashboard_base_url()
    return {
        "name": "Claude Agent Station",
        "url": base,
        "description": "Self-hosted autonomous Claude Code agent. Acts on issues and pull requests in installed repos.",
        "redirect_url": f"{base}/api/github/app/manifest/exchange",
        "callback_urls": [],
        "setup_url": f"{base}/api/github/app/install/callback",
        "setup_on_update": False,
        "public": False,
        "request_oauth_on_install": False,
        # hook_attributes intentionally omitted — GitHub validates the URL
        # against its public-Internet checklist even when active=false, so
        # any localhost or private-IP base URL would block App creation.
        # Webhooks aren't used in the MVP anyway; if a future task wants
        # them, set hook_attributes here behind a public-URL guard.
        "default_events": [],
        "default_permissions": {
            "contents": "write",
            "issues": "write",
            "pull_requests": "write",
            "metadata": "read",
            "workflows": "write",
            "actions": "read",
            "checks": "read",
        },
    }


# In-memory state store: {state_token: created_at_epoch}. Cleared on
# exchange. 15-minute TTL. Single-process; that's fine because the
# manifest flow happens in a single browser window within a few minutes.
_pending_states: dict[str, float] = {}
_STATE_TTL_SECONDS = 900


def _issue_state() -> str:
    """Generate a fresh state token, prune expired entries.

    Uses ``.pop(s, None)`` for the cleanup so two concurrent issuers
    can't race on the same expired key (``del`` would raise ``KeyError``
    on the second writer).
    """
    now = time.time()
    expired = [s for s, created in list(_pending_states.items()) if now - created > _STATE_TTL_SECONDS]
    for s in expired:
        _pending_states.pop(s, None)
    state = secrets.token_urlsafe(32)
    _pending_states[state] = now
    return state


def _consume_state(state: str) -> bool:
    """Return True if the state is valid; remove it from the pending set."""
    return _pending_states.pop(state, None) is not None


@router.post("/manifest/start")
async def manifest_start() -> dict[str, Any]:
    """Issue a state token and the manifest payload the UI POSTs to GitHub."""
    state = _issue_state()
    return {
        "state": state,
        "post_url": f"https://github.com/settings/apps/new?state={state}",
        "manifest": _build_manifest(),
    }


import httpx
from fastapi.responses import RedirectResponse


@router.get("/manifest/exchange")
async def manifest_exchange(code: str, state: str) -> RedirectResponse:
    """Exchange the manifest code for App credentials, persist them, and
    redirect the operator to GitHub's "install on repos" page.

    GitHub redirects here after the operator clicks "Create GitHub App"
    on the form we POSTed in the previous step. The ``code`` is one-time
    use and short-lived (10 minutes per GitHub docs).
    """
    if not _consume_state(state):
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired state token — start the flow again.",
        )

    url = f"https://api.github.com/app-manifests/{code}/conversions"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub unreachable: {exc}") from exc

    if resp.status_code != 201:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"GitHub manifest exchange failed: {resp.text[:300]}",
        )

    data = resp.json()
    creds = {
        "app_id": data["id"],
        "slug": data["slug"],
        "name": data["name"],
        "owner": data.get("owner", {}).get("login"),
        "client_id": data["client_id"],
        "client_secret": data["client_secret"],
        "webhook_secret": data["webhook_secret"],
        "pem": data["pem"],
        "html_url": data["html_url"],
        # installation_id is filled in by the install callback later
        "installation_id": None,
    }
    github_app.write_credentials(creds)
    logger.info("GitHub App created: %s (id=%s, owner=%s)", creds["slug"], creds["app_id"], creds["owner"])

    install_url = f"https://github.com/apps/{creds['slug']}/installations/new"
    return RedirectResponse(install_url, status_code=302)


@router.get("/install/callback")
async def install_callback(installation_id: int, setup_action: str = "install") -> RedirectResponse:
    """GitHub redirects here after the operator picks repos and clicks Install.

    We look up the App credentials persisted during manifest exchange and
    store the new installation_id on top, then send the operator back to
    the dashboard's Settings page.

    SECURITY NOTE: this endpoint trusts the ``installation_id`` query
    parameter — anyone reaching it can write a value into our credentials
    store. The risk is low because the dashboard isn't typically
    internet-exposed (compose binds 8420 to localhost; bare-metal puts it
    behind firewalld). A hardened follow-up would fetch
    ``/app/installations/{id}`` with the App JWT and verify
    ``account.login`` matches the stored ``owner``. Tracked as a
    follow-up below.
    """
    creds = github_app.read_credentials()
    if not creds:
        raise HTTPException(
            status_code=400,
            detail="No App credentials found — finish the manifest creation step first.",
        )
    creds["installation_id"] = installation_id
    github_app.write_credentials(creds)
    # Invalidate any stale token from a previous installation
    github_app._token_cache.pop(installation_id, None)
    logger.info("GitHub App installed: installation_id=%s setup_action=%s", installation_id, setup_action)

    # Redirect back to the dashboard settings page
    return RedirectResponse("/settings?tab=auth", status_code=302)


@router.get("/status")
async def status() -> dict[str, Any]:
    """Three-state App machine plus a flag for whether a PAT is configured.

    The PAT and App are independent: ``pat_set`` reflects only the PAT
    file; the ``state`` field still describes the App lifecycle. The
    ``/token`` endpoint resolves precedence (PAT wins when both are set).
    """
    pat_set = github_pat.read_pat() is not None
    creds = github_app.read_credentials()
    if not creds:
        return {"state": "not_created", "pat_set": pat_set}
    if not creds.get("installation_id"):
        return {
            "state": "created_not_installed",
            "slug": creds["slug"],
            "name": creds.get("name"),
            "owner": creds.get("owner"),
            "html_url": creds.get("html_url"),
            "pat_set": pat_set,
        }
    return {
        "state": "installed",
        "slug": creds["slug"],
        "name": creds.get("name"),
        "owner": creds.get("owner"),
        "installation_id": creds["installation_id"],
        "html_url": creds.get("html_url"),
        "pat_set": pat_set,
    }


@router.delete("")
async def disconnect() -> dict[str, str]:
    """Clear local App credentials.

    The App and its installation continue to exist on GitHub — uninstall
    those manually at https://github.com/settings/installations if desired.
    """
    github_app.delete_credentials()
    github_app._token_cache.clear()
    return {"status": "disconnected"}


from fastapi import Header


@router.get("/token")
async def token(x_launcher_token: str | None = Header(default=None)) -> dict[str, str]:
    """Return a usable GitHub auth secret.

    Resolution order:
      1. PAT (if the user explicitly configured one — counts as override)
      2. App installation token (if the App is installed)
      3. 404

    Token-gated when ``STATION_LAUNCHER_TOKEN`` is set so only the agent's
    launcher (which already has the same shared secret) can fetch it. The
    response includes a ``source`` field so the caller can log/debug
    which path produced the token.
    """
    expected = os.environ.get("STATION_LAUNCHER_TOKEN", "")
    if expected and x_launcher_token != expected:
        raise HTTPException(status_code=401, detail="invalid or missing launcher token")

    pat = github_pat.read_pat()
    if pat:
        return {"token": pat, "source": "pat"}

    creds = github_app.read_credentials()
    if not creds or not creds.get("installation_id"):
        raise HTTPException(
            status_code=404,
            detail="No GitHub auth configured — set a PAT or install the GitHub App at /settings",
        )

    tok = await github_app.get_installation_token()
    if not tok:
        raise HTTPException(status_code=502, detail="Failed to obtain installation credential")
    return {"token": tok, "source": "app"}


class _PATBody(BaseModel):
    token: str


@router.put("/pat")
async def set_pat(body: _PATBody) -> dict[str, str]:
    """Save a Personal Access Token. Used as override/fallback for the App."""
    cleaned = body.token.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="PAT cannot be empty")
    github_pat.write_pat(cleaned)
    logger.info("GitHub PAT saved (length=%s)", len(cleaned))
    return {"status": "saved"}


@router.delete("/pat")
async def clear_pat() -> dict[str, str]:
    """Remove the persisted PAT. Idempotent."""
    github_pat.delete_pat()
    return {"status": "cleared"}


@router.get("/repos")
async def list_repos() -> dict[str, list[dict[str, Any]]]:
    """List repositories the configured GitHub auth can access.

    PAT path → ``GET /user/repos`` (returns the user's own repos as a list).
    App path → ``GET /installation/repositories`` with an installation token
    (returns ``{"repositories": [...]}``, only repos the App is installed on).

    Returns ``{"repos": []}`` when nothing is configured or GitHub returns
    an error, so the UI can degrade gracefully back to manual repo entry.
    """
    pat = github_pat.read_pat()
    if pat:
        token = pat
        url = "https://api.github.com/user/repos?per_page=100&sort=updated"
        wrapper_key: str | None = None
    else:
        creds = github_app.read_credentials()
        if not creds or not creds.get("installation_id"):
            return {"repos": []}
        token = await github_app.get_installation_token()
        if not token:
            return {"repos": []}
        url = "https://api.github.com/installation/repositories?per_page=100"
        wrapper_key = "repositories"

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("GitHub repos fetch failed: %s", exc)
        return {"repos": []}
    if resp.status_code != 200:
        logger.warning("GitHub repos returned %s: %s", resp.status_code, resp.text[:200])
        return {"repos": []}
    try:
        data = resp.json()
        raw = data.get(wrapper_key, []) if wrapper_key else data
    except (ValueError, AttributeError) as exc:
        logger.warning("GitHub repos response unparseable: %s", exc)
        return {"repos": []}

    return {
        "repos": [
            {
                "full_name": r["full_name"],
                "private": r.get("private", False),
                "html_url": r.get("html_url", f"https://github.com/{r['full_name']}"),
                "default_branch": r.get("default_branch", "main"),
            }
            for r in raw
            if "full_name" in r
        ],
    }
