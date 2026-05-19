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


def _make_verdict(reasoning: str, branch: str = "feat/test"):
    """Tiny verdict factory — only fields the validator inspects."""
    from agent.verdict_execution import Verdict
    return Verdict(
        project="org/repo",
        issue_number=None,
        verdict="REJECT",
        branch=branch,
        reasoning=reasoning,
    )


def test_validator_returns_empty_when_verdict_matches_all_contracts(tmp_path):
    """Verdict whose reasoning doesn't conflict with any contract → no violations."""
    from agent.team_contracts import (
        parse_contracts, validate_verdict_against_contracts, CONTRACTS_FILENAME,
    )
    (tmp_path / CONTRACTS_FILENAME).write_text(SAMPLE_CONTRACTS)
    contracts = parse_contracts(tmp_path)
    v = _make_verdict("Backend implements depreciation correctly with purchaseCost field.")
    violations = validate_verdict_against_contracts(v, contracts, tmp_path)
    assert violations == []


def test_validator_flags_field_name_conflict(tmp_path):
    """The manager's reasoning names BOTH the contracted name and a conflicting one."""
    from agent.team_contracts import (
        parse_contracts, validate_verdict_against_contracts, CONTRACTS_FILENAME,
    )
    (tmp_path / CONTRACTS_FILENAME).write_text(SAMPLE_CONTRACTS)
    contracts = parse_contracts(tmp_path)
    # Real-world style reasoning from run-20260517T191757Z:
    v = _make_verdict(
        "Backend returns purchasePrice while QA expects purchaseCost"
    )
    violations = validate_verdict_against_contracts(v, contracts, tmp_path)
    assert any(
        vi.section == "field_names" and "purchaseCost" in vi.expected
        for vi in violations
    )


def test_validator_flags_route_ownership_conflict(tmp_path):
    """Reasoning names two siblings owning the same route."""
    from agent.team_contracts import (
        parse_contracts, validate_verdict_against_contracts, CONTRACTS_FILENAME,
    )
    (tmp_path / CONTRACTS_FILENAME).write_text(SAMPLE_CONTRACTS)
    contracts = parse_contracts(tmp_path)
    v = _make_verdict(
        "Frontend created /api/depreciation route but backend owns it per contract."
    )
    violations = validate_verdict_against_contracts(v, contracts, tmp_path)
    assert any(vi.section == "route_ownership" for vi in violations)


def test_validator_flags_enum_value_conflict(tmp_path):
    """Reasoning names an enum value not in the contracted list."""
    from agent.team_contracts import (
        parse_contracts, validate_verdict_against_contracts, CONTRACTS_FILENAME,
    )
    (tmp_path / CONTRACTS_FILENAME).write_text(SAMPLE_CONTRACTS)
    contracts = parse_contracts(tmp_path)
    v = _make_verdict(
        "QA changed license status from 'expiring' to 'expiring_soon'."
    )
    violations = validate_verdict_against_contracts(v, contracts, tmp_path)
    assert any(
        vi.section == "enum_values" and "expiring_soon" in vi.found
        for vi in violations
    )


def test_violation_message_format(tmp_path):
    """Violation includes section + expected + found + context strings."""
    from agent.team_contracts import (
        parse_contracts, validate_verdict_against_contracts, CONTRACTS_FILENAME,
    )
    (tmp_path / CONTRACTS_FILENAME).write_text(SAMPLE_CONTRACTS)
    contracts = parse_contracts(tmp_path)
    v = _make_verdict("Backend used purchasePrice instead of the agreed purchaseCost.")
    violations = validate_verdict_against_contracts(v, contracts, tmp_path)
    assert violations  # at least one
    vi = violations[0]
    assert vi.section
    assert vi.expected
    assert vi.found
    assert vi.context


def test_validator_no_false_positive_on_benign_approve_reasoning(tmp_path):
    """Common APPROVE-style prose that names contracted fields must not fire."""
    from agent.team_contracts import (
        parse_contracts, validate_verdict_against_contracts, CONTRACTS_FILENAME,
    )
    (tmp_path / CONTRACTS_FILENAME).write_text(SAMPLE_CONTRACTS)
    contracts = parse_contracts(tmp_path)
    v = _make_verdict(
        "Backend implemented purchaseCost correctly, but should add tests for salvageValue."
    )
    violations = validate_verdict_against_contracts(v, contracts, tmp_path)
    assert violations == [], (
        f"Benign reasoning produced violations: {violations}"
    )


def test_validator_does_not_flag_frontend_as_route_consumer(tmp_path):
    """Frontend consuming a backend-owned route is NOT a violation."""
    from agent.team_contracts import (
        parse_contracts, validate_verdict_against_contracts, CONTRACTS_FILENAME,
    )
    (tmp_path / CONTRACTS_FILENAME).write_text(SAMPLE_CONTRACTS)
    contracts = parse_contracts(tmp_path)
    v = _make_verdict(
        "Backend implemented /api/depreciation correctly. Frontend consumes it."
    )
    violations = validate_verdict_against_contracts(v, contracts, tmp_path)
    # No route-ownership violation: frontend as consumer is fine.
    assert not any(vi.section == "route_ownership" for vi in violations)


def test_validator_does_not_flag_two_contracted_field_names_together(tmp_path):
    """Mentioning TWO contracted field names in one sentence is benign."""
    from agent.team_contracts import (
        parse_contracts, validate_verdict_against_contracts, CONTRACTS_FILENAME,
    )
    (tmp_path / CONTRACTS_FILENAME).write_text(SAMPLE_CONTRACTS)
    contracts = parse_contracts(tmp_path)
    v = _make_verdict(
        "Reviewed: no issues with purchaseCost or salvageValue. Both look fine."
    )
    violations = validate_verdict_against_contracts(v, contracts, tmp_path)
    assert violations == [], (
        f"Two contracted names together produced violations: {violations}"
    )


