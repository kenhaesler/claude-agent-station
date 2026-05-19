# Sibling-Teammate Contract Coordination — Design

**Issue:** Closes #456 (sibling teammates have no contract-sync channel — same conflict persisted across two consecutive manager rejections)
**Author:** Claude Opus 4.7 (1M context)
**Date:** 2026-05-18
**Target branch:** PRs land on `dev` (per project policy)

## Problem

The Agent Teams architecture spawns three role-specialized teammates (`backend`, `frontend`, `qa`) in isolated worktrees with no coordination channel between them on cross-team contract details (field names, response shapes, enum values, route ownership). When two siblings independently choose conflicting contracts, the manager-review catches the conflict but only at the end of the run — both teammates have already burned tokens building incompatible work, and the next run repeats the same conflict because each teammate only sees the manager's per-branch feedback in isolation.

Live evidence across two consecutive runs on `next-itsm` issues #29/#30/#31:

- **Run 1** (`run-20260517T191757Z`): 3× REJECT. Backend used `purchasePrice`/`residualValue`; frontend AND qa used `purchaseCost`/`salvageValue`. Three independent implementations of the same depreciation route.
- **Run 2** (`run-20260517T210912Z`): 2× REJECT + 1× APPROVE. Frontend self-corrected (stopped creating duplicate routes; got APPROVED for UI work on #29 + #31). Backend ↔ QA never agreed on the field-name contract — identical conflict recurred.

The system is stable but stuck. The gap is in the **coordination protocol between teammates**, not in any plumbing defect.

## Goals

- Prevent first-run cross-team contract conflicts via a pre-spawn contract pass written by the lead.
- Surface prior-run verdicts (including verdicts on other siblings' branches) into the lead's context so re-runs converge.
- Programmatically validate manager verdicts against the contract so contract violations are visible in dashboard surfaces.
- Fail-soft: when the lead skips contract authoring or prior verdicts are absent, the pipeline degrades to current behavior — no new crash paths.

## Non-Goals

- No new SDK session / agent definition. The lead absorbs the contract-author responsibility.
- No automatic conflict resolution when the lead can't decide. Lead either picks or leaves the section vague; manager has final say.
- No dashboard view of `contracts.md`. Future polish.
- No schema versioning for `contracts.md`. The 5 sections are stable enough for V1.
- No programmatic enforcement that the LEAD actually wrote the file — degrades gracefully when absent.

## High-Level Architecture

Two complementary pieces, both running before the manager-review step:

### A — Pre-spawn contract pass (proactive)
- The lead, during its plan-review window and BEFORE spawning role-specialized teammates, writes `<workspace>/.claude-team-contracts.md` documenting cross-team contracts.
- The file uses structured Markdown sections the orchestrator can parse: `## API Routes`, `## Field Names`, `## Response Shapes`, `## Enum Values`, `## Route Ownership`.
- Each teammate's spawn prompt gets a `READ FIRST` block pointing at the file.
- The manager review prompt cross-references the contract: any verdict that names a field/route/enum that conflicts gets `REJECT` with the contract citation.

### C — Cross-branch feedback injection (reactive)
- Before building the lead's spawn prompt, the orchestrator scans for prior verdicts files matching the same project repo (most-recent first).
- If found, the most-recent file's contents (or its prose summary) are folded into the lead's prompt as a "Recent verdicts (last run)" section.
- The lead is instructed: when writing `contracts.md`, resolve conflicts the prior verdicts flagged. When spawning teammates, mention what their prior rejection reason was.

### Combined flow

```
prior verdicts exist? ──► lead reads prior digest ──► writes contracts.md ──► spawns siblings ──► manager review uses contracts.md as ground truth ──► verdicts.json written
                          (only if exists)             (always — when not plan_only)              (always)                                                (becomes input for next run)
```

Option B (new `contract-architect` teammate) was rejected: it adds an SDK session per run for what is fundamentally a prompt-level coordination concern, and it would not get any information the lead does not already have during plan-review.

## Components

### 1. New module: `agent/team_contracts.py`

Small, focused, ~150 lines.

**Constants:**
```python
CONTRACTS_FILENAME = ".claude-team-contracts.md"
CONTRACT_SECTIONS = (
    "API Routes",
    "Field Names",
    "Response Shapes",
    "Enum Values",
    "Route Ownership",
)
```

**Public types:**
```python
@dataclass
class Route:
    method: str       # "GET", "POST", etc.
    path: str         # "/api/depreciation"
    owner: str        # "backend"
    response_shape: str  # free-form: "{ depreciation: number, ... }"

@dataclass
class TeamContracts:
    routes: list[Route]
    field_names: dict[str, str]      # canonical key -> chosen field name
    response_shapes: dict[str, str]  # route_path -> shape description
    enum_values: dict[str, list[str]]  # enum_name -> allowed values
    route_ownership: dict[str, str]  # route_path -> owning role

@dataclass
class Violation:
    section: str       # "field_names" / "route_ownership" / ...
    expected: str
    found: str
    context: str       # human-readable explanation
```

**Public functions:**
```python
def parse_contracts(workspace_path: Path) -> TeamContracts | None:
    """Return parsed contracts or None on missing/malformed file."""

def validate_verdict_against_contracts(
    verdict: Verdict, contracts: TeamContracts, workspace_path: Path
) -> list[Violation]:
    """Inspect what the manager wrote about this verdict + the underlying
    branch files and return contract violations.

    Inputs the validator inspects (in priority order):
    1. ``verdict.reasoning`` — the manager's prose; contracts violated names
       often appear here directly (e.g. "purchasePrice" vs "purchaseCost").
       String-match against contract field names / route paths / enum
       values; flag the ones the manager named.
    2. ``verdict.branch`` — if checkout-able from ``workspace_path``, the
       validator may grep the branch's diff for contract names. Best-effort;
       no failure on missing branch (e.g. teammate didn't push).
    3. ``verdict.requirements_missing`` — any required-but-missing item that
       names a contracted route/field is flagged.

    The validator is intentionally heuristic. It is not a full code parser
    or LSP integration. The goal is to surface obvious contract violations
    the manager already wrote about — not to second-guess the manager.
    """
```

Parser is lenient: unknown sections ignored; missing sections become empty lists/dicts; hard parse errors caught and return `None`. Validator is heuristic; it does not block verdicts or fail the run on detection.

### 2. Modified: `agent/station_orchestrator.py::build_team_prompt`

- Adds a new keyword-only parameter `prior_verdicts_summary: str | None = None`.
- When `prior_verdicts_summary` is non-None, prepends a "## Recent verdicts (last run on this project)" section before the workflow section.
- When `project_mode != "plan_only"`, prepends a "## Required: write team contracts" instruction directing the lead to populate `.claude-team-contracts.md` before spawning siblings. Includes the structured-section schema as a literal example.
- Each teammate-spawn-prompt block (in the `{wt_section}` area and the teammate configuration block) gets an explicit "READ FIRST: `.claude-team-contracts.md`" instruction.

### 3. Modified: `agent/project_loop.py::iterate_projects`

Two changes inside the per-project loop:

**3a. Prior-verdicts injection (before `orchestrate_project`):**
```python
# Find most-recent prior verdicts for this project (any prior run).
prior_summary = _summarize_prior_verdicts(
    log_dir=log_dir, project_repo=project["repo"]
)
# Pass through to build_team_prompt via orchestrate_project's call chain.
```

`_summarize_prior_verdicts` is a small helper (~30 lines) that:
- Globs `log_dir / "run-*-verdicts.json"` sorted by mtime descending.
- For each file, parses the JSON, filters verdicts whose `project` matches `project_repo`, stops at the first file with any match.
- Returns a short prose summary (~10–20 lines) suitable for prompt injection — verdict per branch, reasoning excerpt, feedback excerpt.
- Returns `None` if no prior verdicts file references this project.

**3b. Contract-violation logging (after `_read_verdicts_file`):**
```python
contracts = parse_contracts(Path(workspace_path))
if contracts and raw_verdicts:
    for raw_verdict in raw_verdicts:
        v = Verdict.from_dict(raw_verdict)
        violations = validate_verdict_against_contracts(
            v, contracts, Path(workspace_path)
        )
        if violations:
            logger.warning(
                "contract violations on verdict %s: %s",
                v.branch, violations,
            )
            # Surface to dashboard via the existing webhook channel.
            _emit_contract_violations(run_id, v, violations)
```

The verdict itself is **not** auto-flipped — we trust the manager's judgment. Violations are surfaced for operator visibility and feed the next-run feedback loop.

### 4. Modified: `orchestrate_project` (call-site thread-through)

`orchestrate_project` already takes a `project` dict and `config`. It needs one new keyword argument `prior_verdicts_summary: str | None = None` so the value can reach `build_team_prompt`. No other change.

### 5. Docs: `docs/architecture.md`

Add a "Sibling-teammate coordination" subsection under the Agent Teams flow describing:
- The `.claude-team-contracts.md` file (location, schema, who writes it).
- The prior-verdicts injection (when it fires, what's included).
- The programmatic validator (advisory only).

## Data Flow

```
iterate_projects
 ├─► _summarize_prior_verdicts(log_dir, project["repo"])
 │     └─► returns prose summary or None
 ├─► orchestrate_project(project, config, run_id, workspaces_dir,
 │                       prior_verdicts_summary=...)
 │     └─► build_team_prompt(..., prior_verdicts_summary=...)
 │           └─► lead's SDK session
 │                 ├─► reads prior verdicts (from prompt)
 │                 ├─► writes .claude-team-contracts.md (instructed)
 │                 ├─► spawns backend / frontend / qa with READ-FIRST instruction
 │                 └─► manager-review (sees contracts in workspace,
 │                       cites in verdicts.json)
 ├─► _read_verdicts_file(verdicts_path)
 ├─► parse_contracts(workspace_path)
 ├─► for each verdict: validate_verdict_against_contracts()
 │     └─► log violations + emit webhook (advisory)
 └─► proceed to executor / plan-review gate
```

## Error Handling

| Condition | Behavior |
|---|---|
| `contracts.md` missing | `parse_contracts` returns `None`; pipeline continues without validator. `logger.warning("no contracts.md — sibling-coordination disabled this run")`. |
| `contracts.md` malformed | Parser is lenient; unknown sections ignored. Hard parse errors caught, returns `None`. Same fallback as missing. |
| Prior verdicts file missing | `_summarize_prior_verdicts` returns `None`; no injection. Today's behavior preserved. |
| Prior verdicts file malformed | Caught; logger warning; returns `None`. |
| Validator finds violations | Logged at WARNING; advisory webhook fired; verdict NOT auto-flipped. |
| Multiple prior verdicts files | Pick most recent by mtime; don't merge across runs. |
| `plan_only` mode | No siblings spawned; contract-write instruction omitted from prompt; validator never runs (no verdicts file). |
| First-ever run on a project | No prior verdicts → C inactive. A still runs (lead writes contracts.md from plan-review knowledge). |
| Single-issue / single-teammate run | Contract file still serves as a record. Lead may write a minimal file or skip — both fine. |
| Prior run was idle/skipped | Verdicts file may not exist. Same as "missing" — pipeline continues, no injection. |

## Testing

### Unit tests — `dashboard/backend/tests/test_team_contracts.py` (new, ~10 tests)

1. `parse_contracts` returns `None` on missing file.
2. Returns `None` on binary/unparseable content.
3. Parses all 5 section types from a well-formed file.
4. Unknown sections are ignored.
5. Missing sections become empty containers.
6. `validate_verdict_against_contracts` returns `[]` for a verdict matching every contract.
7. Returns violations for field-name mismatch.
8. Returns violations for response-shape mismatch.
9. Returns violations for enum-value mismatch.
10. Returns violations for route-ownership conflict.
11. Violation message format includes the contract section + the specific mismatch.

### Integration tests — extend `dashboard/backend/tests/test_iterate_projects_python.py`

1. Prior verdicts file exists → orchestrator passes summary through to `build_team_prompt`.
2. No prior verdicts file → no summary, no crash.
3. Validator violations logged but verdict unchanged.
4. `contracts.md` missing → all behavior degrades to current state, no crash.
5. `plan_only` mode → contract-write instruction NOT in prompt.

### Prompt-builder snapshot tests — extend `dashboard/backend/tests/test_orchestrator_wiring.py`

1. `build_team_prompt(prior_verdicts_summary=...)` includes the "Recent verdicts" section.
2. Includes the "READ FIRST" line in each teammate-spawn instruction block.
3. Includes the contract-write instruction with the section schema.

### Live verification (post-merge, NOT in CI)

Trigger one run on the same `next-itsm` project. **Acceptance**: the depreciation field-name conflict does NOT recur in the new manager verdicts. The `purchasePrice`/`residualValue` vs `purchaseCost`/`salvageValue` choice is documented in `contracts.md` and both backend and qa branches use the chosen value.

## Backwards Compatibility

- `contracts.md` is a new file in the workspace; existing runs without it behave exactly as before.
- The new `prior_verdicts_summary` parameter has a `None` default → existing callers compile unchanged.
- The new advisory webhook event (contract violations) is purely informational; existing dashboard consumers ignore unknown events.
- No DB migration; no schema change to `runs`, `coordinator_tasks`, or `verdicts`.
- Historical verdicts files in `/var/log/claude-agent/` are read-only inputs; their schema is already stable.

## Acceptance Criteria

- [ ] `agent/team_contracts.py` exists with `parse_contracts` and `validate_verdict_against_contracts`.
- [ ] `agent/station_orchestrator.py::build_team_prompt` accepts `prior_verdicts_summary` and emits the new sections + READ-FIRST instructions.
- [ ] `agent/project_loop.py::iterate_projects` calls `_summarize_prior_verdicts` and `validate_verdict_against_contracts` in the right places.
- [ ] All unit + integration + snapshot tests pass.
- [ ] `docs/architecture.md` documents the new file, the injection, and the validator.
- [ ] Live verification: a fresh run on `next-itsm` with issues #29/#30/#31 produces a `.claude-team-contracts.md` and the depreciation field-name conflict is gone.
- [ ] PR targets `dev`. Closes #456.

## Out-of-Scope Follow-Ups

- Dashboard view of `contracts.md` per run.
- Schema versioning of `contracts.md`.
- Auto-resolution of contracts when the lead leaves a section blank.
- Sibling peek tool (Option D from the issue) — lets a teammate read another teammate's worktree. Heaviest, most expressive, deferred.
