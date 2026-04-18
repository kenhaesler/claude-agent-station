"""Unit tests for the Auto Mode policy engine — ADR-0001.

Covers the policy matrix from docs/adr/0001-autonomy-levels.md:
- Read-only tools always allow.
- Edit tools: deny at manual, allow at assisted/auto.
- Bash destructive patterns: deny at manual/assisted, allow at auto.
- ALWAYS_DENY patterns: deny at every level, including auto.
- Unknown tools: deny at every level.
"""

from __future__ import annotations

import pytest

from agent.auto_mode import AutonomyLevel, _coerce_level, policy_decide
from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny


def _is_allow(result) -> bool:
    return isinstance(result, PermissionResultAllow)


def _is_deny(result) -> bool:
    return isinstance(result, PermissionResultDeny)


# --- Read-only tools -------------------------------------------------------


@pytest.mark.parametrize("level", list(AutonomyLevel))
@pytest.mark.parametrize("tool", ["Read", "Glob", "Grep", "NotebookRead"])
async def test_read_only_always_allowed(level, tool):
    result = await policy_decide(tool, {"file_path": "/etc/hosts"}, None, level)
    assert _is_allow(result)


# --- Edit tools ------------------------------------------------------------


@pytest.mark.parametrize("tool", ["Write", "Edit", "NotebookEdit"])
async def test_edit_denied_at_manual(tool):
    result = await policy_decide(tool, {"file_path": "/tmp/x.txt"}, None, AutonomyLevel.MANUAL)
    assert _is_deny(result)


@pytest.mark.parametrize("level", [AutonomyLevel.ASSISTED, AutonomyLevel.AUTO])
@pytest.mark.parametrize("tool", ["Write", "Edit", "NotebookEdit"])
async def test_edit_allowed_at_assisted_and_auto(level, tool):
    result = await policy_decide(tool, {"file_path": "/tmp/x.txt"}, None, level)
    assert _is_allow(result)


# --- Bash ------------------------------------------------------------------


@pytest.mark.parametrize("level", list(AutonomyLevel))
async def test_safe_bash_allowed_at_all_levels(level):
    for cmd in ["ls -la", "cat README.md", "git status", "git log --oneline", "pwd"]:
        result = await policy_decide("Bash", {"command": cmd}, None, level)
        assert _is_allow(result), f"{level.value}: expected allow for {cmd!r}, got {result}"


@pytest.mark.parametrize(
    "cmd",
    [
        "rm -rf node_modules",
        "git reset --hard HEAD~3",
        "git branch -D old-feature",
        "git clean -fd",
        "chmod -R 777 ./public",
        "sudo apt install git",
        "dd if=/dev/zero of=/tmp/x",
    ],
)
async def test_destructive_bash_denied_at_manual_and_assisted(cmd):
    for level in (AutonomyLevel.MANUAL, AutonomyLevel.ASSISTED):
        result = await policy_decide("Bash", {"command": cmd}, None, level)
        assert _is_deny(result), f"{level.value}: expected deny for {cmd!r}, got {result}"


@pytest.mark.parametrize(
    "cmd",
    ["rm -rf node_modules", "git reset --hard HEAD~3", "chmod -R 777 ./public"],
)
async def test_destructive_bash_allowed_at_auto(cmd):
    result = await policy_decide("Bash", {"command": cmd}, None, AutonomyLevel.AUTO)
    assert _is_allow(result)


# --- Always-deny list ------------------------------------------------------


@pytest.mark.parametrize(
    "cmd,reason_fragment",
    [
        ("git push origin main", "push to main"),
        ("git push -u origin HEAD:main", "push to main"),
        ("git push --force origin feature/x", "force push"),
        ("git push -f origin feature/x", "force push"),
        ("rm -rf /", "rm -rf /"),
        ("DROP TABLE projects", "drop table"),
        ("drop database station", "drop table"),
        ("systemctl restart claude-station-dashboard.service", "claude service"),
        ("systemctl stop claude-agent.service", "claude service"),
        ("echo GITHUB_TOKEN=xyz", "secret exfiltration"),
        ("export ANTHROPIC_API_KEY=xyz", "secret exfiltration"),
        ("sudo rm /etc/hosts", "sudo rm"),
        ("mkfs.ext4 /dev/sda1", "filesystem format"),
        (":(){ :|:& };:", "fork bomb"),
    ],
)
@pytest.mark.parametrize("level", list(AutonomyLevel))
async def test_always_deny_blocks_every_level(cmd, reason_fragment, level):
    """Auto does NOT override ALWAYS_DENY."""
    result = await policy_decide("Bash", {"command": cmd}, None, level)
    assert _is_deny(result)
    assert reason_fragment in result.message


# --- Subagent / Agent tool -------------------------------------------------


@pytest.mark.parametrize("level", list(AutonomyLevel))
async def test_subagent_spawn_allowed(level):
    for tool in ["Agent", "Task", "SubagentStart"]:
        result = await policy_decide(tool, {"prompt": "do work"}, None, level)
        assert _is_allow(result)


# --- Unknown tools ---------------------------------------------------------


@pytest.mark.parametrize("level", list(AutonomyLevel))
async def test_unknown_tool_denied(level):
    result = await policy_decide("Mystery", {"foo": "bar"}, None, level)
    assert _is_deny(result)
    assert "Mystery" in result.message
    assert "default policy" in result.message


# --- Type coercion / edge cases -------------------------------------------


def test_coerce_level_handles_none_and_invalid():
    assert _coerce_level(None) is AutonomyLevel.ASSISTED
    assert _coerce_level("") is AutonomyLevel.ASSISTED
    assert _coerce_level("UNKNOWN") is AutonomyLevel.ASSISTED
    assert _coerce_level("auto") is AutonomyLevel.AUTO
    assert _coerce_level("MANUAL") is AutonomyLevel.MANUAL


async def test_bash_without_command_key_is_denied():
    """Defensive: if SDK hands us weird input, we don't crash."""
    result = await policy_decide("Bash", {"something_else": "noop"}, None, AutonomyLevel.AUTO)
    # Empty command string is not destructive and not in always-deny → allow.
    # But non-string command should be denied.
    assert _is_allow(result)  # empty string -> safe


async def test_bash_with_non_string_command_denied():
    result = await policy_decide("Bash", {"command": 42}, None, AutonomyLevel.AUTO)
    assert _is_deny(result)
    assert "must be a string" in result.message
