"""Auto Mode policy engine — ADR-0001.

Decides whether a tool invocation is allowed at a given autonomy level.
Wired into the Claude Agent SDK's `can_use_tool` callback. See
`docs/adr/0001-autonomy-levels.md` for the policy matrix.

Design goals:
- Pure function where possible — easy to unit test.
- Default-deny on unknown tools.
- Always-deny list cannot be overridden by level=auto.
- Never raise — the SDK expects a PermissionResult, not an exception.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from claude_agent_sdk.types import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

PermissionResult = PermissionResultAllow | PermissionResultDeny


class AutonomyLevel(str, Enum):
    MANUAL = "manual"
    ASSISTED = "assisted"
    AUTO = "auto"


def _coerce_level(value: str | None) -> AutonomyLevel:
    """Tolerate None / unknown strings — default to ASSISTED."""
    if not value:
        return AutonomyLevel.ASSISTED
    try:
        return AutonomyLevel(value.lower())
    except ValueError:
        return AutonomyLevel.ASSISTED


# --- Always-deny list ------------------------------------------------------
# Patterns here are rejected at every level, including Auto. Added to catch
# destructive side-effects that should never happen from an autonomous run.

ALWAYS_DENY: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"git\s+push\s+(?:\S+\s+)*(?:HEAD:)?\bmain\b"), "push to main"),
    (re.compile(r"git\s+push\s+--force"), "force push"),
    (re.compile(r"git\s+push\s+-f\b"), "force push (-f)"),
    (re.compile(r"\brm\s+-rf\s+/(\s|$)"), "rm -rf /"),
    (re.compile(r"\bDROP\s+(TABLE|DATABASE)\b", re.IGNORECASE), "drop table/database"),
    (re.compile(r"systemctl\s+(restart|stop|disable)\s+claude-"), "control claude service"),
    (
        re.compile(r"\b(AWS_SECRET|ANTHROPIC_API_KEY|GITHUB_TOKEN|STATION_WEBHOOK_SECRET)\s*="),
        "secret exfiltration",
    ),
    (re.compile(r"(?:^|\W)sudo\s+rm\b"), "sudo rm"),
    (re.compile(r"mkfs\."), "filesystem format"),
    (re.compile(r":\(\)\s*\{"), "fork bomb"),
]


# --- Destructive Bash ------------------------------------------------------
# Denied at manual/assisted, allowed at auto (still subject to ALWAYS_DENY).

DESTRUCTIVE_BASH: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+-rf\b"),
    re.compile(r"\brm\s+-fr\b"),
    re.compile(r"\bchmod\s+-R\s+777\b"),
    re.compile(r"\bgit\s+reset\s+--hard\b"),
    re.compile(r"\bgit\s+clean\s+-fd?x?\b"),
    re.compile(r"\bgit\s+branch\s+-D\b"),
    re.compile(r"(?:^|[\s;&|])sudo\b"),
    re.compile(r"\bdd\s+if="),
]


READ_ONLY_TOOLS = frozenset({"Read", "Glob", "Grep", "NotebookRead"})
EDIT_TOOLS = frozenset({"Write", "Edit", "NotebookEdit"})
BASH_TOOL = "Bash"
AGENT_TOOL = "Agent"
# Subagent spawn variants used by the SDK.
SUBAGENT_TOOLS = frozenset({"Agent", "Task", "SubagentStart"})


def _input_to_text(tool_input: dict[str, Any]) -> str:
    """Flatten a tool input dict to a searchable string for deny-list regex."""
    parts: list[str] = []
    for value in tool_input.values():
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, (int, float, bool)):
            parts.append(str(value))
    return " ".join(parts)


async def policy_decide(
    tool_name: str,
    tool_input: dict[str, Any],
    ctx: ToolPermissionContext | None,
    level: AutonomyLevel,
    *,
    run_id: str | None = None,
    agent_id: str = "lead",
) -> PermissionResult:
    """Decide whether `tool_name(tool_input)` is allowed at `level`.

    Parameters
    ----------
    tool_name
        The SDK tool name, e.g. "Bash", "Read", "Edit".
    tool_input
        The tool's argument dict (e.g. ``{"command": "ls -la"}``).
    ctx
        The SDK-provided ``ToolPermissionContext``. Unused today but required
        by the SDK callback signature; kept so hooks can evolve without a
        call-site change.
    level
        The autonomy level for the calling run.
    run_id, agent_id
        Used when ``STATION_TRAY_REFERRAL=1`` to tag a tray referral row.
        Without these, referral falls back to deny-return.
    """
    del ctx  # Reserved for future per-call context checks.

    as_text = _input_to_text(tool_input)

    # 1. Always-deny list — catches push-to-main etc. regardless of level.
    #    These are never referred; the agent must never do them.
    for pattern, reason in ALWAYS_DENY:
        if pattern.search(as_text):
            return PermissionResultDeny(
                message=f"blocked by always-deny policy: {reason}",
                interrupt=False,
            )

    # 2. Read-only tools — safe at all levels.
    if tool_name in READ_ONLY_TOOLS:
        return PermissionResultAllow()

    # 3. Subagent spawn — lead may always fan out to teammates.
    if tool_name in SUBAGENT_TOOLS:
        return PermissionResultAllow()

    # 4. Edit tools — manual refers / defers, assisted+ allows.
    if tool_name in EDIT_TOOLS:
        if level is AutonomyLevel.MANUAL:
            return await _defer_or_deny(
                level=level,
                tool_name=tool_name,
                tool_input=tool_input,
                run_id=run_id,
                agent_id=agent_id,
                reason="edits require human approval at manual level",
            )
        return PermissionResultAllow()

    # 5. Bash — split on destructive patterns.
    if tool_name == BASH_TOOL:
        cmd = tool_input.get("command") or ""
        if not isinstance(cmd, str):
            return PermissionResultDeny(
                message=f"Bash input 'command' must be a string, got {type(cmd).__name__}",
            )
        is_destructive = any(pat.search(cmd) for pat in DESTRUCTIVE_BASH)
        if is_destructive:
            if level is AutonomyLevel.AUTO:
                return PermissionResultAllow()
            snippet = cmd[:80].replace("\n", " ")
            return await _defer_or_deny(
                level=level,
                tool_name=tool_name,
                tool_input=tool_input,
                run_id=run_id,
                agent_id=agent_id,
                reason=f"destructive bash at {level.value}: {snippet}",
            )
        return PermissionResultAllow()

    # 6. Unknown tool — conservative deny.
    return PermissionResultDeny(
        message=f"unknown tool {tool_name!r}; denied by default policy",
    )


async def _defer_or_deny(
    *,
    level: AutonomyLevel,
    tool_name: str,
    tool_input: dict[str, Any],
    run_id: str | None,
    agent_id: str,
    reason: str,
) -> PermissionResult:
    """Route a policy-deferred call either to the tray (if enabled + run_id
    available) or to a straight deny. Import is lazy so tests can exercise
    the policy matrix without importing the urllib-touching referral module.
    """
    if run_id:
        try:
            from agent.tray_referral import referral_enabled, refer_to_operator
        except Exception:
            referral_enabled = lambda: False  # noqa: E731
            refer_to_operator = None  # type: ignore[assignment]

        if referral_enabled() and refer_to_operator is not None:
            return await refer_to_operator(
                run_id=run_id,
                agent_id=agent_id,
                level=level,
                tool_name=tool_name,
                tool_input=tool_input,
                reason=reason,
            )
    return PermissionResultDeny(message=reason)
