"""Smoke test: claude_agent_sdk.query resume works for chat-style usage.

If this test fails, the chat backend MUST fall back to transcript-replay
(see spec § Resume strategy).

This test spawns the real `claude` CLI and burns tokens, so it's
default-skipped via an env-var gate rather than a marker — markers
are decorative unless the pytest invocation filters on them, and CI
doesn't filter. To run it locally:

    RUN_INTEGRATION_TESTS=1 pytest tests/test_vision_sdk_resume.py
"""

import os

import pytest
from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.types import AssistantMessage, ResultMessage

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_INTEGRATION_TESTS"),
    reason="integration test — set RUN_INTEGRATION_TESTS=1 to enable",
)


async def _user_msg(text: str):
    """Generate a single user message as an async iterable."""
    yield {
        "type": "user",
        "session_id": "",
        "message": {"role": "user", "content": text},
        "parent_tool_use_id": None,
    }


@pytest.mark.asyncio
async def test_query_resume_chat_style_remembers_context():
    """First turn establishes context; second turn (resumed) recalls it."""
    options1 = ClaudeAgentOptions(
        system_prompt="Reply concisely. Remember anything I tell you.",
        model="claude-haiku-4-5-20251001",
        max_turns=1,
    )
    sid = None
    async for msg in query(prompt=_user_msg("My name is Sam."), options=options1):
        sid = getattr(msg, "session_id", None) or sid
    assert sid, "no session_id captured"

    options2 = ClaudeAgentOptions(
        system_prompt="Reply concisely.",
        model="claude-haiku-4-5-20251001",
        max_turns=1,
        resume=sid,
        continue_conversation=True,
    )
    final = ""
    async for msg in query(prompt=_user_msg("What is my name?"), options=options2):
        if isinstance(msg, AssistantMessage):
            for b in getattr(msg, "content", []) or []:
                final += getattr(b, "text", "")
    assert "Sam" in final, f"resume failed to recall name; got: {final!r}"
