"""Vision authoring endpoints (Phase 1)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import Project
from app.schemas import VisionRead
from app.services import github_contents

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["vision"])

CACHE_TTL_SECONDS = 5 * 60  # 5 minutes


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
