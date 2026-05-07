"""Vision authoring endpoints (Phase 1)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import Project, VisionChatSession
from app.schemas import VisionRead, VisionCommitIn, VisionCommitOut, VisionStaleSha, VisionChatTurnIn
from app.services import github_contents
from app.services.vision_render import render_vision_doc
from app.services import vision_chat as vc_service
from app.services.vision_chat import (
    create_session, get_active_session, mark_cancelled,
    SessionAlreadyActive, SessionNotFound,
)

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


# ---------------------------------------------------------------------------
# SSE chat turn endpoint
# ---------------------------------------------------------------------------

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "agent" / "prompts"


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _sse_format(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")


@router.post("/{project_id}/vision/chat")
async def chat_turn(
    project_id: int,
    body: VisionChatTurnIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Stream a chat turn as SSE events."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    # Resolve session: existing if session_id supplied, else create
    if body.session_id:
        session = await db.get(VisionChatSession, body.session_id)
        if not session or session.project_id != project_id or session.state != "active":
            raise HTTPException(status_code=404, detail="active session not found")
    else:
        try:
            session = await create_session(db, project_id)
        except SessionAlreadyActive as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "session_exists", "session_id": exc.existing_session_id},
            )

    # Pick the right system prompt
    if project.vision_cached_body:
        prompt_template = _load_prompt("vision_refine.md")
        system_prompt = prompt_template.replace(
            "{{CURRENT_VISION_MARKDOWN}}", project.vision_cached_body,
        )
    else:
        system_prompt = _load_prompt("vision_create.md")

    # Pick the model — read the station config JSON directly
    import asyncio as _asyncio
    from app.services.config_sync import _read_config_json
    config = await _asyncio.to_thread(_read_config_json)
    model = (config.get("models") or {}).get("planner") or "claude-sonnet-4-6"

    async def event_stream():
        from app.services.vision_chat import run_chat_turn
        async for chunk in run_chat_turn(
            db,
            session_id=session.id,
            user_message=body.message,
            system_prompt=system_prompt,
            model=model,
            sdk_session_id=session.sdk_session_id,
        ):
            kind = chunk.pop("type")
            yield _sse_format(kind, chunk)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
