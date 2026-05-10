# Conflict Resolution Design

**Date:** 2026-05-10
**Status:** Draft — pending user review

## Problem

Today the agent station creates pull requests against `autonomous/dev` and tries to merge them. When `git push` succeeds but `gh pr merge` fails because of a conflict with the base, `run-manager.sh:2251` logs `PR merge failed — left open for manual review` and the run completes. The conflicting branch sits indefinitely until a human resolves it, which defeats the purpose of an autonomous agent — operators currently have to babysit `dev` to keep the merge queue moving.

A live example on `laboef1900/next-itsm` at the time of writing: PR #19 (10,748 additions / 29 files) and PR #20 (8,120 additions / 23 files) have been `CONFLICTING / DIRTY` since older runs got overtaken by newer work. Neither will ever merge without human intervention.

The agent should resolve conflicts on its own work whenever it can.

## Goals

- After a manager APPROVE/PR verdict, attempt rebase + resolution in the worktree before opening the PR (`pre-PR rebase`).
- When `gh pr merge` fails on conflicts, attempt resolution and retry the merge (`at-merge resolution`).
- Resolve mechanically when possible (rebase, lockfile regen). Fall through to an LLM resolver for semantic conflicts.
- Cap cost with a per-PR token budget so unresolvable cases don't run forever.
- Reuse the existing manager review pipeline as the gate for any LLM-produced commits — a bad resolution faces the same gate any other change faces.

## Non-goals

- A periodic post-PR sweeper that finds existing `CONFLICTING` PRs and tries to resolve them. Useful, but a different kind of project (cron timing, what to do on PRs from runs whose worktrees no longer exist) — out of scope for v1.
- Auto-closing PRs that are too stale or too big to be worth resolving. The operator explicitly rejected a give-up outcome; the token budget is the only termination.
- Cross-PR conflict detection ("PRs #21 and #22 will collide after both merge").
- Multi-base resolution.
- A new dashboard surface. Conflict-resolution attempts surface through existing audit/event infrastructure for v1.

## Lifecycle hooks

Two integration points, both inside `agent/scripts/run-manager.sh`:

**Pre-PR rebase.** A new `rebase_against_base` function runs after the manager APPROVE/PR verdict is decided but before each `gh pr create` block (currently lines ~2210, ~2238, ~2311). Operates in the worktree the teammate left behind. If the rebase produces no conflicts, it's a fast no-op for any branch already in sync.

**At-merge resolution.** The existing `gh pr merge` call at line ~2247 grows an `else` branch. Today it logs `PR merge failed — left open for manual review`. New behaviour: invoke the same `rebase_against_base` helper, retry the merge once on success, fall through to existing manual-review behaviour only if the resolver itself was budget-exhausted or errored.

The actual resolution work lives in a new helper script called by both hooks:

- `agent/scripts/resolve-conflicts.sh` — orchestrates phases mechanical → lockfile → LLM, manages the lockfile, posts webhook events.
- `python -m agent.conflict_resolver` — the LLM resolver itself, invoked by the helper. Uses the Claude Agent SDK the same way `station_orchestrator.py` does and the same audit hooks.

## Resolver phases

`resolve-conflicts.sh` runs a linear pipeline. Each phase only runs if the previous didn't fully resolve.

### Phase 0 — Token budget check

Read `conflict_resolutions` rows for `branch` in the last 24 hours. If `SUM(tokens_total) >= rolling_24h_token_budget`, exit `99` (budget exhausted). Caller posts a `conflict-budget-exhausted` label + comment on the PR with the LLM's last partial attempt (if any) and stops. Loop resumes the next day automatically when the rolling 24h window slides past prior usage.

### Phase 1 — Mechanical rebase

```
git fetch origin
git rebase origin/<base>
```

If clean, push (force-with-lease) and exit `0`.

### Phase 2 — Lockfile regeneration

