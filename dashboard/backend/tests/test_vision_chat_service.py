import json
import pytest
import pytest_asyncio
from app.database import Base, async_session, engine, init_db
from app.models import Project, VisionChatSession
from app.services.vision_chat import (
    create_session,
    get_active_session,
    append_turn,
    mark_approved,
    mark_cancelled,
    SessionAlreadyActive,
    SessionNotFound,
)


@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session(setup_db):
    async with async_session() as session:
        yield session


@pytest_asyncio.fixture
async def project(db_session):
    p = Project(repo="o/r", branch="main")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


async def test_create_session_returns_active_session_with_uuid(db_session, project):
    s = await create_session(db_session, project.id)
    assert s.id  # UUID assigned
    assert s.state == "active"
    assert s.phase == "freeform"
    assert json.loads(s.coverage) == {}


async def test_create_second_session_while_active_raises(db_session, project):
    await create_session(db_session, project.id)
    with pytest.raises(SessionAlreadyActive):
        await create_session(db_session, project.id)


async def test_get_active_session_returns_only_active_state(db_session, project):
    s1 = await create_session(db_session, project.id)
    await mark_approved(db_session, s1.id)
    await db_session.commit()
    found = await get_active_session(db_session, project.id)
    assert found is None  # approved doesn't count


async def test_create_session_after_previous_approved(db_session, project):
    s1 = await create_session(db_session, project.id)
    await mark_approved(db_session, s1.id)
    await db_session.commit()
    s2 = await create_session(db_session, project.id)
    assert s2.id != s1.id
    assert s2.state == "active"


async def test_append_turn_adds_to_messages_and_updates_coverage(db_session, project):
    s = await create_session(db_session, project.id)
    await append_turn(
        db_session,
        s.id,
        user_message="Hi",
        assistant_message="Hello!",
        coverage={"problem": True},
        phase="structured",
    )
    refreshed = await db_session.get(VisionChatSession, s.id)
    msgs = json.loads(refreshed.messages)
    assert msgs == [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
    ]
    assert json.loads(refreshed.coverage) == {"problem": True}
    assert refreshed.phase == "structured"


async def test_mark_cancelled_with_unknown_id_raises(db_session, setup_db):
    with pytest.raises(SessionNotFound):
        await mark_cancelled(db_session, "no-such-id")
        await db_session.commit()


from unittest.mock import patch

@pytest.mark.asyncio
async def test_run_chat_turn_yields_text_meta_and_done(db_session, project):
    """run_chat_turn yields TextChunk, MetaChunk, DoneChunk for a normal turn."""
    from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

    s = await create_session(db_session, project.id)

    # Build real SDK message instances so isinstance() checks pass
    assistant_text = (
        "Hello!\n\n"
        "```vision-meta\n"
        '{"phase": "freeform", "covered": ["problem"], '
        '"ready_to_assemble": false}\n'
        "```\n"
    )
    msg_assistant = AssistantMessage(
        content=[TextBlock(text=assistant_text)],
        model="claude-sonnet-4-6",
    )
    msg_result = ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=90,
        is_error=False,
        num_turns=1,
        session_id="sdk-sid-1",
    )

    async def fake_query(prompt, options):
        yield msg_assistant
        yield msg_result

    from app.services import vision_chat as vc
    with patch.object(vc, "query", new=fake_query):
        chunks = []
        async for chunk in vc.run_chat_turn(
            db_session, session_id=s.id, user_message="Hi",
            system_prompt="<test prompt>", model="claude-sonnet-4-6",
        ):
            chunks.append(chunk)

    kinds = [c["type"] for c in chunks]
    assert "assistant_text" in kinds
    assert "coverage_update" in kinds
    assert "phase_change" in kinds
    assert kinds[-1] == "done"
