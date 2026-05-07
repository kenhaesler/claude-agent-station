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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import VisionChatSession

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
    session = await db.get(VisionChatSession, session_id)
    if session is None:
        raise SessionNotFound(session_id)
    session.state = "approved"
    if assembled is not None:
        session.assembled = json.dumps(assembled)
    session.updated_at = datetime.now(timezone.utc)
    await db.commit()


async def mark_cancelled(db: AsyncSession, session_id: str) -> None:
    session = await db.get(VisionChatSession, session_id)
    if session is None:
        raise SessionNotFound(session_id)
    session.state = "cancelled"
    session.updated_at = datetime.now(timezone.utc)
    await db.commit()
