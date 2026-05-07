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


async def write_file(
    repo: str,
    path: str,
    branch: str,
    body: str,
    message: str,
    current_sha: str | None,
) -> str:
    """PUT a file to a branch.

    Pass current_sha=None for first-create. Pass the previously-fetched
    sha to update; on conflict, re-fetches the live state and raises
    StaleSha so callers can surface a 409 envelope.

    Returns the new blob sha on success.
    """
    token = await _get_token()
    url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {
        "message": message,
        "content": base64.b64encode(body.encode("utf-8")).decode("ascii"),
        "branch": branch,
        "committer": COMMIT_AUTHOR,
    }
    if current_sha is not None:
        payload["sha"] = current_sha

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.put(url, headers=headers, json=payload)

    if resp.status_code in (200, 201):
        return resp.json()["content"]["sha"]

    if resp.status_code in (409, 422):
        # GitHub returns 409 Conflict or 422 Unprocessable Entity for sha mismatch.
        # Re-fetch live state so the caller can surface a useful 409 envelope.
        try:
            current = await read_file(repo=repo, path=path, branch=branch)
        except FileNotFound:
            raise HTTPException(
                status_code=409,
                detail="Concurrent edit and file no longer exists; please retry.",
            )
        raise StaleSha(current_sha=current.sha, current_body=current.body)

    logger.warning("GitHub Contents write failed: %s %s", resp.status_code, resp.text[:200])
    raise HTTPException(status_code=resp.status_code, detail=resp.text)
