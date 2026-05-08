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
from app.schemas import VisionRead, VisionCommitIn, VisionCommitOut, VisionStaleSha, VisionChatTurnIn, VisionChatSessionOut, VisionProposalsRead
from app.services import github_contents
from app.services.vision_render import render_vision_doc
from app.services import vision_chat as vc_service
from app.services import service_control
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

    # Trigger B (spec 2026-05-08-vision-issue-bootstrap-design.md):
    # fire the analyst when the vision SHA actually changed. We set
    # last_vision_analyzed_sha at *dispatch* time (not on completion) so a
    # failed analyst doesn't loop on identical re-commits.
    dispatched: bool = False
    if fresh.sha != project.last_vision_analyzed_sha:
        try:
            result = await service_control.start_vision_analyst(project_id)
            if not result.get("success") and result.get("status_code") != 409:
                logger.warning(
                    "vision commit B-trigger dispatch failed: %s",
                    result.get("error") or result.get("stderr"),
                )
            else:
                # 200 or 409 — both mean "an analyst run will happen"
                project.last_vision_analyzed_sha = fresh.sha
                dispatched = True
        except Exception as exc:
            logger.warning("vision commit B-trigger dispatch exception: %s", exc)

    # Mark any active chat session as approved with the assembled doc
    active = await vc_service.get_active_session(db, project_id)
    if active:
        await vc_service.mark_approved(db, active.id, assembled=body.vision_doc.model_dump())

    await db.commit()
    return VisionCommitOut(sha=new_sha, html_url=fresh.html_url, analyst_dispatched=dispatched)


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


# ---------------------------------------------------------------------------
# Session resume / cancel endpoints
# ---------------------------------------------------------------------------

from fastapi import status as http_status


@router.get("/{project_id}/vision/chat", response_model=VisionChatSessionOut)
async def get_chat_session(project_id: int, db: AsyncSession = Depends(get_db)):
    """Return the active chat session for a project (for UI rehydration)."""
    session = await get_active_session(db, project_id)
    if not session:
        raise HTTPException(status_code=404, detail="no active session")
    return VisionChatSessionOut(
        id=session.id,
        project_id=session.project_id,
        state=session.state,
        phase=session.phase,
        coverage=json.loads(session.coverage),
        messages=json.loads(session.messages),
        assembled=json.loads(session.assembled) if session.assembled else None,
        created_at=session.created_at.isoformat() if session.created_at else "",
        updated_at=session.updated_at.isoformat() if session.updated_at else "",
    )


@router.delete("/{project_id}/vision/chat", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_chat_session(project_id: int, db: AsyncSession = Depends(get_db)):
    """Cancel the active chat session for a project."""
    session = await get_active_session(db, project_id)
    if not session:
        raise HTTPException(status_code=404, detail="no active session")
    await mark_cancelled(db, session.id)
    await db.commit()


@router.post("/{project_id}/vision/find-gaps")
async def find_gaps(project_id: int, db: AsyncSession = Depends(get_db)):
    """Dispatch the vision_analyst to find gaps in the project vision."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    if not project.vision_cached_body:
        raise HTTPException(status_code=400, detail="project has no vision yet")

    result = await service_control.start_vision_analyst(project_id)
    if not result.get("success"):
        raise HTTPException(
            status_code=result.get("status_code") or 500,
            detail=result.get("error") or result.get("stderr") or "failed to start vision-analyst",
        )
    return {"status": "triggered", **{k: v for k, v in result.items() if k not in {"success", "status_code"}}}


# ---------------------------------------------------------------------------
# Vision proposals: open + recently-accepted vision-suggested issues
# ---------------------------------------------------------------------------

# Module-level cache: {project_id: (timestamp, payload)}.
# 60-second TTL is enough to absorb dashboard re-renders without
# overwhelming the rate-limited gh CLI.
_PROPOSALS_CACHE: dict[int, tuple[float, dict]] = {}
_PROPOSALS_TTL_S = 60


def _count_issues(repo: str, *, state: str, label: str, days_back: int | None = None) -> int:
    """Run `gh issue list` and count results. Returns 0 on any failure."""
    import subprocess
    import datetime as _dt

    cmd = [
        "gh", "issue", "list",
        "--repo", repo,
        "--state", state,
        "--label", label,
        "--limit", "100",
        "--json", "number",
    ]
    if days_back is not None:
        # Use gh's --search; resolve the date in Python to avoid shell expansion
        # surprises.
        cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days_back)).strftime("%Y-%m-%d")
        cmd += ["--search", f"closed:>={cutoff}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return 0
        return len(json.loads(result.stdout or "[]"))
    except Exception:
        return 0


@router.get("/{project_id}/vision/proposals", response_model=VisionProposalsRead)
async def vision_proposals(project_id: int, db: AsyncSession = Depends(get_db)):
    """Return open + recently-accepted proposal counts for the Vision tab."""
    import time

    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    cached = _PROPOSALS_CACHE.get(project_id)
    if cached and (time.time() - cached[0]) < _PROPOSALS_TTL_S:
        return VisionProposalsRead(**cached[1])

    import asyncio as _asyncio
    open_count = await _asyncio.to_thread(
        _count_issues, project.repo, state="open", label="vision-suggested"
    )
    # Accepted = closed within last 7 days that previously had vision-suggested.
    # The label may have been removed when the issue was accepted, so this is
    # an approximation — close enough for an info strip.
    accepted = await _asyncio.to_thread(
        _count_issues, project.repo, state="closed", label="vision-suggested", days_back=7,
    )

    payload = {"open": open_count, "accepted_recent": accepted}
    _PROPOSALS_CACHE[project_id] = (time.time(), payload)
    return VisionProposalsRead(**payload)
