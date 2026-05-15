"""Spawn the issue-splitter SDK session and return a SplitDecision (#391).

The splitter is a single short-lived Sonnet run: read-only on the repo,
capped at 30 turns, producing a JSON array on stdout. We invoke it via
the Claude Agent SDK rather than as a bash subprocess so the SDK session
is observable in the dashboard alongside every other run.

Empty-array contract: an empty array is the splitter's explicit "run as-is"
signal. We surface that as ``SplitDecision(proposals=(), warnings=())``
rather than ``None`` — callers that need to branch on "did the splitter
choose to split" check ``decision.proposals`` and don't have to also
disambiguate ``None`` from a raised :class:`SplitterError`.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, query

from agent.issue_splitter.schema import (
    SplitDecision,
    parse_splitter_output,
)

logger = logging.getLogger(__name__)

ROLE_FILE = Path(__file__).resolve().parents[1] / "agents" / "issue-splitter.md"
PROMPT_FILE = Path(__file__).resolve().parents[1] / "prompts" / "issue-splitter.md"

SPLITTER_MODEL = "claude-sonnet-4-6"
SPLITTER_MAX_TURNS = 30
SPLITTER_ALLOWED_TOOLS = ["Read", "Glob", "Grep", "Bash"]

_STREAM_CLOSE_TIMEOUT_MS = "1800000"


def _ensure_stream_close_timeout() -> None:
    """Set ``CLAUDE_CODE_STREAM_CLOSE_TIMEOUT`` on the current process env.

    Same workaround as ``agent/conflict_resolver/sdk_runner.py``: after
    #384's ClaudeSDKClient migration the bundled CLI begins a stdin-close
    countdown once it emits its first ResultMessage; once stdin closes,
    PreToolUse / PostToolUse callbacks raise "Stream closed". Modules
    that still use the one-shot ``query()`` API own setting this.

    ``setdefault`` rather than assignment so an operator debugging the
    stdin-close path can override via the environment.
    """
    os.environ.setdefault("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", _STREAM_CLOSE_TIMEOUT_MS)


async def _invoke_splitter_sdk(
    *,
    issue: dict,
    run_id: str,
    repo_summary: str,
    vision: str,
) -> str:
    """Spawn the SDK session and capture the splitter's JSON output.

    Returns the raw string the splitter wrote as its final assistant
    text. Schema validation happens in :func:`run_splitter`. Patched
    wholesale in unit tests; the real SDK is exercised by PR-4's
    integration test.

    ``run_id`` is currently unused inside the function — it threads
    through the call signature so the eventual audit-hook plumbing
    (PR-3) can attribute tool calls to a specific splitter run without
    a follow-up signature change.
    """
    _ensure_stream_close_timeout()

    role_md = ROLE_FILE.read_text()
    prompt_md = PROMPT_FILE.read_text()
    user_message = (
        f"{prompt_md}\n\n"
        f"## Parent issue\n\n{json.dumps(issue, indent=2)}\n\n"
        f"## Repo summary\n\n{repo_summary or '(no summary)'}\n\n"
        f"## Vision\n\n{vision or '(no vision)'}\n\n"
        "Output ONLY the JSON array. No prose."
    )

    options = ClaudeAgentOptions(
        system_prompt=role_md,
        model=SPLITTER_MODEL,
        max_turns=SPLITTER_MAX_TURNS,
        permission_mode="bypassPermissions",
        allowed_tools=SPLITTER_ALLOWED_TOOLS,
    )

    # Mirror the conflict-resolver's accumulation pattern: walk every
    # message, keep the latest assistant text, and treat the result
    # message as the terminator. The splitter prompt instructs the model
    # to emit *only* the JSON array as its final response, so the last
    # captured text is what we parse.
    last_text: str | None = None
    async for message in query(prompt=user_message, options=options):
        mtype = getattr(message, "type", None)
        if mtype == "assistant":
            content = getattr(message, "content", None)
            if content:
                last_text = str(content)
        elif mtype == "result":
            # ``result`` ends the stream; nothing more to capture.
            break
    return (last_text or "").strip()


async def run_splitter(
    *,
    issue: dict,
    run_id: str,
    repo_summary: str,
    vision: str,
) -> SplitDecision:
    """Return the parsed :class:`SplitDecision` for the given parent issue.

    An empty-array output is a valid result and surfaces as a
    ``SplitDecision`` with ``proposals=()`` — that's the splitter's
    "don't split; run as-is" signal. Malformed output raises
    :class:`SplitterError` (propagated from
    :func:`parse_splitter_output`).
    """
    raw = await _invoke_splitter_sdk(
        issue=issue,
        run_id=run_id,
        repo_summary=repo_summary,
        vision=vision,
    )
    return parse_splitter_output(raw)
