# agent/conflict_resolver/sdk_runner.py
"""Run the Claude Agent SDK with the conflict-resolution prompt.

Reuses the audit hooks and policy engine from agent.audit_hook and
agent.auto_mode so every git/edit/bash call lands in audit_log keyed by
actor='conflict-resolver'.

Returns a structured outcome (ResolverOutcome) the harness uses to decide
whether to push, retry, or finalize as failed.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from claude_agent_sdk import ClaudeAgentOptions, query

from agent.audit_hook import (
    make_audited_policy,
)
from agent.auto_mode import AutonomyLevel

logger = logging.getLogger(__name__)

_STREAM_CLOSE_TIMEOUT_MS = "1800000"


def _ensure_stream_close_timeout() -> None:
    """Set ``CLAUDE_CODE_STREAM_CLOSE_TIMEOUT`` on the current process env.

    This module is the last caller of the SDK's one-shot ``query()`` API
    after issue #384's ClaudeSDKClient migration. The bundled CLI begins a
    stdin-close countdown after emitting its first ResultMessage; once
    stdin closes, every PreToolUse / PostToolUse hook callback raises
    ``Error: Stream closed`` (cli.js:7552 sendRequest). The launcher used
    to set this env var globally (PR #371); after #392 it sets nothing,
    and modules that still rely on the hook lifecycle own the setter.

    Called from :func:`run_resolver` rather than at module import time
    so that importing this module for tests (or for symbol inspection
    via ``inspect.getsource``) does not mutate the process environment
    and leak state into other tests.
    """
    # ``setdefault`` (not ``[...] = ...``) respects operator overrides:
    # a debug session can ``CLAUDE_CODE_STREAM_CLOSE_TIMEOUT=60000`` to
    # exercise the stdin-close path and this helper won't trample it.
    os.environ.setdefault("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", _STREAM_CLOSE_TIMEOUT_MS)


@dataclass
class ResolverOutcome:
    """What the SDK run produced."""
    completed: bool                # the model exited cleanly
    tokens_input: int
    tokens_output: int
    tokens_total: int
    last_text: str | None          # last assistant text — used as failure reason on retry
    error: str | None              # set when SDK errored


async def _single_user_message_stream(prompt: str):
    """Wrap a single user prompt as the streaming-mode async iterable the
    SDK requires when ``can_use_tool`` is set.

    The SDK refuses a plain string prompt + ``can_use_tool`` callback with::

        can_use_tool callback requires streaming mode. Please provide
        prompt as an AsyncIterable instead of a string.

    (see ``claude_agent_sdk/_internal/client.py`` ~line 55). The shape the
    SDK expects per its docstring at ``claude_agent_sdk/query.py:46-53`` is
    ``{"type": "user", "message": {"role": "user", "content": ...}}``.

    Wrapping at the runner seam keeps the public ``run_resolver`` API
    string-only — callers still pass a rendered prompt string.
    """
    yield {
        "type": "user",
        "message": {"role": "user", "content": prompt},
    }


async def run_resolver(
    *,
    prompt: str,
    workspace: str,
    run_id: str,
    model: str,
    max_turns: int,
    max_budget_usd: float | None = None,
) -> ResolverOutcome:
    """Run the resolver inside `workspace`. Tool calls audited as
    actor='conflict-resolver'.
    """
    # #392: set the SDK stream-close timeout for this caller's lifetime.
    # Side effect deferred from import time to here so module-import
    # tests don't leak env state across the suite.
    _ensure_stream_close_timeout()

    options = ClaudeAgentOptions(
        cwd=workspace,
        env={"GITHUB_REPO": ""},  # set by caller via os.environ if desired
        allowed_tools=["Read", "Bash", "Edit", "Write", "Glob", "Grep"],
        max_turns=max_turns,
        model=model,
        can_use_tool=make_audited_policy(
            run_id=run_id,
            level=AutonomyLevel.AUTO,  # resolver runs autonomously by design
            agent_id="conflict-resolver",
        ),
        max_budget_usd=max_budget_usd,
    )

    tokens_input = 0
    tokens_output = 0
    last_text: str | None = None
    error: str | None = None
    completed = False

    try:
        # TODO(v1.1): track which model the SDK actually used per turn (it
        # may fall back from Opus -> Sonnet -> Haiku). The caller currently
        # records `model_used=args.model` (the configured primary), not the
        # model that produced the work — review finding #5. To fix, capture
        # `getattr(message, "model", None)` on each assistant message and
        # plumb the most-recent value back to record_attempt_finish.
        #
        # Stream the prompt as an AsyncIterable because ``can_use_tool``
        # above pins us into streaming mode — see
        # :func:`_single_user_message_stream` for the shape.
        async for message in query(
            prompt=_single_user_message_stream(prompt),
            options=options,
        ):
            mtype = getattr(message, "type", None)
            if mtype == "assistant":
                usage = getattr(message, "usage", None) or {}
                tokens_input += int(usage.get("input_tokens", 0) or 0)
                tokens_output += int(usage.get("output_tokens", 0) or 0)
                # Capture the latest text response so a failure mode (e.g.
                # "I cannot resolve this safely") shows up as the prior
                # failure reason on retry.
                content = getattr(message, "content", None)
                if content:
                    last_text = str(content)[:4000]
            elif mtype == "result":
                completed = not getattr(message, "is_error", False)
    except Exception as exc:  # pragma: no cover — defensive
        error = str(exc)[:500]
        logger.warning("conflict resolver SDK error: %s", exc)

    return ResolverOutcome(
        completed=completed,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        tokens_total=tokens_input + tokens_output,
        last_text=last_text,
        error=error,
    )
