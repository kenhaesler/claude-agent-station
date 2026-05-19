# QA Test-Fixture Discipline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the QA-discipline gap surfaced in PR #457's verification — tighten the lead's spawn prompt to require QA test assertions to match the contracted Response Shapes, and add a 4th heuristic violation type to the advisory validator that flags `"test expects X"` patterns where X is not contracted and a divergence signal follows.

**Architecture:** Two tiny touch points. (A) `agent/station_orchestrator.py::build_team_prompt` gets a QA-specific paragraph appended to the existing READ FIRST instruction at lines 1096–1100. (B) `agent/team_contracts.py::validate_verdict_against_contracts` calls a new private helper `_looks_like_test_drift` after the existing 3 violation passes. Both pieces are advisory — no auto-flipping of verdicts, no schema change to `.claude-team-contracts.md`.

**Tech Stack:** Python 3.11 / pytest / Claude Agent SDK (prompt-level changes only).

**Spec:** `docs/superpowers/specs/2026-05-19-qa-test-fixture-discipline-design.md`
**Issue:** Closes #458
**Target branch:** PR `--base dev` (per project policy)

---

## File Structure

| File | Role |
|---|---|
| `agent/station_orchestrator.py` | MODIFY the READ FIRST string at lines 1096–1100 — append a QA-specific paragraph. ~6 lines added. |
| `agent/team_contracts.py` | ADD module-level constant `_DIVERGENCE_SIGNALS`, regex `_TEST_TRIGGER_RE`, and private helper `_looks_like_test_drift`. EXTEND `validate_verdict_against_contracts` to call it after the existing 3 passes. ~45 lines added. |
| `dashboard/backend/tests/test_team_contracts.py` | ADD 4 new tests: 1 positive (real drift) + 3 negative (benign mentions). |
| `dashboard/backend/tests/test_orchestrator_wiring.py` | ADD 1 snapshot test for the new QA paragraph in the prompt. |
| `docs/architecture.md` | EXTEND the "Advisory validator" paragraph in the Sibling-teammate coordination subsection to mention the 4th violation type. |

---

## Task 1: Prompt addition — QA-specific paragraph in READ FIRST block

**Files:**
- Modify: `agent/station_orchestrator.py:1096-1100` (the existing teammate-spawn instruction block)
- Test: `dashboard/backend/tests/test_orchestrator_wiring.py` (extend with 1 snapshot test)

- [ ] **Step 1: Write the failing test**

Append to `dashboard/backend/tests/test_orchestrator_wiring.py`:

```python
def test_build_team_prompt_qa_instruction_in_read_first_block():
    """The QA-specific test-fixture instruction must appear in the
    teammate-spawn block for non-plan_only modes. #458."""
    from agent.station_orchestrator import build_team_prompt
    prompt = build_team_prompt(
        repo="org/repo",
        issues=[{"number": 29, "title": "Test"}],
        config={"projects": []},
        run_id="run-test",
        project_mode="full",
    )
    assert "For the QA teammate specifically" in prompt
    # The instruction must name the contract section it points at:
    assert "Response Shapes" in prompt
    # And state the rule plainly:
    assert "fix the test to match the contract" in prompt.lower() or \
           "fix the test" in prompt.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard/backend && python3 -m pytest tests/test_orchestrator_wiring.py::test_build_team_prompt_qa_instruction_in_read_first_block -xvs`

Expected: FAIL with `AssertionError` — the string `"For the QA teammate specifically"` is not in the prompt yet.

- [ ] **Step 3: Append the QA paragraph to the READ FIRST block**

Edit `agent/station_orchestrator.py:1096-1100`. The current block is:

```python
When spawning a teammate, include in their prompt:
"Your worktree is at <path>. Run `cd <path>` as your FIRST action before doing anything else.
READ FIRST: `.claude-team-contracts.md` in your worktree root. It documents the cross-team
contracts (field names, route ownership, response shapes, enum values) you MUST follow.
Manager review will reject any branch that violates the contract."
```

