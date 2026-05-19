# Acceptance-Criteria Decomposition + QA Test Ownership — Design

**Issue:** Closes #464
**Author:** Claude Opus 4.7 (1M context)
**Date:** 2026-05-19
**Target branch:** PR targets `dev` (per project policy)

## Problem

When an issue's acceptance criteria include a test-file requirement on a backend-scoped task, the agent station's QA teammate fails to produce the test file. The result: backend implements the route correctly, frontend correctly SKIPs (no UI work), QA either doesn't spawn or produces no verdict, and the manager REJECTs because the acceptance criteria's explicit test-file requirement is unmet.

### Live evidence

Run `run-20260519T192715Z` on `laboef1900/next-itsm` issue #61 (`feat: GET /api/health endpoint for service liveness`).

Issue body included verbatim: `Unit tests in src/app/api/health/route.test.ts covering both branches`.

Verdicts file contained exactly 2 entries:

| Role | Verdict | Branch | Outcome |
|---|---|---|---|
| backend | REJECT | `feature/backend-issue-61` | `requirements_missing`: `"Unit tests in src/app/api/health/route.test.ts covering both branches (success and failure)"` |
| frontend | SKIP | None | Correctly SKIP'd as backend-only |
| **qa** | **(absent)** | — | No verdict produced |

The autonomous loop can technically produce the route correctly, but cannot finalize the issue because no teammate creates the test file the acceptance criteria explicitly requires. This blocks any positive end-to-end exercise of PR #461's auto-close path.

## Goals

- Lead's task decomposition explicitly maps acceptance-criteria items to roles (test files → QA).
- QA teammate self-claims test-file work even on backend-scoped issues.
- Advisory validator detects when an issue's body references a test file that no approved verdict acknowledges, surfacing the gap for operator visibility.
- Fail-soft: validator runs alongside the existing 4 violation types; advisory only; never auto-flips verdicts.

## Non-Goals

