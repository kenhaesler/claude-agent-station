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


def _extract_assistant_text(content) -> str | None:
    """Pull the text-block payload out of an SDK assistant message.

    ``message.content`` is a list of content blocks (see
    ``agent/station_orchestrator.py`` for the canonical walking pattern);
    each text block exposes its payload on ``.text`` (or ``["text"]``
    when the SDK hands back a dict instead of a dataclass). Conflict-
    resolver gets away with ``str(content)`` because it only uses the
    captured text as a debugging breadcrumb for retry-failure messages;
    we MUST extract the real text because the splitter's output is
    parsed as JSON. Reviewer note (PR #422): catching this without an
    integration test is hard — the unit tests mock _invoke_splitter_sdk
    wholesale — so this helper has its own coverage at the seam.
    """
    if content is None:
        return None
    blocks = content if isinstance(content, list) else [content]
    parts: list[str] = []
    for block in blocks:
        block_type = (
            getattr(block, "type", None)
            or (block.get("type") if isinstance(block, dict) else None)
        )
        if block_type != "text":
            continue
        text = (
            getattr(block, "text", None)
            or (block.get("text") if isinstance(block, dict) else None)
        )
        if text:
            parts.append(text)
    if not parts:
        return None
    return "\n".join(parts)


async def _invoke_splitter_sdk(
    *,
    issue: dict,
    run_id: str,
    repo_summary: str,
    vision: str,
    cwd: str | None = None,
) -> str:
    """Spawn the SDK session and capture the splitter's JSON output.

    Returns the raw string the splitter wrote as its final assistant
    text. Schema validation happens in :func:`run_splitter`. Patched
    wholesale in unit tests; the real SDK is exercised by PR-4's
    integration test.

    ``cwd`` is the project repo the splitter inspects with its
    read-only tool set; without it the SDK runs against the launcher's
    cwd (``/app`` in container mode), where ``git log`` / ``rg`` find
    nothing useful. PR-3's controller threads the workspace path
    through here.

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
        cwd=cwd,
        system_prompt=role_md,
        model=SPLITTER_MODEL,
        max_turns=SPLITTER_MAX_TURNS,
        permission_mode="bypassPermissions",
        allowed_tools=SPLITTER_ALLOWED_TOOLS,
    )

    # Walk every message, keep the latest assistant text, and treat the
    # result message as the terminator. The splitter prompt instructs
    # the model to emit *only* the JSON array as its final response, so
    # the last captured text is what we parse.
    last_text: str | None = None
    async for message in query(prompt=user_message, options=options):
        mtype = getattr(message, "type", None)
        if mtype == "assistant":
            text = _extract_assistant_text(getattr(message, "content", None))
            if text:
                last_text = text
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
    cwd: str | None = None,
) -> SplitDecision:
    """Return the parsed :class:`SplitDecision` for the given parent issue.

    An empty-array output is a valid result and surfaces as a
    ``SplitDecision`` with ``proposals=()`` — that's the splitter's
    "don't split; run as-is" signal. Malformed output raises
    :class:`SplitterError` (propagated from
    :func:`parse_splitter_output`).

    ``cwd`` is forwarded to :func:`_invoke_splitter_sdk`; pass the
    project's checkout root so the splitter's read-only tool set
    (``git log``, ``rg``, ``find``) inspects the right tree.
    """
    raw = await _invoke_splitter_sdk(
        issue=issue,
        run_id=run_id,
        repo_summary=repo_summary,
        vision=vision,
        cwd=cwd,
    )
    return parse_splitter_output(raw)
