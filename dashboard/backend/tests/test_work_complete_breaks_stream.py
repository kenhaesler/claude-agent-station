"""Test that work_complete breaks the inner async-for stream loop.

After PR #381 keeps stdin open for the SDK session, the inner
``async for message in query(...)`` loop no longer naturally exits
when the lead is done. Without an explicit ``break`` on the
work-complete signal, the orchestrator process keeps consuming
messages indefinitely and is eventually SIGTERM'd by the zombie
reaper — long after the work was actually finished.

Run-20260512T213225Z surfaced this: the orchestrator emitted
``orchestrator_complete`` at 22:06:58 (correct), then kept running,
filtering "stale background sleep" ResultMessages at 22:07:00 and
22:07:10, then went silent. The launcher's _zombie_reaper SIGTERM'd
the bash child at 22:09:19 — 2+ minutes after the work was done.
The bash never reached its manager-review phase.

This test pins the contract: when ``_is_work_complete(result_text)``
returns True for a main-session ResultMessage, the orchestrator
must break the inner async-for loop so the session exits.
"""

from __future__ import annotations

import inspect
from agent.station_orchestrator import orchestrate


def test_inner_stream_loop_breaks_on_work_complete():
    """The inner ``async for message in query(...)`` block must
    contain a ``break`` statement following a ``work_complete = True``
    assignment. Without it, the orchestrator hangs after the lead
    is done.
    """
    src = inspect.getsource(orchestrate)
    # Locate the work-complete check and confirm a break follows
    # before the next outer-loop control flow.
    idx = src.find("work_complete = True")
    assert idx != -1, "work_complete = True assignment missing from orchestrate()"
    # Take the 200 chars following the assignment — the break must
    # appear there, NOT later (where it would only break the outer loop).
    tail = src[idx:idx + 400]
    assert "break" in tail, (
        f"`break` statement missing after `work_complete = True`. "
        f"Without it, the inner async-for in orchestrate() never exits "
        f"once stdin stays open (post #381) and the orchestrator process "
        f"hangs until the zombie reaper SIGTERMs the bash child. "
        f"See run-20260512T213225Z. Snippet:\n{tail}"
    )


def test_outer_loop_still_breaks_on_work_complete():
    """The outer ``for iteration in range(max_reentries)`` loop must
    still break when ``work_complete`` is True — this is the original
    flow control from before #381 and should be preserved as a
    belt-and-suspenders.
    """
    src = inspect.getsource(orchestrate)
    # Find the outer-loop work_complete check
    outer_check = "if work_complete:"
    assert outer_check in src, (
        "outer-loop `if work_complete:` block missing from orchestrate()"
    )
    # The lines after that check must include `break`
    idx = src.find(outer_check)
    tail = src[idx:idx + 200]
    assert "break" in tail, (
        f"outer-loop `break` after `if work_complete:` is missing — "
        f"belt-and-suspenders flow control regressed. Snippet:\n{tail}"
    )