- Auto-promoting REJECT to a coordinator-task-correction loop (the validator is advisory, not corrective).
- Cross-reference resolution in the issue body (e.g. `"tests for #59 are at ..."`).
- Forcing QA to produce a verdict in all cases (some legitimately don't need QA work).
- Dashboard UI surface for `missing_test_coverage` violations (future polish).
- Workflow-section deduplication (text duplicated between approved-plans and fresh-issues branches — separate concern).

## High-Level Architecture

Three additive pieces. All prompt-only OR validator-extension; no orchestration changes, no DB changes.

### A — Lead workflow tightening
In `agent/station_orchestrator.py::build_team_prompt`, add an `Acceptance-criteria decomposition (#464)` block to BOTH workflow branches (approved-plans and fresh-issues). Tells the lead that acceptance-criteria items must map to roles, with test files explicitly belonging to QA.

### B — QA spawn-prompt addendum
Extend the existing QA-specific paragraph in the teammate-spawn READ FIRST block (added by PR #459) with an explicit instruction: QA owns test-file creation, including when the file path lives inside backend territory.

### C — Validator extension: `missing_test_coverage`
In `agent/team_contracts.py::validate_verdict_against_contracts`, add a 5th heuristic pass. Parses the issue body for **strict file-path patterns** (paths containing `/` and either `.test.`/`.spec.` infix OR inside a `/tests/` segment). For each test path found, checks whether any APPROVE/APPROVE_INTEGRATION verdict's `requirements_met` or `feedback_to_employee` text acknowledges it. Missing acknowledgment → `Violation(section="missing_test_coverage", ...)`.

Issue bodies are fetched fresh via `gh issue view <N> --json body --jq .body` from `iterate_projects` and passed through to the validator via a new `issue_bodies: dict[int, str]` kwarg.

## Components

### 1. Modified: `agent/station_orchestrator.py::build_team_prompt`

**A — Acceptance-criteria decomposition block.** Insert in BOTH workflow_section branches (`approved_plan_paths` truthy and the else branch). Adds text after the existing decomposition step:

```
Acceptance-criteria decomposition (#464): Read each issue's body
carefully. For EVERY item in the acceptance criteria, create a
coordinator task assigned to the right role:

  - Source code (routes, services, models, components) → backend or
    frontend per location.
  - Test files (`*.test.ts`, `*.spec.ts`, `Unit tests in ...`,
    `tests/test_*.py`) → ALWAYS QA, even when the file path lives
    inside backend/frontend territory.
  - Docs / configuration → assign to whoever owns the adjacent code.

If the issue body explicitly names a test file path, the QA teammate
MUST create that file. The manager will reject any APPROVE that
doesn't satisfy every acceptance item.
```

**B — QA spawn-prompt addendum.** Extend the existing QA paragraph (lines 1102-1108) by appending:

```
Additionally: when the issue's acceptance criteria reference unit
tests, integration tests, or specific test files, YOU are responsible
for creating those test files. NEVER skip a run just because the
issue is backend-scoped — your scope is testing, which exists for
backend issues too. If the lead didn't create a coordinator task for
you, claim the test work yourself.
```

The closing `"` after the existing "match what your test happened to assert." line stays, and the new text is concatenated inside the same multi-line string.

### 2. Modified: `agent/team_contracts.py`

**`_TEST_FILE_REF_RE` constant** (near the other regex constants):

```python
_TEST_FILE_REF_RE = re.compile(
    r"(?:\b|`)("
    r"(?:[\w.-]+/)+[\w.-]+\.(?:test|spec)\.[a-zA-Z]+"  # foo/bar.test.ts
    r"|"
    r"(?:[\w.-]+/)*tests/[\w./-]+\.\w+"                # tests/foo.py or path/tests/foo.py
    r")(?:\b|`)"
)
```

**`_looks_like_missing_test_coverage` helper:**

```python
def _looks_like_missing_test_coverage(
    issue_body: str, verdicts: list, project_repo: str
) -> list[Violation]:
    """Detect test-file paths in the issue body that no approved verdict
    acknowledges. Heuristic; advisory.

    Strict file-path matching only — bare prose ('write unit tests') is
    NOT flagged because the manager's existing acceptance-criteria
    checks already surface those cases.

    Skips when no APPROVE / APPROVE_INTEGRATION verdicts exist (the
    REJECTs already capture the underlying gap).

    #464.
    """
    if not issue_body:
        return []
    test_paths = list(set(_TEST_FILE_REF_RE.findall(issue_body)))
    if not test_paths:
        return []

    approved_verdicts = [
        v for v in verdicts
        if (v.get("verdict") or "") in ("APPROVE", "APPROVE_INTEGRATION")
    ]
    if not approved_verdicts:
        # No APPROVEs at all — the REJECTs already capture the issue.
        return []

    violations: list[Violation] = []
    for path in test_paths:
        # Check whether any approved verdict's reasoning/requirements
        # acknowledges this path.
        acknowledged = False
        for v in approved_verdicts:
            blob = " ".join(filter(None, [
                v.get("reasoning", ""),
                v.get("feedback_to_employee", ""),
                " ".join(v.get("requirements_met", []) or []),
            ]))
            if path in blob:
                acknowledged = True
                break
        if not acknowledged:
            violations.append(Violation(
                section="missing_test_coverage",
                expected=path,
                found="(no approved verdict acknowledges this path)",
                context=(
                    f"Issue body references test path '{path}', but no "
                    f"APPROVE/APPROVE_INTEGRATION verdict's "
                    f"requirements_met or feedback acknowledges it. "
                    f"QA teammate likely did not create the file."
                ),
            ))
    return violations
```

**Decision: Pass 5 is invoked as a SEPARATE top-level call from `iterate_projects`, not embedded in `validate_verdict_against_contracts`.**

Pass 5 needs visibility into all verdicts (to determine if any APPROVE acknowledges the test path), not just the single verdict being evaluated. Embedding it in `validate_verdict_against_contracts` would either require a new `all_verdicts` kwarg (signature drift) or running the check redundantly per verdict.

Cleaner: keep `validate_verdict_against_contracts`'s signature unchanged. Expose `_looks_like_missing_test_coverage` as a standalone helper that `iterate_projects` calls once per issue with the full verdicts list. See the call-site code in Component 3 below.

### 3. Modified: `agent/project_loop.py::iterate_projects`

After the existing `validate_verdict_against_contracts` call block (added by PR #459), add:

```python
# #464: missing-test-coverage check. Fetches each unique issue body
# once, scans for test-file paths, and flags any path not acknowledged
# by an APPROVE verdict. Advisory only; logged at WARNING.
try:
    from agent.team_contracts import _looks_like_missing_test_coverage
    import json as _json

    unique_issues = {
        v.get("issue_number") for v in raw_verdicts
        if v.get("issue_number") is not None
    }
    issue_bodies: dict[int, str] = {}
    for n in unique_issues:
        result = subprocess.run(
            ["gh", "issue", "view", str(n), "--repo", project["repo"],
             "--json", "body", "-q", ".body"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            issue_bodies[n] = result.stdout

    for n, body in issue_bodies.items():
        coverage_violations = _looks_like_missing_test_coverage(
            body, raw_verdicts, project["repo"]
        )
        if coverage_violations:
            logger.warning(
                "missing test coverage on issue #%s: %s",
                n,
                [f"{v.section}: {v.context}" for v in coverage_violations],
            )
except Exception:  # noqa: BLE001
    logger.exception("test-coverage validator failed (non-fatal)")
```

### 4. Modified: `docs/architecture.md`

Extend the validator section's enumeration of violation types:

```markdown
The validator scans each manager verdict's `reasoning` text for
contract violations: field-name mismatch, route-ownership conflict,
enum-value drift, test-assertion drift, AND test-file coverage
(when the issue body references a test path that no approved
verdict acknowledges).
```

## Data Flow

```
iterate_projects (after existing 4-pass validator call)
 ├─► Collect unique issue numbers from raw_verdicts
 ├─► For each unique issue: gh issue view N --json body --jq .body
 │     └─► Cache: {issue_number: body_text} (fail-soft on per-issue failure)
 ├─► For each (issue_number, body) in cache:
 │   └─► _looks_like_missing_test_coverage(body, raw_verdicts, repo)
 │         ├─► _TEST_FILE_REF_RE.findall(body) → test_paths
 │         ├─► Filter raw_verdicts to APPROVE/APPROVE_INTEGRATION
 │         ├─► For each test_path: scan approved verdicts'
 │         │   reasoning + feedback_to_employee + requirements_met
 │         │   for the path; if absent in ALL → Violation
 │         └─► Return list[Violation]
 └─► If any violations: logger.warning with section + context
```

Pass 5 runs as a sibling to the existing 4-pass call, not nested inside it. Cleaner separation, single GitHub API call site, easy to disable for a specific run if needed.

## Error Handling

| Condition | Behavior |
|---|---|
| `gh issue view N` fails for some issue | Skip that issue (no entry in cache). Pass 5 simply doesn't run for it. WARNING logged. |
| Issue body fetched but contains no test-path references | `_TEST_FILE_REF_RE.findall` returns `[]`; Pass 5 returns `[]`. Silent. |
| No APPROVE/APPROVE_INTEGRATION verdicts exist | Pass 5 returns `[]` (REJECTs already surface the issue). |
| `requirements_met`/`feedback`/`reasoning` empty on a verdict | Use empty string in the concatenated blob. Acknowledgment check still runs; just less likely to match. |
| Validator helper raises unexpectedly | Outer `try/except Exception` in `iterate_projects` catches it. Run continues; `logger.exception` records the trace. |
| Issue body contains test path inside backticks | Regex's `(?:\b|`)` boundary handles both forms; captured into group 1 the same way. |

## Testing

### Unit tests in `dashboard/backend/tests/test_team_contracts.py` (5 new)

1. **`test_test_file_ref_regex_matches_strict_paths`** — assert the regex captures:
   - `src/app/api/health/route.test.ts`
   - `dashboard/backend/tests/test_foo.py`
   - `lib/utils.spec.ts`
   - The same paths wrapped in backticks
   - REJECTS bare prose: `"write unit tests"`, `"test pass"`, `"the test"`.

2. **`test_validator_flags_missing_test_coverage_when_no_verdict_addresses_path`** — call `_looks_like_missing_test_coverage` directly with a body containing `src/app/api/health/route.test.ts` and a verdicts list of one APPROVE whose `requirements_met` lists only the route impl. Assert one `missing_test_coverage` violation with `expected="src/app/api/health/route.test.ts"`.

3. **`test_validator_passes_when_verdict_acknowledges_test_path`** — same body; verdict's `requirements_met` includes `"Unit tests in src/app/api/health/route.test.ts"`. Assert `[]`.

4. **`test_validator_does_not_fire_when_no_test_paths_in_body`** — body has no test-path references. Assert `[]` regardless of verdicts.

5. **`test_validator_does_not_fire_when_no_approve_verdicts_exist`** — body references test paths but all verdicts are REJECT. Assert `[]`.

### Snapshot test in `dashboard/backend/tests/test_orchestrator_wiring.py` (1 new)

6. **`test_build_team_prompt_includes_acceptance_decomposition_and_qa_test_ownership`** — assert both `"Acceptance-criteria decomposition"` AND `"YOU are responsible for creating those test files"` appear in the prompt for `project_mode="full"`.

### Integration test in `dashboard/backend/tests/test_iterate_projects_python.py` (1 new)

7. **`test_iterate_projects_logs_missing_test_coverage_warning`** — monkeypatch `subprocess.run` to return a stubbed `gh issue view` response containing a test path; mock verdicts list with one APPROVE that doesn't acknowledge the path. Drive `iterate_projects` once; assert a WARNING is logged with substring `"missing test coverage"`.

### Live verification (post-merge)

Re-trigger the next-itsm run with issue #61 still OPEN. **Acceptance**: 
- QA produces a verdict (no longer absent).
- At least one branch contains `src/app/api/health/route.test.ts`.
- Manager APPROVES.
- PR #461's auto-close fires.
- Issue #61 transitions to CLOSED on GitHub at run end.

This finally exercises the auto-close path live.

## Backwards Compatibility

- `validate_verdict_against_contracts` keeps its existing positional signature. New `issue_bodies` and `all_verdicts` kwargs default to `None`. Old callers (tests, dispatch) unchanged.
- Pass 5 lives in a sibling call from `iterate_projects` (not embedded in the per-verdict validator). New section name `"missing_test_coverage"` adds to the existing four types — unknown sections are ignored by old log consumers.
- Prompt additions are unconditional within non-`plan_only` mode (same gating as PR #457/#459/#462 additions).
- No DB/schema/wire-format changes.

## Acceptance

- [ ] `agent/station_orchestrator.py::build_team_prompt` includes the Acceptance-criteria decomposition block (A) in both workflow branches.
- [ ] QA spawn-prompt addendum (B) appended to the existing QA paragraph.
- [ ] `_TEST_FILE_REF_RE` and `_looks_like_missing_test_coverage` exist in `agent/team_contracts.py`.
- [ ] `iterate_projects` fetches issue bodies and runs the test-coverage check after the existing validator.
- [ ] All 7 new tests pass.
- [ ] Broader backend sweep clean.
- [ ] `docs/architecture.md` lists the 5th violation type.
- [ ] PR targets `dev`. Closes #464.
- [ ] Post-merge live verification: run on next-itsm #61 produces a QA verdict, APPROVE outcome, and PR #461's auto-close transitions #61 to CLOSED.

## Out-of-Scope Follow-Ups

- Workflow-section text deduplication (the two branches duplicate body text — separate concern).
- Cross-reference resolution (`"tests for #59 are at ..."`).
- Auto-promotion `claude-agent-station` → `main`.
- A dashboard UI surface for `missing_test_coverage` violations.
- Programmatic enforcement that QA always produces a verdict (currently QA can legitimately skip).
