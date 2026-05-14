# APPROVE_INTEGRATION Verdict Tier — Design

**Status**: design
**Date**: 2026-05-14
**Author**: tier-2-architect
**Issue**: [#388](https://github.com/kenhaesler/claude-agent-station/issues/388) — *Tier 2 / Issue C* of epic [#382](https://github.com/kenhaesler/claude-agent-station/issues/382)

## Context

The operator-reported problem is that "we are still creating a lot of PRs, but none of them get merged in to the branch which we defined in the settings page." Investigation in PR #381 confirmed the system is doing exactly what its config asks for. The current verdict ladder is binary: `APPROVE` (merge directly to base — feels dangerous to the manager prompt) or `PR` (open a draft for human review — slow). With `integration.enabled=true` and `integration.auto_promote=false`, every `APPROVE` from the manager turns into a merge to the integration/dev branch and the dev branch never promotes; every `PR` adds a draft that no human ever lands. The result is a steady stream of work that produces drafts but never PRs that auto-merge.

The manager prompt at `agent/prompts/manager.md:148-153` instructs the manager to choose `PR` for "large scope, sensitive code (auth, payments, config), uncertain coverage, ambiguous requirements." Auth-related work hits "sensitive" almost every time, so it never reaches `APPROVE`. The verdict execution today lives in two places: `agent/scripts/run-manager.sh:2272+` (the live bash path) and `agent/verdict_execution.py` (Python primitives staged for the bash deletion; not yet wired). Both implement four cases — `APPROVE`, `PR`, `REJECT`, `SKIP` — and the bash `APPROVE` case at `run-manager.sh:2273-2279` already routes to `merge_to_dev` from `agent/scripts/integration-branch.sh` when integration is enabled. There is no intermediate "tested but not main-worthy" tier.

`APPROVE_INTEGRATION` introduces that tier. It pushes the feature branch, opens a non-draft PR against the project's `integration.dev_branch` (or a per-project promotion target), and turns on GitHub's auto-merge so the PR lands the moment CI passes — humans never have to click. This is a strict superset of "open a PR" but with the merge already armed; CI is the gate, not a reviewer. For non-trivial work that has tests, this becomes the new default; `PR_FOR_REVIEW` (the renamed `PR`) becomes reserved for cases where a human is genuinely required.

## Goals

- Add a fourth verdict, `APPROVE_INTEGRATION`, that opens a non-draft PR against the integration branch with auto-merge enabled.
- Update the manager prompt so sensitive-but-tested code prefers `APPROVE_INTEGRATION` over `PR_FOR_REVIEW`.
- Surface the new verdict in the dashboard run list, verdict filters, and audit log without breaking older runs.
- Preserve the existing `APPROVE`/`PR`/`REJECT`/`SKIP` semantics; this is an additive change, not a rename.

## Non-goals

- Rewriting the verdict execution. The bash path stays primary until the broader bash → Python migration (Item 5 of the 2026-05-11 run-lifecycle spec) lands.
- Changing branch protection on integration / dev. CI is the gate; that already exists.
- Replacing `gh pr merge --auto`. Other auto-merge mechanisms (squash bots, custom workflows) are out of scope.
- Adding new verdict for analyze/plan modes — those keep their existing `APPROVE`/`REJECT` semantics.

## Approach

### Verdict literal and dispatcher

`agent/verdict_execution.py` adds `APPROVE_INTEGRATION` to `VerdictKind`:

```python
VerdictKind = Literal[
    "APPROVE",              # Direct merge to base (today's APPROVE)
    "APPROVE_INTEGRATION",  # PR against integration branch + --auto --squash
    "PR",                   # Draft PR for human (today's PR; alias: PR_FOR_REVIEW)
    "REJECT",
    "SKIP",
]
```

New executor `execute_approve_integration`. Sketch:

```python
def execute_approve_integration(
    verdict: Verdict,
    *,
    workspace: Path,
    run_id: str | None = None,
    env: dict[str, str] | None = None,
    dev_branch: str | None = None,
) -> ExecutionResult:
    # 1. git push -u origin <branch>
    # 2. gh pr create --repo <project> --head <branch> --base <dev_branch>
    #    --title ... --body ... (NO --draft)
    # 3. gh pr merge --auto --squash <pr_url>
    # 4. gh issue comment <n> with "Manager verdict: APPROVE_INTEGRATION,
    #    auto-merge armed against <dev_branch>. CI gates merge."
```

The base branch for the PR is the project's `integration.dev_branch` (resolved by the caller). If integration is not enabled for the project, `APPROVE_INTEGRATION` degrades to `APPROVE` with a warning logged — the manager shouldn't have emitted it, but we accept it rather than fail the run. Register in `_EXECUTORS`. Update `execute()` so the dispatcher routes the new verdict.

### Bash path (`agent/scripts/run-manager.sh`)

Add a new case alongside the existing `APPROVE | PR | REJECT | SKIP` block near line 2272. The flow:

```bash
APPROVE_INTEGRATION)
    log_info "APPROVE_INTEGRATION: pushing $branch, opening auto-merge PR against $pr_base_branch"
    if ! integration_enabled; then
        log_warn "APPROVE_INTEGRATION but integration disabled — falling through to APPROVE"
        # fallthrough to APPROVE handling
    fi
    git push -u origin "$branch" || { log_error "git push failed"; continue; }
    pr_url=$(gh pr create --repo "$project" --head "$branch" --base "$pr_base_branch" \
        --title "..." --body "...") || { log_error "pr create failed"; continue; }
    gh pr merge --auto --squash "$pr_url" || log_warn "auto-merge enable failed"
    webhook_event "verdict_execute" ...
    # issue comment, label cleanup
    ;;
```

`pr_base_branch` is already computed earlier in the loop (lines 2080–2087) from `integration.dev_branch`, so no new resolution code is needed.

The `record-outcome` block at line 2153 treats `APPROVE_INTEGRATION` as success for the learning loop — `_outcome_success="true"` (lines 2170–2171 update to include the new verdict). `failure_category` becomes the verdict literal as today.

### Manager prompt (`agent/prompts/manager.md`)

Three edits:

1. The `<verdicts>` block (lines 141-170) gains an `APPROVE_INTEGRATION` entry between `APPROVE` and `PR`:

   ```markdown
   ### APPROVE_INTEGRATION
   - Work is complete and tested, but touches sensitive code (auth, payments,
     config) or is large enough to want CI-as-gate before landing.
   - Action: Push branch, open non-draft PR against the integration/dev branch,
     enable auto-merge (`gh pr merge --auto --squash`). CI gates the merge; no
     human review required.
   ```

2. The decision tree gains a branch:

   ```markdown
   - Work complete + normal scope + non-sensitive? → APPROVE
   - Work complete + sensitive (auth/payments/config) + tests pass? → APPROVE_INTEGRATION
   - Work complete + ambiguous requirements OR tests skipped OR scope > 30 files? → PR
   - Work incomplete? → REJECT
   - No work to do? → SKIP
   ```

3. The "Confidence-Based Verdict Modifiers" table is rewritten so the 0.7–0.9 row recommends `APPROVE_INTEGRATION` instead of "Consider PR for human review."

The `<output-format>` block's example verdict accepts the new literal verbatim — no schema change.

### Dashboard surface

- `dashboard/backend/app/routers/runs.py::list_runs` already accepts a `verdict` query param backed by the `Run.verdict` index — adding a new literal value works without schema changes.
- `dashboard/backend/app/models.py:59` — `Run.verdict` is `Text`; no migration needed.
- Frontend verdict filter chips (`dashboard/frontend/src/lib/...` in the run-list view) gain a fourth option. Verdict badge component renders a distinct colour (e.g. teal) for `APPROVE_INTEGRATION`.
- Audit-log render: any place that pretty-prints verdicts needs a label — "Auto-merge to dev" or similar — short enough for table cells.

### `agent_events` event payload

The bash `webhook_event "verdict_execute"` payload (line 2101-2106) keeps the same shape; only the `verdict` field value changes. No event-handler changes on the dashboard side.

## Acceptance criteria

From the issue body, expanded:

- [ ] **`agent/prompts/manager.md`: `APPROVE_INTEGRATION` added to verdict ladder + decision tree.** Concretely: the `<verdicts>` section has a `### APPROVE_INTEGRATION` heading; the decision tree at lines 164-168 has an entry that prefers it for "sensitive + tested" cases; the confidence-modifier table reflects the new tier.
- [ ] **`agent/verdict_execution.py`: new `execute_approve_integration` function.** Mirrors the shape of `execute_approve` but adds the `gh pr merge --auto --squash` step. Registered in `_EXECUTORS`. `Verdict.from_dict` accepts the new literal.
- [ ] **Bash verdict case block handles the new verdict.** A new `APPROVE_INTEGRATION)` arm in `run-manager.sh` near line 2272 with push + non-draft PR + auto-merge. Mirrors the existing `APPROVE` arm's logging and webhook emission.
- [ ] **Dashboard verdict filters / displays the new value.** `verdict` query param accepts the literal; frontend filter chip and badge render distinctly.
- [ ] **Test: simulated auth-PR scenario produces `APPROVE_INTEGRATION` not `PR_FOR_REVIEW`.** A pytest fixture feeds the manager a synthetic review package describing an auth change with passing tests and asserts the verdict literal in the produced JSON. (Implemented as a prompt-regression test invoking `claude -p` with mocked review package — or a unit test against the dispatcher only.)
- [ ] **Production: at least one issue lands on the integration branch via this path.** Tracked manually post-deploy; success indicator on the issue tracker, not in this spec.

## Dependencies / Blocks

- **Independent** — does not depend on any other epic-382 sub-issue. Ships standalone against `dev`.
- **Does not block** [[2026-05-14-issue-386-per-project-containers]] or [[2026-05-14-issue-393-postgres-migration]].
- **Loose synergy** with [[2026-05-14-issue-387-run-timeline-api]] — the timeline view becomes more useful once `APPROVE_INTEGRATION` rows are surfaced as a distinct event kind.

## Risks and rollback

- **Auto-merge enables before CI is green.** GitHub's `gh pr merge --auto` is the safe path here — auto-merge waits for required checks; if branch protection doesn't require checks, the PR merges immediately. Mitigation: document the prerequisite ("integration/dev branch must have at least one required check") in `docs/configuration.md`, and have the bash arm probe `gh api repos/.../branches/$dev_branch/protection` once and warn loudly if no required checks are configured.
- **Manager confusion between `APPROVE_INTEGRATION` and `APPROVE` when integration is disabled.** The bash arm degrades to `APPROVE` with a warning; the audit row records the original literal so post-hoc analysis sees the manager's intent.
- **Verdict-literal proliferation.** Adding a fifth value to a four-way ladder. Mitigation: the dispatcher's "unknown verdict → REJECT" guard (`verdict_execution.py:310`) keeps misspellings safe; the prompt enumerates the four allowed strings explicitly.
- **Rollback**: revert the manager prompt and dispatcher in two small commits. The Run rows with `verdict='APPROVE_INTEGRATION'` are inert under the rollback — the dashboard renders them as a generic string until the prompt no longer emits them.

## Test strategy

- **Unit (pytest, `tests/test_verdict_execution.py`)**: parametrized test exercises each verdict's dispatcher branch; the `APPROVE_INTEGRATION` test mocks `gh_run` and `subprocess.run` and asserts: (1) `git push` issued, (2) `gh pr create` issued WITHOUT `--draft`, (3) `gh pr merge --auto --squash` issued against the returned PR URL, (4) issue comment posted, (5) `ExecutionResult.success == True` and `pr_url` populated.
- **Unit (`tests/test_verdict_execution.py`)**: integration-disabled fallback — assert that the dispatcher logs a warning and degrades to `execute_approve` rather than failing.
- **Manager prompt regression**: a smoke test feeding the prompt a synthetic "sensitive + tested" review package and asserting the verdict literal. Lives under `tests/manager_prompt/`. Optional; valuable but expensive (`claude -p` invocation).
- **Bash smoke**: shell test under `tests/bash/` that stubs `gh` and `git` and runs the new case arm with a synthetic `verdicts.json`. Asserts the order of subprocess calls.
- **Manual production verification**: trigger an auth-touching issue, confirm the manager emits `APPROVE_INTEGRATION`, confirm the PR opens non-draft against `dev` with auto-merge armed, confirm it lands after CI.

## Notes

- The issue body uses both `PR` and `PR_FOR_REVIEW` interchangeably. The existing codebase uses `PR`; this spec keeps `PR` as the literal and treats `PR_FOR_REVIEW` as a friendlier display label for docs/UI. Renaming the literal would force a coordinated migration across `agent/scripts/run-manager.sh`, the `Run.verdict` index, the dashboard filter, and any historical analytics. Out of scope for this issue.
- The issue body also names `APPROVE_DIRECT` as the renamed-`APPROVE`. Same reasoning — kept as `APPROVE` for backwards compat; the prompt and docs can call it "direct merge" in prose without changing the literal.
