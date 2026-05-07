"""GitHub Contents API wrapper for vision document storage.

The dashboard reads/writes docs/vision.md via this module using the App
installation token already wired up in app.services.github_app.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

import httpx
from fastapi import HTTPException

from app.services.github_app import get_installation_token

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
COMMIT_AUTHOR = {"name": "Claude Station", "email": "claude-station@noreply.local"}


class FileNotFound(Exception):
    """Raised when the file does not exist on the requested ref."""


class StaleSha(Exception):
    """Raised on PUT when the supplied sha doesn't match GitHub's current sha."""

    def __init__(self, current_sha: str, current_body: str):
        self.current_sha = current_sha
        self.current_body = current_body
        super().__init__(f"stale sha; current is {current_sha}")


@dataclass
class ContentsResult:
    sha: str
    body: str
    html_url: str


async def _get_token() -> str:
    token = await get_installation_token()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="GitHub App not installed; cannot access repo contents.",
        )
    return token


async def read_file(repo: str, path: str, branch: str) -> ContentsResult:
    """Fetch a file's content + sha from a branch.

    Raises FileNotFound on 404, HTTPException on other errors.
    """
    token = await _get_token()
    url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    params = {"ref": branch}

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=headers, params=params)

    if resp.status_code == 404:
        raise FileNotFound(f"{repo}:{branch}:{path}")
    if resp.status_code >= 400:
        logger.warning("GitHub Contents read failed: %s %s", resp.status_code, resp.text[:200])
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    payload = resp.json()
    if payload.get("encoding") != "base64":
        raise HTTPException(status_code=500, detail="unexpected encoding from GitHub")
    body = base64.b64decode(payload["content"]).decode("utf-8", errors="replace")
    return ContentsResult(sha=payload["sha"], body=body, html_url=payload.get("html_url", ""))
