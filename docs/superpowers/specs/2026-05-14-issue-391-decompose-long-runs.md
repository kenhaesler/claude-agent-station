# Decompose Long Runs into Smaller Atomic Units — Design

**Status**: design
**Date**: 2026-05-14
**Issue**: #391 (Tier 3 / C of epic #382)
**Depends on**: Tier 1C — structured `RunComplete`; Tier 2A — concurrent containers

## Context

Production observed runs in this session:

| Run | Duration | Issue |
|---|---|---|
| `run-20260512T193836Z` | 52 min | #27 (auth) |
| `run-20260512T213225Z` | 58 min | #27 + #28 |
| `run-20260513T044331Z` | 45 min | mixed |
| `run-20260513T151408Z` | 28 min | small follow-up — the clean one |

A 45-to-60-minute run is structurally expensive:

- It locks `max_concurrent_employees=1` for the entire window. No other
  issue can be picked up during the run.
- Cascading failures are unrecoverable late in the run. The manager
  review's previous 30-turn cap (issue #390 removes that, but the run-
  level cap remains) hitting at the 40-minute mark means starting over
  from scratch.
- Mid-run diagnosis is hard. Operators cannot tell from the dashboard
  whether the run is "still working" or "wedged" — at minute 35 the
  shape looks the same in either case.
- The work *is decomposable*. An "auth" issue today is fanned out
  three ways at the **specialist level** (backend / frontend / qa) but
  the underlying surface area is several independent atomic features:
  login API, `me` endpoint, GitHub OAuth callback, route-protection
  middleware. Each of these on its own would fit in a ~10-minute run.

The current decomposition pattern (specialist-level) duplicates planning
effort: each specialist spends ~15 minutes reading the issue, exploring
the repo, and drafting an approach before producing code. Three
specialists on the same issue do this independently, often
re-discovering the same constraints.

This spec introduces decomposition at the **issue level**, before any
specialist is spawned. A new role — `issue-splitter` — examines a large
incoming issue and produces 2–5 sub-issues, each scoped tightly enough
that a single short run with a small team can complete it end-to-end.
Sub-runs execute concurrently (enabled by Tier 2A's containers) and
merge to an integration branch independently, with CI as the
integration test.

> **Note**: the issue body references `agent/coordinator/smart_router.py`
> as the integration point for the "too big, split first" decision.
> Verification (2026-05-14): `agent/coordinator/smart_router.py` does
> not exist in the current tree. `agent/coordinator/` contains compiled
> bytecode (`__pycache__`) but no source `.py` files at the top level.
> The actual smart-router logic appears to live in
> `agent/coordinator/{dag,db,decide,employee_runner,guidance,modes,
> skill_loader}.py` (inferred from the `.pyc` filenames). The
> implementation PR must locate the correct module before wiring in the
> splitter call site.

## Goals

- A new agent role, `issue-splitter`, exists and is invokable from the
  run-manager flow.
- The router can decide "too big, split first" before spawning a
  specialist team, with the decision recorded on the run for
  observability.
- A successful split produces 2–5 GitHub sub-issues, each linked back
  to the parent.
- Sub-issues can run **concurrently** in isolated containers (depends
  on Tier 2A).
- Per-sub-issue failure does not cascade to siblings — each sub-run is
  independently revertable.
- The auth refactor (issue #27 today) completes in ≤4 × 10-minute runs
  instead of 1 × 45-minute run.

## Non-goals

- **Always-decompose**. Single-criterion issues stay single-run. The
  router decides; a default-no policy means we never over-split.
- **Real-time merge orchestration** across sub-runs. Initial scope:
  each sub-run merges to an integration branch independently and CI on
  the integration branch validates the composite. Cross-sub-run
  resolution comes later.
- **Auto-splitting human-authored issues without operator approval**.
  Sub-issues land with a `splitter-proposed` label and require manual
  un-labelling (mirroring `vision-suggested` in
  `agent/vision_analyst.py:5`) before they're eligible for autonomous
  pickup. Operator review is the safety net during rollout. (Open
  decision — see "Open questions".)

## Approach

### New role: `issue-splitter`

A new agent definition `agent/agents/issue-splitter.md`:

```yaml
---
name: issue-splitter
description: Decomposes a large GitHub issue into 2–5 self-contained sub-issues.
tools: Read, Glob, Grep, Bash
model: claude-sonnet-4-6
permissionMode: bypassPermissions
maxTurns: 30
---
```

Prompt structure (kept in `agent/prompts/issue-splitter.md` if Agent
Teams supports prompt include, else inlined):

1. **Inputs**: the parent issue body, repo summary, vision (when
   present), and a budget hint ("target 4–10 minute sub-runs").
2. **Task**: produce a JSON array of sub-issues. Each item:
   `{title, body, labels, acceptance, depends_on}`.
3. **Constraints**: each sub-issue must be implementable end-to-end by
   a single specialist team in ≤15 minutes. The acceptance criteria
   must be testable. `depends_on` is an index into the array (zero or
   one entry) for serialization hints.
4. **Output**: the JSON array, written to a file path passed in the
   spawn prompt.

The splitter is **read-only** on the repo (no `Edit`, no `Write`
outside the output file path) — it does not propose code, only scoping.

### Router integration: "too big, split first"

Today the coordinator's decide module (`agent/coordinator/decide.py`
based on `.pyc` evidence) selects the next eligible issue and dispatches
it to a specialist team. The new step inserts before dispatch:

```python
def maybe_split(issue: GhIssue, run_id: str) -> SplitDecision:
    """Return SPLIT(sub_issues=[...]) or RUN_AS_IS()."""
```

Heuristics for `maybe_split`:

1. **Issue body length** > N tokens → candidate for split.
2. **Acceptance-criterion count** ≥ 4 → candidate for split.
3. **Cross-cutting label set** (e.g., simultaneous `backend` +
   `frontend` + `db-migration`) → candidate for split.
4. **Operator opt-in label** (`split-me`) → always split.
5. **Operator opt-out label** (`do-not-split`) → never split.

Candidate issues are sent to the splitter; the splitter's JSON output
is parsed, validated, and persisted as GitHub sub-issues linked to the
parent via a `Parent: #N` line in the body and a back-link comment on
the parent.

### Sub-issue creation contract

Mirror `vision_analyst.py`'s issue-creation flow
(`agent/vision_analyst.py:43+`):

- POST `/repos/{owner}/{repo}/issues` per sub-issue.
- Apply labels: `splitter-proposed` (always, like
  `vision-suggested`), plus any inherited labels from the parent.
- Body prefix: a disclaimer noting the sub-issue was generated from
  parent `#N`.
- Acceptance criteria copied verbatim from the splitter's output.
- Parent gets a back-link comment listing all sub-issue numbers.
- Parent stays open but acquires a `split` label so the router
  doesn't re-consider it.

### Concurrent execution (Tier 2A dependency)

The run scheduler picks N eligible sub-issues at the start of each
schedule tick. After Tier 2A's containers land, each gets its own
container; before then, the scheduler serializes (single-employee
constraint).

The scheduler must:

- Honour `depends_on` from the splitter's output (don't start a
  dependent sub-issue before its prerequisite has merged).
