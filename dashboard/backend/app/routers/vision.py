"""Vision authoring endpoints (Phase 1)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import Project
from app.schemas import VisionRead, VisionCommitIn, VisionCommitOut, VisionStaleSha
from app.services import github_contents
from app.services.vision_render import render_vision_doc
from app.services import vision_chat as vc_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["vision"])

CACHE_TTL_SECONDS = 5 * 60  # 5 minutes
COMMIT_MESSAGE = "docs(vision): refine via Claude Station"


@router.get("/{project_id}/vision", response_model=VisionRead)
async def get_vision(project_id: int, db: AsyncSession = Depends(get_db)) -> VisionRead:
    """Return the current vision document, cache-aware."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    now = datetime.now(timezone.utc)
    cache_fresh = (
        project.vision_cached_body is not None
        and project.vision_cached_at is not None
        and (now - project.vision_cached_at.replace(tzinfo=timezone.utc)).total_seconds() < CACHE_TTL_SECONDS
    )
    if cache_fresh:
        age = int((now - project.vision_cached_at.replace(tzinfo=timezone.utc)).total_seconds())
        return VisionRead(
            sha=project.vision_cached_sha,
            body=project.vision_cached_body,
            cache_age_seconds=age,
        )

    # Fall through to GitHub
    try:
        result = await github_contents.read_file(
            repo=project.repo, path="docs/vision.md", branch=project.branch or "main",
        )
    except github_contents.FileNotFound:
        raise HTTPException(status_code=404, detail="docs/vision.md not found on base branch")

    project.vision_cached_sha = result.sha
    project.vision_cached_body = result.body
    project.vision_cached_at = now
    await db.commit()

    return VisionRead(sha=result.sha, body=result.body, cache_age_seconds=0)


@router.post("/{project_id}/vision", response_model=VisionCommitOut)
async def commit_vision(
    project_id: int,
    body: VisionCommitIn,
    db: AsyncSession = Depends(get_db),
) -> VisionCommitOut:
    """Render vision_doc to markdown, commit to GitHub, update cache."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    now = datetime.now(timezone.utc)
    md = render_vision_doc(
        body.vision_doc.model_dump(),
        repo=project.repo,
        refined_at=now,
    )

    try:
        new_sha = await github_contents.write_file(
            repo=project.repo,
            path="docs/vision.md",
            branch=project.branch or "main",
            body=md,
            message=COMMIT_MESSAGE,
            current_sha=project.vision_cached_sha,
        )
    except github_contents.StaleSha as exc:
        raise HTTPException(
            status_code=409,
            detail=VisionStaleSha(
                current_sha=exc.current_sha,
                current_body=exc.current_body,
            ).model_dump(),
        )

    # Re-fetch to get html_url; also updates the cache
    fresh = await github_contents.read_file(
        repo=project.repo, path="docs/vision.md", branch=project.branch or "main",
    )
    project.vision_cached_sha = fresh.sha
    project.vision_cached_body = fresh.body
    project.vision_cached_at = now

    # Mark any active chat session as approved with the assembled doc
    active = await vc_service.get_active_session(db, project_id)
    if active:
        await vc_service.mark_approved(db, active.id, assembled=body.vision_doc.model_dump())

    await db.commit()
    return VisionCommitOut(sha=new_sha, html_url=fresh.html_url)
