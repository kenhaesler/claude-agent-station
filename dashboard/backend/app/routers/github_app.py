"""HTTP surface for the GitHub App lifecycle.

Endpoints:
  POST /api/github/app/manifest/start    — issue a fresh state token, return manifest
  GET  /api/github/app/manifest/exchange — receive code from GitHub, persist credentials
  GET  /api/github/app/install/callback  — receive installation_id, persist
  GET  /api/github/app/status            — return state (not_created / created_not_installed / installed)
  DELETE /api/github/app                 — clear local credentials (App stays in GitHub)
  GET  /api/github/app/token             — mint a fresh installation token (token-gated)
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from typing import Any

from fastapi import APIRouter, HTTPException

from app.services import github_app

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
        "hook_attributes": {
            # Webhooks deliberately disabled in the MVP — installation events
            # are captured via the setup_url redirect instead.
            "url": f"{base}/api/github/app/webhook",
            "active": False,
        },
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
