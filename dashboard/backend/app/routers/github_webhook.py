"""GitHub webhook receiver for event-driven queue population."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.models import Project, QueueItem
from app.schemas import QueueItemOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/github-webhook", tags=["github-webhook"])


def _verify_signature(payload: bytes, signature: str | None, secret: str) -> bool:
    """Verify GitHub webhook HMAC-SHA256 signature."""
    if not secret:
        return True  # No secret configured, skip verification
    if not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


def _extract_mode_from_labels(labels: list[dict]) -> str | None:
    """Extract mode from issue labels (mode/fix, mode/full, etc.)."""
    for label in labels:
        name = label.get("name", "")
        if name.startswith("mode/"):
            mode = name.split("/", 1)[1]
            if mode in ("full", "fix", "analyze", "plan", "triage", "review"):
                return mode
    return None


def _extract_priority(labels: list[dict]) -> int:
    """Map priority labels to queue priority integer."""
    for label in labels:
        name = label.get("name", "")
        if name == "priority/critical":
            return 10
        elif name == "priority/high":
            return 7
        elif name == "priority/medium":
            return 5
        elif name == "priority/low":
            return 2
    return 3  # Default priority


async def _find_project(repo_full_name: str, db: AsyncSession) -> Project | None:
    """Find a project by its full repo name."""
    result = await db.execute(
        select(Project).where(Project.repo == repo_full_name, Project.enabled == True)  # noqa: E712
    )
    return result.scalar_one_or_none()


async def _has_active_item(
    project_repo: str,
    issue_number: int,
    db: AsyncSession,
) -> bool:
    """Check if there's already an active queue item for this issue."""
    active_states = {"pending", "claimed", "assigned", "planning", "in_progress", "review", "verifying"}
    result = await db.execute(
        select(QueueItem).where(
            QueueItem.project_repo == project_repo,
            QueueItem.issue_number == issue_number,
            QueueItem.state.in_(active_states),
        )
    )
    return result.scalar_one_or_none() is not None


@router.post("")
async def handle_github_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_hub_signature_256: str | None = Header(None),
    x_github_event: str | None = Header(None),
):
    """Handle incoming GitHub webhook events.

    Supported events:
    - issues.opened → triage queue item
    - issues.labeled (mode/*) → specific mode queue item
    - issues.labeled (priority/critical) → high-priority queue item
    - pull_request.opened / review_requested → review queue item
    """
    body = await request.body()

    # Verify signature if webhook secret is configured
    webhook_secret = getattr(settings, "github_webhook_secret", "")
    if webhook_secret and not _verify_signature(body, x_hub_signature_256, webhook_secret):
        raise HTTPException(403, "Invalid webhook signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON payload")

    action = payload.get("action", "")
    event_type = x_github_event or ""
    repo = payload.get("repository", {}).get("full_name", "")

    # Find matching project
    project = await _find_project(repo, db)
    if not project:
        return {"status": "ignored", "reason": "repository not configured"}

    items_created = []

    if event_type == "issues":
        issue = payload.get("issue", {})
        issue_number = issue.get("number")
        issue_title = issue.get("title", "")
        labels = issue.get("labels", [])

        if not issue_number:
            return {"status": "ignored", "reason": "no issue number"}

        # Check dedup
        if await _has_active_item(repo, issue_number, db):
            return {"status": "deduplicated", "issue_number": issue_number}

        if action == "opened":
            # New issue → triage queue item
            item = QueueItem(
                project_repo=repo,
                issue_number=issue_number,
                issue_title=issue_title,
                state="pending",
                priority=_extract_priority(labels),
                mode=_extract_mode_from_labels(labels) or "triage",
            )
            db.add(item)
            items_created.append(f"triage #{issue_number}")

        elif action == "labeled":
            label = payload.get("label", {})
            label_name = label.get("name", "")

            if label_name.startswith("mode/"):
                mode = label_name.split("/", 1)[1]
                if mode in ("full", "fix", "analyze", "plan", "triage", "review"):
                    item = QueueItem(
                        project_repo=repo,
                        issue_number=issue_number,
                        issue_title=issue_title,
                        state="pending",
                        priority=_extract_priority(labels),
                        mode=mode,
                    )
                    db.add(item)
                    items_created.append(f"{mode} #{issue_number}")

            elif label_name == "priority/critical":
                item = QueueItem(
                    project_repo=repo,
                    issue_number=issue_number,
                    issue_title=issue_title,
                    state="pending",
                    priority=10,
                    mode=_extract_mode_from_labels(labels) or project.mode,
                )
                db.add(item)
                items_created.append(f"critical #{issue_number}")

    elif event_type == "pull_request":
        pr = payload.get("pull_request", {})
        pr_number = pr.get("number")
        pr_title = pr.get("title", "")

        if action in ("opened", "review_requested"):
            if pr_number and not await _has_active_item(repo, pr_number, db):
                item = QueueItem(
                    project_repo=repo,
                    issue_number=pr_number,
                    issue_title=f"Review: {pr_title}",
                    state="pending",
                    priority=5,
                    mode="review",
                )
                db.add(item)
                items_created.append(f"review PR #{pr_number}")

    if items_created:
        await db.commit()
        logger.info("GitHub webhook: created queue items: %s", ", ".join(items_created))

    return {
        "status": "processed",
        "event": event_type,
        "action": action,
        "items_created": items_created,
    }