If the *only* remaining conflicts are in `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, or `Cargo.lock`:

1. `git checkout --theirs <lockfile>` (take base's version).
2. Run the appropriate package manager (`npm install`, `yarn install`, `pnpm install`, `cargo build --offline`) to regenerate the lock from the merged source.
3. `git add <lockfile> && git rebase --continue`.

If clean after this, push and exit `0`. If install fails, log the install error in `conflict_resolutions.error_detail`, abort the rebase, fall through to Phase 3.

### Phase 3 — LLM resolver

Spawn `python -m agent.conflict_resolver --workspace <wt> --base <branch>` in the conflicted worktree. The resolver:

- Uses the configured model (Opus 4.7 default; SDK fallback chain in `agent/launcher.py:196` covers primary failures: Opus 4.7 → Sonnet 4.6 → Haiku 4.5).
- Reads each conflict marker, produces a resolution, runs the project's test command if configured, commits.
- Records turn-by-turn token usage to the `conflict_resolutions` table — Phase 0 reads from the same place on the next attempt.
- Goes through the existing audit hook so every git/edit/bash call lands in `audit_log` keyed by `actor='conflict-resolver'`.
- Runs in the same trust boundary as teammate work — same container, same network access, same filesystem privileges. The project's `test_command` (Phase 4) executes against agent-generated code with the same surface; this is no riskier than a regular teammate run, but worth noting because conflict resolution can run on PRs whose test scripts a human reviewer hasn't yet vetted.
- On producing a resolved commit, writes a synthesized employee report at `<workspace>/.claude-employee-report-conflict-resolver.json` (same shape as PR #332's orchestrator-side synthesizer: `branch`, `base_branch`, `commits`, `files_changed`, `synthesized_by="conflict-resolver"`). This is the hand-off record that lets the existing manager review pipeline (Phase 5) treat the resolution as reviewable work.

### Phase 4 — Validation

If the project has a `test_command` configured (per-project setting), run it on the resolved tree. If absent → skip the test gate, manager review is the only signal.

### Phase 5 — Manager review

Run the existing manager review pipeline on the resolved commit using the synthesized employee report from Phase 3. If APPROVE, push. If REJECT, the resolver's loop (see "Resolution loop" below) decides whether to retry.

### Resolution loop (Phases 3–5 form a cycle)

Phases 3, 4, and 5 are not strictly sequential — they form a feedback cycle gated by a single `attempts_remaining` counter and the budget:

```
attempts_remaining = max_feedback_rounds  (default 3)

loop:
    Phase 3: LLM produces a resolution and synthesized report
    Phase 4: tests run (if configured)
        - pass → fall through to Phase 5
        - fail → feed test output to LLM; attempts_remaining -= 1; restart loop
    Phase 5: manager review
        - APPROVE → exit loop, go to Phase 6 (push)
        - REJECT → feed manager reasoning to LLM; attempts_remaining -= 1; restart loop

    if attempts_remaining == 0 → record outcome=tests_failed (if last failure was tests)
                                  or outcome=manager_rejected (if last was manager); exit
    if budget exhausted at any iteration → record outcome=budget_exhausted; exit
