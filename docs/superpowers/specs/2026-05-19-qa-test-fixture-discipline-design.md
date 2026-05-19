# QA Test-Fixture Discipline — Design

**Issue:** Closes #458 (QA tests pass on QA branch but break on merge — test-fixture contracts not enforced)
**Author:** Claude Opus 4.7 (1M context)
**Date:** 2026-05-19
**Target branch:** PR targets `dev` (per project policy)

## Problem

PR #457 (sibling-coordination) prevents cross-team contract conflicts on API response shapes, field names, route ownership, and enum values. Verification run `run-20260519T070352Z` produced 3× APPROVE_INTEGRATION — a success vs. the prior 3× REJECT / 2× REJECT outcomes.

But the manager flagged a residual problem in the QA verdict:

> *the new activity timeline test expects body/newValue fields which will break when the backend branch merges (backend now returns content/author). This cross-branch integration issue needs resolution after merge.*

The contract had the right info — `ActivityEntry`'s response shape explicitly included `content` and `author`. QA followed the contract for the enum-value rule (`expiring_soon`) but its test assertions used `body`/`newValue` anyway. The manager approved each branch individually, but the merged result would fail QA's own tests.

So contracts.md tells teammates what the API contract IS, but doesn't enforce that QA's test assertions follow it. The system needs a stronger nudge for QA specifically.

## Goals

- QA understands explicitly that test assertions must match the contracted response shape.
- The advisory validator flags the specific failure pattern (`"test expects X but backend returns Y"`) so operators see it in the log.
- Fail-soft: no new crash paths; advisory only.

## Non-Goals

- New section in the contracts.md schema for test fixtures (the existing Response Shapes section already has the info).
- Manager downgrade-to-REJECT on cross-branch breakage (Option 2 from issue — reserved for if recurrence persists).
- Backend/frontend role-specific addenda. The failure pattern is QA-specific.
- Auto-fixing QA's test fixtures. The validator stays advisory.

## High-Level Architecture

Two complementary pieces, both lightweight:

