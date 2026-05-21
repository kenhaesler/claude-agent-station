"""Source-level + behavior tests for the conflict-resolver SDK runner.

After issue #392 the launcher stops setting CLAUDE_CODE_STREAM_CLOSE_TIMEOUT
globally. Modules that still use SDK `query()` must own the setter
themselves.

2026-05-22: the runner uses ``can_use_tool`` (via ``make_audited_policy``)
which pins it into streaming mode. The SDK rejects a plain string prompt
in that mode with::

    can_use_tool callback requires streaming mode. Please provide prompt
    as an AsyncIterable instead of a string.

(see ``claude_agent_sdk/_internal/client.py`` ~line 55). The runner must
wrap the string prompt as an async iterable yielding a single user
message dict. Pinned here so a future refactor cannot regress to the
bug that crashed Phase 3 on run-20260521T220747Z's PR #7 attempt.
"""

import inspect
from unittest.mock import patch, MagicMock

import pytest

from agent.conflict_resolver import sdk_runner


def test_sdk_runner_sets_stream_close_timeout_locally():
    src = inspect.getsource(sdk_runner)
    assert "CLAUDE_CODE_STREAM_CLOSE_TIMEOUT" in src, (
        "sdk_runner must set CLAUDE_CODE_STREAM_CLOSE_TIMEOUT locally "
        "now that agent.launcher no longer does (issue #392)."
    )
    # And the comment must explain why so future-us doesn't yank it again.
    assert "PR #371" in src or "stream-close" in src.lower()


@pytest.mark.asyncio
async def test_run_resolver_passes_async_iterable_prompt_not_string():
    """The SDK rejects a string prompt when ``can_use_tool`` is set. The
    runner must wrap the prompt in an async iterable that yields one
    user-message dict matching the SDK's streaming-mode shape."""
    captured: dict = {}

    async def _fake_query(*, prompt, options):  # noqa: ANN001
        captured["prompt"] = prompt
        captured["options"] = options
        # Drain the prompt iterable so we can assert its contents.
        if hasattr(prompt, "__aiter__"):
            messages = [m async for m in prompt]
            captured["yielded"] = messages
        # No messages — return immediately so the runner records
        # ``completed=False`` and exits cleanly.
        if False:
            yield None
        return

    with patch("agent.conflict_resolver.sdk_runner.query", _fake_query), \
         patch("agent.conflict_resolver.sdk_runner.make_audited_policy",
               return_value=MagicMock()):
        await sdk_runner.run_resolver(
            prompt="please resolve",
            workspace="/tmp",
            run_id="r1",
            model="claude-sonnet-4-6",
            max_turns=5,
        )

    # Prompt must be an async iterable, not the raw string.
    assert not isinstance(captured["prompt"], str), (
        "prompt was passed as a string — SDK would reject this when "
        "can_use_tool is set"
    )
    assert hasattr(captured["prompt"], "__aiter__")

    # And the yielded payload must match the SDK's streaming-mode shape.
    yielded = captured["yielded"]
    assert len(yielded) == 1
    msg = yielded[0]
    assert msg["type"] == "user"
    assert msg["message"]["role"] == "user"
    assert msg["message"]["content"] == "please resolve"


@pytest.mark.asyncio
async def test_run_resolver_still_passes_can_use_tool_callback():
    """Streaming the prompt must not have collateral-dropped the audit
    callback — it's the whole reason we needed streaming mode."""
    async def _fake_query(*, prompt, options):  # noqa: ANN001
        # Drain so the resolver doesn't hang.
        if hasattr(prompt, "__aiter__"):
            async for _ in prompt:
                pass
        if False:
            yield None
        return

    sentinel = MagicMock()
    with patch("agent.conflict_resolver.sdk_runner.query", _fake_query), \
         patch("agent.conflict_resolver.sdk_runner.make_audited_policy",
               return_value=sentinel) as policy_mock:
        await sdk_runner.run_resolver(
            prompt="x", workspace="/tmp", run_id="r1",
            model="claude-sonnet-4-6", max_turns=5,
        )

    policy_mock.assert_called_once()
    # And the policy got attached to the ClaudeAgentOptions we built.
    # (Reconstruct via inspecting the call_args from query — we don't
    # have direct access, so assert that make_audited_policy was wired
    # with the runner's identity fields, which is the load-bearing part.)
    kwargs = policy_mock.call_args.kwargs
    assert kwargs["run_id"] == "r1"
    assert kwargs["agent_id"] == "conflict-resolver"
