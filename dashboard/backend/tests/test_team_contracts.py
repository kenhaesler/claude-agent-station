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


SAMPLE_CONTRACTS = """\
# Team Contracts

## API Routes

- GET /api/depreciation (owner: backend) — { depreciation: number, residualValue: number }
- POST /api/lifecycle (owner: backend) — { ok: bool }

## Field Names

- purchase_cost: purchaseCost
- salvage_value: salvageValue

## Response Shapes

- /api/licenses: { licenses: [License] }

## Enum Values

- LicenseStatus: active, expiring, expired

## Route Ownership

- /api/depreciation: backend
- /api/lifecycle: backend
- /api/licenses: backend
"""


def test_parse_contracts_extracts_all_sections(tmp_path):
    """Well-formed file parses every section correctly."""
    from agent.team_contracts import parse_contracts, CONTRACTS_FILENAME
    (tmp_path / CONTRACTS_FILENAME).write_text(SAMPLE_CONTRACTS)

    contracts = parse_contracts(tmp_path)

    assert contracts is not None
    assert len(contracts.routes) == 2
    assert contracts.routes[0].method == "GET"
    assert contracts.routes[0].path == "/api/depreciation"
    assert contracts.routes[0].owner == "backend"
    assert contracts.field_names["purchase_cost"] == "purchaseCost"
    assert contracts.field_names["salvage_value"] == "salvageValue"
    assert contracts.response_shapes["/api/licenses"] == "{ licenses: [License] }"
    assert contracts.enum_values["LicenseStatus"] == ["active", "expiring", "expired"]
    assert contracts.route_ownership["/api/depreciation"] == "backend"


def test_parse_contracts_ignores_unknown_sections(tmp_path):
    """Sections outside the schema are silently skipped."""
    from agent.team_contracts import parse_contracts, CONTRACTS_FILENAME
    text = "# Team Contracts\n\n## Unrelated Section\n- noise\n\n## Field Names\n- key: chosenName\n"
    (tmp_path / CONTRACTS_FILENAME).write_text(text)

    contracts = parse_contracts(tmp_path)
    assert contracts is not None
    assert contracts.field_names == {"key": "chosenName"}
    assert contracts.routes == []


def test_parse_contracts_missing_sections_yield_empty_containers(tmp_path):
    """A file containing only ``## Field Names`` returns empty other sections."""
    from agent.team_contracts import parse_contracts, CONTRACTS_FILENAME
    (tmp_path / CONTRACTS_FILENAME).write_text("## Field Names\n- a: A\n")

    contracts = parse_contracts(tmp_path)
    assert contracts is not None
    assert contracts.field_names == {"a": "A"}
    assert contracts.routes == []
    assert contracts.response_shapes == {}
    assert contracts.enum_values == {}
    assert contracts.route_ownership == {}


def test_parse_contracts_empty_file_returns_empty_contracts(tmp_path):
    """An empty markdown file parses to empty TeamContracts (NOT None)."""
    from agent.team_contracts import parse_contracts, CONTRACTS_FILENAME
    (tmp_path / CONTRACTS_FILENAME).write_text("")
    contracts = parse_contracts(tmp_path)
    assert contracts is not None
    assert contracts.routes == []
    assert contracts.field_names == {}
