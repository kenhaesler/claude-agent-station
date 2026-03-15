"""Brainstorm Mode — interactive AI expert collaboration."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import BrainstormMessage, BrainstormSession, Project

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/brainstorm", tags=["brainstorm"])

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class SessionCreate(BaseModel):
    title: str | None = None
    project_id: int | None = None
    persona: str = "architect"


class MessageCreate(BaseModel):
    content: str


class SessionResponse(BaseModel):
    id: str
    project_id: int | None = None
    title: str | None = None
    persona: str = "architect"
    created_at: str | None = None
    updated_at: str | None = None
    message_count: int = 0
    project_repo: str | None = None


class MessageResponse(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    created_at: str | None = None


class SessionDetail(SessionResponse):
    messages: list[MessageResponse] = []


# ---------------------------------------------------------------------------
# Expert persona system prompts
# ---------------------------------------------------------------------------

PERSONA_PROMPTS: dict[str, str] = {
    "architect": (
        "You are JARVIS — an elite software architect and strategic advisor. "
        "You think in systems, trade-offs, and long-term consequences. "
        "You are proactive: you suggest ideas unprompted, ask probing questions, "
        "challenge weak assumptions, and connect dots across domains. "
        "You are opinionated but collaborative — you present strong viewpoints "
        "while remaining open to the user's direction. "
        "You have deep expertise in distributed systems, API design, data modeling, "
        "and software architecture patterns. "
        "When the user shares a project context, you internalize it and reference "
        "it naturally throughout the conversation. "
        "Use markdown formatting for clarity: code blocks, headers, lists, bold."
    ),
    "security": (
        "You are JARVIS — an elite security engineer and threat analyst. "
        "You think adversarially: every system has attack surfaces, every input "
        "is untrusted, every dependency is a liability. "
        "You are proactive: you identify vulnerabilities before they're exploited, "
        "suggest hardening measures unprompted, and challenge security assumptions. "
        "You have deep expertise in OWASP, authentication/authorization patterns, "
        "cryptography, supply chain security, and incident response. "
        "You balance security with pragmatism — you prioritize high-impact, "
        "low-effort mitigations and avoid security theater. "
        "Use markdown formatting for clarity: code blocks, headers, lists, bold."
    ),
    "performance": (
        "You are JARVIS — an elite performance engineer and optimization specialist. "
        "You think in bottlenecks, latency distributions, and scalability curves. "
        "You are proactive: you identify performance risks before they manifest, "
        "suggest profiling strategies, and challenge premature optimization. "
        "You have deep expertise in database query optimization, caching strategies, "
        "concurrency patterns, memory management, and load testing. "
        "You insist on measurement before optimization — no guessing, only data. "
        "You balance performance with maintainability and development velocity. "
        "Use markdown formatting for clarity: code blocks, headers, lists, bold."
    ),
    "devops": (
        "You are JARVIS — an elite DevOps engineer and platform architect. "
        "You think in pipelines, infrastructure as code, and operational excellence. "
        "You are proactive: you identify deployment risks, suggest automation "
        "opportunities, and challenge manual processes. "
        "You have deep expertise in CI/CD, container orchestration, cloud platforms, "
        "monitoring/observability, and incident management. "
        "You champion reliability: SLOs, error budgets, canary deployments, "
        "and progressive rollouts are your bread and butter. "
        "Use markdown formatting for clarity: code blocks, headers, lists, bold."
    ),
}

DEFAULT_PERSONA = "architect"


def _get_system_prompt(persona: str, project_repo: str | None = None) -> str:
    """Build the system prompt for the given persona and optional project context."""
    base = PERSONA_PROMPTS.get(persona, PERSONA_PROMPTS[DEFAULT_PERSONA])
    if project_repo:
        base += (
            f"\n\nThe user is working on the project: {project_repo}. "
            "Keep this context in mind and reference it when relevant."
        )
    base += (
        "\n\nIMPORTANT: This is a conversational brainstorm session. "
        "Do NOT use any tools — respond with text only. Never call tools like "
        "Read, Bash, WebSearch, Glob, Grep, etc. Just think and respond."
    )
    return base


def _get_model() -> str:
    """Return the model to use for brainstorm sessions."""
    return os.environ.get("BRAINSTORM_MODEL", "claude-sonnet-4-6")


def _find_claude_cli() -> str:
    """Find the claude CLI binary."""
    path = shutil.which("claude")
    if not path:
        for fallback in ["/home/claude-agent/.local/bin/claude", "/usr/local/bin/claude"]:
            if os.path.isfile(fallback) and os.access(fallback, os.X_OK):
                return fallback
        raise HTTPException(status_code=503, detail="claude CLI not found in PATH")
    return path


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/sessions", response_model=SessionResponse)
async def create_session(
    body: SessionCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new brainstorm session."""
    session_id = f"bs-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    # Validate project_id if provided
    project_repo: str | None = None
    if body.project_id is not None:
        result = await db.execute(
            select(Project).where(Project.id == body.project_id)
        )
        project = result.scalar_one_or_none()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        project_repo = project.repo

    persona = body.persona if body.persona in PERSONA_PROMPTS else DEFAULT_PERSONA

    session = BrainstormSession(
        id=session_id,
        project_id=body.project_id,
        title=body.title or "New brainstorm",
        persona=persona,
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    await db.commit()

    return SessionResponse(
        id=session_id,
        project_id=body.project_id,
        title=session.title,
        persona=persona,
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
        message_count=0,
        project_repo=project_repo,
    )


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(db: AsyncSession = Depends(get_db)):
    """List all brainstorm sessions, most recent first."""
    result = await db.execute(
        select(BrainstormSession).order_by(BrainstormSession.updated_at.desc())
    )
    sessions = result.scalars().all()

    # Count messages per session using SQL GROUP BY
    from sqlalchemy import func
    count_result = await db.execute(
        select(BrainstormMessage.session_id, func.count(BrainstormMessage.id))
        .group_by(BrainstormMessage.session_id)
    )
    msg_counts: dict[str, int] = {row[0]: row[1] for row in count_result.all()}

    # Get project repos
    project_ids = {s.project_id for s in sessions if s.project_id is not None}
    project_map: dict[int, str] = {}
    if project_ids:
        proj_result = await db.execute(
            select(Project).where(Project.id.in_(project_ids))
        )
        for p in proj_result.scalars().all():
            project_map[p.id] = p.repo

    return [
        SessionResponse(
            id=s.id,
            project_id=s.project_id,
            title=s.title,
            persona=s.persona or DEFAULT_PERSONA,
            created_at=s.created_at.isoformat() if s.created_at else None,
            updated_at=s.updated_at.isoformat() if s.updated_at else None,
            message_count=msg_counts.get(s.id, 0),
            project_repo=project_map.get(s.project_id) if s.project_id else None,
        )
        for s in sessions
    ]


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a brainstorm session with its full message history."""
    result = await db.execute(
        select(BrainstormSession).where(BrainstormSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    msg_result = await db.execute(
        select(BrainstormMessage)
        .where(BrainstormMessage.session_id == session_id)
        .order_by(BrainstormMessage.created_at)
    )
    messages = msg_result.scalars().all()

    project_repo: str | None = None
    if session.project_id:
        proj_result = await db.execute(
            select(Project).where(Project.id == session.project_id)
        )
        proj = proj_result.scalar_one_or_none()
        if proj:
            project_repo = proj.repo

    return SessionDetail(
        id=session.id,
        project_id=session.project_id,
        title=session.title,
        persona=session.persona or DEFAULT_PERSONA,
        created_at=session.created_at.isoformat() if session.created_at else None,
        updated_at=session.updated_at.isoformat() if session.updated_at else None,
        message_count=len(messages),
        project_repo=project_repo,
        messages=[
            MessageResponse(
                id=m.id,
                session_id=m.session_id,
                role=m.role,
                content=m.content,
                created_at=m.created_at.isoformat() if m.created_at else None,
            )
            for m in messages
        ],
    )


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a brainstorm session and all its messages."""
    result = await db.execute(
        select(BrainstormSession).where(BrainstormSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Delete messages first, then session
    await db.execute(
        delete(BrainstormMessage).where(BrainstormMessage.session_id == session_id)
    )
    await db.delete(session)
    await db.commit()
    return {"status": "deleted", "session_id": session_id}


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    body: MessageCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Send a user message and stream the AI response via SSE."""
    # Validate session exists
    result = await db.execute(
        select(BrainstormSession).where(BrainstormSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Save user message
    user_msg_id = f"msg-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    user_msg = BrainstormMessage(
        id=user_msg_id,
        session_id=session_id,
        role="user",
        content=body.content,
        created_at=now,
    )
    # Fetch existing messages BEFORE adding the new one (avoid autoflush duplication)
    msg_count_result = await db.execute(
        select(BrainstormMessage)
        .where(BrainstormMessage.session_id == session_id)
        .order_by(BrainstormMessage.created_at)
    )
    existing_msgs = msg_count_result.scalars().all()

    # Auto-title from first user message
    if len(existing_msgs) == 0 and session.title == "New brainstorm":
        session.title = body.content[:60].rstrip() + ("..." if len(body.content) > 60 else "")

    db.add(user_msg)
    session.updated_at = now
    await db.commit()

    # Build conversation history for the API call
    history = [
        {"role": m.role, "content": m.content}
        for m in existing_msgs
    ]
    history.append({"role": "user", "content": body.content})

    # Resolve project repo for system prompt
    project_repo: str | None = None
    if session.project_id:
        proj_result = await db.execute(
            select(Project).where(Project.id == session.project_id)
        )
        proj = proj_result.scalar_one_or_none()
        if proj:
            project_repo = proj.repo

    persona = session.persona or DEFAULT_PERSONA
    system_prompt = _get_system_prompt(persona, project_repo)
    model = _get_model()

    async def generate_sse():
        """Stream the AI response via Claude CLI subprocess."""
        assistant_msg_id = f"msg-{uuid.uuid4().hex[:12]}"
        full_response = ""
        sp_path: str | None = None

        try:
            claude_bin = _find_claude_cli()

            # Write system prompt to a temp file for --system-prompt-file
            with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as sp_file:
                sp_file.write(system_prompt)
                sp_path = sp_file.name

            # Build the user prompt from history (all prior messages + current)
            # Claude -p with --resume doesn't support multi-turn, so we flatten
            # prior conversation into context within the user prompt.
            if len(history) > 1:
                context_parts = []
                for msg in history[:-1]:
                    role_label = "User" if msg["role"] == "user" else "Assistant"
                    context_parts.append(f"[{role_label}]: {msg['content']}")
                user_prompt = (
                    "Here is our conversation so far:\n\n"
                    + "\n\n".join(context_parts)
                    + "\n\n[User]: " + history[-1]["content"]
                    + "\n\nPlease respond to the latest user message."
                )
            else:
                user_prompt = history[-1]["content"]

            cmd = [
                claude_bin, "-p",
                "--verbose",
                "--output-format", "stream-json",
                "--no-session-persistence",
                "--model", model,
                "--max-turns", "3",
                "--system-prompt-file", sp_path,
                user_prompt,
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=1024 * 1024,  # 1MB line buffer (hook output can be large)
            )

            # Read stdout lines with a 2s timeout so we can emit SSE keepalive
            # comments while the CLI is thinking. This prevents proxy timeouts
            # and lets the frontend know the connection is alive.
            got_text = False
            while True:
                if await request.is_disconnected():
                    proc.kill()
                    break

                try:
                    line = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=2.0
                    )
                except asyncio.TimeoutError:
                    if not got_text:
                        yield ": keepalive\n\n"
                    continue

                if not line:  # EOF
                    break

                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue

                try:
                    event = json.loads(line_str)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type", "")

                # Claude CLI stream-json emits "assistant" events with full content
                if etype == "assistant":
                    msg_data = event.get("message", {})
                    for block in msg_data.get("content", []):
                        if block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                got_text = True
                                full_response += text
                                event_data = json.dumps({"type": "delta", "text": text})
                                yield f"data: {event_data}\n\n"

                # Also handle the final result as a fallback
                elif etype == "result" and not full_response:
                    text = event.get("result", "")
                    if text:
                        got_text = True
                        full_response = text
                        event_data = json.dumps({"type": "delta", "text": text})
                        yield f"data: {event_data}\n\n"

            await proc.wait()

            # Log stderr for debugging CLI issues
            if proc.stderr:
                stderr_data = await proc.stderr.read()
                if stderr_data:
                    stderr_str = stderr_data.decode("utf-8", errors="replace").strip()
                    if stderr_str:
                        logger.warning("Claude CLI stderr: %s", stderr_str[:500])

        except Exception as e:
            logger.exception("Error streaming brainstorm response")
            error_detail = f"{type(e).__name__}: {e}"
            error_data = json.dumps({"type": "error", "message": f"Brainstorm error — {error_detail}"})
            yield f"data: {error_data}\n\n"
            return
        finally:
            if sp_path:
                try:
                    os.unlink(sp_path)
                except OSError:
                    pass

        # If the CLI produced no text (e.g. it tried to use tools and hit
        # max-turns), surface an error instead of saving an empty message.
        if not full_response:
            error_data = json.dumps({
                "type": "error",
                "message": "JARVIS didn't generate a response. Please try rephrasing your question.",
            })
            yield f"data: {error_data}\n\n"
            return

        # Save assistant message to DB
        try:
            from app.database import async_session as session_factory
            async with session_factory() as save_db:
                assistant_msg = BrainstormMessage(
                    id=assistant_msg_id,
                    session_id=session_id,
                    role="assistant",
                    content=full_response,
                    created_at=datetime.now(timezone.utc),
                )
                save_db.add(assistant_msg)

                s_result = await save_db.execute(
                    select(BrainstormSession).where(BrainstormSession.id == session_id)
                )
                s = s_result.scalar_one_or_none()
                if s:
                    s.updated_at = datetime.now(timezone.utc)
                await save_db.commit()
        except Exception:
            logger.exception("Failed to save assistant message")

        # Send done event
        done_data = json.dumps({
            "type": "done",
            "message_id": assistant_msg_id,
            "full_content": full_response,
        })
        yield f"data: {done_data}\n\n"

    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
