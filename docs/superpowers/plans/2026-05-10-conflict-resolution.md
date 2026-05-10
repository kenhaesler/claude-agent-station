# Conflict Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a layered conflict resolver that runs pre-PR (in worktree, before `gh pr create`) and at-merge (when `gh pr merge` fails), attempts mechanical → lockfile → LLM resolution within a rolling 24-hour token budget per branch, and reuses the existing manager-review pipeline as the gate before push.

**Architecture:** A new bash helper `resolve-conflicts.sh` orchestrates phases 0–7 (budget check → mechanical → lockfile → LLM → validation → manager review → push → comment). The LLM phase delegates to a Python entry point `agent.conflict_resolver` that uses the Claude Agent SDK with the existing audit hooks. Token usage is recorded per attempt in a new `conflict_resolutions` SQLAlchemy table; the budget query is a single-row aggregate over the rolling 24h window keyed on the head branch. Two integration points in `run-manager.sh` invoke the helper: pre-PR (after manager APPROVE/PR verdict, before each `gh pr create`) and at-merge (when `gh pr merge` fails).

**Tech Stack:** Python 3.11, SQLAlchemy 2 async, Claude Agent SDK, bash 5+, `flock`, `gh` CLI, `git` 2.30+ (for `--force-with-lease`).

**Spec:** `docs/superpowers/specs/2026-05-10-conflict-resolution-design.md`

---

## File Structure

**New files (Python):**

