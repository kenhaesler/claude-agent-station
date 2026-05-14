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
from claude_agent_sdk.types import HookMatcher

from agent.audit_hook import (
    make_audited_policy,
    make_post_tool_hook,
    make_pre_tool_hook,
)
from agent.auto_mode import AutonomyLevel

logger = logging.getLogger(__name__)

# This module is the last caller of the SDK's one-shot ``query()`` API
# after issue #384's ClaudeSDKClient migration. The bundled CLI begins a
# stdin-close countdown after emitting its first ResultMessage; once
# stdin closes, every PreToolUse / PostToolUse hook callback raises
# ``Error: Stream closed`` (cli.js:7552 sendRequest). The launcher used
# to set this env var globally (PR #371); after #392 it sets nothing,
# and modules that still rely on the hook lifecycle own the setter.
os.environ.setdefault("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", "1800000")


@dataclass
class ResolverOutcome:
    """What the SDK run produced."""
    completed: bool                # the model exited cleanly
    tokens_input: int
    tokens_output: int
    tokens_total: int
    last_text: str | None          # last assistant text — used as failure reason on retry
    error: str | None              # set when SDK errored


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
        hooks={
            "PreToolUse": [HookMatcher(hooks=[
                make_pre_tool_hook(
                    run_id=run_id,
                    actor="conflict-resolver",
                    trace_id=run_id,
                ),
            ])],
            "PostToolUse": [HookMatcher(hooks=[
                make_post_tool_hook(
                    run_id=run_id,
                    actor="conflict-resolver",
                ),
            ])],
        },
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
        async for message in query(prompt=prompt, options=options):
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