Change to:

```python
When spawning a teammate, include in their prompt:
"Your worktree is at <path>. Run `cd <path>` as your FIRST action before doing anything else.
READ FIRST: `.claude-team-contracts.md` in your worktree root. It documents the cross-team
contracts (field names, route ownership, response shapes, enum values) you MUST follow.
Manager review will reject any branch that violates the contract.

For the QA teammate specifically: when writing or modifying tests,
every assertion on an API response field MUST match the contract's
Response Shapes section. If your test expects a field name that
isn't in the contract, the test is wrong — fix the test to match
the contract. Never change source files (routes, services, models)
to match what your test happened to assert."
```

Note: this is the literal text inside the lead's spawn-prompt template. The closing `"` after `assert."` ends the multi-line quoted instruction. Keep the indentation level identical to the surrounding lines (no indentation — this is inside a Python f-string that's already at module-level indent).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard/backend && python3 -m pytest tests/test_orchestrator_wiring.py::test_build_team_prompt_qa_instruction_in_read_first_block -xvs`

Expected: PASS.

- [ ] **Step 5: Run broader prompt-builder tests to confirm no regressions**

Run: `cd dashboard/backend && python3 -m pytest tests/test_orchestrator_wiring.py -x 2>&1 | tail -5`

Expected: all PASS (~98 tests — the existing prompt tests plus the new one). If any pre-existing test fails because it does a length check or whitespace-sensitive comparison, the literal string addition is the only change; investigate and update the assertion if it was checking the old wording verbatim.

- [ ] **Step 6: Commit**

```bash
git add agent/station_orchestrator.py dashboard/backend/tests/test_orchestrator_wiring.py
git commit -m "$(cat <<'EOF'
feat(orchestrator): add QA-specific test-fixture discipline to teammate spawn (#458)

build_team_prompt's READ FIRST block now appends a paragraph scoped
to the QA teammate: every test assertion on an API response field
must match the contract's Response Shapes section. If a test expects
a field not in the contract, the test is wrong — never change source
files to match the test.

The paragraph is unconditional within the existing non-plan_only
READ FIRST gating; backend/frontend teammates ignore it because they
aren't writing tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Validator extension — `test_assertion_drift` heuristic

**Files:**
- Modify: `agent/team_contracts.py` — add `_TEST_TRIGGER_RE`, `_DIVERGENCE_SIGNALS`, `_looks_like_test_drift`; extend `validate_verdict_against_contracts` to call it.
- Test: `dashboard/backend/tests/test_team_contracts.py` (extend with 4 new tests).

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/backend/tests/test_team_contracts.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard/backend && python3 -m pytest tests/test_team_contracts.py -xvs -k "test_assertion_drift or test_drift or test_validator_flags_test or test_validator_does_not_flag_benign or test_validator_does_not_flag_contracted_field_in_test_phrase or test_validator_does_not_flag_divergence_without_test_trigger"`

Expected: 1 FAIL (the positive test — no `test_assertion_drift` violation produced because the helper doesn't exist yet). The 3 negative tests will pass vacuously because the new section never appears.

- [ ] **Step 3: Implement `_looks_like_test_drift` and wire it into the validator**

Edit `agent/team_contracts.py`. First, add the new module-level regex and constant near the other helpers (right after the existing `_QUOTED_TOKEN_RE = re.compile(...)` line around line 261):

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
```

Next, add the helper function. Place it after `_is_enum_family_member` (around line 325 after that function's body ends):

```python
def _looks_like_test_drift(reasoning: str, contracts: TeamContracts) -> list[Violation]:
    """Detect 'test expects X' patterns where X is not contracted AND a
    divergence signal appears AFTER the trigger phrase.

    Heuristic by design — same approach as the other validator passes.
    False-positive defenses:
      1. Both the trigger phrase (``test expects X`` / ``tests assert X``)
         AND a divergence signal (``will break``, ``after merge``, etc.)
         must be present. Neither alone fires.
      2. The identifier ``X`` must NOT be in any contracted name set
         (field_names values, response_shapes tokens). Mentioning a
         contracted name in a test phrase is benign.
      3. The divergence signal must appear AFTER the trigger phrase in
         the text (positional check), not anywhere.

    Issue: #458.
    """
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
        contracted_list = sorted(contracted_names)
        expected_str = ", ".join(contracted_list[:5])
        if len(contracted_list) > 5:
            expected_str += "..."
        violations.append(Violation(
            section="test_assertion_drift",
            expected=expected_str,
            found=identifier,
            context=(
                f"QA test expects '{identifier}' but contract doesn't include "
                f"this field. Tests must follow the contracted response shape."
            ),
        ))
    return violations
```

Finally, wire the helper into `validate_verdict_against_contracts`. Find the existing function body around line 155–240. After the existing 3 passes (field-name, route-ownership, enum-value), add the 4th pass just before `return violations`:

```python
    # 4. Test-assertion drift (#458). Look for "test expects FIELD" patterns
    #    where FIELD is not contracted AND a divergence signal follows.
    violations.extend(_looks_like_test_drift(reasoning, contracts))

    return violations
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard/backend && python3 -m pytest tests/test_team_contracts.py -xvs`

Expected: PASS (19 total — 15 prior + 4 new).

- [ ] **Step 5: Run broader sweep**

Run: `cd dashboard/backend && python3 -m pytest tests/test_team_contracts.py tests/test_iterate_projects_python.py tests/test_orchestrator_wiring.py -x 2>&1 | tail -5`

Expected: all PASS. Same scope as PR #457's final sweep.

- [ ] **Step 6: Empirical sanity-check on the run-20260519T070352Z verification reasoning**

Run this one-off probe to verify the helper triggers on the exact reasoning the manager wrote during verification:

```bash
cd /home/simon/Documents/claude-agent-station && python3 -c "
import sys
sys.path.insert(0, '.')
from pathlib import Path
from tempfile import TemporaryDirectory
from agent.team_contracts import parse_contracts, validate_verdict_against_contracts, CONTRACTS_FILENAME
from agent.verdict_execution import Verdict

contract = '''
## Field Names
- comment_body: content
- comment_author: author

## Response Shapes
- /api/tickets/[id]/activity: { id, type, author, content, createdAt }
'''

with TemporaryDirectory() as d:
    (Path(d) / CONTRACTS_FILENAME).write_text(contract)
    c = parse_contracts(Path(d))
    v = Verdict(
        project='x', issue_number=None, verdict='APPROVE_INTEGRATION', branch='qa',
        reasoning='the new activity timeline test expects body fields which will break when the backend branch merges (backend now returns content/author)',
    )
    violations = validate_verdict_against_contracts(v, c, Path(d))
    drift = [v for v in violations if v.section == 'test_assertion_drift']
    print(f'test_assertion_drift violations: {len(drift)}')
    for v in drift:
        print(f'  found={v.found}  context={v.context[:80]}')
"
```

Expected output:
```
test_assertion_drift violations: 1
  found=body  context=QA test expects 'body' but contract doesn't include this field...
```

If zero violations fire, the trigger regex or contracted-name filtering isn't matching — debug before committing.

- [ ] **Step 7: Commit**

```bash
git add agent/team_contracts.py dashboard/backend/tests/test_team_contracts.py
git commit -m "$(cat <<'EOF'
feat(team_contracts): add test_assertion_drift violation type to validator (#458)

validate_verdict_against_contracts now performs a 4th pass detecting
the 'test expects X' pattern where X isn't in any contracted response
shape or field name AND a divergence signal appears AFTER the trigger
phrase (e.g. 'will break', 'after merge', 'instead of').

Heuristic by design — same approach as the existing 3 violation types.
False-positive defenses:
1. Both trigger phrase AND divergence signal required.
2. Contracted identifiers are filtered.
3. Divergence signal must be positional (after trigger, not anywhere).

Empirically verified against the run-20260519T070352Z manager prose
('test expects body fields which will break when the backend branch
merges').

Advisory only — does NOT auto-flip verdicts.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Update `docs/architecture.md`

**Files:**
- Modify: `docs/architecture.md` — the Sibling-teammate coordination subsection added by PR #457.

- [ ] **Step 1: Locate the Advisory validator paragraph**

Run: `grep -n "Advisory validator\|field-name mismatch\|route-ownership conflict\|enum-value drift" docs/architecture.md | head -5`

Expected: one or two matches in the Sibling-teammate coordination subsection (around the docs added by PR #457). Note the exact line.

- [ ] **Step 2: Extend the violation-types list to include the new type**

Find the paragraph that describes the validator's violation types. It should read something like:

```markdown
**Advisory validator:** `agent/team_contracts.py::validate_verdict_against_contracts`
scans each manager verdict's `reasoning` text for contract violations
(field-name mismatch, route-ownership conflict, enum-value drift).
Violations are logged at WARNING; verdicts are NOT auto-flipped —
the manager has final say. The validator is heuristic by design
(string matching against the manager's prose), not a full code parser.
```

Change the parenthetical list from:

```
(field-name mismatch, route-ownership conflict, enum-value drift)
```

to:

```
(field-name mismatch, route-ownership conflict, enum-value drift,
test-assertion drift)
```

Also add a one-sentence inline note after the list, before "Violations are logged":

```markdown
The test-assertion drift check (#458) flags `"test expects X"`
patterns where X isn't in the contract's Response Shapes section AND
a divergence signal (`will break`, `after merge`, etc.) follows.
```

- [ ] **Step 3: Commit**

```bash
git add docs/architecture.md
git commit -m "$(cat <<'EOF'
docs(architecture): document test_assertion_drift validator pass (#458)

Extends the Sibling-teammate coordination subsection to list the
4th violation type the advisory validator now detects. Keeps docs
in lockstep with the implementation per CLAUDE.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Full test sweep

**Files:** none (validation only).

- [ ] **Step 1: Focused scope**

```bash
cd dashboard/backend && python3 -m pytest \
  tests/test_team_contracts.py \
  tests/test_orchestrator_wiring.py \
  tests/test_iterate_projects_python.py \
  -xvs 2>&1 | tail -10
```

Expected: all PASS. `test_team_contracts.py` should now show 19 tests (15 prior + 4 new). `test_orchestrator_wiring.py` should show ~98 (existing + 1 new).

- [ ] **Step 2: Broader backend sweep**

```bash
cd dashboard/backend && python3 -m pytest tests/ \
  --ignore=tests/test_database.py \
  --ignore=tests/test_migration_script.py \
  --ignore=tests/test_pubsub.py \
  -x 2>&1 | tail -5
```

Expected: ~1497 passed, 1 skipped (5 more than PR #457's final sweep).

- [ ] **Step 3: No commit** (validation only).

---

## Task 5: Push + open PR + post-merge live verification

**Files:** none (workflow).

- [ ] **Step 1: Push branch**

```bash
git push -u origin <branch-name>
```

- [ ] **Step 2: Open PR against `dev`**

```bash
gh pr create --base dev --title "feat: QA test-fixture discipline (#458)" --body "$(cat <<'EOF'
## Summary

Closes #458. Two complementary pieces:

1. **Prompt addition** — \`build_team_prompt\` appends a QA-specific paragraph to the existing READ FIRST block. QA is told plainly: test assertions must match the contract's Response Shapes section; if a test expects a non-contracted field, fix the test, not the source.

2. **Validator extension** — \`validate_verdict_against_contracts\` gains a 4th pass detecting the \`"test expects X"\` pattern where X isn't contracted and a divergence signal follows. Same heuristic approach as the existing 3 violation types; advisory only.

## Spec

\`docs/superpowers/specs/2026-05-19-qa-test-fixture-discipline-design.md\`

## Changes by file

- \`agent/station_orchestrator.py\` — 6-line addition to the teammate-spawn instruction at the READ FIRST block.
- \`agent/team_contracts.py\` — new \`_TEST_TRIGGER_RE\` / \`_DIVERGENCE_SIGNALS\` / \`_looks_like_test_drift\`. \`validate_verdict_against_contracts\` calls it after the existing 3 passes.
- \`docs/architecture.md\` — extends the Advisory validator paragraph in the Sibling-teammate coordination subsection.

## Tests

- 4 new unit tests in \`test_team_contracts.py\`: 1 positive (real drift pattern from run-20260519T070352Z verification) + 3 negative (benign test mentions, contracted field in test phrase, divergence without trigger).
- 1 new snapshot test in \`test_orchestrator_wiring.py\` covering the new QA paragraph.
- Focused scope: ~117 pass; broader sweep clean.

## Closes

Closes #458

## Test plan

- [x] Unit tests cover positive + negative cases.
- [x] Empirical probe against verification-run prose triggers the validator.
- [ ] Post-merge live verification: trigger a fresh \`next-itsm\` run; the manager's QA verdict should not cite a cross-branch test/API mismatch.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: After merge — rebuild and smoke**

```bash
git fetch origin && git reset --hard origin/dev
docker compose build dashboard agent && docker compose up -d --force-recreate dashboard agent
```

Trigger a run:

```bash
API_KEY=$(grep '^STATION_API_KEY=' .env | cut -d= -f2)
curl -s -X POST http://localhost:8420/api/runs/trigger \
  -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json'
```

Wait for terminal. Then inspect the QA verdict:

```bash
RUN_ID=<from trigger response>
docker exec cas-dashboard cat /var/log/claude-agent/$RUN_ID-verdicts.json | python3 -m json.tool | grep -A 3 '"qa"' | head -20
```

Expected:
- Manager's QA verdict reasoning does NOT contain `"test expects ... will break"` / `"after merge"` / `"cross-branch"` patterns.
- If `test_assertion_drift` violations appear in agent logs, they correspond to real cross-branch test/API mismatches (which is the validator working as designed).
- Pass: QA verdict APPROVE_INTEGRATION OR REJECT with reason citing the contract (not a silent merge-time breakage).

If the smoke run reveals the QA teammate still ignores the contract for test assertions, that's a stronger signal that Option 2 (manager downgrade-to-REJECT on cross-branch breakage) is needed — file a follow-up issue but do NOT revert this PR.

If all checks pass, close #458:

```bash
MERGE_COMMIT=$(gh pr view <PR-NUMBER> --json mergeCommit -q .mergeCommit.oid | cut -c1-10)
gh issue close 458 --comment "Fixed in PR #<PR-NUMBER> (commit ${MERGE_COMMIT}), merged into dev. Verified via live smoke run on \`next-itsm\`."
```

---

## Self-Review

**Spec coverage:**
- A. QA-specific prompt addition ✅ Task 1.
- B. Validator extension `test_assertion_drift` ✅ Task 2.
- Tests: 4 unit (positive + 3 negative) ✅ Task 2 Step 1. Snapshot test ✅ Task 1 Step 1.
- Docs update ✅ Task 3.
- Live verification ✅ Task 5 Step 3.

**Placeholder scan:** No TBD/TODO. All code blocks complete. All commands have expected output stated.

**Type consistency:** `_TEST_TRIGGER_RE`, `_DIVERGENCE_SIGNALS`, `_looks_like_test_drift` named identically in Task 2 implementation and Task 4 sweep. `Violation.section="test_assertion_drift"` used consistently in spec, plan, tests, and docs.

**False-positive defenses match the spec:** The three defenses listed in the spec's Section 2 (trigger + divergence; contracted-name skip; positional divergence) are all wired into the helper in Task 2 Step 3.

Self-review clean.
