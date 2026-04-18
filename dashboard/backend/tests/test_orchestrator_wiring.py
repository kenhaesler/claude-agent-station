"""Smoke tests for Auto Mode wiring in agent/station_orchestrator.py — ADR-0001.

These do not boot the Claude Agent SDK. They verify the module-level plumbing:
- orchestrator imports AutonomyLevel and make_audited_policy
- _coerce_level maps common project-config values to the right enum
- make_audited_policy returns an awaitable that consults policy_decide
"""

from __future__ import annotations

import inspect

import pytest

from agent import station_orchestrator
from agent.audit_hook import make_audited_policy
from agent.auto_mode import AutonomyLevel, _coerce_level
from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny


def test_orchestrator_exposes_auto_mode_imports():
    """Regression guard: the orchestrator must import the policy + hook."""
    assert station_orchestrator.make_audited_policy is make_audited_policy
    assert station_orchestrator.AutonomyLevel is AutonomyLevel
    assert station_orchestrator._coerce_level is _coerce_level


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, AutonomyLevel.ASSISTED),
        ("", AutonomyLevel.ASSISTED),
        ("manual", AutonomyLevel.MANUAL),
        ("MANUAL", AutonomyLevel.MANUAL),
        ("assisted", AutonomyLevel.ASSISTED),
        ("auto", AutonomyLevel.AUTO),
        ("nonsense", AutonomyLevel.ASSISTED),
    ],
)
def test_coerce_level_via_orchestrator(raw, expected):
    assert station_orchestrator._coerce_level(raw) is expected


async def test_wired_policy_denies_push_to_main_regardless_of_level(tmp_path):
    db = tmp_path / "wire.db"
    # No schema init — best-effort audit should swallow the error.

    for level in AutonomyLevel:
        policy = make_audited_policy(
            run_id=f"run-wire-{level.value}",
            level=level,
            db_path=str(db),
        )
        decision = await policy("Bash", {"command": "git push origin main"}, None)
        assert isinstance(decision, PermissionResultDeny), (
            f"push to main must be denied at {level.value}, got {decision}"
        )


async def test_wired_policy_allows_read_at_all_levels(tmp_path):
    db = tmp_path / "wire.db"

    for level in AutonomyLevel:
        policy = make_audited_policy(
            run_id=f"run-r-{level.value}",
            level=level,
            db_path=str(db),
        )
        decision = await policy("Read", {"file_path": "/etc/hosts"}, None)
        assert isinstance(decision, PermissionResultAllow)


def test_orchestrator_options_block_contains_can_use_tool():
    """The source must wire can_use_tool=make_audited_policy(...) into
    ClaudeAgentOptions — otherwise the policy + audit never run.

    This is a file-level string assertion rather than a runtime check: we
    don't want to boot the SDK just to validate the integration.
    """
    source = inspect.getsource(station_orchestrator)
    assert "can_use_tool=make_audited_policy" in source, (
        "ClaudeAgentOptions must pass can_use_tool=make_audited_policy(...)"
    )
    assert 'agent_id="lead"' in source
