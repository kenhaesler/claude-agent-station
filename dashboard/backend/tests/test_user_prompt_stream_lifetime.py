"""Tests for agent.station_orchestrator._user_prompt_stream.

This generator is the load-bearing fix for the SDK's stdin-close behaviour
that crippled the audit_hook on long Agent Teams runs. See:

- Failure mode: ``cli.js:7552 sendRequest`` raises ``Error("Stream closed")``
  for every hook callback that fires after stdin is closed.
- Root cause: the SDK's ``Query.stream_input`` calls
  ``wait_for_result_and_end_input`` the moment our generator returns,
  which closes stdin as soon as the first ResultMessage arrives (i.e.
  seconds into a 50-minute Agent Teams session).
- Fix: keep the generator alive by ``await``ing an anyio Event that
  never fires. ``stream_input``'s ``async for`` blocks forever, and
  stdin only closes when ``query.close()`` cancels the task group.
"""

from __future__ import annotations

import asyncio


def test_generator_yields_the_user_message_first():
    """First yield MUST be the user-message dict the SDK control protocol
    expects (type=user, message.role=user, etc.). Pinning the shape here
    so a future refactor doesn't accidentally change the wire contract.
    """
    from agent.station_orchestrator import _user_prompt_stream

    async def first():
        agen = _user_prompt_stream("hello world")
        return await agen.__anext__()

    msg = asyncio.run(first())
    assert msg["type"] == "user"
    assert msg["message"]["role"] == "user"
    assert msg["message"]["content"] == "hello world"
    # session_id is set by the SDK on the wire; we send empty to defer.
    assert msg["session_id"] == ""
    assert msg["parent_tool_use_id"] is None


def test_generator_suspends_after_first_yield_does_not_return():
    """The whole point: after yielding the user message, the generator
    MUST NOT return. If it did, the SDK would close stdin and every
    subsequent hook callback would fail with 'Stream closed'.

    Verified by trying to advance the generator past the first yield
    under a tight timeout — anext() must time out, not raise
    StopAsyncIteration.
    """
    from agent.station_orchestrator import _user_prompt_stream

    async def advance_past_first_yield():
        agen = _user_prompt_stream("hi")
        await agen.__anext__()  # consume the user message
        # Now try to advance one more step. If the generator returned,
        # this raises StopAsyncIteration immediately. If it correctly
        # suspends on anyio.Event().wait(), this hangs forever — so
        # we wrap it in a tight timeout and assert the timeout fires.
        try:
            await asyncio.wait_for(agen.__anext__(), timeout=0.5)
        except asyncio.TimeoutError:
            return "suspended"
        except StopAsyncIteration:
            return "returned"
        return "yielded-again"

    outcome = asyncio.run(advance_past_first_yield())
    assert outcome == "suspended", (
        f"Generator returned/yielded after first message: {outcome!r}. "
        f"This regresses the stdin-stays-open contract and the SDK will "
        f"close stdin as soon as the first ResultMessage arrives, "
        f"causing every PreToolUse/PostToolUse hook to fail with "
        f"'Stream closed' for the rest of the Agent Teams run."
    )


def test_generator_cancels_cleanly_when_consumer_aborts():
    """When the SDK's task group cancels the stream task (during
    ``query.close()``), the suspended ``await anyio.Event().wait()``
    must propagate the cancellation cleanly — no swallowed exceptions,
    no leaked task. Models the real teardown path.
    """
    from agent.station_orchestrator import _user_prompt_stream

    async def cancel_consumer():
        agen = _user_prompt_stream("hi")
        await agen.__anext__()

        # asyncio CancelledError is the equivalent of anyio cancellation
        # for this purpose — the generator's await must respect it.
        task = asyncio.create_task(agen.__anext__())
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return "cancelled"
        return "completed-somehow"

    outcome = asyncio.run(cancel_consumer())
    assert outcome == "cancelled", (
        f"Cancellation did not propagate: {outcome!r}"
    )