- Maintain an integration branch per parent issue, e.g.
  `integration/issue-27`. Each sub-run merges its feature branch into
  this integration branch (not `dev` directly).
- Final aggregation: when all sub-runs complete, a single PR from the
  integration branch to `dev` is opened, with the parent issue and
  all sub-issues referenced.

### Run-level state additions

`Run.run_kind` column (new TEXT, nullable for legacy rows):
`primary` | `sub-of-<parent-issue-number>` | `split-decision`. A
`split-decision` run is the short-lived run that hosted the splitter
itself; primary runs continue today's pattern. Sub-runs reference their
parent.

`RunComplete` (Tier 1C) gains a `sub_runs: list[str]` field on parent
runs and a `parent_run: str | None` field on sub-runs, so the dashboard
can render the tree.

### Failure semantics

- **One sub-run fails**: its branch never merges to the integration
  branch. Siblings continue. A `failed_sub_issues` list goes into the
  parent run's `RunComplete` payload.
- **All sub-runs fail**: the parent issue gets a `splitter-needs-
  rework` label so an operator can adjust the split decision or fall
  back to a primary run.
- **Integration CI fails**: standard PR-failure path on the
  integration branch's PR to `dev`. The integration branch persists
  for manual intervention.

### Dashboard surface

- Run list shows sub-runs nested under their parent (one level only,
  no recursion).
- Run detail for a parent run shows a fan-out diagram (sub-run IDs,
  per-sub-run verdict).
- Operator can manually trigger a re-split via a "Re-split" button
  that opens a confirmation modal and a chance to nudge the splitter
  prompt.

## Acceptance criteria

Quoted from #391, expanded:

- [ ] **"New agent role: issue-splitter, with prompt + tests"** —
      `agent/agents/issue-splitter.md` exists with frontmatter and
      role description; `agent/prompts/issue-splitter.md` exists with
      the full splitter prompt; unit tests cover the JSON output
      parser and the back-link comment generator.
- [ ] **"Smart-router can decide 'too big, split first' before
      spawning teammates"** — `maybe_split` (or its eventual name)
      exists in the correct coordinator module; the heuristics above
      are implemented; the decision is recorded as
      `Run.split_decision` (JSON column or `agent_events` row).
- [ ] **"Sub-issues link back to parent issue"** — every splitter-
      created sub-issue body contains a `Parent: #N` line; the parent
      issue has a comment listing the sub-issues.
- [ ] **"Run scheduler can fan out 2–5 runs on sub-issues
      concurrently"** — when Tier 2A is in place, three of four sub-
      issues for #27 execute in parallel containers in a measured
      test. When Tier 2A is not yet in place, they serialize cleanly
      with no deadlocks.