### A — QA-specific prompt addition
In `agent/station_orchestrator.py::build_team_prompt`, extend the existing READ FIRST instruction (added by #457 at lines 1097–1100) with a paragraph scoped to QA. The lead embeds this in each teammate's spawn prompt; backend/frontend treat it as a no-op.

### B — Validator extension: `test_assertion_drift` violation type
In `agent/team_contracts.py::validate_verdict_against_contracts`, add a 4th pass that scans the manager's `reasoning` for the specific pattern of `"test expects X"` where X is NOT in any contracted response shape AND the reasoning also contains a divergence signal nearby. Heuristic by design — same approach as the existing 3 violation types, with the false-positive defenses we tightened in PR #457's re-review.

## Components

### 1. Modified: `agent/station_orchestrator.py::build_team_prompt`

Around the existing READ FIRST instruction (lines 1097–1100), append a QA-specific paragraph. Literal text added:

```
For the QA teammate specifically: when writing or modifying tests,
every assertion on an API response field MUST match the contract's
Response Shapes section. If your test expects a field name that
isn't in the contract, the test is wrong — fix the test to match
the contract. Never change source files (routes, services, models)
to match what your test happened to assert.
```

The paragraph appears unconditionally in the spawn-prompt template — backend/frontend ignore it because they aren't writing tests. No mode-gating needed (the existing READ FIRST already exists for all non-`plan_only` modes; this addendum follows the same gating).

### 2. Modified: `agent/team_contracts.py::validate_verdict_against_contracts`

Add a 4th pass after the existing field-name / route-ownership / enum-value passes:

```python
# 4. Test-assertion drift. Look for "test expects FIELD" patterns where
#    FIELD is not in any contracted response shape or field name AND a
#    divergence signal appears AFTER the trigger phrase.
violations.extend(_looks_like_test_drift(reasoning, contracts))
```

Helper `_looks_like_test_drift(reasoning: str, contracts: TeamContracts) -> list[Violation]` (~40 lines):

```python
_TEST_TRIGGER_RE = re.compile(
    r"\b(?:test|tests)\s+(?:expects?|asserts?)\s+(?P<id>[a-zA-Z_][\w]*)",
    flags=re.IGNORECASE,
)

_DIVERGENCE_SIGNALS = (
    "will break",
    "but backend returns",
    "instead of",
    "after merge",
    "cross-branch",
)


def _looks_like_test_drift(reasoning: str, contracts: TeamContracts) -> list[Violation]:
    """Detect 'test expects X' patterns where X is not contracted AND a
    divergence signal appears after the trigger phrase. Heuristic; advisory."""
    violations: list[Violation] = []
    if not reasoning:
        return violations

    # Build the set of every name the contract considers valid.
    contracted_names = set(contracts.field_names.values())
    # Response-shape values are free-form strings; extract identifier-like
    # tokens from them so the validator knows they're contract-blessed.
    for shape in contracts.response_shapes.values():
        for token in _FIELD_NAME_TOKEN_RE.findall(shape):
            contracted_names.add(token)

    lower = reasoning.lower()
    for match in _TEST_TRIGGER_RE.finditer(reasoning):
        identifier = match.group("id")
        if identifier in contracted_names:
            continue
        # The divergence signal must appear AFTER the trigger phrase.
        trigger_end = match.end()
        suffix = lower[trigger_end:]
        if not any(sig in suffix for sig in _DIVERGENCE_SIGNALS):
            continue
        violations.append(Violation(
            section="test_assertion_drift",
            expected=", ".join(sorted(contracted_names)[:5]) + ("..." if len(contracted_names) > 5 else ""),
            found=identifier,
            context=(
                f"QA test expects '{identifier}' but contract doesn't include "
                f"this field. Tests must follow the contracted response shape."
            ),
        ))
    return violations
```

False-positive defenses (mirroring the lessons from PR #457's re-review):
- Both the trigger phrase AND a divergence signal must be present — neither alone fires.
- Skip when the identifier IS contracted (no flagging contracted names against themselves).
- Divergence signal must appear AFTER the trigger phrase in the text (positional, not anywhere).
- The trigger regex requires both `"test"`/`"tests"` AND `"expects"`/`"asserts"` — generic uses of the word "test" (e.g. "all tests pass") don't trigger.

### 3. New tests in `dashboard/backend/tests/test_team_contracts.py`

Four tests:

1. **`test_validator_flags_test_assertion_drift`** — reasoning contains the verification-run pattern (`"test expects body/newValue fields which will break when the backend branch merges (backend now returns content/author)"`). Asserts a `test_assertion_drift` violation fires with `found="body"`.

2. **`test_validator_does_not_flag_benign_test_mentions`** — reasoning is `"all tests pass on the QA branch."` Asserts no `test_assertion_drift` violation.

3. **`test_validator_does_not_flag_contracted_field_in_test_phrase`** — reasoning is `"test expects purchaseCost as documented in the contract."` Asserts no `test_assertion_drift` violation. Both false-positive defenses apply here: the identifier `purchaseCost` IS contracted (skip via the `contracted_names` filter), AND no divergence signal follows (skip via the suffix check). Either defense alone is sufficient; this test relies on the contracted-name filter primarily.

4. **`test_validator_does_not_flag_divergence_without_test_trigger`** — reasoning is `"backend returns content but frontend wants body."` Asserts no `test_assertion_drift` violation (no `"test expects"` phrase). Note: the existing field-name validator may catch this separately; this test only asserts the test-drift check returns no violations.

### 4. New snapshot test in `dashboard/backend/tests/test_orchestrator_wiring.py`

`test_build_team_prompt_qa_instruction_in_read_first_block` — asserts the literal string `"For the QA teammate specifically"` appears in the returned prompt for non-`plan_only` modes.

### 5. Updated docs: `docs/architecture.md`

In the Sibling-teammate coordination subsection added by #457, extend the "Advisory validator" paragraph to mention the 4th violation type:

> The validator scans each manager verdict's `reasoning` text for contract violations: field-name mismatch, route-ownership conflict, enum-value drift, and **test-assertion drift (QA tests expecting fields not in the contract's Response Shapes)**.

## Data Flow

```
build_team_prompt
 └─► returns prompt INCLUDING the QA-specific paragraph
     └─► lead embeds in each teammate spawn (QA receives + acts on it; backend/frontend no-op)

validate_verdict_against_contracts (existing pipeline)
 ├─► pass 1: field-name conflicts (existing)
 ├─► pass 2: route-ownership conflicts (existing)
 ├─► pass 3: enum-value conflicts (existing)
 └─► pass 4: NEW — test-assertion drift via _looks_like_test_drift()
```

No new external touch points. No DB / wire-format / config changes.

## Error Handling

- `_looks_like_test_drift` is pure (no IO, no exceptions). Returns empty list on no match.
- Fail-soft already inherited from the outer `validate_verdict_against_contracts` wrapper (`try/except` in `iterate_projects`).
- New violation type is purely additive to the result list — older log consumers ignore unknown `section` values.

## Testing

### Unit tests — `dashboard/backend/tests/test_team_contracts.py` (4 new)
Listed in Component 3. All 4 must pass; all 15 prior tests must remain green.

### Snapshot test — `dashboard/backend/tests/test_orchestrator_wiring.py` (1 new)
Listed in Component 4.

### Live verification (post-merge, NOT in CI)
Trigger a run on `next-itsm` with the same set of issues (#29/#30 if still open, or any open issues that exercise the activity-timeline area). **Acceptance**: the manager's QA verdict does NOT cite a cross-branch test/API mismatch in its reasoning. If the run produces a `test_assertion_drift` violation in the log AND the QA verdict is REJECT, that's also acceptable (the validator caught it for the operator's visibility).

## Backwards Compatibility

- New prompt paragraph is unconditional within the existing non-`plan_only` READ FIRST gating. No new mode signals.
- New `Violation.section = "test_assertion_drift"` is additive — existing code paths that switch on section name still work; new code can opt to handle the new value.
- No DB / wire-format changes.

## Acceptance

- [ ] `build_team_prompt`'s READ FIRST block contains the QA-specific paragraph.
- [ ] `validate_verdict_against_contracts` calls `_looks_like_test_drift` after the existing 3 passes.
- [ ] All 4 new unit tests pass.
- [ ] All 15 prior `test_team_contracts.py` tests pass.
- [ ] Snapshot test in `test_orchestrator_wiring.py` passes.
- [ ] Broader backend sweep clean.
- [ ] `docs/architecture.md` updated.
- [ ] PR targets `dev`. Closes #458.
- [ ] Post-merge live verification on `next-itsm` (no recurrence of the test/API mismatch in QA verdicts).

## Out-of-Scope Follow-Ups

- Adding a Test Fixtures section to the contracts.md schema (defer until evidence shows the Response Shapes section is insufficient).
- Option 2: manager downgrade-to-REJECT on cross-branch breakage (reserve for if recurrence persists despite this PR).
- Backend/frontend-specific addenda (no observed failure pattern).
- Programmatic AST-level inspection of QA test files (beyond a heuristic prose validator).