# --- #458: test_assertion_drift violation type ---


def test_validator_flags_test_assertion_drift(tmp_path):
    """QA verdict reasoning matching the run-20260519T070352Z pattern:
    'test expects body/newValue fields which will break when the backend
    branch merges (backend now returns content/author)' must flag a
    test_assertion_drift violation with found='body'."""
    from agent.team_contracts import (
        parse_contracts, validate_verdict_against_contracts, CONTRACTS_FILENAME,
    )
    # Contract uses 'content' and 'author' in the response shape; QA tests
    # asserting on 'body' / 'newValue' is the drift case.
    contracts_text = """\
## Field Names
- comment_body: content
- comment_author: author

## Response Shapes
- /api/tickets/[id]/activity: { id, type, author, content, createdAt }
"""
    (tmp_path / CONTRACTS_FILENAME).write_text(contracts_text)
    contracts = parse_contracts(tmp_path)
    v = _make_verdict(
        "QA branch test expects body fields which will break when the "
        "backend branch merges (backend now returns content/author)."
    )
    violations = validate_verdict_against_contracts(v, contracts, tmp_path)
    drift = [vi for vi in violations if vi.section == "test_assertion_drift"]
    assert drift, f"Expected test_assertion_drift violation, got: {violations}"
    # The found identifier should be 'body' (the non-contracted field the
    # test asserts on).
    assert any(vi.found == "body" for vi in drift), (
        f"Expected found='body' in violations: {drift}"
    )


def test_validator_does_not_flag_benign_test_mentions(tmp_path):
    """A reasoning that mentions 'tests' in a non-drift way must not fire."""
    from agent.team_contracts import (
        parse_contracts, validate_verdict_against_contracts, CONTRACTS_FILENAME,
    )
    (tmp_path / CONTRACTS_FILENAME).write_text(SAMPLE_CONTRACTS)
    contracts = parse_contracts(tmp_path)
    v = _make_verdict("All tests pass on the QA branch.")
    violations = validate_verdict_against_contracts(v, contracts, tmp_path)
    assert not any(vi.section == "test_assertion_drift" for vi in violations), (
        f"Benign 'tests pass' produced drift violations: {violations}"
    )


def test_validator_does_not_flag_contracted_field_in_test_phrase(tmp_path):
    """When the test expects a CONTRACTED field name (no real drift),
    the test_assertion_drift check must NOT fire. Both defenses apply:
    the identifier is in the contracted set, AND no divergence signal
    follows the trigger."""
    from agent.team_contracts import (
        parse_contracts, validate_verdict_against_contracts, CONTRACTS_FILENAME,
    )
    (tmp_path / CONTRACTS_FILENAME).write_text(SAMPLE_CONTRACTS)
    contracts = parse_contracts(tmp_path)
    v = _make_verdict("Test expects purchaseCost as documented in the contract.")
    violations = validate_verdict_against_contracts(v, contracts, tmp_path)
    assert not any(vi.section == "test_assertion_drift" for vi in violations), (
        f"Test for contracted field produced drift violation: {violations}"
    )


def test_validator_does_not_flag_divergence_without_test_trigger(tmp_path):
    """A divergence signal alone (without 'test expects') must NOT trigger
    test_assertion_drift. The field-name validator may catch this case
    separately; this test only asserts the drift check stays silent."""
    from agent.team_contracts import (
        parse_contracts, validate_verdict_against_contracts, CONTRACTS_FILENAME,
    )
    (tmp_path / CONTRACTS_FILENAME).write_text(SAMPLE_CONTRACTS)
    contracts = parse_contracts(tmp_path)
    v = _make_verdict("Backend returns content but frontend wants body.")
    violations = validate_verdict_against_contracts(v, contracts, tmp_path)
    assert not any(vi.section == "test_assertion_drift" for vi in violations), (
        f"Divergence without test trigger produced drift violation: {violations}"
    )


def test_validator_drift_does_not_fire_on_stopword_identifier(tmp_path):
    """Regex must not capture English stopwords as the test-expects identifier."""
    from agent.team_contracts import (
        parse_contracts, validate_verdict_against_contracts, CONTRACTS_FILENAME,
    )
    (tmp_path / CONTRACTS_FILENAME).write_text(SAMPLE_CONTRACTS)
    contracts = parse_contracts(tmp_path)
    for stopword_reasoning in [
        "test expects the response to include data after merge",
        "test expects a valid token which will break after deployment",
        "test expects an error after merge",
    ]:
        v = _make_verdict(stopword_reasoning)
        drift = [vi for vi in validate_verdict_against_contracts(v, contracts, tmp_path)
                 if vi.section == "test_assertion_drift"]
        assert not drift, (
            f"Stopword reasoning fired drift: {stopword_reasoning!r} -> {drift}"
        )


def test_validator_drift_does_not_fire_on_technical_rationale(tmp_path):
    """'instead of' must not be a divergence signal — too common in benign prose."""
    from agent.team_contracts import (
        parse_contracts, validate_verdict_against_contracts, CONTRACTS_FILENAME,
    )
    (tmp_path / CONTRACTS_FILENAME).write_text(SAMPLE_CONTRACTS)
    contracts = parse_contracts(tmp_path)
    v = _make_verdict(
        "test expects legacyName using fieldA instead of fieldB to avoid duplication"
    )
    drift = [vi for vi in validate_verdict_against_contracts(v, contracts, tmp_path)
             if vi.section == "test_assertion_drift"]
    assert not drift, (
        f"Technical 'instead of' rationale fired drift: {drift}"
    )