- [ ] **"Auth refactor (issue #27 today) completes in ≤4 × 10-min runs
      instead of 1 × 45-min run"** — measured on the dev box: four
      sub-issues for #27 each complete in ≤10 minutes; total wall-
      clock is ≤15 minutes with concurrency, ≤40 minutes serialized.
- [ ] **"Failure of one sub-issue doesn't block the others"** —
      synthetic test: poison one sub-issue's specialist team, observe
      the other three complete and merge to the integration branch,
      while the poisoned one stays open with a failure comment.

## Dependencies / blocks

- **Hard dependency**: Tier 1C (structured `RunComplete`). The
  parent-with-sub-runs aggregation needs a stable, machine-readable
  per-run verdict object.
- **Hard dependency**: Tier 2A (concurrent containers). Without it the
  decomposition still works but loses its main wall-clock win;
  serialized four-sub-issue runs are still useful (smaller blast
  radius per run) but not the headline 4× speedup.
- **Soft dependency**: Issue #390 (manager-as-sibling). Easier to
  reason about per-sub-run verdicts when the manager is in-session.
- **Soft dependency**: `agent/vision_analyst.py`'s existing
  issue-creation patterns. Reuse them rather than re-implement.

## Risks and rollback

- **Risk**: the splitter produces nonsensical sub-issues (overlap,
  missing acceptance criteria, infinite split loop). Mitigation:
  splitter output schema is strictly validated; on parse failure the
  run falls back to single-issue mode and the parent stays
  unchanged. The `splitter-proposed` label keeps the human in the
  loop during rollout.
- **Risk**: integration-branch CI exposes regressions late. The four
  sub-runs each pass their own CI but the merged combination fails.
  Mitigation: integration-branch CI is the integration test by
  design; this is acceptable as long as failures don't cascade. The
  integration branch persists for manual inspection.
- **Risk**: cost (every "too big" issue now incurs a splitter
  invocation). Mitigation: the splitter is cheap (Sonnet, short
  prompt, capped at 30 turns) and only invoked once per parent
  issue; primary run cost dominates.
- **Risk**: the smart-router module path is wrong. Mitigation: the
  implementation PR enumerates `agent/coordinator/*.py` and picks the
  correct integration point before writing wiring code (see Note).
- **Risk**: concurrent sub-runs contending for the same files in the
  workspace. Mitigation: each sub-run has its own worktree (today's
  pattern for specialist teammates), and Tier 2A's containers
  isolate further.
- **Rollback**: feature-flag the `maybe_split` call (`STATION_SPLIT_
  ENABLED`, default off). Disabling reverts the router to its current
  single-issue dispatch. Sub-issues already created via the splitter
  remain in GitHub but, without the splitter active, are picked up
  by the autonomous router on their own (gated by the
  `splitter-proposed` label).

## Test strategy

- **Unit (pytest)**:
  - Splitter output parser: valid JSON of 2–5 items → parsed; 1 item
    → rejected with `SplitterError`; 6+ items → truncated to 5 with a
    warning; malformed JSON → `SplitterError`.
  - `maybe_split` heuristics: each of the five triggers above has a
    targeted test producing the expected `SplitDecision`.
  - Back-link comment generator: produces the correct GH-flavored
    Markdown.
- **Integration**:
  - Stub GitHub API; drive the splitter end-to-end against a fake
    parent issue; assert N sub-issues created with correct labels and
    back-links.
  - Scheduler test: enqueue 4 sub-issues with one `depends_on`
    relationship; assert the scheduler respects the dependency edge.
- **Live test (dev box)**:
  - Apply `split-me` label to a synthetic large issue; observe four
    sub-issues created; observe four sub-runs (serialized or
    concurrent depending on Tier 2A status) complete with verdicts;
    observe the parent issue updated with the sub-issue list.
- **Regression watch**:
  - Dashboard metric: median primary-run duration. Expected to drop
    once decomposition is on for matching issues.
  - Failure-cascade detector: number of sub-runs whose siblings also
    failed within the same parent should not exceed historical
    cascade rate.

## Open questions

- **Operator-in-the-loop vs auto-promote**. The
  `splitter-proposed` label requires manual un-labelling before the
  router picks up sub-issues, mirroring `vision-suggested` today.
  Decision: keep the label during initial rollout; revisit promotion
  to auto-pickup after one month of data.
- **Multi-level decomposition** (sub-issue itself splits). Out of
  scope for initial cut; the splitter is single-level only,
  enforced by the heuristics (the splitter's output is gated by an
  "is this already small enough" check before secondary split is
  considered).
- **Cross-sub-run dependencies beyond `depends_on`**. Initial scope
  supports linear `depends_on`; richer DAG support would build on
  Tier 1C's `RunComplete` aggregation but is deferred.