```

Test failures and manager REJECTs share the **same** `attempts_remaining` counter — three failures total across the two gates, not three each. Both consume from the same daily token budget. The budget check is per-iteration; a single LLM turn that exceeds the budget terminates immediately rather than waiting for the next loop boundary.

### Phase 6 — Push

```
git push --force-with-lease origin <branch>
```

Only the PR's *head* branch is force-pushed. Never `dev`, never `main` — the existing branch protection rules cover this. Force-with-lease prevents stomping on a concurrent human push.

### Phase 7 — Comment + label

Always — successful resolutions also leave `🤖 Conflicts auto-resolved (phase N, X tokens)` so reviewers know the diff isn't from a human. On budget exhaustion: `conflict-budget-exhausted` label + comment with the LLM's last partial attempt as a diff in a code block.

The comment also notes the force-push UX caveat when applicable: "this PR was rebased, so any in-flight review comments may now show as outdated against rewritten commits." This sets expectations for human reviewers without trying to fix it (GitHub's PR review UI is what it is).

The `conflict-budget-exhausted` label is created on first use via idempotent `gh label create conflict-budget-exhausted --color D93F0B --description "..." 2>/dev/null || true` so the resolver doesn't fail on a fresh repo where the label doesn't exist yet.

## Pre-attempt advisory tiers

The thresholds from the brainstorm act as advisory inputs to model selection, not hard refusals. The resolver still attempts every conflict — the tiers just inform how much budget and which model to throw at it:

| Condition | Effect |
|---|---|
| Conflict diff > 500 lines | Start with Opus 4.7 (skip Sonnet attempt). Increase `max_turns`. |
| Conflicting files > 10 | Same as above. |
| PR older than 7 days | Same. Plus include "this PR has been stale; the base may have diverged significantly" in the system prompt. |
| Already-attempted-and-still-dirty | Include the prior attempt's failure reason in the system prompt as context. |

These advisory tiers live in the resolver's prompt assembly, not in `resolve-conflicts.sh`'s control flow.

## Storage

A new `conflict_resolutions` table keyed on `branch` for the budget query.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `branch` | TEXT NOT NULL | the head branch — drives all queries |
| `repo` | TEXT NOT NULL | `owner/name` |
| `pr_number` | INTEGER | NULL for pre-PR attempts |
| `started_at` | TIMESTAMP NOT NULL | |
| `finished_at` | TIMESTAMP | NULL while in flight |
| `phase_reached` | TEXT NOT NULL | `mechanical` / `lockfile` / `llm` / `budget_exhausted` |
| `outcome` | TEXT NOT NULL | `resolved` / `tests_failed` / `manager_rejected` / `budget_exhausted` / `error` |
| `tokens_input` | INTEGER | sum across all LLM turns this attempt |
| `tokens_output` | INTEGER | |
| `tokens_total` | INTEGER | denormalized for cheap budget queries |
| `model_used` | TEXT | e.g. `claude-opus-4-7` |
| `feedback_rounds` | INTEGER | how many times we retried after test/manager failures |
| `triggered_by` | TEXT NOT NULL | `pre_pr` / `at_merge` |
| `run_id` | TEXT | links to the run that owned this attempt, NULL if standalone |
| `error_detail` | TEXT | for debugging when `outcome=error` |

Indexed on `(branch, started_at)` for the budget query:

```sql
SELECT SUM(tokens_total) FROM conflict_resolutions
WHERE branch = ? AND started_at > now() - INTERVAL '24 hours'
```

Migration follows the additive pattern in `dashboard/backend/app/database.py`.

## Configuration

Additions to `manager-config.json`, all optional with sensible defaults:

```json
{
  "conflict_resolution": {
    "enabled": true,
    "rolling_24h_token_budget": 200000,
    "max_feedback_rounds": 3,
    "model": "claude-opus-4-7",
    "max_turns": 30,
    "lock_ttl_seconds": 1800,
    "force_push_with_lease": true
  }
}
```

Per-project overrides (rare) live under `projects[].conflict_resolution` matching the existing override pattern. Example for a project that should disable LLM resolution entirely (e.g. compliance-sensitive repos where mechanical-only is the only acceptable outcome):

```json
{
  "projects": [
    {
      "repo": "acme/compliance-sensitive",
      "conflict_resolution": {
        "enabled": true,
        "model": null,
        "rolling_24h_token_budget": 0
      }
    }
  ]
}
```

A `rolling_24h_token_budget` of 0 means Phase 3 is unreachable (Phase 0 always exhausts immediately); the resolver still runs Phases 1–2 (mechanical + lockfile) on those repos.

A new optional **per-project** field for the validation phase:

```json
{
  "repo": "laboef1900/next-itsm",
  "test_command": "npm test --silent"
}
```

If absent → skip the test gate.

## Concurrency

Concurrent resolution attempts on the same PR are prevented by an `flock`-protected lockfile at `/var/lib/claude-agent-station/locks/conflict-<branch>.lock`. Stale lock auto-released after `lock_ttl_seconds` (default 30min). The `flock` is taken at the start of `resolve-conflicts.sh` and released on exit (including via trap).

## Error handling matrix

| Failure | What happens |
|---|---|
| `git fetch origin` fails (network) | retry once, then exit non-zero, leave PR untouched, log warning. Next run retries. |
| `git rebase` produces a merge conflict | continue to Phase 2/3 — that's the design |
| `npm install` (or equivalent) fails on lockfile regen | abort the lockfile attempt, fall through to Phase 3, log install error in `error_detail` |
| LLM provider returns 5xx | SDK fallback chain handles it. If exhausted, finalize attempt with `outcome=error`. |
| Tests fail after every feedback round within budget | `outcome=tests_failed`, comment on PR with last failing test output, no push |
| Manager review REJECTs and budget exhausted | `outcome=manager_rejected`, same as tests_failed |
| `git push --force-with-lease` rejected (someone else pushed) | re-enter the pipeline at Phase 1 (mechanical rebase) — a concurrent push may have introduced new conflicts. Phase 0 still applies, so the retry consumes from the same budget. If the second push also fails, `outcome=error`, comment on PR. |
| Stale flock from a crashed prior attempt | TTL releases at `lock_ttl_seconds`. Recovery automatic. |
| `gh` CLI auth missing | refuse to start, log error. Same precondition the rest of run-manager.sh requires. |

## Observability

- Every phase transition emits a webhook event (`conflict_resolution_started`, `conflict_resolution_phase`, `conflict_resolution_completed`) the dashboard picks up via the existing `/api/webhook/run-event` endpoint. New `WebhookRunEvent` fields not required — these reuse the existing `event` field with `summary` JSON for phase/tokens.
- Every git command and LLM call goes through the same audit hook teammates use, so `audit_log` rows for conflict resolution show up in the existing audit timeline keyed by `actor='conflict-resolver'`.
- The `[hook-cb-fail]` marker from PR #333 covers SDK stream-close issues during the LLM phase too.
- v1 dashboard surface: Run Detail page renders any `conflict_resolutions` rows linked to the run via `run_id`. No new pages.

## Testing

- **Python unit (pytest)**: `agent.conflict_resolver` parsing of conflict markers, prompt assembly, budget query helper. Located at `dashboard/backend/tests/test_conflict_resolver.py` matching existing conventions.
- **Bash unit**: `agent/scripts/tests/test_resolve_conflicts_helpers.sh` covers `rebase_against_base`, the lockfile-only-conflict predicate, and the budget query.
- **Integration**: `test_conflict_e2e.py` creates a tiny git repo with a contrived lockfile-only conflict, runs the resolver against a mocked Anthropic API, asserts the rebase + push flow. Gated by `RUN_E2E=1` so normal CI stays fast.
- **Manual verification**: the next *real* run that produces a conflict will exercise this. The existing test PRs (#19/#20) are too big and stale to test v1 against — they're a stretch goal once a sweeper is built.

## File and component summary

**New files:**

- `agent/scripts/resolve-conflicts.sh` — phase orchestrator
- `agent/scripts/tests/test_resolve_conflicts_helpers.sh` — bash unit tests
- `agent/conflict_resolver.py` — Python LLM resolver (Claude Agent SDK)
- `agent/prompts/conflict_resolver.md` — system prompt for the resolver. Must cover at minimum:
  - **Inputs the resolver sees**: the conflicted file paths, base branch, head branch, prior attempt's failure reason (if any), advisory tier flags from "Pre-attempt advisory tiers".
  - **Operating procedure**: read each conflict marker fully (both `<<<<<<<` and `>>>>>>>` sides + surrounding context), resolve in-place, run `test_command` if configured, commit with a descriptive message starting `chore(resolve): `.
  - **Uncertainty handling**: when the right resolution isn't obvious from local context (e.g. semantic disagreement between two implementations), the resolver may use the project's `gh issue view` and `git log -p` to gather more context before resolving — but must NOT fabricate behavior not present in either side.
  - **Stop conditions**: the resolver returns when commits are clean and tests pass, or when it judges further attempts won't help (signals to the harness with a structured exit). The harness is what enforces budget; the prompt's job is to make the resolver decisive within its turn.
  - **Hard prohibitions**: no force-pushing (the harness handles push), no merging into base, no edits outside the conflict regions unless required to make the resolution compile.
- `dashboard/backend/tests/test_conflict_resolver.py` — Python unit tests
- `dashboard/backend/tests/test_conflict_e2e.py` — gated integration test

**Modified files:**

- `agent/scripts/run-manager.sh` — add `rebase_against_base` helper + `else` branch on `gh pr merge` failure
- `dashboard/backend/app/models.py` — add `ConflictResolution` ORM model
- `dashboard/backend/app/database.py` — additive migration creating the table
- `dashboard/backend/app/main.py` — register `/api/webhook/run-event` already handles new event names; no router changes required
- `agent/config/manager-config.example.json` — document new config keys
- `docs/configuration.md` + `docs/architecture.md` — sync with the new components

**No changes:**

- Frontend. v1 ships purely via existing audit + event surfaces.

## Open questions

None — all decisions resolved during brainstorming.
