"""Tests for agent/team_contracts.py — parse + validate.

Spec: docs/superpowers/specs/2026-05-18-sibling-coordination-design.md
Issue: #456
"""

from pathlib import Path
import pytest


def test_constants_exist():
    """Module exports the expected public names."""
    from agent import team_contracts as tc
    assert tc.CONTRACTS_FILENAME == ".claude-team-contracts.md"
    assert set(tc.CONTRACT_SECTIONS) == {
        "API Routes",
        "Field Names",
        "Response Shapes",
        "Enum Values",
        "Route Ownership",
    }


def test_parse_contracts_returns_none_when_file_missing(tmp_path):
    """No contracts.md → parser returns None. Fail-soft."""
    from agent.team_contracts import parse_contracts
    assert parse_contracts(tmp_path) is None


def test_parse_contracts_returns_none_when_file_unreadable(tmp_path):
    """Binary garbage file → returns None, no exception escapes."""
    from agent.team_contracts import parse_contracts, CONTRACTS_FILENAME
    (tmp_path / CONTRACTS_FILENAME).write_bytes(b"\x00\x01\x02\xff\xfe")
    assert parse_contracts(tmp_path) is None
