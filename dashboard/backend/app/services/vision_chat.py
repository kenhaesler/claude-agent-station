"""Chat session state machine for collaborative vision authoring.

Sessions are owned by Project. "One active per project" is enforced here
(SQLite has no partial unique indexes); historical approved/cancelled
rows coexist freely.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.types import AssistantMessage, ResultMessage

from app.models import VisionChatSession
from app.services.vision_chat_parser import (
    extract_vision_meta,
    extract_vision_doc,
    strip_fenced_blocks,
)

logger = logging.getLogger(__name__)


class SessionAlreadyActive(Exception):
    def __init__(self, existing_session_id: str):
        self.existing_session_id = existing_session_id
        super().__init__(f"session {existing_session_id} already active")


class SessionNotFound(Exception):
    pass


async def get_active_session(db: AsyncSession, project_id: int) -> VisionChatSession | None:
    result = await db.execute(
        select(VisionChatSession)
        .where(
            VisionChatSession.project_id == project_id,
            VisionChatSession.state == "active",
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_session(db: AsyncSession, project_id: int) -> VisionChatSession:
    """Create a new active session.

    Raises SessionAlreadyActive if one already exists for this project.
    """
    existing = await get_active_session(db, project_id)
    if existing is not None:
        raise SessionAlreadyActive(existing.id)

    session = VisionChatSession(
        id=str(uuid.uuid4()),
        project_id=project_id,
        state="active",
        phase="freeform",
        coverage="{}",
        messages="[]",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def append_turn(
    db: AsyncSession,
    session_id: str,
    *,
    user_message: str,
    assistant_message: str,
    coverage: dict | None = None,
    phase: str | None = None,
    sdk_session_id: str | None = None,
) -> VisionChatSession:
    """Append a user→assistant turn and update coverage/phase if provided."""
    session = await db.get(VisionChatSession, session_id)
    if session is None:
        raise SessionNotFound(session_id)

    msgs = json.loads(session.messages)
    msgs.append({"role": "user", "content": user_message})
    msgs.append({"role": "assistant", "content": assistant_message})
    session.messages = json.dumps(msgs)

    if coverage is not None:
        session.coverage = json.dumps(coverage)
    if phase is not None:
        session.phase = phase
    if sdk_session_id is not None:
        session.sdk_session_id = sdk_session_id
    session.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(session)
    return session


async def mark_approved(
    db: AsyncSession, session_id: str, assembled: dict | None = None
) -> None:
    """Mark a session as approved and optionally store the assembled doc.

    Accumulates mutations only — the **caller is responsible for committing**
    so that this write and any surrounding mutations land in one transaction.
    """
    session = await db.get(VisionChatSession, session_id)
    if session is None:
        raise SessionNotFound(session_id)
    session.state = "approved"
    if assembled is not None:
        session.assembled = json.dumps(assembled)
    session.updated_at = datetime.now(timezone.utc)


async def mark_cancelled(db: AsyncSession, session_id: str) -> None:
    """Mark a session as cancelled.

    Accumulates mutations only — the **caller is responsible for committing**
    so that this write and any surrounding mutations land in one transaction.
    """
    session = await db.get(VisionChatSession, session_id)
    if session is None:
        raise SessionNotFound(session_id)
    session.state = "cancelled"
    session.updated_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# SDK streaming wrapper
# ---------------------------------------------------------------------------

# Order matches dashboard/backend/app/services/vision_render.py:SECTIONS.
# Issue #335 inserted tech_stack and runtime_target between end_state and
# non_goals.
_SECTIONS = [
    "problem", "users", "end_state",
    "tech_stack", "runtime_target",
    "non_goals", "principles", "horizons", "anti_patterns",
]


async def _user_prompt_stream(text: str):
    """One-shot async iterable wrapping a user message.

    Same pattern as agent/station_orchestrator.py — required when using
    can_use_tool, but harmless and simpler than maintaining two paths.
    """
    yield {
        "type": "user",
        "session_id": "",
        "message": {"role": "user", "content": text},
        "parent_tool_use_id": None,
    }


async def run_chat_turn(
    db: AsyncSession,
    *,
    session_id: str,
    user_message: str,
    system_prompt: str,
    model: str,
    sdk_session_id: str | None = None,
) -> AsyncIterator[dict]:
    """Run one chat turn against the bundled CLI; yield SSE-shaped chunks.

    Yields dicts with shape ``{type, ...}`` ready to serialise to SSE events:

    - ``{"type": "assistant_text", "delta": "..."}`` — incremental, append
    - ``{"type": "coverage_update", "covered": [...], "remaining": [...]}``
    - ``{"type": "phase_change", "phase": "freeform" | "structured"}``
    - ``{"type": "vision_ready", "vision_doc": {...}}``
    - ``{"type": "error", "code": "...", "message": "..."}``
    - ``{"type": "done"}``

    Persists the turn (user + visible assistant text without fences) on
    completion via :func:`append_turn`.
    """
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=model,
        max_turns=1,  # one turn per call; UI loops as user types
    )
    if sdk_session_id:
        options.resume = sdk_session_id
        options.continue_conversation = True

    full_text = ""
    new_sdk_sid: str | None = None
    last_meta: dict | None = None
    final_doc: dict | None = None

    try:
        async for message in query(prompt=_user_prompt_stream(user_message), options=options):
            sid = getattr(message, "session_id", None)
            if sid:
                new_sdk_sid = sid

            if isinstance(message, AssistantMessage):
                for block in getattr(message, "content", []) or []:
                    text = getattr(block, "text", None)
                    if not text:
                        continue
                    full_text += text
                    yield {"type": "assistant_text", "delta": text}

            elif isinstance(message, ResultMessage):
                # End of turn — extract metadata and final doc if present.
                last_meta = extract_vision_meta(full_text)
                final_doc = extract_vision_doc(full_text)

    except Exception as e:
        logger.exception("run_chat_turn failed")
        yield {"type": "error", "code": "sdk_error", "message": str(e)}
        return

    # Emit metadata-derived events
    if last_meta:
        covered = last_meta.get("covered", []) or []
        yield {
            "type": "coverage_update",
            "covered": covered,
            "remaining": [s for s in _SECTIONS if s not in covered],
        }
        phase = last_meta.get("phase")
        if phase in ("freeform", "structured"):
            yield {"type": "phase_change", "phase": phase}

    if final_doc is not None:
        yield {"type": "vision_ready", "vision_doc": final_doc}

    # Persist the visible assistant text (fences stripped)
    visible = strip_fenced_blocks(full_text)
    coverage_dict: dict | None = None
    if last_meta:
        coverage_dict = {s: (s in (last_meta.get("covered") or [])) for s in _SECTIONS}

    await append_turn(
        db,
        session_id,
        user_message=user_message,
        assistant_message=visible,
        coverage=coverage_dict,
        phase=(last_meta.get("phase") if last_meta else None),
        sdk_session_id=new_sdk_sid,
    )

    yield {"type": "done"}