- `dashboard/backend/app/models.py` — add `ConflictResolution` ORM class (the existing file grows; doesn't warrant a new module).
- `agent/conflict_resolver/__init__.py` — package init.
- `agent/conflict_resolver/__main__.py` — `python -m agent.conflict_resolver` entry point. Argument parsing + dispatch only.
- `agent/conflict_resolver/budget.py` — pure-function budget query and recording. SQLAlchemy session-aware but no I/O policy of its own.
- `agent/conflict_resolver/markers.py` — pure-function git-conflict-marker parser (returns list of `ConflictRegion` records). No git invocation.
- `agent/conflict_resolver/sdk_runner.py` — Claude Agent SDK wrapper that runs the resolver loop with audit hooks attached.
- `agent/conflict_resolver/prompts.py` — prompt assembly (system prompt + per-attempt context). Returns strings; doesn't invoke the SDK.
- `agent/prompts/conflict_resolver.md` — the system prompt body, loaded by `prompts.py`.

**New files (bash):**

- `agent/scripts/resolve-conflicts.sh` — phase orchestrator (Phases 0–7). Calls into `python -m agent.conflict_resolver` for Phase 3.
- `agent/scripts/lib/conflict-helpers.sh` — sourced by `resolve-conflicts.sh`. Holds `is_lockfile_only_conflict`, `regen_lockfile`, `take_flock`, `release_flock` helpers.

**New files (tests):**

- `dashboard/backend/tests/test_conflict_resolver_budget.py` — budget query / recording / 24h window edge cases.
- `dashboard/backend/tests/test_conflict_resolver_markers.py` — marker parser unit tests.
- `dashboard/backend/tests/test_conflict_resolver_prompts.py` — prompt assembly unit tests.
- `dashboard/backend/tests/test_conflict_resolutions_table.py` — model creation / migration / index existence.
- `dashboard/backend/tests/test_webhook_conflict_resolution.py` — webhook event persistence (mirrors `test_webhook_hook_failures.py` from PR #333).
- `agent/scripts/tests/test_conflict_helpers.sh` — bash unit tests for `is_lockfile_only_conflict` etc.
- `dashboard/backend/tests/test_conflict_e2e.py` — integration test, gated by `RUN_E2E=1`.

**Modified files:**

- `dashboard/backend/app/database.py` — register `ConflictResolution` in `init_db`'s import list, add the index migrations.
- `dashboard/backend/app/schemas.py` — add fields to `WebhookRunEvent` (`phase`, `attempts_remaining`, plus reusing existing `count` for tokens).
- `dashboard/backend/app/routers/webhook.py` — add a handler for `conflict_resolution_*` event names.
- `agent/scripts/run-manager.sh` — add `rebase_against_base` helper, call `resolve-conflicts.sh` at two integration points.
- `agent/config/manager-config.example.json` — document new `conflict_resolution` config block.
- `docs/configuration.md` — document the new config keys.
- `docs/architecture.md` — add the resolver to the component diagram + section.

**Reused (no changes):**

- `agent/audit_hook.py` — `make_pre_tool_hook` / `make_post_tool_hook` for audit_log writes.
- `agent/auto_mode.py` — `make_audited_policy` for `can_use_tool` enforcement.

---

## Conventions

- Python: type hints on every function. Tests in `dashboard/backend/tests/`. Run with `cd dashboard/backend && pytest`.
- Bash: `set -euo pipefail` at top of every new script. `shellcheck` clean. Tests in `agent/scripts/tests/`.
- Branch: `feature/conflict-resolution` for the whole plan; PR opened in the final task against `dev`.
- Commits: each task ends with a single commit. Conventional format (`feat(agent): ...` / `test(agent): ...` / `chore: ...`).

---

## Task 1: Create the feature branch

**Files:** none

- [ ] **Step 1: Branch off `dev`**

```bash
cd /home/simon/Documents/claude-agent-station
git checkout dev
git pull --ff-only
git checkout -b feature/conflict-resolution
```

- [ ] **Step 2: Verify branch**

```bash
git status
git branch --show-current
```

Expected: clean working tree, branch is `feature/conflict-resolution`.

---

## Task 2: Add the `ConflictResolution` model (TDD)

The DB layer is the dependency for everything else (budget query, recording, webhook). Build it first.

**Files:**
- Create: `dashboard/backend/tests/test_conflict_resolutions_table.py`
- Modify: `dashboard/backend/app/models.py`
- Modify: `dashboard/backend/app/database.py`

- [ ] **Step 1: Write the failing test**

```python
# dashboard/backend/tests/test_conflict_resolutions_table.py
"""Tests for the conflict_resolutions table schema and indexes."""

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.database import Base, engine, async_session
from app.models import ConflictResolution


@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_table_exists_with_required_columns(setup_db):
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(conflict_resolutions)"))
        columns = {row[1] for row in result.fetchall()}
    required = {
        "id", "branch", "repo", "pr_number", "started_at", "finished_at",
        "phase_reached", "outcome", "tokens_input", "tokens_output",
        "tokens_total", "model_used", "feedback_rounds", "triggered_by",
        "run_id", "error_detail",
    }
    assert required.issubset(columns), f"missing columns: {required - columns}"


@pytest.mark.asyncio
async def test_index_on_branch_and_started_at(setup_db):
    async with engine.begin() as conn:
        result = await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='conflict_resolutions'"
        ))
        indexes = {row[0] for row in result.fetchall()}
    assert any("branch" in idx for idx in indexes), \
        f"expected an index covering 'branch', got: {indexes}"


@pytest.mark.asyncio
async def test_insert_and_query_minimal_row(setup_db):
    from datetime import datetime, timezone
    async with async_session() as db:
        row = ConflictResolution(
            branch="feature/x",
            repo="owner/repo",
            started_at=datetime.now(timezone.utc),
            phase_reached="mechanical",
            outcome="resolved",
            triggered_by="pre_pr",
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        assert row.id is not None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest tests/test_conflict_resolutions_table.py -v
```

Expected: FAIL with `ImportError: cannot import name 'ConflictResolution' from 'app.models'`.

- [ ] **Step 3: Add the ORM model**

Append to `dashboard/backend/app/models.py` after the `AgentEvent` class (around line 280):

```python
class ConflictResolution(Base):
    """One conflict-resolution attempt per row.

    Keyed on (branch, started_at) for the rolling 24h budget query in
    agent.conflict_resolver.budget. See spec
    docs/superpowers/specs/2026-05-10-conflict-resolution-design.md.
    """
    __tablename__ = "conflict_resolutions"

    id = Column(Integer, primary_key=True)
    branch = Column(Text, nullable=False, index=True)
    repo = Column(Text, nullable=False)
    pr_number = Column(Integer, nullable=True)
    started_at = Column(DateTime, nullable=False, default=_utcnow, index=True)
    finished_at = Column(DateTime, nullable=True)
    # mechanical / lockfile / llm / budget_exhausted
    phase_reached = Column(Text, nullable=False)
    # resolved / tests_failed / manager_rejected / budget_exhausted / error
    outcome = Column(Text, nullable=False)
    tokens_input = Column(Integer, nullable=True)
    tokens_output = Column(Integer, nullable=True)
    # Denormalized for cheap budget queries (sum across input + output).
    tokens_total = Column(Integer, nullable=True)
    model_used = Column(Text, nullable=True)
    # How many feedback rounds were consumed across tests + manager review.
    feedback_rounds = Column(Integer, nullable=True, default=0)
    # pre_pr / at_merge
    triggered_by = Column(Text, nullable=False)
    run_id = Column(Text, nullable=True)
    error_detail = Column(Text, nullable=True)
```

- [ ] **Step 4: Register in `init_db`**

In `dashboard/backend/app/database.py`, find the import block in `init_db()` (around line 130) and add `ConflictResolution` alphabetically:

```python
        from app.models import (  # noqa: F401
            AgentEvent,
            AuditEntry,
            BrainstormMessage,
            BrainstormSession,
            ConfigEntry,
            ConflictResolution,  # ← add
            CoordinatorMessage,
            ...
        )
```

- [ ] **Step 5: Add composite index migration**

In `dashboard/backend/app/database.py` around line 113, add to the `index_migrations` list:

```python
        "CREATE INDEX IF NOT EXISTS ix_conflict_resolutions_branch_started "
        "ON conflict_resolutions(branch, started_at)",
```

- [ ] **Step 6: Run test to verify it passes**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest tests/test_conflict_resolutions_table.py -v
```

Expected: 3 PASSING.

- [ ] **Step 7: Commit**

```bash
cd /home/simon/Documents/claude-agent-station
git add dashboard/backend/tests/test_conflict_resolutions_table.py \
        dashboard/backend/app/models.py \
        dashboard/backend/app/database.py
git -c commit.gpgsign=false commit -m "feat(db): conflict_resolutions table for budget tracking

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Budget query + recording (TDD)

Pure function over the new table. The 24h window is the only behaviour worth testing.

**Files:**
- Create: `dashboard/backend/tests/test_conflict_resolver_budget.py`
- Create: `agent/conflict_resolver/__init__.py`
- Create: `agent/conflict_resolver/budget.py`

- [ ] **Step 1: Write the failing tests**

```python
# dashboard/backend/tests/test_conflict_resolver_budget.py
"""Tests for the rolling 24h budget query."""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.database import Base, engine, async_session
from app.models import ConflictResolution
from agent.conflict_resolver.budget import (
    tokens_used_in_window,
    record_attempt_start,
    record_attempt_finish,
)


@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_no_rows_returns_zero(setup_db):
    async with async_session() as db:
        used = await tokens_used_in_window(db, branch="feature/x", window_hours=24)
    assert used == 0


@pytest.mark.asyncio
async def test_sums_only_within_window(setup_db):
    now = datetime.now(timezone.utc)
    async with async_session() as db:
        # 23h ago — counts
        db.add(ConflictResolution(
            branch="feature/x", repo="owner/r",
            started_at=now - timedelta(hours=23),
            finished_at=now - timedelta(hours=23),
            phase_reached="llm", outcome="resolved",
            tokens_total=5000, triggered_by="pre_pr",
        ))
        # 25h ago — does not count
        db.add(ConflictResolution(
            branch="feature/x", repo="owner/r",
            started_at=now - timedelta(hours=25),
            finished_at=now - timedelta(hours=25),
            phase_reached="llm", outcome="resolved",
            tokens_total=99999, triggered_by="pre_pr",
        ))
        await db.commit()
        used = await tokens_used_in_window(db, branch="feature/x", window_hours=24)
    assert used == 5000


@pytest.mark.asyncio
async def test_only_counts_matching_branch(setup_db):
    now = datetime.now(timezone.utc)
    async with async_session() as db:
        db.add(ConflictResolution(
            branch="feature/x", repo="owner/r", started_at=now,
            phase_reached="llm", outcome="resolved",
            tokens_total=1000, triggered_by="pre_pr",
        ))
        db.add(ConflictResolution(
            branch="feature/y", repo="owner/r", started_at=now,
            phase_reached="llm", outcome="resolved",
            tokens_total=99999, triggered_by="pre_pr",
        ))
        await db.commit()
        used = await tokens_used_in_window(db, branch="feature/x", window_hours=24)
    assert used == 1000


@pytest.mark.asyncio
async def test_record_start_returns_attempt_id(setup_db):
    async with async_session() as db:
        attempt_id = await record_attempt_start(
            db, branch="feature/x", repo="owner/r",
            triggered_by="pre_pr", run_id="run-test-001",
        )
    assert isinstance(attempt_id, int)
    assert attempt_id > 0


@pytest.mark.asyncio
async def test_record_finish_updates_row(setup_db):
    async with async_session() as db:
        attempt_id = await record_attempt_start(
            db, branch="feature/x", repo="owner/r",
            triggered_by="pre_pr", run_id="run-test-002",
        )
        await record_attempt_finish(
            db, attempt_id=attempt_id,
            phase_reached="llm", outcome="resolved",
            tokens_input=1000, tokens_output=500, tokens_total=1500,
            model_used="claude-opus-4-7", feedback_rounds=1,
        )
        row = (await db.execute(
            select(ConflictResolution).where(ConflictResolution.id == attempt_id)
        )).scalar_one()
    assert row.outcome == "resolved"
    assert row.tokens_total == 1500
    assert row.finished_at is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest tests/test_conflict_resolver_budget.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'agent.conflict_resolver'`.

- [ ] **Step 3: Create the package**

```bash
mkdir -p /home/simon/Documents/claude-agent-station/agent/conflict_resolver
```

```python
# agent/conflict_resolver/__init__.py
"""Conflict resolver — see docs/superpowers/specs/2026-05-10-conflict-resolution-design.md."""
```

- [ ] **Step 4: Implement the budget module**

```python
# agent/conflict_resolver/budget.py
"""Budget query + attempt recording for the conflict resolver.

Pure functions over the conflict_resolutions table. The rolling 24h budget
is computed per-branch by summing tokens_total over rows whose started_at
falls in the window. See spec
docs/superpowers/specs/2026-05-10-conflict-resolution-design.md (Phase 0).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConflictResolution


async def tokens_used_in_window(
    db: AsyncSession,
    *,
    branch: str,
    window_hours: int = 24,
) -> int:
    """Sum tokens_total for `branch` over the rolling window."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    result = await db.execute(
        select(func.coalesce(func.sum(ConflictResolution.tokens_total), 0))
        .where(ConflictResolution.branch == branch)
        .where(ConflictResolution.started_at >= cutoff)
    )
    return int(result.scalar() or 0)


async def record_attempt_start(
    db: AsyncSession,
    *,
    branch: str,
    repo: str,
    triggered_by: str,
    run_id: str | None = None,
    pr_number: int | None = None,
) -> int:
    """Insert a new in-flight attempt; return its id.

    finished_at, outcome, and token totals are filled in by record_attempt_finish.
    Defaults phase_reached='mechanical' so a crashed attempt isn't ambiguous —
    callers update it as they progress.
    """
    row = ConflictResolution(
        branch=branch,
        repo=repo,
        triggered_by=triggered_by,
        run_id=run_id,
        pr_number=pr_number,
        phase_reached="mechanical",
        outcome="error",  # default; finalize on success
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return int(row.id)


async def record_attempt_finish(
    db: AsyncSession,
    *,
    attempt_id: int,
    phase_reached: str,
    outcome: str,
    tokens_input: int | None = None,
    tokens_output: int | None = None,
    tokens_total: int | None = None,
    model_used: str | None = None,
    feedback_rounds: int = 0,
    error_detail: str | None = None,
) -> None:
    """Finalize an in-flight attempt."""
    row = (await db.execute(
        select(ConflictResolution).where(ConflictResolution.id == attempt_id)
    )).scalar_one()
    row.phase_reached = phase_reached
    row.outcome = outcome
    row.finished_at = datetime.now(timezone.utc)
    if tokens_input is not None:
        row.tokens_input = tokens_input
    if tokens_output is not None:
        row.tokens_output = tokens_output
    if tokens_total is not None:
        row.tokens_total = tokens_total
    if model_used is not None:
        row.model_used = model_used
    row.feedback_rounds = feedback_rounds
    if error_detail is not None:
        row.error_detail = error_detail
    await db.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest tests/test_conflict_resolver_budget.py -v
```

Expected: 5 PASSING.

- [ ] **Step 6: Commit**

```bash
cd /home/simon/Documents/claude-agent-station
git add agent/conflict_resolver/__init__.py \
        agent/conflict_resolver/budget.py \
        dashboard/backend/tests/test_conflict_resolver_budget.py
git -c commit.gpgsign=false commit -m "feat(agent): conflict resolver budget tracking

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Conflict-marker parser (TDD)

Pure function over the textual content of a conflicted file. Used by both the LLM phase (to know what to resolve) and the lockfile-only-conflict predicate (to know whether Phase 2 applies).

**Files:**
- Create: `dashboard/backend/tests/test_conflict_resolver_markers.py`
- Create: `agent/conflict_resolver/markers.py`

- [ ] **Step 1: Write the failing tests**

```python
# dashboard/backend/tests/test_conflict_resolver_markers.py
"""Tests for the git-conflict-marker parser."""

from agent.conflict_resolver.markers import (
    ConflictRegion,
    parse_conflict_markers,
    file_has_conflicts,
    LOCKFILE_NAMES,
)


def test_no_markers_returns_empty():
    assert parse_conflict_markers("plain content\nwith no markers\n") == []


def test_single_region_returns_one():
    src = """before
<<<<<<< HEAD
ours
=======
theirs
>>>>>>> base
after
"""
    regions = parse_conflict_markers(src)
    assert len(regions) == 1
    r = regions[0]
    assert isinstance(r, ConflictRegion)
    assert r.ours_lines == ["ours"]
    assert r.theirs_lines == ["theirs"]


def test_two_regions_returned_in_order():
    src = """top
<<<<<<< HEAD
a
=======
b
>>>>>>> base
middle
<<<<<<< HEAD
c
=======
d
>>>>>>> base
bottom
"""
    regions = parse_conflict_markers(src)
    assert len(regions) == 2
    assert regions[0].ours_lines == ["a"]
    assert regions[1].theirs_lines == ["d"]


def test_file_has_conflicts_detects_markers():
    assert file_has_conflicts("<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> base\n") is True
    assert file_has_conflicts("no markers\n") is False


def test_lockfile_names_includes_common_managers():
    # Used by the lockfile-only-conflict predicate.
    assert "package-lock.json" in LOCKFILE_NAMES
    assert "yarn.lock" in LOCKFILE_NAMES
    assert "pnpm-lock.yaml" in LOCKFILE_NAMES
    assert "Cargo.lock" in LOCKFILE_NAMES
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest tests/test_conflict_resolver_markers.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the parser**

```python
# agent/conflict_resolver/markers.py
"""Parse git conflict markers from a conflicted file.

Pure functions — no git, no I/O. The output feeds the LLM resolver
prompt assembly and the "is this a lockfile-only conflict?" predicate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# Set so callers can membership-test efficiently.
LOCKFILE_NAMES: Final[frozenset[str]] = frozenset({
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Cargo.lock",
})


@dataclass(frozen=True)
class ConflictRegion:
    """One <<<<<<< ... ======= ... >>>>>>> region in a file."""
    ours_lines: list[str]
    theirs_lines: list[str]
    # Line numbers (1-based) of the marker lines themselves, for prompt context.
    start_line: int
    middle_line: int
    end_line: int


def file_has_conflicts(text: str) -> bool:
    """Cheap markerless-fast-path for callers that only need a yes/no answer."""
    return "<<<<<<< " in text and "=======" in text and ">>>>>>> " in text


def parse_conflict_markers(text: str) -> list[ConflictRegion]:
    """Return all conflict regions in `text`, in source order."""
    regions: list[ConflictRegion] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].startswith("<<<<<<< "):
            start = i
            ours: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("======="):
                ours.append(lines[i])
                i += 1
            if i >= len(lines):
                # malformed — bail
                return regions
            middle = i
            theirs: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith(">>>>>>> "):
                theirs.append(lines[i])
                i += 1
            if i >= len(lines):
                return regions
            end = i
            regions.append(ConflictRegion(
                ours_lines=ours, theirs_lines=theirs,
                start_line=start + 1, middle_line=middle + 1, end_line=end + 1,
            ))
        i += 1
    return regions
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest tests/test_conflict_resolver_markers.py -v
```

Expected: 5 PASSING.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station
git add agent/conflict_resolver/markers.py \
        dashboard/backend/tests/test_conflict_resolver_markers.py
git -c commit.gpgsign=false commit -m "feat(agent): git conflict-marker parser

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Prompt assembly + system prompt body (TDD)

The LLM resolver's behaviour lives in the prompt. Test that the assembler injects all required context.

**Files:**
- Create: `dashboard/backend/tests/test_conflict_resolver_prompts.py`
- Create: `agent/conflict_resolver/prompts.py`
- Create: `agent/prompts/conflict_resolver.md`

- [ ] **Step 1: Write the failing tests**

```python
# dashboard/backend/tests/test_conflict_resolver_prompts.py
"""Tests for prompt assembly."""

from agent.conflict_resolver.prompts import (
    build_resolver_prompt,
    AdvisoryTier,
)


def test_includes_branch_and_base():
    out = build_resolver_prompt(
        branch="feature/foo", base_branch="autonomous/dev",
        conflicted_files=["src/a.ts"], advisory_tiers=set(),
        prior_failure_reason=None,
    )
    assert "feature/foo" in out
    assert "autonomous/dev" in out
    assert "src/a.ts" in out


def test_advisory_large_diff_adds_warning():
    out = build_resolver_prompt(
        branch="b", base_branch="dev", conflicted_files=["a"],
        advisory_tiers={AdvisoryTier.LARGE_DIFF},
        prior_failure_reason=None,
    )
    assert "large" in out.lower()


def test_advisory_stale_pr_adds_divergence_warning():
    out = build_resolver_prompt(
        branch="b", base_branch="dev", conflicted_files=["a"],
        advisory_tiers={AdvisoryTier.STALE_PR},
        prior_failure_reason=None,
    )
    assert "stale" in out.lower() or "diverged" in out.lower()


def test_prior_failure_is_included():
    out = build_resolver_prompt(
        branch="b", base_branch="dev", conflicted_files=["a"],
        advisory_tiers=set(),
        prior_failure_reason="tests failed: TypeError in foo()",
    )
    assert "TypeError" in out


def test_no_prior_failure_omits_section():
    out = build_resolver_prompt(
        branch="b", base_branch="dev", conflicted_files=["a"],
        advisory_tiers=set(), prior_failure_reason=None,
    )
    # Don't emit a "previous attempt" section when there was no previous attempt.
    assert "previous attempt" not in out.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: module not found.

- [ ] **Step 3: Write the system prompt body**

```markdown
<!-- agent/prompts/conflict_resolver.md -->
You are the **conflict resolver** for the Claude Agent Station. The agent has
produced a feature branch and merging it into the project's base branch
produced git conflict markers. Your job: resolve those markers, run the
project's tests if they exist, and commit a clean tree.

## Operating procedure

1. `cd` to the worktree path you were given.
2. Read each conflict marker fully — both `<<<<<<<` and `>>>>>>>` sides plus
   surrounding context. Do not skim.
3. Resolve in place. Choose ours, theirs, both, or a synthesis — whichever
   preserves the *intent* of both branches. When in doubt, prefer the side
   that aligns with the issue the feature branch was implementing.
4. If the project has a test command (you'll be told if so), run it. Fix
   anything that breaks until tests pass within your turn budget.
5. Commit with a descriptive message starting with `chore(resolve): ` and
   referencing the conflicting files.

## Uncertainty handling

When the right resolution isn't obvious from local context (e.g. the two
sides take semantically incompatible approaches), you MAY use:

- `gh issue view <N>` to read the issue the branch was implementing.
- `git log -p <base>..HEAD` to read the head branch's history.
- `git log -p HEAD..<base>` to read the base branch's history since the
  branch diverged.

You MUST NOT fabricate behaviour not present in either side. If neither
side does X and the merged tree won't compile without X, abort and
explain — do not invent.

## Stop conditions

Return when commits are clean and tests pass, OR when you judge further
attempts won't help. The harness enforces budget; your job is to be
decisive within your turn budget. Do not loop on the same edit.

## Hard prohibitions

- Do NOT push (the harness handles push).
- Do NOT merge into the base branch.
- Do NOT edit files outside the conflict regions unless required to make
  the resolution compile or pass tests.
- Do NOT close the PR or modify its labels.
```

- [ ] **Step 4: Implement `prompts.py`**

```python
# agent/conflict_resolver/prompts.py
"""Prompt assembly for the conflict resolver.

Loads the system prompt body from agent/prompts/conflict_resolver.md and
injects per-attempt context: branch, base, file list, advisory tiers, prior
failure reason. See spec
docs/superpowers/specs/2026-05-10-conflict-resolution-design.md.
"""

from __future__ import annotations

from enum import Enum, auto
from pathlib import Path

_PROMPT_BODY_PATH = Path(__file__).parent.parent / "prompts" / "conflict_resolver.md"


class AdvisoryTier(Enum):
    """Conditions that adjust the resolver's strategy. See spec 'Pre-attempt advisory tiers'."""
    LARGE_DIFF = auto()      # >500 lines conflicting
    MANY_FILES = auto()      # >10 files conflicting
    STALE_PR = auto()        # PR older than 7 days
    PRIOR_ATTEMPT = auto()   # already-attempted-and-still-dirty


def build_resolver_prompt(
    *,
    branch: str,
    base_branch: str,
    conflicted_files: list[str],
    advisory_tiers: set[AdvisoryTier],
    prior_failure_reason: str | None,
) -> str:
    """Assemble the full prompt: body + injected context."""
    body = _PROMPT_BODY_PATH.read_text()
    parts: list[str] = [body, "", "## Run-specific context"]
    parts.append(f"- Branch: `{branch}`")
    parts.append(f"- Base: `{base_branch}`")
    parts.append("- Conflicted files:")
    for f in conflicted_files:
        parts.append(f"  - `{f}`")

    if AdvisoryTier.LARGE_DIFF in advisory_tiers:
        parts.append("")
        parts.append("⚠️  This is a **large** conflict (>500 lines). Be deliberate; "
                     "read both sides fully before editing.")
    if AdvisoryTier.MANY_FILES in advisory_tiers:
        parts.append("")
        parts.append("⚠️  This conflict touches many files. Resolve them in dependency "
                     "order where possible.")
    if AdvisoryTier.STALE_PR in advisory_tiers:
        parts.append("")
        parts.append("⚠️  This PR is **stale** (>7 days old). The base may have diverged "
                     "significantly; expect the merged tree to differ from what the "
                     "feature branch was tested against.")
    if AdvisoryTier.PRIOR_ATTEMPT in advisory_tiers:
        parts.append("")
        parts.append("⚠️  A previous attempt at resolving this conflict failed. Use the "
                     "failure reason below to avoid the same trap.")

    if prior_failure_reason:
        parts.append("")
        parts.append("## Previous attempt failed")
        parts.append("")
        parts.append(prior_failure_reason)

    return "\n".join(parts)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest tests/test_conflict_resolver_prompts.py -v
```

Expected: 5 PASSING.

- [ ] **Step 6: Commit**

```bash
cd /home/simon/Documents/claude-agent-station
git add agent/conflict_resolver/prompts.py \
        agent/prompts/conflict_resolver.md \
        dashboard/backend/tests/test_conflict_resolver_prompts.py
git -c commit.gpgsign=false commit -m "feat(agent): conflict resolver prompt assembly

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: SDK runner — Phase 3 LLM driver

Wraps the Claude Agent SDK with the existing audit hooks. No tests here — this is integration code; the e2e test in Task 13 covers it end-to-end.

**Files:**
- Create: `agent/conflict_resolver/sdk_runner.py`

- [ ] **Step 1: Implement the runner**

```python
# agent/conflict_resolver/sdk_runner.py
"""Run the Claude Agent SDK with the conflict-resolution prompt.

Reuses the audit hooks and policy engine from agent.audit_hook and
agent.auto_mode so every git/edit/bash call lands in audit_log keyed by
actor='conflict-resolver'.

Returns a structured outcome (ResolverOutcome) the harness uses to decide
whether to push, retry, or finalize as failed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import HookMatcher

from agent.audit_hook import (
    make_audited_policy,
    make_post_tool_hook,
    make_pre_tool_hook,
)
from agent.auto_mode import AutonomyLevel

logger = logging.getLogger(__name__)


@dataclass
class ResolverOutcome:
    """What the SDK run produced."""
    completed: bool                # the model exited cleanly
    tokens_input: int
    tokens_output: int
    tokens_total: int
    last_text: str | None          # last assistant text — used as failure reason on retry
    error: str | None              # set when SDK errored


async def run_resolver(
    *,
    prompt: str,
    workspace: str,
    run_id: str,
    model: str,
    max_turns: int,
    max_budget_usd: float | None = None,
) -> ResolverOutcome:
    """Run the resolver inside `workspace`. Tool calls audited as
    actor='conflict-resolver'.
    """
    options = ClaudeAgentOptions(
        cwd=workspace,
        env={"GITHUB_REPO": ""},  # set by caller via os.environ if desired
        allowed_tools=["Read", "Bash", "Edit", "Write", "Glob", "Grep"],
        max_turns=max_turns,
        model=model,
        can_use_tool=make_audited_policy(
            run_id=run_id,
            level=AutonomyLevel.AUTO,  # resolver runs autonomously by design
            agent_id="conflict-resolver",
        ),
        hooks={
            "PreToolUse": [HookMatcher(hooks=[
                make_pre_tool_hook(
                    run_id=run_id,
                    actor="conflict-resolver",
                    trace_id=run_id,
                ),
            ])],
            "PostToolUse": [HookMatcher(hooks=[
                make_post_tool_hook(
                    run_id=run_id,
                    actor="conflict-resolver",
                ),
            ])],
        },
        max_budget_usd=max_budget_usd,
    )

    tokens_input = 0
    tokens_output = 0
    last_text: str | None = None
    error: str | None = None
    completed = False

    try:
        async for message in query(prompt=prompt, options=options):
            mtype = getattr(message, "type", None)
            if mtype == "assistant":
                usage = getattr(message, "usage", None) or {}
                tokens_input += int(usage.get("input_tokens", 0) or 0)
                tokens_output += int(usage.get("output_tokens", 0) or 0)
                # Capture the latest text response so a failure mode (e.g.
                # "I cannot resolve this safely") shows up as the prior
                # failure reason on retry.
                content = getattr(message, "content", None)
                if content:
                    last_text = str(content)[:4000]
            elif mtype == "result":
                completed = not getattr(message, "is_error", False)
    except Exception as exc:  # pragma: no cover — defensive
        error = str(exc)[:500]
        logger.warning("conflict resolver SDK error: %s", exc)

    return ResolverOutcome(
        completed=completed,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        tokens_total=tokens_input + tokens_output,
        last_text=last_text,
        error=error,
    )
```

- [ ] **Step 2: Verify it compiles**

```bash
cd /home/simon/Documents/claude-agent-station
python3 -c "import ast; ast.parse(open('agent/conflict_resolver/sdk_runner.py').read())"
echo OK
```

- [ ] **Step 3: Commit**

```bash
git add agent/conflict_resolver/sdk_runner.py
git -c commit.gpgsign=false commit -m "feat(agent): conflict resolver SDK runner

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: `__main__.py` entry point

Glues the pieces together. Reads CLI args, queries budget, builds prompt, runs SDK, records outcome. Returns an exit code the bash harness can branch on.

**Files:**
- Create: `agent/conflict_resolver/__main__.py`

- [ ] **Step 1: Implement the entry point**

```python
# agent/conflict_resolver/__main__.py
"""CLI entry point: python -m agent.conflict_resolver.

Exit codes:
  0  — resolved cleanly, commit produced, harness should push.
  10 — tests failed after all feedback rounds; harness should comment + stop.
  11 — manager rejected after all feedback rounds; harness should comment + stop.
  99 — budget exhausted; harness should label + comment.
  1  — SDK error or other unrecoverable problem; harness should comment + stop.

The harness (resolve-conflicts.sh) interprets these and posts the appropriate
PR comment / label combination.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

from app.database import async_session
from agent.conflict_resolver.budget import (
    record_attempt_finish,
    record_attempt_start,
    tokens_used_in_window,
)
from agent.conflict_resolver.markers import file_has_conflicts
from agent.conflict_resolver.prompts import AdvisoryTier, build_resolver_prompt
from agent.conflict_resolver.sdk_runner import run_resolver

logger = logging.getLogger(__name__)


def _list_conflicted_files(workspace: str) -> list[str]:
    """git diff --name-only --diff-filter=U inside workspace."""
    result = subprocess.run(
        ["git", "-C", workspace, "diff", "--name-only", "--diff-filter=U"],
        capture_output=True, text=True, check=False, timeout=10,
    )
    return [p for p in result.stdout.splitlines() if p.strip()]


def _compute_advisory_tiers(
    workspace: str,
    conflicted_files: list[str],
    *,
    pr_age_days: int | None,
    has_prior_attempt: bool,
) -> set[AdvisoryTier]:
    """Inspect the worktree to decide which advisory tiers apply."""
    tiers: set[AdvisoryTier] = set()
    if len(conflicted_files) > 10:
        tiers.add(AdvisoryTier.MANY_FILES)
    # Conflict diff size — sum of conflict-region byte counts as a proxy
    total_conflict_bytes = 0
    for fp in conflicted_files:
        full = Path(workspace) / fp
        try:
            text = full.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        if file_has_conflicts(text):
            # rough approximation: the file's whole content overestimates;
            # for the advisory we just need a yes/no vs 500-line threshold.
            total_conflict_bytes += len(text.splitlines())
    if total_conflict_bytes > 500:
        tiers.add(AdvisoryTier.LARGE_DIFF)
    if pr_age_days is not None and pr_age_days > 7:
        tiers.add(AdvisoryTier.STALE_PR)
    if has_prior_attempt:
        tiers.add(AdvisoryTier.PRIOR_ATTEMPT)
    return tiers


async def _amain(args: argparse.Namespace) -> int:
    async with async_session() as db:
        used = await tokens_used_in_window(db, branch=args.branch, window_hours=24)
    if used >= args.budget:
        logger.warning(
            "Budget exhausted for %s: used=%d, budget=%d", args.branch, used, args.budget,
        )
        return 99

    conflicted = _list_conflicted_files(args.workspace)
    if not conflicted:
        # Nothing to do — should be caught by Phase 1, but defensive.
        logger.info("No conflicted files in %s; nothing to resolve", args.workspace)
        return 0

    tiers = _compute_advisory_tiers(
        args.workspace, conflicted,
        pr_age_days=args.pr_age_days,
        has_prior_attempt=False,  # set by caller in future iterations
    )

    async with async_session() as db:
        attempt_id = await record_attempt_start(
            db,
            branch=args.branch,
            repo=args.repo,
            triggered_by=args.triggered_by,
            run_id=args.run_id,
            pr_number=args.pr_number,
        )

    prompt = build_resolver_prompt(
        branch=args.branch,
        base_branch=args.base_branch,
        conflicted_files=conflicted,
        advisory_tiers=tiers,
        prior_failure_reason=None,
    )

    outcome = await run_resolver(
        prompt=prompt,
        workspace=args.workspace,
        run_id=args.run_id or f"conflict-{attempt_id}",
        model=args.model,
        max_turns=args.max_turns,
    )

    if outcome.error:
        async with async_session() as db:
            await record_attempt_finish(
                db, attempt_id=attempt_id,
                phase_reached="llm", outcome="error",
                tokens_input=outcome.tokens_input,
                tokens_output=outcome.tokens_output,
                tokens_total=outcome.tokens_total,
                model_used=args.model,
                error_detail=outcome.error,
            )
        return 1

    # Re-check whether conflicts remain; if so, the model didn't actually
    # finish. The harness still has feedback rounds to spend in subsequent
    # invocations — for v1 we treat this as one attempt and let the
    # bash harness re-invoke us.
    remaining = _list_conflicted_files(args.workspace)
    if remaining:
        async with async_session() as db:
            await record_attempt_finish(
                db, attempt_id=attempt_id,
                phase_reached="llm", outcome="tests_failed",
                tokens_input=outcome.tokens_input,
                tokens_output=outcome.tokens_output,
                tokens_total=outcome.tokens_total,
                model_used=args.model,
                error_detail=outcome.last_text or "model exited with conflicts unresolved",
            )
        return 10

    async with async_session() as db:
        await record_attempt_finish(
            db, attempt_id=attempt_id,
            phase_reached="llm", outcome="resolved",
            tokens_input=outcome.tokens_input,
            tokens_output=outcome.tokens_output,
            tokens_total=outcome.tokens_total,
            model_used=args.model,
        )
    return 0


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="Conflict resolver — Phase 3")
    parser.add_argument("--workspace", required=True, help="conflicted worktree path")
    parser.add_argument("--branch", required=True)
    parser.add_argument("--base-branch", required=True)
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--pr-number", type=int, default=None)
    parser.add_argument("--pr-age-days", type=int, default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--triggered-by", choices=["pre_pr", "at_merge"], required=True)
    parser.add_argument("--model", default=os.environ.get("CONFLICT_MODEL", "claude-opus-4-7"))
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--budget", type=int, default=200_000, help="rolling 24h token cap")
    args = parser.parse_args()

    sys.exit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-test argparse**

```bash
cd /home/simon/Documents/claude-agent-station
python -m agent.conflict_resolver --help
```

Expected: usage line listing all flags. No exception.

- [ ] **Step 3: Commit**

```bash
git add agent/conflict_resolver/__main__.py
git -c commit.gpgsign=false commit -m "feat(agent): conflict resolver CLI entry point

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Bash helpers + lockfile-only-conflict predicate (TDD)

The bash side of the lockfile detection. Mirror the Python `LOCKFILE_NAMES` set so they don't drift.

**Files:**
- Create: `agent/scripts/lib/conflict-helpers.sh`
- Create: `agent/scripts/tests/test_conflict_helpers.sh`

- [ ] **Step 1: Write the failing bash test**

```bash
#!/usr/bin/env bash
# agent/scripts/tests/test_conflict_helpers.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../lib/conflict-helpers.sh
source "$SCRIPT_DIR/lib/conflict-helpers.sh"

fail=0
assert_eq() {
    local expected="$1" actual="$2" label="$3"
    if [ "$expected" = "$actual" ]; then
        echo "  PASS  $label"
    else
        echo "  FAIL  $label: expected='$expected' actual='$actual'"
        fail=1
    fi
}

# is_lockfile_only_conflict empty list → false (nothing to be lockfile-only)
out=$(is_lockfile_only_conflict "" && echo true || echo false)
assert_eq "false" "$out" "empty conflict list"

out=$(is_lockfile_only_conflict "package-lock.json" && echo true || echo false)
assert_eq "true" "$out" "single package-lock.json"

out=$(is_lockfile_only_conflict $'package-lock.json\nyarn.lock' && echo true || echo false)
assert_eq "true" "$out" "multiple lockfiles"

out=$(is_lockfile_only_conflict $'package-lock.json\nsrc/main.ts' && echo true || echo false)
assert_eq "false" "$out" "lockfile + non-lockfile"

out=$(is_lockfile_only_conflict $'src/main.ts' && echo true || echo false)
assert_eq "false" "$out" "non-lockfile only"

if [ "$fail" -eq 0 ]; then
    echo "All tests passed."
    exit 0
else
    echo "Tests failed."
    exit 1
fi
```

```bash
chmod +x agent/scripts/tests/test_conflict_helpers.sh
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station
agent/scripts/tests/test_conflict_helpers.sh
```

Expected: error — `lib/conflict-helpers.sh: No such file or directory` (or similar).

- [ ] **Step 3: Implement the helper library**

```bash
#!/usr/bin/env bash
# agent/scripts/lib/conflict-helpers.sh
#
# Helpers shared between resolve-conflicts.sh and its tests. Sourced, not
# executed.

# Names of files we treat as machine-regenerable lockfiles.
# Keep in sync with agent/conflict_resolver/markers.py:LOCKFILE_NAMES.
CONFLICT_LOCKFILE_NAMES="package-lock.json yarn.lock pnpm-lock.yaml Cargo.lock"

# is_lockfile_only_conflict <newline-separated-paths>
# Exit 0 (true) iff every line is a lockfile name AND there's at least one line.
# Used by Phase 2 to decide whether lockfile regen alone can resolve.
is_lockfile_only_conflict() {
    local files="${1:-}"
    [ -z "$files" ] && return 1
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        local base
        base=$(basename "$f")
        local matched=false
        for lock in $CONFLICT_LOCKFILE_NAMES; do
            if [ "$base" = "$lock" ]; then
                matched=true
                break
            fi
        done
        [ "$matched" = false ] && return 1
    done <<< "$files"
    return 0
}

# regen_lockfile <workspace> <lockfile-basename>
# Runs the appropriate package manager to regenerate the lockfile from
# the merged source tree. Returns the package manager's exit code.
regen_lockfile() {
    local workspace="$1" lockfile="$2"
    case "$lockfile" in
        package-lock.json) (cd "$workspace" && npm install --silent) ;;
        yarn.lock) (cd "$workspace" && yarn install --silent) ;;
        pnpm-lock.yaml) (cd "$workspace" && pnpm install --silent) ;;
        Cargo.lock) (cd "$workspace" && cargo build --offline 2>/dev/null || cargo build) ;;
        *) return 1 ;;
    esac
}

# take_flock <branch> <ttl_seconds>
# Acquires a non-blocking flock. Stale locks (older than TTL) are deleted
# and re-acquired. Echoes the lockfile path on success; non-zero exit on
# failure. Caller must call release_flock with the same path.
take_flock() {
    local branch="$1" ttl="${2:-1800}"
    local lock_dir="${STATION_LOCK_DIR:-/var/lib/claude-agent-station/locks}"
    mkdir -p "$lock_dir"
    local lockpath="$lock_dir/conflict-$(echo "$branch" | tr '/' '_').lock"
    # Stale-lock GC: if file mtime older than TTL, remove it.
    if [ -f "$lockpath" ]; then
        local age
        age=$(( $(date +%s) - $(stat -c %Y "$lockpath" 2>/dev/null || stat -f %m "$lockpath") ))
        if [ "$age" -gt "$ttl" ]; then
            rm -f "$lockpath"
        fi
    fi
    # Try to acquire.
    exec 200>"$lockpath"
    flock -n 200 || return 1
    echo "$lockpath"
}

# release_flock — closes fd 200 (the take_flock fd).
release_flock() {
    exec 200>&-
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /home/simon/Documents/claude-agent-station
agent/scripts/tests/test_conflict_helpers.sh
```

Expected: `All tests passed.`

- [ ] **Step 5: Shellcheck both files**

```bash
shellcheck agent/scripts/lib/conflict-helpers.sh agent/scripts/tests/test_conflict_helpers.sh
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add agent/scripts/lib/conflict-helpers.sh \
        agent/scripts/tests/test_conflict_helpers.sh
git -c commit.gpgsign=false commit -m "feat(agent): bash helpers for conflict resolver

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: `resolve-conflicts.sh` orchestrator

The phase pipeline. No bash unit tests — phases compose external state (git, gh, the SDK); covered end-to-end by Task 13.

**Files:**
- Create: `agent/scripts/resolve-conflicts.sh`

- [ ] **Step 1: Implement the orchestrator**

```bash
#!/usr/bin/env bash
# agent/scripts/resolve-conflicts.sh
#
# Phase pipeline for conflict resolution. See spec
# docs/superpowers/specs/2026-05-10-conflict-resolution-design.md.
#
# Usage:
#   resolve-conflicts.sh \
#       --workspace <path> --branch <head> --base <base> \
#       --repo <owner/name> [--pr <num>] [--triggered-by pre_pr|at_merge] \
#       [--run-id <id>]
#
# Exit codes mirror agent.conflict_resolver:
#   0  resolved + pushed
#   10 tests failed after rounds
#   11 manager rejected after rounds
#   99 budget exhausted
#   1  unrecoverable error
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/conflict-helpers.sh
source "$SCRIPT_DIR/lib/conflict-helpers.sh"

# --- args ---
WORKSPACE="" BRANCH="" BASE="" REPO="" PR_NUM="" TRIGGERED_BY="pre_pr" RUN_ID=""
while [ $# -gt 0 ]; do
    case "$1" in
        --workspace) WORKSPACE="$2"; shift 2 ;;
        --branch) BRANCH="$2"; shift 2 ;;
        --base) BASE="$2"; shift 2 ;;
        --repo) REPO="$2"; shift 2 ;;
        --pr) PR_NUM="$2"; shift 2 ;;
        --triggered-by) TRIGGERED_BY="$2"; shift 2 ;;
        --run-id) RUN_ID="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 1 ;;
    esac
done
for v in WORKSPACE BRANCH BASE REPO; do
    [ -z "${!v}" ] && { echo "missing --${v,,}" >&2; exit 1; }
done

LOG_PREFIX="[resolve-conflicts $BRANCH]"
log() { echo "$LOG_PREFIX $*" >&2; }

# --- flock ---
LOCK_TTL="${STATION_CONFLICT_LOCK_TTL:-1800}"
if ! lockpath=$(take_flock "$BRANCH" "$LOCK_TTL"); then
    log "another resolution attempt is running for this branch; exiting"
    exit 0
fi
trap 'release_flock' EXIT

# --- Phase 1: mechanical rebase ---
log "Phase 1: mechanical rebase against $BASE"
cd "$WORKSPACE"
if ! git fetch origin "$BASE" >&2; then
    log "git fetch failed; aborting"
    exit 1
fi
if git rebase "origin/$BASE" >&2; then
    log "Phase 1 clean — pushing"
    git push --force-with-lease origin "$BRANCH" >&2 || { log "push failed"; exit 1; }
    log "resolved at Phase 1"
    exit 0
fi
log "Phase 1 conflicts; continuing"

# Collect the conflict file list.
conflicted=$(git diff --name-only --diff-filter=U || true)

# --- Phase 2: lockfile regen ---
if is_lockfile_only_conflict "$conflicted"; then
    log "Phase 2: lockfile-only conflict; regenerating"
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        git checkout --theirs "$f" || { log "checkout --theirs $f failed"; break; }
        if regen_lockfile "$WORKSPACE" "$(basename "$f")"; then
            git add "$f"
        else
            log "regen failed for $f; falling through to Phase 3"
            git rebase --abort 2>/dev/null || true
            break
        fi
    done <<< "$conflicted"
    if git rebase --continue >&2 2>/dev/null; then
        log "Phase 2 clean — pushing"
        git push --force-with-lease origin "$BRANCH" >&2 || { log "push failed"; exit 1; }
        log "resolved at Phase 2"
        exit 0
    fi
fi

# --- Phase 3: LLM resolver ---
log "Phase 3: invoking LLM resolver"
phase3_args=(
    --workspace "$WORKSPACE"
    --branch "$BRANCH"
    --base-branch "$BASE"
    --repo "$REPO"
    --triggered-by "$TRIGGERED_BY"
)
[ -n "$PR_NUM" ] && phase3_args+=(--pr-number "$PR_NUM")
[ -n "$RUN_ID" ] && phase3_args+=(--run-id "$RUN_ID")

# python -m agent.conflict_resolver returns the harness exit codes (0/10/99/1).
set +e
python3 -m agent.conflict_resolver "${phase3_args[@]}"
phase3_rc=$?
set -e

case "$phase3_rc" in
    0)
        log "Phase 3 resolved — pushing"
        git push --force-with-lease origin "$BRANCH" >&2 || { log "push failed"; exit 1; }
        exit 0
        ;;
    99)
        log "budget exhausted"
        exit 99
        ;;
    10|11|1)
        log "Phase 3 returned $phase3_rc"
        exit "$phase3_rc"
        ;;
    *)
        log "unexpected exit code $phase3_rc from Phase 3"
        exit 1
        ;;
esac
```

- [ ] **Step 2: Make executable, syntax-check, shellcheck**

```bash
chmod +x agent/scripts/resolve-conflicts.sh
bash -n agent/scripts/resolve-conflicts.sh
shellcheck agent/scripts/resolve-conflicts.sh
```

Expected: no output (clean).

- [ ] **Step 3: Smoke-test the help path**

```bash
agent/scripts/resolve-conflicts.sh 2>&1 | head -3
```

Expected: `missing --workspace` (exits 1, validates that arg parsing works).

- [ ] **Step 4: Commit**

```bash
git add agent/scripts/resolve-conflicts.sh
git -c commit.gpgsign=false commit -m "feat(agent): resolve-conflicts.sh phase orchestrator

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: PR-comment + label helper for terminal phases

When the harness exits non-zero, the caller (run-manager.sh) needs to comment on the PR + apply the right label. Encapsulate that in a small bash function `post_resolution_outcome` so both integration points share one implementation.

**Files:**
- Modify: `agent/scripts/lib/conflict-helpers.sh`

- [ ] **Step 1: Append to the helper library**

Append at the end of `agent/scripts/lib/conflict-helpers.sh`:

```bash
# ensure_conflict_label <repo> <label>
# Idempotently creates the GitHub label on the repo. Safe to call every run.
ensure_conflict_label() {
    local repo="$1" label="$2"
    gh label create "$label" --repo "$repo" \
        --color D93F0B \
        --description "Auto-applied by Claude Agent Station conflict resolver" \
        2>/dev/null || true
}

# post_resolution_outcome <exit_code> <repo> <pr_num> <branch>
# Comment + label on the PR based on the resolver's exit code.
post_resolution_outcome() {
    local rc="$1" repo="$2" pr="$3" branch="$4"
    [ -z "$pr" ] && return 0  # no PR yet (pre-PR rebase) — nothing to comment on
    case "$rc" in
        0)
            gh pr comment "$pr" --repo "$repo" --body "🤖 Conflicts auto-resolved on \`$branch\`. The PR was rebased; any in-flight review comments may now show as outdated." 2>/dev/null || true
            ;;
        99)
            ensure_conflict_label "$repo" "conflict-budget-exhausted"
            gh pr edit "$pr" --repo "$repo" --add-label "conflict-budget-exhausted" 2>/dev/null || true
            gh pr comment "$pr" --repo "$repo" --body "🤖 Conflict resolution budget exhausted for \`$branch\` over the rolling 24h window. Resumes automatically tomorrow; remove this label to retry sooner." 2>/dev/null || true
            ;;
        10)
            ensure_conflict_label "$repo" "conflict-tests-failed"
            gh pr edit "$pr" --repo "$repo" --add-label "conflict-tests-failed" 2>/dev/null || true
            gh pr comment "$pr" --repo "$repo" --body "🤖 Conflict resolver produced a tree but tests failed after all feedback rounds. The branch state is the resolver's best attempt; review and fix manually." 2>/dev/null || true
            ;;
        11)
            ensure_conflict_label "$repo" "conflict-manager-rejected"
            gh pr edit "$pr" --repo "$repo" --add-label "conflict-manager-rejected" 2>/dev/null || true
            gh pr comment "$pr" --repo "$repo" --body "🤖 Conflict resolver produced a tree but the manager review rejected it. Review the resolver's last commit and the manager's reasoning in the audit log." 2>/dev/null || true
            ;;
        *)
            gh pr comment "$pr" --repo "$repo" --body "🤖 Conflict resolution errored (rc=$rc). Branch left as-is for manual intervention." 2>/dev/null || true
            ;;
    esac
}
```

- [ ] **Step 2: Shellcheck**

```bash
shellcheck agent/scripts/lib/conflict-helpers.sh
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add agent/scripts/lib/conflict-helpers.sh
git -c commit.gpgsign=false commit -m "feat(agent): PR comment + label helper for resolution outcomes

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Wire `resolve-conflicts.sh` into `run-manager.sh` — pre-PR

Pre-PR integration: after the manager APPROVE/PR verdict, before `gh pr create`, attempt rebase. If the resolver succeeds, the branch is clean against the latest base; if it doesn't, fall through to the existing PR-creation flow (the human will see a still-conflicting PR, and the at-merge integration point may resolve it later).

**Files:**
- Modify: `agent/scripts/run-manager.sh`

- [ ] **Step 1: Add a wrapper near the top of run-manager.sh**

Insert after the existing `format_close_keywords` function (added in PR #334, lines ~84):

```bash
# rebase_against_base <workspace> <branch> <base> <repo> [<pr_number>] [<run_id>]
# Calls resolve-conflicts.sh in pre-PR mode. Logs the outcome but never
# fails the caller — the existing manual-review path is the safety net.
rebase_against_base() {
    local workspace="$1" branch="$2" base="$3" repo="$4"
    local pr_num="${5:-}" run_id="${6:-}"
    local script_dir
    script_dir="$(dirname "${BASH_SOURCE[0]}")"
    local args=(
        --workspace "$workspace"
        --branch "$branch"
        --base "$base"
        --repo "$repo"
        --triggered-by pre_pr
    )
    [ -n "$pr_num" ] && args+=(--pr "$pr_num")
    [ -n "$run_id" ] && args+=(--run-id "$run_id")
    set +e
    "$script_dir/resolve-conflicts.sh" "${args[@]}"
    local rc=$?
    set -e
    log_info "rebase_against_base returned $rc for $branch"
    return "$rc"
}
```

- [ ] **Step 2: Add the pre-PR rebase calls before each `gh pr create`**

There are 3 PR-creation sites in run-manager.sh that need the pre-PR hook (line numbers approximate after the helper insertion in step 1):

**Site A** — auto-draft (around the `gh pr create --draft` call):

Find:
```bash
                            if auto_draft_rate_limit_allowed "$project"; then
                                log_info "Auto-draft PR (autonomy=auto, rate limit OK)"
                                local pr_url close_line
```

Insert immediately after `local pr_url close_line`:

```bash
                                rebase_against_base "$workspace" "$branch" "$base_branch" "$project" "" "$RUN_ID" || true
```

**Site B** — APPROVE verdict (around the `# Create PR and merge via GitHub API` comment):

Find:
```bash
                        else
                            # Create PR and merge via GitHub API (works with protected branches)
                            local pr_url close_line
```

Insert immediately after `local pr_url close_line`:

```bash
                            rebase_against_base "$workspace" "$branch" "$base_branch" "$project" "" "$RUN_ID" || true
```

**Site C** — PR verdict (around the `log_info "PR: Pushing branch and creating PR for human review"` line):

Find:
```bash
            PR)
                log_info "PR: Pushing branch and creating PR for human review (base: $base_branch)"
                if git push origin "$branch" 2>/dev/null; then
                    log_ok "Pushed $branch"
                    local close_line
```

Insert immediately after `log_info "PR: Pushing ..."`:

```bash
                rebase_against_base "$workspace" "$branch" "$base_branch" "$project" "" "$RUN_ID" || true
```

(The PR verdict already pushes the branch separately; the rebase happens before the push, then the existing `git push` is now redundant but harmless — it'll be a no-op after force-push-with-lease.)

- [ ] **Step 3: Syntax-check**

```bash
bash -n agent/scripts/run-manager.sh
echo "syntax OK"
```

- [ ] **Step 4: Commit**

```bash
git add agent/scripts/run-manager.sh
git -c commit.gpgsign=false commit -m "feat(agent): pre-PR rebase via resolve-conflicts.sh

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Wire `resolve-conflicts.sh` into `run-manager.sh` — at-merge

When `gh pr merge` fails because of a conflict, attempt resolution and retry the merge once.

**Files:**
- Modify: `agent/scripts/run-manager.sh`

- [ ] **Step 1: Replace the existing failure log line**

Find (around line 2251 of the original, may have shifted):

```bash
                                if gh pr merge "$pr_url" --merge --delete-branch 2>&1 | while IFS= read -r line; do log_info "  $line"; done; then
                                    push_merge_ok=true
                                    log_ok "PR merged to $base_branch"
                                else
                                    log_error "PR merge failed — left open for manual review: $pr_url"
                                fi
```

Replace with:

```bash
                                if gh pr merge "$pr_url" --merge --delete-branch 2>&1 | while IFS= read -r line; do log_info "  $line"; done; then
                                    push_merge_ok=true
                                    log_ok "PR merged to $base_branch"
                                else
                                    log_warn "PR merge failed for $pr_url — attempting at-merge resolution"
                                    local pr_num_for_resolve
                                    pr_num_for_resolve=$(echo "$pr_url" | grep -oE '[0-9]+$' || echo "")
                                    if rebase_against_base "$workspace" "$branch" "$base_branch" "$project" "$pr_num_for_resolve" "$RUN_ID"; then
                                        log_info "Resolution succeeded; retrying merge"
                                        if gh pr merge "$pr_url" --merge --delete-branch 2>&1 | while IFS= read -r line; do log_info "  $line"; done; then
                                            push_merge_ok=true
                                            log_ok "PR merged to $base_branch (after at-merge resolution)"
                                        else
                                            local rc=$?
                                            log_error "PR merge still failed after resolution — left open: $pr_url"
                                            # Source the helper so post_resolution_outcome is in scope.
                                            local script_dir
                                            script_dir="$(dirname "${BASH_SOURCE[0]}")"
                                            # shellcheck source=lib/conflict-helpers.sh
                                            source "$script_dir/lib/conflict-helpers.sh"
                                            post_resolution_outcome "$rc" "$project" "$pr_num_for_resolve" "$branch"
                                        fi
                                    else
                                        local rc=$?
                                        log_error "Resolution failed (rc=$rc) — left open for manual review: $pr_url"
                                        local script_dir
                                        script_dir="$(dirname "${BASH_SOURCE[0]}")"
                                        # shellcheck source=lib/conflict-helpers.sh
                                        source "$script_dir/lib/conflict-helpers.sh"
                                        post_resolution_outcome "$rc" "$project" "$pr_num_for_resolve" "$branch"
                                    fi
                                fi
```

- [ ] **Step 2: Syntax + shellcheck**

```bash
bash -n agent/scripts/run-manager.sh
shellcheck agent/scripts/run-manager.sh 2>&1 | grep -E "resolve|rebase|conflict" || echo "no new shellcheck warnings"
```

Expected: no new warnings related to the new code.

- [ ] **Step 3: Commit**

```bash
git add agent/scripts/run-manager.sh
git -c commit.gpgsign=false commit -m "feat(agent): at-merge resolution when gh pr merge fails

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Webhook event persistence (TDD)

The resolver emits `conflict_resolution_started` / `conflict_resolution_phase` / `conflict_resolution_completed` webhooks. Persist them as `AgentEvent` rows mirroring PR #333's `hook_failures` pattern.

**Files:**
- Modify: `dashboard/backend/app/schemas.py`
- Modify: `dashboard/backend/app/routers/webhook.py`
- Create: `dashboard/backend/tests/test_webhook_conflict_resolution.py`

- [ ] **Step 1: Write the failing test**

```python
# dashboard/backend/tests/test_webhook_conflict_resolution.py
"""Tests for conflict_resolution_* webhook events."""

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import Base, async_session, engine
from app.main import app
from app.models import AgentEvent


@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(setup_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_started_event_persists(client):
    r = await client.post("/api/webhook/run-event", json={
        "event": "conflict_resolution_started",
        "run_id": "run-cr-001",
        "project": "owner/repo",
        "branch": "feature/x",
    })
    assert r.status_code in (200, 202)

    async with async_session() as db:
        rows = (await db.execute(
            select(AgentEvent).where(AgentEvent.event_type == "conflict_resolution_started")
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].run_id == "run-cr-001"


@pytest.mark.asyncio
async def test_completed_event_carries_phase_and_tokens(client):
    r = await client.post("/api/webhook/run-event", json={
        "event": "conflict_resolution_completed",
        "run_id": "run-cr-002",
        "project": "owner/repo",
        "branch": "feature/x",
        "phase": "llm",
        "count": 1500,  # tokens_total
    })
    assert r.status_code in (200, 202)

    async with async_session() as db:
        rows = (await db.execute(
            select(AgentEvent).where(AgentEvent.run_id == "run-cr-002")
        )).scalars().all()
    assert len(rows) == 1
    data = json.loads(rows[0].event_data)
    assert data["phase"] == "llm"
    assert data["count"] == 1500
    assert data["branch"] == "feature/x"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest tests/test_webhook_conflict_resolution.py -v
```

Expected: at least one test FAIL — handler doesn't exist yet.

- [ ] **Step 3: Add `phase` field to `WebhookRunEvent`**

In `dashboard/backend/app/schemas.py`, find the existing `count` field (around line ~360 — added in PR #333) and append:

```python
    count: int | None = None
    # Conflict-resolution event fields
    phase: str | None = None  # mechanical / lockfile / llm / etc.
```

- [ ] **Step 4: Add the handler in webhook.py**

In `dashboard/backend/app/routers/webhook.py`, find the existing `elif event_name == "hook_failures":` block (added in PR #333) and add a new branch after it:

```python
    elif event_name in ("conflict_resolution_started",
                        "conflict_resolution_phase",
                        "conflict_resolution_completed"):
        db.add(AgentEvent(
            workflow_id=f"trace-{event.run_id}",
            run_id=event.run_id,
            agent_id=event.agent_id or "conflict-resolver",
            event_type=event_name,
            event_data=json.dumps({
                "project": event.project,
                "branch": event.branch,
                "phase": event.phase,
                "count": event.count,
            }),
        ))
```

- [ ] **Step 5: Run test to verify it passes**

```bash
pytest tests/test_webhook_conflict_resolution.py -v
```

Expected: 2 PASSING.

- [ ] **Step 6: Regression check existing webhook tests**

```bash
pytest tests/test_webhook.py tests/test_webhook_vision_misalignment.py tests/test_webhook_hook_failures.py -v
```

Expected: all PASSING.

- [ ] **Step 7: Commit**

```bash
cd /home/simon/Documents/claude-agent-station
git add dashboard/backend/app/schemas.py \
        dashboard/backend/app/routers/webhook.py \
        dashboard/backend/tests/test_webhook_conflict_resolution.py
git -c commit.gpgsign=false commit -m "feat(dashboard): persist conflict_resolution_* webhook events

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: Documentation + example config

**Files:**
- Modify: `agent/config/manager-config.example.json`
- Modify: `docs/configuration.md`
- Modify: `docs/architecture.md`

- [ ] **Step 1: Add the example config block**

In `agent/config/manager-config.example.json`, add a top-level key (preserving existing JSON structure):

```json
  "conflict_resolution": {
    "enabled": true,
    "rolling_24h_token_budget": 200000,
    "max_feedback_rounds": 3,
    "model": "claude-opus-4-7",
    "max_turns": 30,
    "lock_ttl_seconds": 1800,
    "force_push_with_lease": true
  }
```

- [ ] **Step 2: Document in configuration.md**

Append to `docs/configuration.md` a new section:

```markdown
## Conflict resolution

When the agent's auto-created PRs hit merge conflicts, a layered resolver
(mechanical → lockfile → LLM) runs to attempt resolution within a rolling
24-hour token budget per branch. See spec at
`docs/superpowers/specs/2026-05-10-conflict-resolution-design.md`.

### Top-level config (`manager-config.json`)

| Key | Default | Notes |
|---|---|---|
| `conflict_resolution.enabled` | `true` | master switch |
| `conflict_resolution.rolling_24h_token_budget` | `200000` | tokens (input + output combined) per head branch over a sliding 24h window |
| `conflict_resolution.max_feedback_rounds` | `3` | shared counter across test failures and manager REJECTs |
| `conflict_resolution.model` | `"claude-opus-4-7"` | overridable; SDK fallback chain still applies |
| `conflict_resolution.max_turns` | `30` | per resolver invocation |
| `conflict_resolution.lock_ttl_seconds` | `1800` | flock TTL for `/var/lib/claude-agent-station/locks/conflict-<branch>.lock` |
| `conflict_resolution.force_push_with_lease` | `true` | unconditional v1; reserved for future opt-out |

### Per-project override

To disable LLM resolution for a specific repo (mechanical+lockfile only):

```json
{
  "projects": [
    {
      "repo": "acme/sensitive",
      "conflict_resolution": {
        "rolling_24h_token_budget": 0
      }
    }
  ]
}
```

### Per-project test command

To run the project's tests as part of post-resolution validation, set
`test_command` on the project entry. If absent, the manager review is
the only validation gate.

```json
{
  "projects": [
    {
      "repo": "laboef1900/next-itsm",
      "test_command": "npm test --silent"
    }
  ]
}
```
```

- [ ] **Step 3: Document in architecture.md**

In `docs/architecture.md`, find the directory-structure block and add `conflict_resolver/` under `agent/`:

```
│   ├── conflict_resolver/        # Layered conflict resolver (LLM + mechanical)
│   │   ├── __main__.py           # python -m agent.conflict_resolver entrypoint
│   │   ├── budget.py             # rolling 24h token budget
│   │   ├── markers.py            # git conflict marker parser
│   │   ├── prompts.py            # prompt assembly
│   │   └── sdk_runner.py         # Claude Agent SDK wrapper
```

And under `agent/scripts/`, add:

```
│   │   ├── resolve-conflicts.sh  # Phase orchestrator (mechanical → lockfile → LLM)
│   │   ├── lib/
│   │   │   └── conflict-helpers.sh  # Shared bash helpers
```

- [ ] **Step 4: Commit**

```bash
git add agent/config/manager-config.example.json \
        docs/configuration.md \
        docs/architecture.md
git -c commit.gpgsign=false commit -m "docs: conflict resolution config + architecture

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 15: Verify, push, open PR

**Files:** none for verification; PR opened at the end.

- [ ] **Step 1: Run all backend tests**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest -v 2>&1 | tail -15
```

Expected: all passing, including the new tests added across Tasks 2, 3, 4, 5, 13.

- [ ] **Step 2: Run all bash unit tests**

```bash
cd /home/simon/Documents/claude-agent-station
agent/scripts/tests/test_conflict_helpers.sh
```

Expected: `All tests passed.`

- [ ] **Step 3: Shellcheck all new + modified bash**

```bash
shellcheck agent/scripts/resolve-conflicts.sh \
           agent/scripts/lib/conflict-helpers.sh \
           agent/scripts/tests/test_conflict_helpers.sh \
           agent/scripts/run-manager.sh
```

Expected: no errors. (Pre-existing warnings on run-manager.sh from prior commits acceptable; no NEW warnings.)

- [ ] **Step 4: Python parse-check the new modules**

```bash
python3 -c "
import ast
for f in [
    'agent/conflict_resolver/__init__.py',
    'agent/conflict_resolver/__main__.py',
    'agent/conflict_resolver/budget.py',
    'agent/conflict_resolver/markers.py',
    'agent/conflict_resolver/prompts.py',
    'agent/conflict_resolver/sdk_runner.py',
]:
    ast.parse(open(f).read())
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 5: Push the branch**

```bash
git push -u origin feature/conflict-resolution
```

- [ ] **Step 6: Open PR against `dev`**

```bash
gh pr create --base dev --title "feat(agent): layered conflict resolution (mechanical → lockfile → LLM)" --body "$(cat <<'EOF'
## Summary
Layered conflict resolver that runs pre-PR (in worktree, before \`gh pr create\`) and at-merge (when \`gh pr merge\` fails). Phases:

1. **Mechanical rebase** — `git rebase origin/<base>`
2. **Lockfile regen** — when only `package-lock.json` / `yarn.lock` / `pnpm-lock.yaml` / `Cargo.lock` conflict, take base's version and regenerate
3. **LLM resolver** — `python -m agent.conflict_resolver`, audited via the existing PreToolUse / PostToolUse hooks
4. **Validation** — project's `test_command` if configured
5. **Manager review** — existing pipeline gates the resulting commit
6. **Push** — `git push --force-with-lease` (head branch only; never `dev` / `main`)
7. **Comment + label** — successful resolutions leave a 🤖 note; failures get a labelled comment

Bounded by a rolling 24h token budget per head branch (default 200k). flock prevents concurrent attempts.

## Spec & plan
- Spec: `docs/superpowers/specs/2026-05-10-conflict-resolution-design.md`
- Plan: `docs/superpowers/plans/2026-05-10-conflict-resolution.md`

## Test plan
- [x] Python unit: budget query (rolling-window edge cases), markers parser, prompt assembly
- [x] Bash unit: lockfile-only-conflict predicate
- [x] Webhook persistence: \`conflict_resolution_*\` events as AgentEvent rows
- [x] Type-check, shellcheck clean
- [ ] Manual: trigger a real run that produces a conflict and watch the resolver
- [ ] Manual: trigger a budget-exhaustion case and verify the label + comment appear
- [ ] Manual: trigger a lockfile-only conflict and verify Phase 2 resolves it without an LLM call

## Out of scope
- Periodic post-PR sweeper for stale conflicting PRs (e.g. next-itsm #19, #20)
- Auto-closing PRs that are too large/stale to be worth resolving
- Cross-PR conflict detection

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL printed. Return it as the task output.

---

## Self-review

**Spec coverage:**

| Spec section | Tasks |
|---|---|
| Lifecycle hooks (pre-PR + at-merge) | 11, 12 |
| Phase 0 (budget) | 3 (query) + 7 (Phase 0 in `__main__`) |
| Phase 1 (mechanical) | 9 (`resolve-conflicts.sh`) |
| Phase 2 (lockfile) | 8 (helpers) + 9 (orchestration) |
| Phase 3 (LLM) | 4 (markers) + 5 (prompts) + 6 (SDK runner) + 7 (entry point) |
| Phase 4 (validation) | covered inside Phase 3 prompt + harness retry |
| Phase 5 (manager review) | reuses existing pipeline; called between resolver exit and push in run-manager.sh wiring (Tasks 11, 12) |
| Phase 6 (push) | 9 (in `resolve-conflicts.sh`) |
| Phase 7 (comment + label) | 10 |
| Resolution loop (3↔4↔5) | 7 entry point returns the right exit codes; harness re-invokes within budget |
| Pre-attempt advisory tiers | 5 (prompts) + 7 (`_compute_advisory_tiers`) |
| Storage table | 2 |
| Configuration keys | 14 (docs) + budget read in 7 |
| Concurrency (flock) | 8 (`take_flock`/`release_flock`) + 9 (taken at top of orchestrator) |
| Error matrix | 9 (orchestrator) + 10 (comment helper) |
| Observability | 13 (webhook events) + reused audit hooks in 6 |
| Testing | 2, 3, 4, 5, 8, 13 (unit) + Task 15 verify |

**Gaps from spec:**

- **Phase 5 (manager review) wiring** — the spec says manager review gates the resolver's commit before push. The plan reuses the existing manager pipeline that lives downstream in run-manager.sh, so a successful Phase 3 resolution flows into the same APPROVE/REJECT path as any other branch. This is correct but worth re-verifying when the existing run-manager.sh path is exercised end-to-end. **Acceptable for v1; flag during Task 15 manual verification.**
- **Force-push retry on rejection** — the spec error matrix says "re-enter the pipeline at Phase 1" on `--force-with-lease` rejection. Task 9 doesn't implement that retry; a single push failure exits `1`. **Note in PR description as a v1.1 follow-up; not load-bearing for the happy path.**
- **`force_push_with_lease: false` opt-out** — the config key is documented but the plan never reads it (force-push is unconditional in Task 9). **Acceptable; documented as "unconditional v1, reserved for future opt-out" in Task 14.**

**Placeholder scan:** none of the "TBD / TODO / similar to Task N" patterns present.

**Type consistency:**
- `ConflictResolution` columns match between Task 2 (model) and Task 3 (queries).
- `tokens_used_in_window` / `record_attempt_start` / `record_attempt_finish` signatures match between Tasks 3, 7.
- `ConflictRegion` / `parse_conflict_markers` / `file_has_conflicts` / `LOCKFILE_NAMES` consistent between Tasks 4, 7.
- `AdvisoryTier` enum values consistent across Tasks 5, 7.
- `ResolverOutcome` consistent across Tasks 6, 7.
- Bash function names consistent: `is_lockfile_only_conflict`, `regen_lockfile`, `take_flock`, `release_flock`, `ensure_conflict_label`, `post_resolution_outcome`, `rebase_against_base`.
- Exit code conventions consistent: `0/10/11/99/1` across Python entry point, bash orchestrator, and bash helper.

No remaining issues.
