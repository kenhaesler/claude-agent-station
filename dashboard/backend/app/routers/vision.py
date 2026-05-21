"""Vision authoring endpoints (Phase 1)."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import Project, Run, VisionChatAttachment, VisionChatSession
from app.schemas import VisionRead, VisionCommitIn, VisionCommitOut, VisionStaleSha, VisionChatTurnIn, VisionChatSessionOut, VisionProposalsRead, VisionAttachmentOut, VisionRefFailure
from app.services import github_contents
from app.services.vision_render import render_vision_doc
from app.services import vision_chat as vc_service
from app.services import vision_attachments as va
from app.services import service_control
from app.services.github_app import get_installation_token
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

    # Find session + attachments to commit (those with sent_at IS NOT NULL)
    active = await vc_service.get_active_session(db, project_id)
    references_for_render: list[dict] = []
    refs_committed: list[str] = []
    refs_failed: list[dict] = []
    attachments_to_commit: list[VisionChatAttachment] = []
    if active:
        result = await db.execute(
            select(VisionChatAttachment).where(
                VisionChatAttachment.session_id == active.id,
                VisionChatAttachment.sent_at.is_not(None),
            )
        )
        attachments_to_commit = list(result.scalars().all())

    # 1. Upload reference files to docs/vision-refs/<filename>
    from pathlib import Path as _Path
    for att in attachments_to_commit:
        try:
            raw = _Path(att.disk_path).read_bytes()
            await github_contents.write_file(
                repo=project.repo,
                path=f"docs/vision-refs/{att.filename}",
                branch=project.branch or "main",
                body_bytes=raw,
                message=f"docs(vision-refs): add {att.filename}",
                current_sha=None,
            )
            refs_committed.append(att.filename)
            references_for_render.append({"filename": att.filename, "size_bytes": att.size_bytes})
        except Exception as exc:
            logger.warning("vision ref upload failed for %s: %s", att.filename, exc)
            refs_failed.append({"filename": att.filename, "error": str(exc)})

    # 2. Render vision.md WITH references (reflects what was actually committed)
    md = render_vision_doc(
        body.vision_doc.model_dump(),
        repo=project.repo,
        refined_at=now,
        references=references_for_render,
    )

    # 3. Write vision.md to GitHub
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

    # 4. Re-fetch to get html_url; also updates the cache
    fresh = await github_contents.read_file(
        repo=project.repo, path="docs/vision.md", branch=project.branch or "main",
    )
    project.vision_cached_sha = fresh.sha
    project.vision_cached_body = fresh.body
    project.vision_cached_at = now

    # 5. Trigger B (spec 2026-05-08-vision-issue-bootstrap-design.md):
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

    # 6. Mark any active chat session as approved with the assembled doc
    if active:
        await vc_service.mark_approved(db, active.id, assembled=body.vision_doc.model_dump())

    await db.commit()

    # 7. Cleanup disk only when all refs uploaded successfully
    if active and not refs_failed:
        va.cleanup_session_dir(active.id)

    return VisionCommitOut(
        sha=new_sha,
        html_url=fresh.html_url,
        analyst_dispatched=dispatched,
        refs_committed=refs_committed,
        refs_failed=[VisionRefFailure(**rf) for rf in refs_failed],
    )


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
    from app.services.config_sync import _read_config_json
    config = await asyncio.to_thread(_read_config_json)
    model = (config.get("models") or {}).get("planner") or "claude-sonnet-4-6"

    # Build attachment blocks if any IDs supplied
    attachment_blocks: list[dict] | None = None
    user_attachments_dict: list[dict] | None = None
    if body.attachment_ids:
        result = await db.execute(
            select(VisionChatAttachment).where(
                VisionChatAttachment.id.in_(body.attachment_ids),
                VisionChatAttachment.session_id == session.id,
                VisionChatAttachment.sent_at.is_(None),
            )
        )
        rows = list(result.scalars().all())
        if len(rows) != len(body.attachment_ids):
            raise HTTPException(
                status_code=400,
                detail="one or more attachment_ids are invalid, already sent, or from a different session",
            )
        attachment_blocks = await va.build_chat_blocks(
            db, user_text=body.message, attachment_ids=body.attachment_ids,
        )
        user_attachments_dict = [
            {"id": a.id, "filename": a.filename, "mime_type": a.mime_type, "size_bytes": a.size_bytes}
            for a in rows
        ]
        await db.commit()

    async def event_stream():
        from app.services.vision_chat import run_chat_turn
        async for chunk in run_chat_turn(
            db,
            session_id=session.id,
            user_message=body.message,
            system_prompt=system_prompt,
            model=model,
            sdk_session_id=session.sdk_session_id,
            attachment_blocks=attachment_blocks,
            user_attachments=user_attachments_dict,
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

    from app.models import VisionChatAttachment as _VCA
    pending_q = await db.execute(
        select(_VCA).where(_VCA.session_id == session.id, _VCA.sent_at.is_(None))
    )
    pending = [
        VisionAttachmentOut(id=a.id, filename=a.filename, mime_type=a.mime_type, size_bytes=a.size_bytes)
        for a in pending_q.scalars().all()
    ]
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
        pending_attachments=pending,
    )


@router.delete("/{project_id}/vision/chat", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_chat_session(project_id: int, db: AsyncSession = Depends(get_db)):
    """Cancel the active chat session for a project."""
    session = await get_active_session(db, project_id)
    if not session:
        raise HTTPException(status_code=404, detail="no active session")
    sid = session.id
    await mark_cancelled(db, sid)
    await db.commit()
    try:
        va.cleanup_session_dir(sid)
    except Exception:
        logger.warning("cleanup_session_dir failed for session %s", sid, exc_info=True)


# ---------------------------------------------------------------------------
# Attachment upload / delete endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/{project_id}/vision/chat/attachments",
    response_model=VisionAttachmentOut,
)
async def upload_chat_attachment(
    project_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> VisionAttachmentOut:
    """Upload a reference file for the active vision chat session.

    Lazily creates an active session if one doesn't exist (mirrors chat_turn).
    """
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    session = await get_active_session(db, project_id)
    if session is None:
        try:
            session = await create_session(db, project_id)
        except SessionAlreadyActive as exc:
            session = await db.get(VisionChatSession, exc.existing_session_id)

    raw = await file.read()
    try:
        att = await va.store_attachment(
            db, session_id=session.id, raw=raw,
            declared_filename=file.filename or "upload.bin",
        )
    except va.AttachmentRejected as exc:
        msg = str(exc)
        lower = msg.lower()
        if "max 10 mb" in lower or ("session" in lower and "limit" in lower):
            raise HTTPException(status_code=413, detail=msg)
        if "not a supported" in lower:
            raise HTTPException(status_code=415, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    await db.commit()
    return VisionAttachmentOut(
        id=att.id, filename=att.filename,
        mime_type=att.mime_type, size_bytes=att.size_bytes,
    )


@router.delete(
    "/{project_id}/vision/chat/attachments/{attachment_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def delete_chat_attachment(
    project_id: int,
    attachment_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete an attachment before it's been sent in a chat turn."""
    from app.models import VisionChatAttachment
    att = await db.get(VisionChatAttachment, attachment_id)
    if att is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    sess = await db.get(VisionChatSession, att.session_id)
    if sess is None or sess.project_id != project_id:
        raise HTTPException(status_code=404, detail="attachment not found")
    try:
        await va.delete_attachment(db, attachment_id=attachment_id)
    except va.AttachmentRejected as exc:
        msg_lower = str(exc).lower()
        if "already sent" in msg_lower:
            raise HTTPException(status_code=409, detail=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    await db.commit()


async def _check_github_auth() -> bool:
    """Return True iff the dashboard can mint a fresh GitHub App installation
    token.

    This is the same auth path the analyst itself relies on at runtime:
    ``agent.vision_analyst._fetch_gh_token`` calls
    ``GET /api/github/app/token``, which in turn calls
    :func:`get_installation_token`. Probing the mint here lets the
    preflight short-circuit a doomed analyst dispatch and lines the check
    up with the GitHub Settings page (the green chip there reflects the
    same call).

    Replaces the previous ``gh auth status`` shell-out, which was broken
    in compose deploys because ``gh`` isn't installed in the dashboard
    image (the subprocess raised ``FileNotFoundError``, was caught by a
    bare ``except``, and unconditionally returned False — so every
    dispatch hit a 409 regardless of actual App auth state).
    """
    try:
        return bool(await get_installation_token())
    except Exception as exc:
        # Network blip, malformed credentials on disk, etc. We log so
        # operators can spot a flapping mint, but the preflight return
        # stays binary — caller raises a single 409 either way.
        logger.warning("preflight GitHub App auth probe failed: %s", exc)
        return False


async def _vision_preflight(
    db: AsyncSession, project: Project
) -> tuple[bool, int, str]:
    """Preflight checks before dispatching a vision_analyst run.

    Returns ``(ok, status_code, detail)``. When ``ok`` is True, the caller
    may dispatch; when False, raise HTTPException(status_code, detail).

    Two layers (issue #272):
    1. GitHub App auth health — probes the same installation-token mint
       the analyst uses at runtime, so a green chip in Settings and a
       passing preflight refer to the same thing.
    2. Last vision-bootstrap run failed for the *current* vision SHA AND
       no later success exists → block re-runs until the operator either
       fixes auth or commits a new vision.
    """
    # Layer A: can the dashboard mint an App installation token?
    auth_ok = await _check_github_auth()
    if not auth_ok:
        return (
            False,
            409,
            "GitHub App is not installed or unreachable — visit Settings → GitHub to reconnect.",
        )

    # Layer B: stale-failure guard. Find the most-recent vision-bootstrap
    # run for this project; if it failed and no later 'completed' run for
    # the current cached SHA exists, refuse to re-dispatch.
    q = (
        select(Run)
        .where(Run.project_id == project.id, Run.mode == "vision-bootstrap")
        .order_by(desc(Run.started_at))
        .limit(5)
    )
    result = await db.execute(q)
    recent: list[Run] = list(result.scalars().all())
    if recent:
        latest = recent[0]
        if latest.status == "failed":
            # Look for a 'completed' run that started AFTER the latest failure.
            # A success at-or-after the failure means the project recovered.
            recovered = any(
                r.status == "completed"
                and (r.started_at or datetime.min) >= (latest.started_at or datetime.min)
                and r.run_id != latest.run_id
                for r in recent
            )
            if not recovered:
                return (
                    False,
                    409,
                    "Last vision analysis for this commit failed. "
                    "Resolve the issue (commit a new vision or fix gh auth) "
                    "before re-running.",
                )
    return (True, 200, "")


@router.post("/{project_id}/vision/find-gaps")
async def find_gaps(project_id: int, db: AsyncSession = Depends(get_db)):
    """Dispatch the vision_analyst to find gaps in the project vision."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    if not project.vision_cached_body:
        raise HTTPException(status_code=400, detail="project has no vision yet")

    ok, status_code, detail = await _vision_preflight(db, project)
    if not ok:
        raise HTTPException(status_code=status_code, detail=detail)

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
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
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
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    cached = _PROPOSALS_CACHE.get(project_id)
    if cached and (time.time() - cached[0]) < _PROPOSALS_TTL_S:
        return VisionProposalsRead(**cached[1])

    open_count = await asyncio.to_thread(
        _count_issues, project.repo, state="open", label="vision-suggested"
    )
    # Accepted = closed within last 7 days that previously had vision-suggested.
    # The label may have been removed when the issue was accepted, so this is
    # an approximation — close enough for an info strip.
    accepted = await asyncio.to_thread(
        _count_issues, project.repo, state="closed", label="vision-suggested", days_back=7,
    )

    payload = {"open": open_count, "accepted_recent": accepted}
    _PROPOSALS_CACHE[project_id] = (time.time(), payload)
    return VisionProposalsRead(**payload)
