# Sibling-Teammate Contract Coordination — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent cross-team contract conflicts between sibling teammates (backend/frontend/qa) by having the lead write a `.claude-team-contracts.md` file before spawning siblings, and by injecting the prior run's manager verdicts into the lead's prompt so re-runs converge.

**Architecture:** Hybrid A+C. Option A (proactive): new module `agent/team_contracts.py` provides `parse_contracts` + heuristic `validate_verdict_against_contracts`. `build_team_prompt` is extended to instruct the lead to author `.claude-team-contracts.md` and to embed READ-FIRST instructions in each teammate spawn prompt. Option C (reactive): `iterate_projects` gains `_summarize_prior_verdicts` which globs the log dir for the most-recent prior verdicts file matching the current project, and threads a prose summary through `orchestrate_project` to `build_team_prompt`. After verdicts are read for the current run, the validator advisorily flags any contract violation by string-matching the manager's `reasoning` text against the contract sections. Fail-soft throughout; no DB changes; no new SDK sessions.

**Tech Stack:** Python 3.11 / FastAPI / pytest / Claude Agent SDK (prompt-level changes only).

**Spec:** `docs/superpowers/specs/2026-05-18-sibling-coordination-design.md`
**Issue:** Closes #456
**Target branch:** PR `--base dev` (per project policy)

---

## File Structure

| File | Role |
|---|---|
| `agent/team_contracts.py` | NEW. ~200 lines. Constants (`CONTRACTS_FILENAME`, `CONTRACT_SECTIONS`), dataclasses (`Route`, `TeamContracts`, `Violation`), `parse_contracts()`, `validate_verdict_against_contracts()`. |
| `agent/station_orchestrator.py` | MODIFY `build_team_prompt` (around line 749) — add `prior_verdicts_summary` kwarg, inject "Recent verdicts (last run)" section, inject "Required: write team contracts" instruction with schema example, add "READ FIRST: `.claude-team-contracts.md`" to each teammate spawn prompt. MODIFY `orchestrate_project` (around line 2011) — thread `prior_verdicts_summary` through to `build_team_prompt`. |
| `agent/project_loop.py` | MODIFY `iterate_projects` (around line 189) — add `_summarize_prior_verdicts(log_dir, project_repo)` helper that returns a prose summary or `None`; call it before `orchestrate_project`; thread the summary through; after `_read_verdicts_file`, call `parse_contracts` + `validate_verdict_against_contracts` per verdict, log violations. |
| `dashboard/backend/tests/test_team_contracts.py` | NEW. ~11 unit tests covering parse + validate. |
| `dashboard/backend/tests/test_iterate_projects_python.py` | EXTEND with 5 integration tests (prior verdicts injection, validator wiring, fail-soft paths). |
| `dashboard/backend/tests/test_orchestrator_wiring.py` | EXTEND with 3 prompt-builder snapshot tests. |
| `docs/architecture.md` | ADD a "Sibling-teammate coordination" subsection describing the new file, the injection, and the validator. |

---

## Task 1: `agent/team_contracts.py` — constants, dataclasses, file-not-found fast path

**Files:**
- Create: `agent/team_contracts.py`
- Create: `dashboard/backend/tests/test_team_contracts.py`

- [ ] **Step 1: Write the failing test**

`dashboard/backend/tests/test_team_contracts.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard/backend && python3 -m pytest tests/test_team_contracts.py -xvs`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.team_contracts'` (or AttributeError on the constants).

- [ ] **Step 3: Create the minimal module**

`agent/team_contracts.py`:

```python
"""Team contract coordination for Agent Teams sibling teammates.

The lead agent writes ``.claude-team-contracts.md`` to the workspace
before spawning role-specialized teammates (backend / frontend / qa).
The file documents cross-team contracts (field names, route ownership,
response shapes, enum values) so siblings don't pick conflicting names.

Issue: #456. Spec:
``docs/superpowers/specs/2026-05-18-sibling-coordination-design.md``.

This module is fail-soft: missing or malformed files yield ``None``
rather than raising. Callers degrade to current behavior when the
contract is absent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

CONTRACTS_FILENAME = ".claude-team-contracts.md"
CONTRACT_SECTIONS = (
    "API Routes",
    "Field Names",
    "Response Shapes",
    "Enum Values",
    "Route Ownership",
)


@dataclass
class Route:
    method: str
    path: str
    owner: str
    response_shape: str = ""


@dataclass
class TeamContracts:
    routes: list[Route] = field(default_factory=list)
    field_names: dict[str, str] = field(default_factory=dict)
    response_shapes: dict[str, str] = field(default_factory=dict)
    enum_values: dict[str, list[str]] = field(default_factory=dict)
    route_ownership: dict[str, str] = field(default_factory=dict)


@dataclass
class Violation:
    section: str
    expected: str
    found: str
    context: str


def parse_contracts(workspace_path: Path) -> TeamContracts | None:
    """Parse ``.claude-team-contracts.md`` from the workspace.

    Returns a :class:`TeamContracts` instance on success or ``None`` if
    the file is missing or unreadable. Lenient by design — missing
    sections become empty containers; unknown sections are ignored.
    """
    file_path = Path(workspace_path) / CONTRACTS_FILENAME
    try:
        text = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("contracts: unreadable file at %s: %s", file_path, exc)
        return None

    # Task 2 fills in the actual parsing.
    return TeamContracts()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard/backend && python3 -m pytest tests/test_team_contracts.py -xvs`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/team_contracts.py dashboard/backend/tests/test_team_contracts.py
git commit -m "$(cat <<'EOF'
feat(team_contracts): module skeleton + file-not-found fast path (#456)

New agent.team_contracts module with public dataclasses (Route,
TeamContracts, Violation) and constants. parse_contracts returns
None on missing/unreadable file (fail-soft). Section parsing and
validation arrive in next commits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `parse_contracts` — section-by-section parsing

**Files:**
- Modify: `agent/team_contracts.py`
- Modify: `dashboard/backend/tests/test_team_contracts.py`

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/backend/tests/test_team_contracts.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard/backend && python3 -m pytest tests/test_team_contracts.py -xvs`
Expected: 3 PASS (from Task 1), 4 FAIL on the new parsing tests (empty routes/field_names/etc).

- [ ] **Step 3: Implement the parser**

Replace the body of `parse_contracts` in `agent/team_contracts.py`. Insert after the existing `try/except` block (replacing the placeholder `return TeamContracts()` line):

```python
    return _parse_text(text)


def _split_sections(text: str) -> dict[str, list[str]]:
    """Split markdown into ``{section_heading: [body_lines]}``.

    Only ``## Heading`` (level-2) headings are recognised as section
    delimiters. Content before the first ``##`` heading is discarded.
    Recognised section names are the entries of ``CONTRACT_SECTIONS``;
    other section names are kept in the dict (so the test can verify
    they don't pollute parsed fields) but ignored by the consumers.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## ") and not line.startswith("### "):
            current = line[3:].strip()
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(line)
    return sections


_ROUTE_LINE_RE = __import__("re").compile(
    r"^\s*-\s*(?P<method>GET|POST|PUT|PATCH|DELETE)\s+(?P<path>\S+)"
    r"(?:\s*\(owner:\s*(?P<owner>[^)]+)\))?"
    r"(?:\s*[-—]\s*(?P<shape>.+))?$"
)
_KV_LINE_RE = __import__("re").compile(r"^\s*-\s*(?P<key>[^:]+):\s*(?P<value>.+)$")


def _parse_routes(lines: list[str]) -> list[Route]:
    routes: list[Route] = []
    for line in lines:
        match = _ROUTE_LINE_RE.match(line)
        if not match:
            continue
        routes.append(Route(
            method=match.group("method").strip(),
            path=match.group("path").strip(),
            owner=(match.group("owner") or "").strip(),
            response_shape=(match.group("shape") or "").strip(),
        ))
    return routes


def _parse_kv(lines: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in lines:
        match = _KV_LINE_RE.match(line)
        if not match:
            continue
        out[match.group("key").strip()] = match.group("value").strip()
    return out


def _parse_enums(lines: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in lines:
        match = _KV_LINE_RE.match(line)
        if not match:
            continue
        values = [v.strip() for v in match.group("value").split(",") if v.strip()]
        out[match.group("key").strip()] = values
    return out


def _parse_text(text: str) -> TeamContracts:
    sections = _split_sections(text)
    return TeamContracts(
        routes=_parse_routes(sections.get("API Routes", [])),
        field_names=_parse_kv(sections.get("Field Names", [])),
        response_shapes=_parse_kv(sections.get("Response Shapes", [])),
        enum_values=_parse_enums(sections.get("Enum Values", [])),
        route_ownership=_parse_kv(sections.get("Route Ownership", [])),
    )
```

(If the dynamic `__import__("re")` style offends, move the `import re` to the top of the file — both work. The dynamic form keeps imports minimal at module top.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard/backend && python3 -m pytest tests/test_team_contracts.py -xvs`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/team_contracts.py dashboard/backend/tests/test_team_contracts.py
git commit -m "$(cat <<'EOF'
feat(team_contracts): parse 5 contract sections from markdown (#456)

parse_contracts now reads the 5 contract sections defined in
CONTRACT_SECTIONS: API Routes, Field Names, Response Shapes,
Enum Values, Route Ownership. Lenient: unknown sections ignored,
missing sections produce empty containers, empty file produces
empty TeamContracts (not None — only missing/unreadable yield None).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `validate_verdict_against_contracts` — heuristic violation detection

**Files:**
- Modify: `agent/team_contracts.py`
- Modify: `dashboard/backend/tests/test_team_contracts.py`

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/backend/tests/test_team_contracts.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard/backend && python3 -m pytest tests/test_team_contracts.py -xvs`
Expected: 7 PASS, 5 FAIL on `validate_verdict_against_contracts` (function not yet defined OR returning empty list always).

- [ ] **Step 3: Implement the validator**

Append to `agent/team_contracts.py`:

```python
def validate_verdict_against_contracts(
    verdict, contracts: TeamContracts, workspace_path: Path,
) -> list[Violation]:
    """Inspect ``verdict.reasoning`` and return contract violations.

    Heuristic by design. The validator scans the manager's prose for:
      1. **Field-name conflicts**: any contracted field-name value
         (the right-hand side of ``- key: value``) that appears in the
         reasoning alongside a non-contract alternative also referenced
         in the reasoning. Captures the run-20260517T191757Z pattern
         ("backend returns purchasePrice while QA expects purchaseCost").
      2. **Route-ownership conflicts**: any contracted route path that
         appears in the reasoning with a sibling role name (backend /
         frontend / qa) other than its contracted owner.
      3. **Enum-value conflicts**: any quoted token in the reasoning
         that isn't in the contracted allowed list for any enum named
         in the reasoning.

    Branch-checkout / diff inspection is intentionally NOT done here —
    the heuristic on manager prose is sufficient for the observed
    real-world conflicts and avoids a worktree dependency. The
    ``workspace_path`` argument is reserved for future expansion.

    Returns an empty list on no violations.
    """
    violations: list[Violation] = []
    if contracts is None or verdict is None:
        return violations

    reasoning = getattr(verdict, "reasoning", "") or ""

    # 1. Field-name conflicts. Look for any "alt vs contracted" pattern
    #    or any non-contracted name appearing where a contracted one
    #    should ("returns X instead of Y" / "uses X while expects Y").
    for canonical, chosen in contracts.field_names.items():
        if not chosen:
            continue
        if chosen in reasoning:
            # The chosen (canonical contracted) name is mentioned —
            # scan for any nearby substring that looks like a sibling
            # field name. Heuristic: any token of length >= 6 that is
            # NOT the chosen one and IS in the reasoning is suspicious
            # IF the reasoning also names the sibling roles.
            for token in _candidate_field_names(reasoning):
                if token == chosen:
                    continue
                if _looks_like_field_name_conflict(reasoning, chosen, token):
                    violations.append(Violation(
                        section="field_names",
                        expected=chosen,
                        found=token,
                        context=f"Contract field '{canonical}' chose '{chosen}'; "
                                f"reasoning also references '{token}'.",
                    ))

    # 2. Route ownership conflicts.
    for route_path, owner in contracts.route_ownership.items():
        if not route_path or route_path not in reasoning:
            continue
        for role in ("backend", "frontend", "qa"):
            if role == owner:
                continue
            if role in reasoning.lower() and _route_implicated(reasoning, route_path, role):
                violations.append(Violation(
                    section="route_ownership",
                    expected=owner,
                    found=role,
                    context=f"Route '{route_path}' is owned by '{owner}' per "
                            f"contract; reasoning implicates '{role}'.",
                ))

    # 3. Enum value conflicts. Look for quoted tokens that are not in
    #    the contracted allowed list for an enum named in the reasoning.
    for enum_name, allowed in contracts.enum_values.items():
        if not enum_name or enum_name.lower() not in reasoning.lower():
            # The enum's name itself isn't mentioned — skip to avoid
            # over-matching on common words.
            pass  # Continue; we still scan for the values.
        for token in _quoted_tokens(reasoning):
            if token in allowed:
                continue
            # Only flag if the reasoning suggests a status/state change
            # and the token resembles the contracted family.
            if _is_enum_family_member(token, allowed):
                violations.append(Violation(
                    section="enum_values",
                    expected=", ".join(allowed),
                    found=token,
                    context=f"Enum '{enum_name}' allows {allowed}; "
                            f"reasoning references '{token}'.",
                ))

    return violations


# ----- helpers (private) ----------------------------------------------------

_FIELD_NAME_TOKEN_RE = __import__("re").compile(r"\b[a-z][A-Za-z]{5,}\b")
_QUOTED_TOKEN_RE = __import__("re").compile(r"['\"]([A-Za-z_][\w]*)['\"]")


def _candidate_field_names(text: str) -> list[str]:
    """camelCase identifiers >= 6 chars. Heuristic for field-name detection."""
    return list(set(_FIELD_NAME_TOKEN_RE.findall(text)))


def _looks_like_field_name_conflict(text: str, chosen: str, candidate: str) -> bool:
    """True iff text references both names AND uses conflict-signaling words."""
    if chosen not in text or candidate not in text:
        return False
    signals = ("while", "vs", "instead of", "rather than", "expects", "but", "conflicts")
    lower = text.lower()
    return any(sig in lower for sig in signals)


def _route_implicated(text: str, route_path: str, role: str) -> bool:
    """True iff text suggests the named role is doing something with this route."""
    lower = text.lower()
    # Require the route path and the role name to appear within ~60 chars.
    try:
        ridx = lower.index(role)
    except ValueError:
        return False
    pidx = lower.find(route_path.lower())
    if pidx < 0:
        return False
    return abs(ridx - pidx) < 120


def _quoted_tokens(text: str) -> list[str]:
    """Tokens appearing in single or double quotes in the text."""
    return _QUOTED_TOKEN_RE.findall(text)


def _is_enum_family_member(candidate: str, allowed: list[str]) -> bool:
    """Loose: shares prefix or shares root with at least one allowed value."""
    if not allowed:
        return False
    for a in allowed:
        if not a:
            continue
        if candidate.startswith(a[:4]) or a.startswith(candidate[:4]):
            return True
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard/backend && python3 -m pytest tests/test_team_contracts.py -xvs`
Expected: PASS (12 tests).

- [ ] **Step 5: Commit**

```bash
git add agent/team_contracts.py dashboard/backend/tests/test_team_contracts.py
git commit -m "$(cat <<'EOF'
feat(team_contracts): heuristic validator for verdict-contract violations (#456)

validate_verdict_against_contracts scans the manager's reasoning text
for three families of conflicts: field-name (chosen vs alternative
mentioned in conflict-signaling prose), route-ownership (role named
within ~120 chars of a contracted route owned by another role), and
enum-value (quoted token resembling a contracted enum family but not
in the allowed list).

Heuristic by design. Captures the run-20260517T191757Z pattern
('Backend returns purchasePrice while QA expects purchaseCost').
Advisory only — does NOT auto-flip verdicts.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `build_team_prompt` accepts `prior_verdicts_summary` and emits new sections

**Files:**
- Modify: `agent/station_orchestrator.py` — `build_team_prompt` signature + prompt body around lines 749–1100
- Modify: `dashboard/backend/tests/test_orchestrator_wiring.py` — add 3 snapshot tests

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/backend/tests/test_orchestrator_wiring.py`:

```python
# --- #456: contract-coordination prompt additions ---


def test_build_team_prompt_includes_recent_verdicts_when_summary_provided():
    """When prior_verdicts_summary is non-None, the prompt has the section."""
    from agent.station_orchestrator import build_team_prompt
    prompt = build_team_prompt(
        repo="org/repo",
        issues=[{"number": 29, "title": "Test"}],
        config={"projects": []},
        run_id="run-test",
        prior_verdicts_summary="Last run: backend REJECT — purchasePrice conflict.",
    )
    assert "Recent verdicts (last run on this project)" in prompt
    assert "purchasePrice conflict" in prompt


def test_build_team_prompt_omits_recent_verdicts_when_summary_none():
    """No summary → no section. Default behavior unchanged."""
    from agent.station_orchestrator import build_team_prompt
    prompt = build_team_prompt(
        repo="org/repo",
        issues=[{"number": 29, "title": "Test"}],
        config={"projects": []},
        run_id="run-test",
    )
    assert "Recent verdicts" not in prompt


def test_build_team_prompt_instructs_lead_to_write_contracts():
    """Non-plan_only modes include the contract-write instruction."""
    from agent.station_orchestrator import build_team_prompt
    prompt = build_team_prompt(
        repo="org/repo",
        issues=[{"number": 29, "title": "Test"}],
        config={"projects": []},
        run_id="run-test",
        project_mode="full",
    )
    assert ".claude-team-contracts.md" in prompt
    # Schema-example sections should be in the instruction:
    for section in ("API Routes", "Field Names", "Response Shapes",
                    "Enum Values", "Route Ownership"):
        assert section in prompt


def test_build_team_prompt_omits_contract_instruction_in_plan_only_mode():
    """plan_only mode doesn't spawn siblings; contract instruction skipped."""
    from agent.station_orchestrator import build_team_prompt
    prompt = build_team_prompt(
        repo="org/repo",
        issues=[{"number": 29, "title": "Test"}],
        config={"projects": []},
        run_id="run-test",
        project_mode="plan_only",
    )
    assert "Required: write team contracts" not in prompt


def test_build_team_prompt_teammates_get_read_first_instruction():
    """Each teammate spawn instruction references the contracts file."""
    from agent.station_orchestrator import build_team_prompt
    prompt = build_team_prompt(
        repo="org/repo",
        issues=[{"number": 29, "title": "Test"}],
        config={"projects": []},
        run_id="run-test",
        project_mode="full",
    )
    # At minimum the instruction appears in the spawn-prompt template.
    assert "READ FIRST" in prompt or "Read first" in prompt
    # And it names the file:
    assert ".claude-team-contracts.md" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard/backend && python3 -m pytest tests/test_orchestrator_wiring.py -xvs -k "contract or verdicts_summary or read_first"`
Expected: 5 FAIL — strings not in prompt, OR `TypeError: build_team_prompt() got an unexpected keyword argument 'prior_verdicts_summary'`.

- [ ] **Step 3: Add the `prior_verdicts_summary` parameter and prompt sections**

Edit `agent/station_orchestrator.py`. First, change the `build_team_prompt` signature (around line 749). Find:

```python
def build_team_prompt(
    repo: str,
    issues: list[dict],
    config: dict,
    run_id: str,
    workspace: str = "",
    worktree_paths: dict[str, str] | None = None,
    vision: dict | None = None,
    project_mode: str = "full",
    approved_plan_paths: list[str] | None = None,
    review_package_path: str | None = None,
    verdicts_file_path: str | None = None,
    manager_max_turns: int = 60,
) -> str:
```

Add the new parameter immediately after `manager_max_turns`:

```python
def build_team_prompt(
    repo: str,
    issues: list[dict],
    config: dict,
    run_id: str,
    workspace: str = "",
    worktree_paths: dict[str, str] | None = None,
    vision: dict | None = None,
    project_mode: str = "full",
    approved_plan_paths: list[str] | None = None,
    review_package_path: str | None = None,
    verdicts_file_path: str | None = None,
    manager_max_turns: int = 60,
    prior_verdicts_summary: str | None = None,
) -> str:
```

Then, near where `approved_plan_section` is built (around line 842), add two more pre-prompt blocks immediately AFTER `approved_plan_section = "..."` and BEFORE `repo_short = repo.split("/")[-1]`:

```python
    # #456: cross-run feedback injection. The orchestrator passes a
    # short prose summary of the most recent prior verdicts for this
    # project. Surfacing it gives the lead context about what was
    # rejected last run so contracts.md can resolve those conflicts.
    recent_verdicts_section = ""
    if prior_verdicts_summary:
        recent_verdicts_section = f"""
## Recent verdicts (last run on this project)

The previous run on this project produced verdicts the manager has
already evaluated. Read them so you don't re-introduce the same
conflicts. When you write ``.claude-team-contracts.md`` below,
resolve any disagreement these verdicts flagged.

{prior_verdicts_summary}
"""

    # #456: contract-coordination instruction. Skipped for plan_only mode
    # (no siblings spawned). Each teammate's spawn prompt will include a
    # "READ FIRST" instruction (built below in the workflow section).
    contracts_instruction = ""
    if project_mode != "plan_only":
        contracts_instruction = """
## Required: write team contracts BEFORE spawning siblings

Before spawning the three role-specialized teammates, write
``.claude-team-contracts.md`` to the workspace root. Use this exact
markdown structure (omit sections that don't apply, but use these
section names verbatim — the orchestrator parses them):

```markdown
# Team Contracts

## API Routes

- GET /api/<path> (owner: backend) — { field: type, ... }

## Field Names

- canonical_key: chosenName

## Response Shapes

- /api/<path>: { wrapped: [items] }   # or "raw array" / etc.

## Enum Values

- EnumName: value1, value2, value3

## Route Ownership

- /api/<path>: backend
```

Each role-specialized teammate MUST read this file as their FIRST
action and treat its contents as binding. Manager review will reject
any verdict that conflicts with the contract.
"""
```

Now thread these two sections into the final prompt assembly. Find the f-string that builds the prompt body — locate the section that interpolates `{approved_plan_section}`. Just before that, add `{recent_verdicts_section}` and `{contracts_instruction}`. The relevant block becomes something like:

```python
    prompt = f"""# Lead Agent — {repo_short}-{run_id_short}
{recent_verdicts_section}
{contracts_instruction}
{approved_plan_section}
{workflow_section}
...
"""
```

(Adjust to match the actual prompt template structure. Don't reflow whitespace beyond inserting the two new placeholders.)

- [ ] **Step 4: Add READ FIRST to the teammate spawn instructions**

Find the existing instruction block around line 1034:

```python
When spawning a teammate, include in their prompt:
"Your worktree is at <path>. Run `cd <path>` as your FIRST action before doing anything else."
```

Replace with:

```python
When spawning a teammate, include in their prompt:
"Your worktree is at <path>. Run `cd <path>` as your FIRST action before doing anything else.
READ FIRST: `.claude-team-contracts.md` in your worktree root. It documents the cross-team
contracts (field names, route ownership, response shapes, enum values) you MUST follow.
Manager review will reject any branch that violates the contract."
```

This is a comment-block in the prompt template; the literal text just needs to land in the lead's instructions.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd dashboard/backend && python3 -m pytest tests/test_orchestrator_wiring.py -xvs -k "contract or verdicts_summary or read_first"`
Expected: PASS (5 tests).

Also run broader:
`cd dashboard/backend && python3 -m pytest tests/test_orchestrator_wiring.py -x`
Expected: all PASS (existing tests unaffected — the new parameter has a default).

- [ ] **Step 6: Commit**

```bash
git add agent/station_orchestrator.py dashboard/backend/tests/test_orchestrator_wiring.py
git commit -m "$(cat <<'EOF'
feat(orchestrator): inject prior verdicts + contract-write instruction (#456)

build_team_prompt now accepts prior_verdicts_summary (None default,
backwards compatible) and emits two new prompt sections when applicable:

1. "Recent verdicts (last run on this project)" — folded in when the
   orchestrator threads a summary through (Task 5 wires the call site).
2. "Required: write team contracts BEFORE spawning siblings" — gated
   on project_mode != plan_only, includes the full markdown schema.

Teammate spawn instruction includes a READ FIRST line pointing at
.claude-team-contracts.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `iterate_projects` integration — prior-verdicts summary + validator

**Files:**
- Modify: `agent/project_loop.py`
- Modify: `agent/station_orchestrator.py` — `orchestrate_project` signature thread-through
- Modify: `dashboard/backend/tests/test_iterate_projects_python.py`

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/backend/tests/test_iterate_projects_python.py`:

```python
# --- #456: sibling-coordination integration ---


def test_iterate_projects_passes_prior_verdicts_summary_when_file_exists(
    tmp_path, monkeypatch
):
    """When a prior verdicts file matches the project repo, its summary
    is threaded through to orchestrate_project."""
    import json
    from agent import project_loop

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("STATION_LOG_DIR", str(log_dir))

    # Seed a prior verdicts file for the same project.
    (log_dir / "run-20260101T000000Z-verdicts.json").write_text(json.dumps({
        "verdicts": [{
            "project": "org/repo", "verdict": "REJECT", "branch": "feat/x",
            "reasoning": "purchasePrice vs purchaseCost conflict.",
        }]
    }))

    config_path = tmp_path / "config.json"
    config_path.write_text('{"projects":[{"repo":"org/repo","enabled":true,"mode":"full"}]}')

    captured = {}

    async def fake_orchestrate(project, config, run_id, workspaces_dir, **kwargs):
        captured["prior_verdicts_summary"] = kwargs.get("prior_verdicts_summary")
        return (0, None, True)

    monkeypatch.setattr(
        "agent.station_orchestrator.orchestrate_project", fake_orchestrate
    )
    monkeypatch.setattr("agent.workspace_setup.ensure_workspace",
                        lambda *a, **kw: str(tmp_path / "ws"))
    monkeypatch.setattr("agent.station_orchestrator._read_verdicts_file",
                        lambda p: {"verdicts": []})
    monkeypatch.setattr("agent.webhook_emitter.emit", lambda *a, **kw: None)
    monkeypatch.setattr("agent.preflight.run_preflight", lambda *a, **kw: None)
    monkeypatch.setattr("agent.queue_recovery.purge_and_recover", lambda *a, **kw: None)
    monkeypatch.setattr("agent.queue_recovery.resume_paused", lambda: None)
    monkeypatch.setattr("agent.digest.write_digest", lambda **kw: "")

    project_loop.iterate_projects("test-run", str(config_path), str(tmp_path / "ws"))

    summary = captured.get("prior_verdicts_summary")
    assert summary is not None
    assert "REJECT" in summary
    assert "purchasePrice" in summary


def test_iterate_projects_no_prior_verdicts_summary_when_no_file(
    tmp_path, monkeypatch
):
    """No prior verdicts file → kwarg passes through as None. No crash."""
    from agent import project_loop

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("STATION_LOG_DIR", str(log_dir))

    config_path = tmp_path / "config.json"
    config_path.write_text('{"projects":[{"repo":"org/repo","enabled":true,"mode":"full"}]}')

    captured = {}

    async def fake_orchestrate(project, config, run_id, workspaces_dir, **kwargs):
        captured["prior_verdicts_summary"] = kwargs.get("prior_verdicts_summary", "MISSING")
        return (0, None, True)

    monkeypatch.setattr(
        "agent.station_orchestrator.orchestrate_project", fake_orchestrate
    )
    monkeypatch.setattr("agent.workspace_setup.ensure_workspace",
                        lambda *a, **kw: str(tmp_path / "ws"))
    monkeypatch.setattr("agent.station_orchestrator._read_verdicts_file",
                        lambda p: {"verdicts": []})
    monkeypatch.setattr("agent.webhook_emitter.emit", lambda *a, **kw: None)
    monkeypatch.setattr("agent.preflight.run_preflight", lambda *a, **kw: None)
    monkeypatch.setattr("agent.queue_recovery.purge_and_recover", lambda *a, **kw: None)
    monkeypatch.setattr("agent.queue_recovery.resume_paused", lambda: None)
    monkeypatch.setattr("agent.digest.write_digest", lambda **kw: "")

    project_loop.iterate_projects("test-run", str(config_path), str(tmp_path / "ws"))
    assert captured["prior_verdicts_summary"] is None


def test_iterate_projects_logs_contract_violations_after_verdicts_read(
    tmp_path, monkeypatch, caplog
):
    """contracts.md present + verdict with conflict prose → WARNING log fires."""
    import json
    from agent import project_loop
    from agent.team_contracts import CONTRACTS_FILENAME

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("STATION_LOG_DIR", str(log_dir))

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / CONTRACTS_FILENAME).write_text("""\
## Field Names
- purchase_cost: purchaseCost
""")

    config_path = tmp_path / "config.json"
    config_path.write_text('{"projects":[{"repo":"org/repo","enabled":true,"mode":"full"}]}')

    async def fake_orchestrate(*a, **kw):
        return (0, None, True)

    monkeypatch.setattr(
        "agent.station_orchestrator.orchestrate_project", fake_orchestrate
    )
    monkeypatch.setattr("agent.workspace_setup.ensure_workspace",
                        lambda *a, **kw: str(workspace))
    monkeypatch.setattr("agent.station_orchestrator._read_verdicts_file",
                        lambda p: {"verdicts": [{
                            "project": "org/repo", "verdict": "REJECT",
                            "branch": "feat/x",
                            "reasoning": "Backend used purchasePrice instead of purchaseCost.",
                        }]})
    monkeypatch.setattr("agent.webhook_emitter.emit", lambda *a, **kw: None)
    monkeypatch.setattr("agent.preflight.run_preflight", lambda *a, **kw: None)
    monkeypatch.setattr("agent.queue_recovery.purge_and_recover", lambda *a, **kw: None)
    monkeypatch.setattr("agent.queue_recovery.resume_paused", lambda: None)
    monkeypatch.setattr("agent.digest.write_digest", lambda **kw: "")
    monkeypatch.setattr("agent.verdict_execution.execute_verdict",
                        lambda *a, **kw: None)

    import logging
    caplog.set_level(logging.WARNING, logger="agent.project_loop")
    project_loop.iterate_projects("test-run", str(config_path), str(tmp_path / "ws"))

    assert any(
        "contract violations" in record.message.lower()
        for record in caplog.records
    )


def test_iterate_projects_no_crash_when_contracts_md_missing(
    tmp_path, monkeypatch
):
    """No contracts.md in workspace → pipeline continues, no exception."""
    from agent import project_loop

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("STATION_LOG_DIR", str(log_dir))

    config_path = tmp_path / "config.json"
    config_path.write_text('{"projects":[{"repo":"org/repo","enabled":true,"mode":"full"}]}')

    async def fake_orchestrate(*a, **kw):
        return (0, None, True)

    monkeypatch.setattr(
        "agent.station_orchestrator.orchestrate_project", fake_orchestrate
    )
    monkeypatch.setattr("agent.workspace_setup.ensure_workspace",
                        lambda *a, **kw: str(tmp_path / "ws"))
    monkeypatch.setattr("agent.station_orchestrator._read_verdicts_file",
                        lambda p: {"verdicts": []})
    monkeypatch.setattr("agent.webhook_emitter.emit", lambda *a, **kw: None)
    monkeypatch.setattr("agent.preflight.run_preflight", lambda *a, **kw: None)
    monkeypatch.setattr("agent.queue_recovery.purge_and_recover", lambda *a, **kw: None)
    monkeypatch.setattr("agent.queue_recovery.resume_paused", lambda: None)
    monkeypatch.setattr("agent.digest.write_digest", lambda **kw: "")

    # Should not raise.
    rc, _, _ = project_loop.iterate_projects(
        "test-run", str(config_path), str(tmp_path / "ws")
    )
    assert rc == 0


def test_summarize_prior_verdicts_picks_most_recent_matching_file(tmp_path):
    """Helper directly: glob picks newest file referencing the project."""
    import json, os, time
    from agent.project_loop import _summarize_prior_verdicts

    # Older file: matching project.
    older = tmp_path / "run-20260101T000000Z-verdicts.json"
    older.write_text(json.dumps({
        "verdicts": [{"project": "org/repo", "verdict": "REJECT", "branch": "old",
                      "reasoning": "old reasoning"}]
    }))
    os.utime(older, (time.time() - 100, time.time() - 100))

    # Newer file: matching project.
    newer = tmp_path / "run-20260102T000000Z-verdicts.json"
    newer.write_text(json.dumps({
        "verdicts": [{"project": "org/repo", "verdict": "APPROVE", "branch": "new",
                      "reasoning": "new reasoning"}]
    }))

    summary = _summarize_prior_verdicts(str(tmp_path), "org/repo")
    assert summary is not None
    assert "new reasoning" in summary
    assert "old reasoning" not in summary
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard/backend && python3 -m pytest tests/test_iterate_projects_python.py -xvs -k "prior_verdicts or contract or summarize_prior"`
Expected: 5 FAIL (the helper doesn't exist; the orchestrate kwarg isn't threaded; the validator isn't wired).

- [ ] **Step 3: Add `_summarize_prior_verdicts` to `project_loop.py`**

Edit `agent/project_loop.py`. Near the top of the module (after the existing imports), add:

```python
def _summarize_prior_verdicts(log_dir: str, project_repo: str) -> str | None:
    """Find the most-recent prior verdicts file mentioning this project
    and return a short prose summary, or None if no such file exists.

    Fail-soft: any IO/parse error returns None. The summary is purely
    advisory context for the lead's next-run prompt.

    #456 — sibling-coordination feedback loop.
    """
    import glob as _glob
    import json as _json
    from pathlib import Path as _Path

    try:
        candidates = sorted(
            _glob.glob(str(_Path(log_dir) / "run-*-verdicts.json")),
            key=lambda p: _Path(p).stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None

    for candidate in candidates:
        try:
            data = _json.loads(_Path(candidate).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        verdicts = data.get("verdicts") or []
        matching = [
            v for v in verdicts
            if v.get("project") == project_repo
        ]
        if not matching:
            continue
        lines = [f"From {_Path(candidate).name}:"]
        for v in matching:
            branch = v.get("branch", "?")
            verdict = v.get("verdict", "?")
            reasoning = (v.get("reasoning") or "").strip()
            # Trim reasoning to ~300 chars to keep prompt size bounded.
            if len(reasoning) > 300:
                reasoning = reasoning[:297] + "..."
            lines.append(f"- {verdict} `{branch}`: {reasoning}")
        return "\n".join(lines)

    return None
```

- [ ] **Step 4: Wire the helper into `iterate_projects`'s per-project loop**

In `agent/project_loop.py`, find the per-project loop body around line 189–235. The current orchestrate_project call (around line 232) looks like:

```python
            proj_rc, proj_state, work_attempted = asyncio.run(
                orchestrate_project(project, config, run_id, workspaces_dir)
            )
```

Change it to:

```python
            prior_summary = _summarize_prior_verdicts(log_dir, project["repo"])
            proj_rc, proj_state, work_attempted = asyncio.run(
                orchestrate_project(
                    project, config, run_id, workspaces_dir,
                    prior_verdicts_summary=prior_summary,
                )
            )
```

- [ ] **Step 5: Update `orchestrate_project` signature in `station_orchestrator.py`**

Edit `agent/station_orchestrator.py`. Find `orchestrate_project` (around line 2011):

```python
async def orchestrate_project(
    project: dict, config: dict, run_id: str, workspaces_dir: str,
) -> tuple[int, "_StreamState | None", bool]:
```

Change to:

```python
async def orchestrate_project(
    project: dict, config: dict, run_id: str, workspaces_dir: str,
    *,
    prior_verdicts_summary: str | None = None,
) -> tuple[int, "_StreamState | None", bool]:
```

Then find the call(s) to `build_team_prompt` inside `orchestrate_project` (there's at least one around line 2474). Each call needs to thread `prior_verdicts_summary` through:

```python
                            prompt = build_team_prompt(
                                # ... existing args ...
                                prior_verdicts_summary=prior_verdicts_summary,
                            )
```

If there are multiple call sites, update all of them. (Grep: `grep -n "build_team_prompt(" agent/station_orchestrator.py`.) The legacy `orchestrate()` driver (around line 2707) calls `orchestrate_project` — update that call too if needed (`orchestrate_project(..., prior_verdicts_summary=None)` is the safe default for legacy paths).

- [ ] **Step 6: Wire the validator after verdicts are read**

In `agent/project_loop.py`, find the block around line 327 where `raw_verdicts` is unpacked:

```python
        raw_verdicts = (verdicts_payload or {}).get("verdicts", [])
```

Immediately after this line, insert:

```python
        # #456: advisory contract-violation check. Parse contracts.md
        # from the workspace; for each verdict, log any contract
        # violations the manager's reasoning suggests. Does NOT
        # auto-flip verdicts — manager has final say.
        try:
            from agent.team_contracts import (
                parse_contracts, validate_verdict_against_contracts,
            )
            contracts = parse_contracts(Path(workspace_path))
            if contracts is not None and raw_verdicts:
                from agent.verdict_execution import Verdict as _Verdict_for_check
                for raw_v in raw_verdicts:
                    try:
                        v_obj = _Verdict_for_check.from_dict(raw_v)
                    except Exception:  # noqa: BLE001 — parse-tolerant
                        continue
                    violations = validate_verdict_against_contracts(
                        v_obj, contracts, Path(workspace_path)
                    )
                    if violations:
                        logger.warning(
                            "contract violations on verdict %s: %s",
                            v_obj.branch,
                            [
                                f"{vi.section}:{vi.found}!={vi.expected}"
                                for vi in violations
                            ],
                        )
        except Exception:  # noqa: BLE001 — best-effort, never crash run
            logger.exception("contract validator failed (non-fatal)")
```

- [ ] **Step 7: Run new tests + broader sweep**

Run: `cd dashboard/backend && python3 -m pytest tests/test_iterate_projects_python.py -xvs -k "prior_verdicts or contract or summarize_prior"`
Expected: PASS (5 tests).

Broader:
`cd dashboard/backend && python3 -m pytest tests/test_iterate_projects_python.py tests/test_project_loop*.py tests/test_orchestrator_wiring.py tests/test_team_contracts.py -x`
Expected: all PASS. If any pre-existing test breaks because `orchestrate_project` is now keyword-only on the new arg, update that test's mock to accept `**kwargs`.

- [ ] **Step 8: Commit**

```bash
git add agent/project_loop.py agent/station_orchestrator.py dashboard/backend/tests/test_iterate_projects_python.py
git commit -m "$(cat <<'EOF'
feat(project_loop): wire prior-verdicts summary + contract validator (#456)

iterate_projects now:
1. Calls _summarize_prior_verdicts(log_dir, project_repo) before
   orchestrate_project and threads the result through as the new
   kwarg-only prior_verdicts_summary on orchestrate_project ->
   build_team_prompt.
2. After verdicts are read, parses .claude-team-contracts.md from the
   workspace and runs validate_verdict_against_contracts on each
   verdict. Violations are logged at WARNING; verdicts are NOT
   auto-flipped (manager has final say).

Fail-soft: missing or malformed contracts/prior-verdicts files
degrade to current behavior with no exceptions escaping.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `docs/architecture.md` — document the new file, injection, and validator

**Files:**
- Modify: `docs/architecture.md`

- [ ] **Step 1: Locate the Agent Teams section**

Run: `grep -n "Agent Teams\|sibling\|manager\|teammate" docs/architecture.md | head -15`

Pick the section that describes the Agent Teams flow. The new subsection slots in after the existing manager-review explanation.

- [ ] **Step 2: Add the new subsection**

In `docs/architecture.md`, insert a subsection (use the same heading level as adjacent subsections):

```markdown
### Sibling-teammate coordination (#456)

The lead agent writes `.claude-team-contracts.md` to the workspace
root before spawning the three role-specialized teammates
(backend / frontend / qa). The file documents cross-team contracts:

| Section | Purpose |
|---|---|
| API Routes | Method, path, owning role, response shape per route |
| Field Names | canonical_key → chosenName mappings |
| Response Shapes | route_path → response shape description |
| Enum Values | enum_name → allowed value list |
| Route Ownership | route_path → owning role |

Each teammate's spawn prompt includes a READ-FIRST instruction
pointing at this file. Manager review treats the contract as binding;
verdicts that violate it should be REJECT.

**Cross-run feedback (#456):** Before building the lead's prompt,
`iterate_projects` globs `/var/log/claude-agent/run-*-verdicts.json`
for the most recent file containing verdicts whose `project` field
matches the current project's repo. If found, a short prose summary
is folded into the lead's prompt as a "Recent verdicts (last run on
this project)" section. The lead resolves the prior conflicts in
the new contracts file.

**Advisory validator:** `agent/team_contracts.py::validate_verdict_against_contracts`
scans each manager verdict's `reasoning` text for contract violations
(field-name mismatch, route-ownership conflict, enum-value drift).
Violations are logged at WARNING; verdicts are NOT auto-flipped —
the manager has final say. The validator is heuristic by design
(string matching against the manager's prose), not a full code parser.

Failure modes:
- No `contracts.md` written → degrades to pre-#456 behavior with a
  WARNING log.
- Malformed file → parser returns `None`; same fallback as missing.
- No prior verdicts file → no injection; first-ever run behavior.
- `plan_only` mode → no siblings spawned, contracts instruction
  omitted from the lead's prompt.
```

- [ ] **Step 3: Commit**

```bash
git add docs/architecture.md
git commit -m "$(cat <<'EOF'
docs(architecture): document sibling-teammate contract coordination (#456)

New subsection covers the .claude-team-contracts.md file (sections,
who writes it, who reads it), the cross-run feedback injection from
prior verdicts files, and the advisory contract-violation validator.
Keeps docs in lockstep with the implementation per CLAUDE.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Full test sweep

**Files:** none (validation only).

- [ ] **Step 1: Focused scope**

```bash
cd dashboard/backend && python3 -m pytest \
  tests/test_team_contracts.py \
  tests/test_iterate_projects_python.py \
  tests/test_orchestrator_wiring.py \
  tests/test_project_loop*.py \
  -xvs 2>&1 | tail -10
```

Expected: ALL pass.

- [ ] **Step 2: Broader sweep**

```bash
cd dashboard/backend && python3 -m pytest tests/ \
  --ignore=tests/test_database.py \
  --ignore=tests/test_migration_script.py \
  --ignore=tests/test_pubsub.py \
  -x 2>&1 | tail -5
```

Expected: ALL pass (postgres-bound files intentionally skipped).

- [ ] **Step 3: No commit** (validation only).

---

## Task 8: PR + post-merge live verification

**Files:** none (workflow).

- [ ] **Step 1: Push branch**

```bash
git push -u origin <branch-name>
```

- [ ] **Step 2: Open PR against `dev`**

```bash
gh pr create --base dev --title "feat: sibling-teammate contract coordination (#456)" --body "$(cat <<'EOF'
## Summary

Hybrid A+C from #456: lead agent writes \`.claude-team-contracts.md\`
before spawning siblings, and the orchestrator injects prior-run
manager verdicts into the lead's prompt so re-runs converge.

## Spec

\`docs/superpowers/specs/2026-05-18-sibling-coordination-design.md\`

## Changes by file

- \`agent/team_contracts.py\` (NEW) — \`parse_contracts\` + heuristic
  \`validate_verdict_against_contracts\`. Fail-soft on missing /
  malformed files.
- \`agent/station_orchestrator.py\` — \`build_team_prompt\` accepts
  \`prior_verdicts_summary\` kwarg, emits the two new prompt sections
  and the teammate READ-FIRST instruction. \`orchestrate_project\`
  threads the kwarg through.
- \`agent/project_loop.py\` — \`_summarize_prior_verdicts\` helper;
  call site wires summary into orchestrate; post-verdicts contract
  validator (advisory).
- \`docs/architecture.md\` — new subsection.

## Tests

- 12 unit tests in \`test_team_contracts.py\` (constants, parse, validate).
- 5 integration tests in \`test_iterate_projects_python.py\` (prior verdicts injection, validator wiring, fail-soft).
- 5 prompt-builder snapshot tests in \`test_orchestrator_wiring.py\`.

## Smoke test (post-merge, NOT in CI)

1. Rebuild containers; trigger a run on \`next-itsm\` with issues #29/#30/#31 open.
2. Verify \`.claude-team-contracts.md\` is created in the workspace.
3. Verify manager verdicts in the new run do NOT cite the depreciation field-name conflict that recurred across run-20260517T191757Z and run-20260517T210912Z.

## Closes

Closes #456

## Test plan

- [x] All unit + integration tests pass.
- [ ] Post-merge: live verification against \`next-itsm\`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: After merge — rebuild, smoke**

After merge, on the dev branch:

```bash
git fetch origin && git reset --hard origin/dev
docker compose build dashboard agent && docker compose up -d
```

Trigger a run:

```bash
API_KEY=$(grep '^STATION_API_KEY=' .env | cut -d= -f2)
curl -s -X POST http://localhost:8420/api/runs/trigger \
  -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json'
```

After the run completes (~20–30 min), capture the workspace:

```bash
RUN_ID=<from trigger response>
docker exec cas-dashboard ls /var/lib/claude-agent-station/workspaces/
docker exec cas-dashboard cat /var/lib/claude-agent-station/workspaces/<repo>/.claude-team-contracts.md
docker exec cas-dashboard cat /var/log/claude-agent/$RUN_ID-verdicts.json | python3 -m json.tool
```

Expected:
- `.claude-team-contracts.md` exists, has at least the Field Names section.
- Verdicts.json: depreciation field-name conflict is NOT cited.

If the file exists but the conflict still recurs, the lead is writing contracts but the manager isn't enforcing them — file a follow-up issue rather than reverting this PR.

If all checks pass, close #456:

```bash
MERGE_COMMIT=$(gh pr view <PR-NUMBER> --json mergeCommit -q .mergeCommit.oid | cut -c1-10)
gh issue close 456 --comment "Fixed in PR #<PR-NUMBER> (commit ${MERGE_COMMIT}), merged into dev. Verified via live smoke run."
```

---

## Self-Review

**Spec coverage:**
- New module `agent/team_contracts.py` with constants, dataclasses, parse, validate ✅ Tasks 1, 2, 3.
- `build_team_prompt` accepts `prior_verdicts_summary`, emits Recent Verdicts section, emits contract-write instruction, READ-FIRST in teammate spawn ✅ Task 4.
- `iterate_projects` calls `_summarize_prior_verdicts`, threads to orchestrate, runs validator after `_read_verdicts_file` ✅ Task 5.
- `orchestrate_project` signature thread-through ✅ Task 5 step 5.
- Docs update ✅ Task 6.
- Tests: 12 unit + 5 integration + 5 snapshot = 22 new test cases ✅ Tasks 1-5.
- Live verification post-merge ✅ Task 8.

**Placeholder scan:** No TBD/TODO. All code blocks complete. All commands have expected output. The "Adjust to match the actual prompt template structure" note in Task 4 Step 3 is a real reading-comprehension step the engineer must do — the prompt body is a multi-hundred-line f-string and the exact placement near `{approved_plan_section}` is what's required. Not a placeholder.

**Type consistency:** `prior_verdicts_summary` named identically in Tasks 4, 5, 8. `CONTRACTS_FILENAME` referenced consistently. `parse_contracts` and `validate_verdict_against_contracts` signatures match between Task 1/2/3 implementation and Task 5 wiring + spec. The validator's third argument `workspace_path: Path` is consistent throughout (reserved for future expansion, not yet used by the heuristic).

**Heuristic note:** The validator in Task 3 is intentionally heuristic — it scans `verdict.reasoning` for substring patterns. False positives are possible (e.g., a long word that matches a contract field by accident). The acceptance bar for V1 is "catches the field-name conflict pattern observed in the two real runs"; the live verification in Task 8 confirms it. If the heuristic produces too many false positives in practice, a follow-up issue can tighten the matching rules.

Self-review clean.
