# Decompose Long Runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce issue-level decomposition: a new `issue-splitter` role decides if a large GitHub issue should be split into 2–5 self-contained sub-issues, creates those sub-issues with `splitter-proposed` labels and a `Parent: #N` back-link, and lets the run scheduler fan them out concurrently (one container per sub-run, integration branch per parent).

**Architecture:** A new agent role definition (`agent/agents/issue-splitter.md`) plus prompt (`agent/prompts/issue-splitter.md`) defines the splitter. The coordinator's `decide` module (`agent/coordinator/decide.py`) gains a `maybe_split(issue, run_id) -> SplitDecision` pre-dispatch hook gated by a feature flag (`STATION_SPLIT_ENABLED`, default `0`). The splitter is invoked as a short-lived "split-decision" run (Sonnet, capped 30 turns, read-only on the repo) and emits a JSON array of `{title, body, labels, acceptance, depends_on}` items. A new module `agent/issue_splitter/` owns the JSON schema, parser, GitHub issue-creation flow (mirroring `vision_analyst.py:300+`), and back-link comment generator. Sub-runs are scheduled by a new `agent/issue_splitter/scheduler.py` that picks N eligible sub-issues per tick and routes each through the existing run-trigger flow — one per ephemeral container (#386). An integration branch per parent (`integration/issue-<N>`) is the merge target; the existing CI runs against it. New columns `Run.run_kind` and `Run.parent_run_id` track tree structure; `RunComplete` (#385) gains `sub_runs` / `parent_run` fields. Failure of one sub-run does not block siblings.

**Tech Stack:** Python 3.11+ / Claude Agent SDK / FastAPI / SQLAlchemy 2 async / Alembic, Docker compose, pytest + responses (HTTP mocking).

**Tracking issue:** [#391](https://github.com/kenhaesler/claude-agent-station/issues/391)

**Spec:** `docs/superpowers/specs/2026-05-14-issue-391-decompose-long-runs.md`

**Hard prerequisites (must be merged before starting):**
- **#385** (RunComplete tool) — needed for structured per-run verdict + parent/sub aggregation fields.
- **#386** (per-project containers) — needed for genuine concurrent sub-runs. Without it the plan still ships, but sub-runs serialize through the single-employee bottleneck.

**Soft prerequisites:**
- **#390** (manager-as-sibling) — makes per-sub-run verdicts easier to reason about, not required.

Tasks 14 and 15 explicitly depend on #385 and #386; the plan flags them as gated.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `agent/agents/issue-splitter.md` | **new** | Agent Teams role definition: name, description, tools (read-only), model, maxTurns. |
| `agent/prompts/issue-splitter.md` | **new** | Full splitter prompt: inputs, task, output schema, refusal rules. |
| `agent/issue_splitter/__init__.py` | **new** | Package marker. |
| `agent/issue_splitter/schema.py` | **new** | `SubIssueProposal` + `SplitDecision` dataclasses; `parse_splitter_output(raw)` JSON parser with strict validation. |
| `agent/issue_splitter/heuristics.py` | **new** | `maybe_split(issue)` — issue-body length, acceptance-criterion count, cross-cutting labels, opt-in / opt-out labels. |
| `agent/issue_splitter/github_ops.py` | **new** | `create_sub_issues(parent_issue, proposals, gh_client)`; `add_backlink_comment(parent, sub_numbers, gh_client)`; ensures `splitter-proposed` label exists (mirrors `vision_analyst.py:300`). |
| `agent/issue_splitter/runner.py` | **new** | `run_splitter(issue, run_id, sdk_options) -> SplitDecision` — spawns the splitter SDK session, captures the JSON output, returns the parsed decision. |
| `agent/issue_splitter/scheduler.py` | **new** | `pick_eligible_subruns(parent_issue_id, integration_branch)`; respects `depends_on`; triggers sub-runs via the existing launcher `/run` endpoint. |
| `agent/coordinator/decide.py` | modify | Insert pre-dispatch hook calling `maybe_split` + `run_splitter` when the flag is on. |
| `dashboard/backend/app/models.py` | modify | `Run.run_kind` (Text), `Run.parent_run_id` (Text, indexed nullable), `Run.split_decision_json` (JsonType nullable). |
| `dashboard/backend/alembic/versions/0003_run_kind_parent.py` | **new** | Alembic revision. |
| `dashboard/backend/app/schemas.py` | modify | `RunOut.run_kind`, `RunOut.parent_run_id`. |
| `dashboard/backend/app/routers/runs.py` | modify | `GET /api/runs/{run_id}/tree` returns the parent + sub-runs aggregate. |
| `dashboard/frontend/src/pages/RunDetail.svelte` | modify | Render "Fan-out" panel for parent runs (sub-run IDs + verdicts). |
| `dashboard/frontend/src/pages/MissionControl.svelte` | modify | Run list nests sub-runs under their parent (one level only). |
| `dashboard/backend/tests/test_issue_splitter_schema.py` | **new** | JSON parser: valid 2-5 items, malformed, too-many, too-few. |
| `dashboard/backend/tests/test_issue_splitter_heuristics.py` | **new** | Each of the five `maybe_split` triggers. |
| `dashboard/backend/tests/test_issue_splitter_github_ops.py` | **new** | Mocked gh client: label ensure, sub-issue create, back-link comment. |
| `dashboard/backend/tests/test_issue_splitter_scheduler.py` | **new** | `depends_on` honoured; failed sub-run doesn't block siblings. |
| `dashboard/backend/tests/test_runs_tree.py` | **new** | `/api/runs/{run_id}/tree` aggregation. |
| `dashboard/backend/tests/integration/test_splitter_e2e.py` | **new** | Synthetic split flow against a stubbed GitHub API. |
| `docs/architecture.md` | modify | Add "Issue decomposition" section. |
| `docs/configuration.md` | modify | Document `STATION_SPLIT_ENABLED`, `splitter-proposed`, `split-me`, `do-not-split`. |

---

## Setup (run once per execution session)

### Task 0: Confirm prerequisites + sync

- [ ] **Step 1: Pull latest dev**

```bash
git checkout dev && git pull --ff-only origin dev
```

- [ ] **Step 2: Confirm #385 + #386 are merged**

```bash
gh pr list --state merged --search "385 in:title"
gh pr list --state merged --search "386 in:title"
```

Expected: at least one merged PR per. If either is missing, **stop**. (Tasks 14, 15 are explicitly gated; the rest of the plan can proceed, but the integration test in Task 16 requires #386.)

- [ ] **Step 3: Confirm tests pass clean**

```bash
cd dashboard/backend && python3 -m pytest -q
```

Expected: green.

- [ ] **Step 4: Confirm the smart-router integration point**

```bash
ls -la /home/simon/Documents/claude-agent-station/agent/coordinator/
```

Expected: source `.py` files including `decide.py`. The spec's open question (no `smart_router.py` source — only `.pyc`) is resolved by reading the package files. If `decide.py` source is absent, **stop** and reconstruct from the bytecode before this plan continues.

- [ ] **Step 5: Create branch**

```bash
git checkout -b feature/391-issue-splitter
```

---

# PR 1 — Schema + heuristics + parser (foundation)

## Task 1: `SubIssueProposal` + `SplitDecision` schemas

**Files:**
- New: `agent/issue_splitter/__init__.py`
- New: `agent/issue_splitter/schema.py`
- New: `dashboard/backend/tests/test_issue_splitter_schema.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_issue_splitter_schema.py`:

```python
"""Splitter JSON output parser (#391)."""
from __future__ import annotations

import json

import pytest

from agent.issue_splitter.schema import (
    SplitDecision,
    SplitterError,
    SubIssueProposal,
    parse_splitter_output,
)


def test_parse_minimum_valid_two_items():
    raw = json.dumps([
        {"title": "Add login endpoint",
         "body": "POST /api/auth/login returns 200 + token.",
         "labels": ["backend"],
         "acceptance": ["Returns 200", "Issues JWT"],
         "depends_on": None},
        {"title": "Add /me endpoint",
         "body": "GET /api/me returns the current user from the token.",
         "labels": ["backend"],
         "acceptance": ["Returns 200 when authenticated"],
         "depends_on": 0},
    ])
    decision = parse_splitter_output(raw)
    assert isinstance(decision, SplitDecision)
    assert len(decision.proposals) == 2
    assert decision.proposals[0].title == "Add login endpoint"
    assert decision.proposals[1].depends_on == 0


def test_parse_rejects_single_item():
    raw = json.dumps([{"title": "x", "body": "y", "labels": [], "acceptance": ["a"]}])
    with pytest.raises(SplitterError, match="at least 2"):
        parse_splitter_output(raw)


def test_parse_truncates_to_five():
    items = [
        {"title": f"item {i}", "body": "b", "labels": [], "acceptance": ["a"], "depends_on": None}
        for i in range(7)
    ]
    decision = parse_splitter_output(json.dumps(items))
    assert len(decision.proposals) == 5
    assert decision.warnings  # truncation warning recorded


def test_parse_rejects_malformed_json():
    with pytest.raises(SplitterError, match="json"):
        parse_splitter_output("not json")


def test_parse_rejects_missing_required_fields():
    raw = json.dumps([
        {"title": "a", "body": "b", "labels": [], "acceptance": ["x"]},
        {"title": "c"},  # missing body, labels, acceptance
    ])
    with pytest.raises(SplitterError, match="missing"):
        parse_splitter_output(raw)


def test_parse_rejects_invalid_depends_on():
    raw = json.dumps([
        {"title": "a", "body": "b", "labels": [], "acceptance": ["x"], "depends_on": None},
        {"title": "c", "body": "d", "labels": [], "acceptance": ["x"], "depends_on": 99},
    ])
    with pytest.raises(SplitterError, match="depends_on"):
        parse_splitter_output(raw)
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_issue_splitter_schema.py -q
```

Expected: `ModuleNotFoundError: No module named 'agent.issue_splitter'`.

- [ ] **Step 3: Implement schema + parser**

Create `agent/issue_splitter/__init__.py` (empty).

Create `agent/issue_splitter/schema.py`:

```python
"""Splitter output schema + parser (#391).

The splitter emits a JSON array of sub-issue proposals. Strict validation
keeps the autonomous flow from acting on malformed output — on any
validation failure the run falls back to single-issue mode and the parent
issue stays untouched.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

MAX_PROPOSALS = 5
MIN_PROPOSALS = 2

REQUIRED_FIELDS = ("title", "body", "labels", "acceptance")


class SplitterError(Exception):
    """Raised by ``parse_splitter_output`` on any validation failure."""


@dataclass(frozen=True, slots=True)
class SubIssueProposal:
    title: str
    body: str
    labels: tuple[str, ...]
    acceptance: tuple[str, ...]
    depends_on: int | None = None  # index into the proposals array


@dataclass(frozen=True, slots=True)
class SplitDecision:
    proposals: tuple[SubIssueProposal, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)


def parse_splitter_output(raw: str) -> SplitDecision:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SplitterError(f"invalid json: {exc}") from exc

    if not isinstance(data, list):
        raise SplitterError("top-level must be a json array")

    if len(data) < MIN_PROPOSALS:
        raise SplitterError(f"need at least {MIN_PROPOSALS} proposals, got {len(data)}")

    warnings: list[str] = []
    items = data
    if len(items) > MAX_PROPOSALS:
        warnings.append(f"truncated {len(items)} proposals to {MAX_PROPOSALS}")
        items = items[:MAX_PROPOSALS]

    proposals: list[SubIssueProposal] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise SplitterError(f"item {i} is not an object")
        missing = [f for f in REQUIRED_FIELDS if f not in item]
        if missing:
            raise SplitterError(f"item {i} missing fields: {missing}")
        proposals.append(
            SubIssueProposal(
                title=str(item["title"]),
                body=str(item["body"]),
                labels=tuple(map(str, item.get("labels") or ())),
                acceptance=tuple(map(str, item.get("acceptance") or ())),
                depends_on=item.get("depends_on"),
            )
        )

    for i, prop in enumerate(proposals):
        if prop.depends_on is None:
            continue
        if not isinstance(prop.depends_on, int):
            raise SplitterError(f"item {i} depends_on must be int or null")
        if not 0 <= prop.depends_on < len(proposals):
            raise SplitterError(f"item {i} depends_on={prop.depends_on} out of range")
        if prop.depends_on == i:
            raise SplitterError(f"item {i} depends_on itself")

    return SplitDecision(proposals=tuple(proposals), warnings=tuple(warnings))
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_issue_splitter_schema.py -q
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/issue_splitter/__init__.py agent/issue_splitter/schema.py dashboard/backend/tests/test_issue_splitter_schema.py
git commit -m "feat(splitter): SubIssueProposal + SplitDecision schema (#391)"
```

---

## Task 2: `maybe_split` heuristics

**Files:**
- New: `agent/issue_splitter/heuristics.py`
- New: `dashboard/backend/tests/test_issue_splitter_heuristics.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_issue_splitter_heuristics.py`:

```python
"""maybe_split heuristic tests (#391)."""
from __future__ import annotations

import pytest

from agent.issue_splitter.heuristics import (
    HeuristicResult,
    LONG_BODY_TOKENS,
    maybe_split,
)


def _issue(*, body: str = "", labels: tuple[str, ...] = ()) -> dict:
    return {"number": 27, "title": "auth", "body": body, "labels": list(labels)}


def test_short_simple_issue_does_not_split():
    res = maybe_split(_issue(body="Add a tooltip."))
    assert res.should_split is False


def test_long_body_triggers_split():
    body = "x " * (LONG_BODY_TOKENS + 100)
    res = maybe_split(_issue(body=body))
    assert res.should_split is True
    assert "body_length" in res.reasons


def test_four_acceptance_criteria_triggers_split():
    body = (
        "## Acceptance criteria\n"
        "- [ ] login api\n"
        "- [ ] me endpoint\n"
        "- [ ] oauth callback\n"
        "- [ ] route middleware\n"
    )
    res = maybe_split(_issue(body=body))
    assert res.should_split is True
    assert "acceptance_count" in res.reasons


def test_cross_cutting_labels_trigger_split():
    res = maybe_split(_issue(labels=("backend", "frontend", "db-migration")))
    assert res.should_split is True
    assert "cross_cutting" in res.reasons


def test_split_me_label_forces_split():
    res = maybe_split(_issue(body="x", labels=("split-me",)))
    assert res.should_split is True
    assert "opt_in" in res.reasons


def test_do_not_split_label_forces_no_split():
    body = "x " * (LONG_BODY_TOKENS + 500)
    res = maybe_split(_issue(body=body, labels=("do-not-split",)))
    assert res.should_split is False
    assert "opt_out" in res.reasons
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_issue_splitter_heuristics.py -q
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the heuristics**

Create `agent/issue_splitter/heuristics.py`:

```python
"""Pre-dispatch heuristics for the issue splitter (#391).

Default policy: don't split. The five triggers below escalate to a split
candidate; ``do-not-split`` always vetoes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

LONG_BODY_TOKENS = 1200            # crude word-count proxy for tokens
ACCEPTANCE_COUNT_THRESHOLD = 4
CROSS_CUTTING_TRIPLES = (
    {"backend", "frontend", "db-migration"},
    {"backend", "frontend", "infra"},
)


@dataclass(frozen=True, slots=True)
class HeuristicResult:
    should_split: bool
    reasons: tuple[str, ...]


_BULLET_RE = re.compile(r"^\s*[-*]\s*\[[ x]\]", re.MULTILINE)


def _acceptance_count(body: str) -> int:
    return len(_BULLET_RE.findall(body or ""))


def _body_token_estimate(body: str) -> int:
    # Crude — replace with tiktoken if it's already a dep; not worth a new dep here.
    return len((body or "").split())


def maybe_split(issue: dict) -> HeuristicResult:
    labels = set(issue.get("labels") or ())
    if "do-not-split" in labels:
        return HeuristicResult(should_split=False, reasons=("opt_out",))
    if "split-me" in labels:
        return HeuristicResult(should_split=True, reasons=("opt_in",))

    reasons: list[str] = []
    body = issue.get("body") or ""

    if _body_token_estimate(body) > LONG_BODY_TOKENS:
        reasons.append("body_length")

    if _acceptance_count(body) >= ACCEPTANCE_COUNT_THRESHOLD:
        reasons.append("acceptance_count")

    if any(triple <= labels for triple in CROSS_CUTTING_TRIPLES):
        reasons.append("cross_cutting")

    return HeuristicResult(should_split=bool(reasons), reasons=tuple(reasons))
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_issue_splitter_heuristics.py -q
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/issue_splitter/heuristics.py dashboard/backend/tests/test_issue_splitter_heuristics.py
git commit -m "feat(splitter): pre-dispatch heuristics (#391)"
```

---

## Task 3: Agent role + prompt files

**Files:**
- New: `agent/agents/issue-splitter.md`
- New: `agent/prompts/issue-splitter.md`
- New: `dashboard/backend/tests/test_issue_splitter_prompt.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_issue_splitter_prompt.py`:

```python
"""Splitter agent role + prompt presence (#391)."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_agent_role_file_exists():
    p = REPO_ROOT / "agent/agents/issue-splitter.md"
    assert p.exists(), p
    text = p.read_text()
    assert "name: issue-splitter" in text
    assert "tools:" in text
    # Read-only: no Edit / Write outside output file.
    assert "Edit" not in text or "# read-only" in text
    assert "model:" in text


def test_prompt_file_lists_required_sections():
    p = REPO_ROOT / "agent/prompts/issue-splitter.md"
    assert p.exists(), p
    text = p.read_text()
    for section in ("## Inputs", "## Task", "## Constraints", "## Output"):
        assert section in text, section
    # JSON schema sketch must be present.
    assert '"title"' in text and '"acceptance"' in text and '"depends_on"' in text
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_issue_splitter_prompt.py -q
```

Expected: both fail.

- [ ] **Step 3: Create the files**

Create `agent/agents/issue-splitter.md`:

```markdown
---
name: issue-splitter
description: Decomposes a large GitHub issue into 2-5 self-contained sub-issues with acceptance criteria and dependency hints.
tools: Read, Glob, Grep, Bash
model: claude-sonnet-4-6
permissionMode: bypassPermissions
maxTurns: 30
---

You are the **issue splitter** for Claude Agent Station. Your job is to
inspect a single GitHub issue and decide whether it should be implemented
as one short run or split into 2-5 smaller, independently-implementable
sub-issues.

You are **read-only on the repository**. You can read files, run `git`
commands, and inspect the codebase to understand scope. You must not
edit, write, or create files outside the explicit output file path you
are given in the spawn prompt.

Follow the format in `agent/prompts/issue-splitter.md` exactly. Your
output is parsed by a strict JSON validator; any deviation causes the
run to fall back to single-issue mode.
```

Create `agent/prompts/issue-splitter.md`:

```markdown
# Issue Splitter — Prompt

## Inputs

You will receive:

- The **parent issue body** (Markdown), including its title, labels, and acceptance criteria.
- A **repo summary** (file tree depth-3, recent commits, README excerpt).
- The current **project vision** (if present in `docs/vision.md`).
- A **budget hint**: target each sub-run to complete in **4-10 minutes** of agent wall-clock.

## Task

Decide if the parent issue is decomposable into 2-5 atomic sub-issues
that can be implemented independently by a single specialist team.

If the parent is already small (≤ ~30 minutes of expected work, single
acceptance criterion, scoped to one subsystem), **do not split** — emit
an empty array `[]`. The harness treats this as "run as-is".

If the parent is decomposable, emit a JSON array of 2-5 sub-issue
proposals. **No prose around the JSON** — your entire output must be
the JSON array, parseable by `json.loads`.

## Constraints

- Each sub-issue must be **implementable end-to-end by a single specialist
  team in ≤15 minutes of agent time**.
- Each sub-issue must have **testable acceptance criteria** (no vague
  "make it work" criteria).
- Sub-issue **bodies must be self-contained** — a downstream agent
  reading only the sub-issue body must have enough context to implement
  it. Inline the relevant excerpt from the parent.
- **`depends_on`** is the index (zero-based) of a prerequisite sibling
  in this same array, or `null` if independent. Use sparingly — only
  when sub-issue B actually requires B's branch to be merged before
  sub-issue A can compile.
- **Never** propose splitting a sub-issue further (no recursive splits
  — this is single-level).
- **Never** propose sub-issues whose union is larger than the parent.
  Decomposition, not amplification.

## Output

A JSON array of objects:

```json
[
  {
    "title": "Add /api/auth/login endpoint",
    "body": "Full self-contained body. Inline parent context as needed.",
    "labels": ["backend", "auth"],
    "acceptance": [
      "POST /api/auth/login with valid credentials returns 200 + JWT.",
      "POST /api/auth/login with invalid credentials returns 401."
    ],
    "depends_on": null
  },
  {
    "title": "Add /api/me endpoint",
    "body": "...",
    "labels": ["backend", "auth"],
    "acceptance": ["GET /api/me with valid JWT returns the current user."],
    "depends_on": 0
  }
]
```

If you decide **not** to split, emit `[]`.

Output **only** the JSON array — no explanation, no commentary, no
Markdown fence around it.
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_issue_splitter_prompt.py -q
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/agents/issue-splitter.md agent/prompts/issue-splitter.md dashboard/backend/tests/test_issue_splitter_prompt.py
git commit -m "feat(splitter): agent role + prompt (#391)"
```

---

## Task 4: PR 1 — open

- [ ] **Step 1: Push**

```bash
git push -u origin feature/391-issue-splitter
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --base dev --title "feat(splitter): schema + heuristics + role (#391, PR 1/4)" --body "$(cat <<'EOF'
Part 1 of 4 for #391.

## Summary
- `agent/issue_splitter/{schema.py, heuristics.py}` + tests.
- `agent/agents/issue-splitter.md` + `agent/prompts/issue-splitter.md`.

## Test plan
- [ ] `cd dashboard/backend && pytest tests/test_issue_splitter_schema.py tests/test_issue_splitter_heuristics.py tests/test_issue_splitter_prompt.py -q`

No code path is yet wired into the run flow. Subsequent PRs add GitHub I/O (PR 2), scheduler + DB columns (PR 3), and the integration test + dashboard surface (PR 4).
EOF
)"
```

- [ ] **Step 3-5: Wait for CI, merge, sync.**

---

# PR 2 — GitHub ops + runner

## Task 5: Branch + `create_sub_issues` + `add_backlink_comment`

**Files:**
- New: `agent/issue_splitter/github_ops.py`
- New: `dashboard/backend/tests/test_issue_splitter_github_ops.py`

- [ ] **Step 1: Branch**

```bash
git checkout dev && git pull --ff-only origin dev && git checkout -b feature/391-splitter-gh
```

- [ ] **Step 2: Write failing tests**

Create `dashboard/backend/tests/test_issue_splitter_github_ops.py`:

```python
"""GitHub issue creation for splitter (#391)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.issue_splitter.github_ops import (
    add_backlink_comment,
    create_sub_issues,
    ensure_splitter_label,
)
from agent.issue_splitter.schema import SubIssueProposal


def _proposal(title: str, depends_on: int | None = None) -> SubIssueProposal:
    return SubIssueProposal(
        title=title,
        body="parent-context inlined here\n\nimplementation detail",
        labels=("backend",),
        acceptance=("Returns 200",),
        depends_on=depends_on,
    )


def test_ensure_splitter_label_creates_when_missing():
    gh = MagicMock()
    gh.label_exists.return_value = False
    ensure_splitter_label("kenhaesler", "claude-agent-station", gh)
    gh.create_label.assert_called_once()
    args, _ = gh.create_label.call_args
    assert args[0] == "kenhaesler"
    assert args[1] == "claude-agent-station"
    assert args[2] == "splitter-proposed"


def test_ensure_splitter_label_idempotent_when_present():
    gh = MagicMock()
    gh.label_exists.return_value = True
    ensure_splitter_label("kenhaesler", "claude-agent-station", gh)
    gh.create_label.assert_not_called()


def test_create_sub_issues_posts_each_with_correct_labels():
    gh = MagicMock()
    gh.create_issue.side_effect = [
        {"number": 101}, {"number": 102}, {"number": 103},
    ]
    parent = {"number": 27, "labels": ["backend", "auth"], "repo": "kenhaesler/claude-agent-station"}
    proposals = [_proposal("a"), _proposal("b", depends_on=0), _proposal("c")]
    created = create_sub_issues(parent, proposals, gh)

    assert [c["number"] for c in created] == [101, 102, 103]
    for call in gh.create_issue.call_args_list:
        kwargs = call.kwargs
        assert "splitter-proposed" in kwargs["labels"]
    # Body includes parent back-link.
    body_a = gh.create_issue.call_args_list[0].kwargs["body"]
    assert "Parent: #27" in body_a
    # depends_on of item 1 references sibling at index 0 -> #101.
    body_b = gh.create_issue.call_args_list[1].kwargs["body"]
    assert "Depends on #101" in body_b


def test_create_sub_issues_applies_parent_label_set():
    gh = MagicMock()
    gh.create_issue.side_effect = [{"number": 101}, {"number": 102}]
    parent = {"number": 27, "labels": ["backend"], "repo": "x/y"}
    create_sub_issues(parent, [_proposal("a"), _proposal("b")], gh)
    labels = gh.create_issue.call_args_list[0].kwargs["labels"]
    assert "backend" in labels


def test_add_backlink_comment_writes_summary():
    gh = MagicMock()
    add_backlink_comment(parent_repo="x/y", parent_number=27,
                         sub_numbers=[101, 102, 103], gh=gh)
    gh.create_issue_comment.assert_called_once()
    args, kwargs = gh.create_issue_comment.call_args
    body = kwargs.get("body") or args[2]
    assert "#101" in body and "#102" in body and "#103" in body
```

- [ ] **Step 3: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_issue_splitter_github_ops.py -q
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 4: Implement `github_ops.py`**

Create `agent/issue_splitter/github_ops.py`:

```python
"""GitHub issue creation for the splitter (#391).

Mirrors the issue-creation flow in agent/vision_analyst.py:300+ but with
a different label set (``splitter-proposed``) and a per-sub-issue body
template that inlines the ``Parent: #N`` back-link and any
``Depends on #M`` cross-reference.
"""
from __future__ import annotations

from typing import Iterable, Sequence

from agent.issue_splitter.schema import SubIssueProposal

SPLITTER_LABEL = "splitter-proposed"
SPLITTER_LABEL_COLOR = "0E8A16"
SPLITTER_LABEL_DESCRIPTION = (
    "Issue proposed by Claude Station's issue-splitter agent. Review and "
    "remove this label to make the issue eligible for autonomous pickup."
)


def ensure_splitter_label(owner: str, repo: str, gh) -> None:
    """Mirror vision_analyst's pattern (#391, #vision-suggested)."""
    if gh.label_exists(owner, repo, SPLITTER_LABEL):
        return
    gh.create_label(
        owner, repo, SPLITTER_LABEL,
        color=SPLITTER_LABEL_COLOR,
        description=SPLITTER_LABEL_DESCRIPTION,
    )


def _format_body(parent_number: int, proposal: SubIssueProposal,
                 sibling_numbers_by_index: dict[int, int]) -> str:
    lines = [
        f"_This sub-issue was proposed by Claude Station's issue-splitter "
        f"from parent #{parent_number}. Review by removing the "
        f"`{SPLITTER_LABEL}` label._",
        "",
        f"Parent: #{parent_number}",
    ]
    if proposal.depends_on is not None:
        prereq_number = sibling_numbers_by_index[proposal.depends_on]
        lines.append(f"Depends on #{prereq_number}")
    lines += ["", proposal.body, "", "## Acceptance criteria", ""]
    lines += [f"- [ ] {c}" for c in proposal.acceptance]
    return "\n".join(lines)


def create_sub_issues(
    parent: dict,
    proposals: Sequence[SubIssueProposal],
    gh,
) -> list[dict]:
    owner, repo = parent["repo"].split("/", 1)
    ensure_splitter_label(owner, repo, gh)

    parent_labels = set(parent.get("labels") or ())
    created: list[dict] = []
    sibling_numbers: dict[int, int] = {}
    for i, prop in enumerate(proposals):
        body = _format_body(parent["number"], prop, sibling_numbers)
        labels = list({SPLITTER_LABEL, *parent_labels, *prop.labels})
        issue = gh.create_issue(
            owner, repo,
            title=prop.title,
            body=body,
            labels=labels,
        )
        sibling_numbers[i] = issue["number"]
        created.append(issue)
    return created


def add_backlink_comment(*, parent_repo: str, parent_number: int,
                         sub_numbers: Iterable[int], gh) -> None:
    owner, repo = parent_repo.split("/", 1)
    lines = [
        "Claude Station's issue-splitter has decomposed this issue:",
        "",
    ]
    lines += [f"- #{n}" for n in sub_numbers]
    lines += ["", "Each sub-issue requires manual approval (remove the "
              f"`{SPLITTER_LABEL}` label) before autonomous pickup."]
    gh.create_issue_comment(owner, repo, parent_number, body="\n".join(lines))
```

- [ ] **Step 5: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_issue_splitter_github_ops.py -q
```

Expected: 5 passed.

```bash
git add agent/issue_splitter/github_ops.py dashboard/backend/tests/test_issue_splitter_github_ops.py
git commit -m "feat(splitter): GitHub issue creation + back-link (#391)"
```

---

## Task 6: `run_splitter` — invoke the SDK session

**Files:**
- New: `agent/issue_splitter/runner.py`
- New: `dashboard/backend/tests/test_issue_splitter_runner.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_issue_splitter_runner.py`:

```python
"""run_splitter spawns the SDK session and parses its output (#391)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from agent.issue_splitter.runner import run_splitter
from agent.issue_splitter.schema import SplitterError


@pytest.mark.asyncio
async def test_run_splitter_returns_parsed_decision(monkeypatch):
    captured = json.dumps([
        {"title": "A", "body": "B", "labels": ["x"], "acceptance": ["a1"], "depends_on": None},
        {"title": "B", "body": "C", "labels": ["x"], "acceptance": ["a1"], "depends_on": 0},
    ])
    with patch("agent.issue_splitter.runner._invoke_splitter_sdk", new=AsyncMock(return_value=captured)):
        decision = await run_splitter(
            issue={"number": 27, "body": "x", "labels": []},
            run_id="run-split-decision-1",
            repo_summary="repo info",
            vision="vision text",
        )
    assert len(decision.proposals) == 2
    assert decision.proposals[1].depends_on == 0


@pytest.mark.asyncio
async def test_run_splitter_propagates_schema_errors():
    with patch("agent.issue_splitter.runner._invoke_splitter_sdk",
               new=AsyncMock(return_value="garbage")):
        with pytest.raises(SplitterError):
            await run_splitter(
                issue={"number": 1, "body": "x", "labels": []},
                run_id="r1", repo_summary="", vision="",
            )


@pytest.mark.asyncio
async def test_run_splitter_empty_array_means_run_as_is():
    with patch("agent.issue_splitter.runner._invoke_splitter_sdk",
               new=AsyncMock(return_value="[]")):
        decision = await run_splitter(
            issue={"number": 1, "body": "x", "labels": []},
            run_id="r1", repo_summary="", vision="",
        )
    assert decision is None  # signal "run as-is"
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_issue_splitter_runner.py -q
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `runner.py`**

Create `agent/issue_splitter/runner.py`:

```python
"""Spawn the issue-splitter SDK session and return a SplitDecision (#391).

The splitter is a single short-lived Sonnet run: read-only on the repo,
capped at 30 turns, producing a JSON array on stdout. We invoke it via
the Claude Agent SDK rather than as a bash subprocess so the SDK
session is observable in the dashboard (split-decision run kind).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from agent.issue_splitter.schema import (
    SplitDecision,
    SplitterError,
    parse_splitter_output,
)

logger = logging.getLogger(__name__)

ROLE_FILE = Path(__file__).resolve().parents[1] / "agents" / "issue-splitter.md"
PROMPT_FILE = Path(__file__).resolve().parents[1] / "prompts" / "issue-splitter.md"


async def _invoke_splitter_sdk(
    *,
    issue: dict,
    run_id: str,
    repo_summary: str,
    vision: str,
) -> str:
    """Spawn the SDK session and capture the splitter's JSON output.

    Returns the *raw* string the splitter wrote to its output channel.
    Schema validation happens in the caller. Patched in tests.
    """
    # Import the SDK lazily — the test path patches this whole function.
    from claude_agent_sdk import query

    role_md = ROLE_FILE.read_text()
    prompt_md = PROMPT_FILE.read_text()
    user_message = (
        f"{prompt_md}\n\n"
        f"## Parent issue\n\n{json.dumps(issue, indent=2)}\n\n"
        f"## Repo summary\n\n{repo_summary or '(no summary)'}\n\n"
        f"## Vision\n\n{vision or '(no vision)'}\n\n"
        "Output ONLY the JSON array. No prose."
    )

    chunks: list[str] = []
    async for message in query(
        prompt=user_message,
        options={
            "system_prompt": role_md,
            "model": "claude-sonnet-4-6",
            "max_turns": 30,
            "permission_mode": "bypassPermissions",
            "allowed_tools": ["Read", "Glob", "Grep", "Bash"],
        },
    ):
        if hasattr(message, "content") and isinstance(message.content, str):
            chunks.append(message.content)
    return "\n".join(chunks).strip()


async def run_splitter(
    *,
    issue: dict,
    run_id: str,
    repo_summary: str,
    vision: str,
) -> SplitDecision | None:
    """Return the parsed SplitDecision, or ``None`` if the splitter
    decided to run as-is (empty array)."""
    raw = await _invoke_splitter_sdk(
        issue=issue, run_id=run_id, repo_summary=repo_summary, vision=vision,
    )
    if raw.strip() == "[]":
        return None
    return parse_splitter_output(raw)
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_issue_splitter_runner.py -q
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/issue_splitter/runner.py dashboard/backend/tests/test_issue_splitter_runner.py
git commit -m "feat(splitter): SDK session wrapper (#391)"
```

---

## Task 7: PR 2 — open

- [ ] **Step 1: Push**

```bash
git push -u origin feature/391-splitter-gh
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --base dev --title "feat(splitter): GitHub ops + SDK runner (#391, PR 2/4)" --body "$(cat <<'EOF'
Part 2 of 4 for #391.

## Summary
- `github_ops.py`: ensure `splitter-proposed` label, create sub-issues with `Parent: #N` back-link, comment on parent.
- `runner.py`: invoke the splitter via the Claude Agent SDK; parse JSON output.

## Test plan
- [ ] `cd dashboard/backend && pytest tests/test_issue_splitter_github_ops.py tests/test_issue_splitter_runner.py -q`
EOF
)"
```

- [ ] **Step 3-5: Wait for CI, merge, sync dev.**

---

# PR 3 — DB columns + scheduler + decide-hook (feature-flagged)

## Task 8: Branch + new DB columns

**Files:**
- Modify: `dashboard/backend/app/models.py`
- New: `dashboard/backend/alembic/versions/0003_run_kind_parent.py`
- New: `dashboard/backend/tests/test_run_kind_parent.py`

- [ ] **Step 1: Branch**

```bash
git checkout dev && git pull --ff-only origin dev && git checkout -b feature/391-splitter-scheduler
```

- [ ] **Step 2: Write the failing test**

Create `dashboard/backend/tests/test_run_kind_parent.py`:

```python
"""Run.run_kind / parent_run_id / split_decision_json columns (#391)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_run_kind_parent_columns(async_session_factory):
    from app.models import Run
    async with async_session_factory() as db:
        db.add(Run(run_id="run-parent-1", run_kind="primary",
                   started_at=datetime.now(timezone.utc),
                   split_decision_json={"proposals": 4}))
        db.add(Run(run_id="run-sub-a", run_kind="sub-of-27",
                   parent_run_id="run-parent-1",
                   started_at=datetime.now(timezone.utc)))
        await db.commit()

    async with async_session_factory() as db:
        sub = (await db.execute(select(Run).where(Run.run_id == "run-sub-a"))).scalar_one()
        assert sub.run_kind == "sub-of-27"
        assert sub.parent_run_id == "run-parent-1"
        parent = (await db.execute(select(Run).where(Run.run_id == "run-parent-1"))).scalar_one()
        assert parent.split_decision_json == {"proposals": 4}
```

- [ ] **Step 3: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_kind_parent.py -q
```

Expected: `AttributeError`.

- [ ] **Step 4: Add columns + Alembic revision**

In `dashboard/backend/app/models.py`, inside `class Run`, add:

```python
    run_kind = Column(Text, nullable=True)  # "primary" | "sub-of-<N>" | "split-decision"
    parent_run_id = Column(Text, nullable=True, index=True)
    split_decision_json = Column(JsonType, nullable=True)
```

Ensure `JsonType` is in scope (added by #393).

Create `dashboard/backend/alembic/versions/0003_run_kind_parent.py`:

```python
"""Add Run.run_kind / parent_run_id / split_decision_json (#391).

Revision ID: 0003_run_kind_parent
Revises: 0002_runner_quotas
Create Date: 2026-05-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_run_kind_parent"
down_revision = "0002_runner_quotas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("run_kind", sa.Text(), nullable=True))
        batch.add_column(sa.Column("parent_run_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("split_decision_json", sa.JSON(), nullable=True))
    op.create_index("ix_runs_parent_run_id", "runs", ["parent_run_id"])


def downgrade() -> None:
    op.drop_index("ix_runs_parent_run_id", "runs")
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("run_kind")
        batch.drop_column("parent_run_id")
        batch.drop_column("split_decision_json")
```

- [ ] **Step 5: Verify it passes + commit**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_kind_parent.py -q
git add dashboard/backend/app/models.py dashboard/backend/alembic/versions/0003_run_kind_parent.py dashboard/backend/tests/test_run_kind_parent.py
git commit -m "feat(splitter): Run.run_kind + parent_run_id columns (#391)"
```

---

## Task 9: Scheduler — `pick_eligible_subruns`

**Files:**
- New: `agent/issue_splitter/scheduler.py`
- New: `dashboard/backend/tests/test_issue_splitter_scheduler.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_issue_splitter_scheduler.py`:

```python
"""Scheduler dependency / failure semantics (#391)."""
from __future__ import annotations

from agent.issue_splitter.scheduler import pick_eligible_subruns


def _sub(number: int, *, depends_on_number: int | None = None,
         state: str = "open", merged: bool = False) -> dict:
    return {
        "number": number,
        "labels": ["splitter-proposed"],
        "depends_on_number": depends_on_number,
        "state": state,
        "merged_into_integration": merged,
    }


def test_picks_all_independents_first():
    subs = [_sub(101), _sub(102), _sub(103, depends_on_number=101)]
    eligible = pick_eligible_subruns(subs)
    numbers = {s["number"] for s in eligible}
    assert numbers == {101, 102}


def test_dependent_unlocks_after_prereq_merged():
    subs = [_sub(101, merged=True), _sub(102, depends_on_number=101)]
    eligible = pick_eligible_subruns(subs)
    assert {s["number"] for s in eligible} == {102}


def test_failed_sibling_does_not_block_others():
    subs = [_sub(101, state="closed"), _sub(102)]
    eligible = pick_eligible_subruns(subs)
    assert {s["number"] for s in eligible} == {102}


def test_no_eligible_when_label_removed():
    subs = [{"number": 101, "labels": [], "depends_on_number": None, "state": "open"}]
    eligible = pick_eligible_subruns(subs)
    assert eligible == []
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_issue_splitter_scheduler.py -q
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement scheduler**

Create `agent/issue_splitter/scheduler.py`:

```python
"""Sub-run scheduler (#391).

Picks 0-N eligible sub-issues per scheduler tick. A sub-issue is eligible
when:

1. Its issue is still open.
2. Its ``splitter-proposed`` label is **absent** — present means the
   operator hasn't approved it for autonomous pickup yet. (Same gate
   as ``vision-suggested``.)
3. Its ``depends_on_number`` is either absent or has been merged into
   the integration branch.

The caller (the existing run-trigger flow) is responsible for spawning
the run; this module only decides *which* sub-issues are ready.
"""
from __future__ import annotations

from typing import Iterable

SPLITTER_LABEL = "splitter-proposed"


def _is_approved(sub: dict) -> bool:
    return SPLITTER_LABEL not in (sub.get("labels") or ())


def _prereq_satisfied(sub: dict, by_number: dict[int, dict]) -> bool:
    dep = sub.get("depends_on_number")
    if dep is None:
        return True
    prereq = by_number.get(dep)
    if prereq is None:
        return False  # parent says depend on N but we can't find N
    return bool(prereq.get("merged_into_integration"))


def pick_eligible_subruns(subs: Iterable[dict]) -> list[dict]:
    subs = list(subs)
    by_number = {s["number"]: s for s in subs}
    eligible: list[dict] = []
    for sub in subs:
        if sub.get("state") != "open":
            continue
        if not _is_approved(sub):
            continue
        if not _prereq_satisfied(sub, by_number):
            continue
        eligible.append(sub)
    return eligible
```

Note: the test fixtures use `state="open"` and `labels=["splitter-proposed"]` — re-read the test's `_sub` helper: the eligible tests set `labels=["splitter-proposed"]`. That contradicts the spec's "operator must remove the label". Reconcile: tests must set `labels=[]` for an approved sub. **Fix the test fixtures** before running step 4:

- [ ] **Step 4: Adjust the test fixtures and verify**

Update `_sub` in `dashboard/backend/tests/test_issue_splitter_scheduler.py` to default `labels: list[str] | None = None` and treat `None` as "approved" (no `splitter-proposed`); explicitly opt into a not-yet-approved sub with `labels=["splitter-proposed"]`:

```python
def _sub(number: int, *, depends_on_number: int | None = None,
         state: str = "open", merged: bool = False,
         labels: list[str] | None = None) -> dict:
    return {
        "number": number,
        "labels": labels if labels is not None else [],
        "depends_on_number": depends_on_number,
        "state": state,
        "merged_into_integration": merged,
    }


def test_unapproved_sub_not_picked():
    subs = [_sub(101, labels=["splitter-proposed"])]
    eligible = pick_eligible_subruns(subs)
    assert eligible == []
```

Replace the previous `test_no_eligible_when_label_removed` (which had the inverse semantics) with the corrected version above. Run:

```bash
cd dashboard/backend && python3 -m pytest tests/test_issue_splitter_scheduler.py -q
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/issue_splitter/scheduler.py dashboard/backend/tests/test_issue_splitter_scheduler.py
git commit -m "feat(splitter): scheduler with depends_on + label gate (#391)"
```

---

## Task 10: Pre-dispatch hook in `coordinator/decide.py`

**Files:**
- Modify: `agent/coordinator/decide.py`
- New: `dashboard/backend/tests/test_coordinator_decide_split_hook.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_coordinator_decide_split_hook.py`:

```python
"""coordinator/decide split pre-dispatch hook (#391)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_decide_falls_through_when_flag_disabled(monkeypatch):
    monkeypatch.delenv("STATION_SPLIT_ENABLED", raising=False)
    from agent.coordinator import decide
    issue = {"number": 27, "title": "x", "body": "y", "labels": []}
    with patch("agent.coordinator.decide.run_splitter", new=AsyncMock()) as splitter_mock:
        ok = await decide.maybe_run_splitter(issue, run_id="r1",
                                              repo_summary="", vision="")
    assert ok is None
    splitter_mock.assert_not_called()


@pytest.mark.asyncio
async def test_decide_skips_splitter_when_heuristic_says_no(monkeypatch):
    monkeypatch.setenv("STATION_SPLIT_ENABLED", "1")
    from agent.coordinator import decide
    issue = {"number": 27, "title": "x", "body": "small", "labels": []}
    with patch("agent.coordinator.decide.run_splitter", new=AsyncMock()) as splitter_mock:
        ok = await decide.maybe_run_splitter(issue, run_id="r1",
                                              repo_summary="", vision="")
    assert ok is None
    splitter_mock.assert_not_called()


@pytest.mark.asyncio
async def test_decide_invokes_splitter_when_eligible(monkeypatch):
    monkeypatch.setenv("STATION_SPLIT_ENABLED", "1")
    from agent.coordinator import decide
    from agent.issue_splitter.schema import SplitDecision, SubIssueProposal
    decision = SplitDecision(
        proposals=(
            SubIssueProposal("a", "b", (), ("x",)),
            SubIssueProposal("c", "d", (), ("y",)),
        ),
    )
    issue = {"number": 27, "title": "x",
             "body": "## Acceptance criteria\n- [ ] a\n- [ ] b\n- [ ] c\n- [ ] d\n",
             "labels": []}
    with patch("agent.coordinator.decide.run_splitter",
               new=AsyncMock(return_value=decision)) as splitter_mock:
        result = await decide.maybe_run_splitter(issue, run_id="r1",
                                                  repo_summary="", vision="")
    splitter_mock.assert_called_once()
    assert result is decision
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_coordinator_decide_split_hook.py -q
```

Expected: `AttributeError: maybe_run_splitter`.

- [ ] **Step 3: Add the hook to `decide.py`**

In `agent/coordinator/decide.py`, append:

```python
import os
import logging

from agent.issue_splitter.heuristics import maybe_split
from agent.issue_splitter.runner import run_splitter
from agent.issue_splitter.schema import SplitDecision, SplitterError

logger = logging.getLogger(__name__)


async def maybe_run_splitter(
    issue: dict,
    *,
    run_id: str,
    repo_summary: str,
    vision: str,
) -> SplitDecision | None:
    """Pre-dispatch hook (#391). Returns a SplitDecision when the issue
    should be split, ``None`` otherwise (caller falls through to the
    normal single-issue dispatch path).

    Feature-gated by ``STATION_SPLIT_ENABLED=1``.
    """
    if os.environ.get("STATION_SPLIT_ENABLED") != "1":
        return None
    heuristic = maybe_split(issue)
    if not heuristic.should_split:
        return None
    try:
        return await run_splitter(
            issue=issue, run_id=run_id,
            repo_summary=repo_summary, vision=vision,
        )
    except SplitterError as exc:
        logger.warning("splitter failed for issue #%s: %s — falling back to single-issue",
                       issue.get("number"), exc)
        return None
```

Call `maybe_run_splitter` at the top of whatever function `decide.py` exposes as the dispatch entry point (likely `pick_next` or `decide_next`). The integration is a single conditional:

```python
decision = await maybe_run_splitter(
    next_issue, run_id=current_run_id,
    repo_summary=repo_summary, vision=vision,
)
if decision is not None:
    # Hand off to the splitter pipeline (Task 11).
    return await execute_split_decision(next_issue, decision, current_run_id)
```

The `execute_split_decision` function is wired in Task 11. For this task, define a stub that raises `NotImplementedError` so the type linkage is in place:

```python
async def execute_split_decision(issue, decision, run_id) -> None:
    raise NotImplementedError("Task 11 wires this")
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_coordinator_decide_split_hook.py -q
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/coordinator/decide.py dashboard/backend/tests/test_coordinator_decide_split_hook.py
git commit -m "feat(splitter): pre-dispatch hook in coordinator/decide (#391)"
```

---

## Task 11: `execute_split_decision` — create sub-issues + open integration branch

**Files:**
- Modify: `agent/coordinator/decide.py`
- New: `dashboard/backend/tests/test_execute_split_decision.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_execute_split_decision.py`:

```python
"""execute_split_decision side effects (#391)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.coordinator.decide import execute_split_decision
from agent.issue_splitter.schema import SplitDecision, SubIssueProposal


@pytest.mark.asyncio
async def test_execute_split_decision_creates_sub_issues_and_backlinks():
    decision = SplitDecision(
        proposals=(
            SubIssueProposal("A", "body-a", (), ("x",)),
            SubIssueProposal("B", "body-b", (), ("y",), depends_on=0),
        ),
    )
    parent = {"number": 27, "title": "auth", "labels": ["backend"],
              "repo": "kenhaesler/claude-agent-station"}

    gh = MagicMock()
    gh.label_exists.return_value = True
    gh.create_issue.side_effect = [{"number": 101}, {"number": 102}]
    with patch("agent.coordinator.decide._gh_client", return_value=gh), \
         patch("agent.coordinator.decide._ensure_integration_branch") as iib:
        await execute_split_decision(parent, decision, run_id="rsd-1")

    assert gh.create_issue.call_count == 2
    gh.create_issue_comment.assert_called_once()
    iib.assert_called_once_with("kenhaesler/claude-agent-station", 27)
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_execute_split_decision.py -q
```

Expected: `NotImplementedError`.

- [ ] **Step 3: Implement `execute_split_decision`**

Replace the stub in `agent/coordinator/decide.py`:

```python
from agent.issue_splitter.github_ops import (
    add_backlink_comment,
    create_sub_issues,
)
from agent.gh_client import GhClient


def _gh_client() -> GhClient:
    return GhClient()


def _ensure_integration_branch(repo: str, parent_number: int) -> str:
    """Create ``integration/issue-<N>`` off ``dev`` if missing. Returns name."""
    branch = f"integration/issue-{parent_number}"
    GhClient().ensure_branch(repo, branch, from_branch="dev")
    return branch


async def execute_split_decision(
    parent: dict,
    decision: SplitDecision,
    *,
    run_id: str,
) -> None:
    gh = _gh_client()
    created = create_sub_issues(parent, decision.proposals, gh)
    sub_numbers = [c["number"] for c in created]
    add_backlink_comment(
        parent_repo=parent["repo"],
        parent_number=parent["number"],
        sub_numbers=sub_numbers,
        gh=gh,
    )
    # Tag the parent so the router doesn't re-consider it.
    gh.add_labels(*parent["repo"].split("/", 1), parent["number"], ["split"])
    _ensure_integration_branch(parent["repo"], parent["number"])

    # Persist the split decision on the run row for observability.
    from app.database import async_session
    from app.models import Run
    from sqlalchemy import update
    async with async_session() as db:
        await db.execute(
            update(Run).where(Run.run_id == run_id).values(
                run_kind="split-decision",
                split_decision_json={
                    "parent_number": parent["number"],
                    "sub_numbers": sub_numbers,
                    "warnings": list(decision.warnings),
                },
            )
        )
        await db.commit()
```

`GhClient.add_labels`, `ensure_branch`, `create_label`, `label_exists`, `create_issue`, `create_issue_comment` are expected on the existing client at `agent/gh_client.py`. If any are missing, add a thin wrapper around the existing `subprocess.run(["gh", ...])` calls in that module (one helper per method).

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_execute_split_decision.py -q
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/coordinator/decide.py dashboard/backend/tests/test_execute_split_decision.py
git commit -m "feat(splitter): execute_split_decision creates sub-issues + integration branch (#391)"
```

---

## Task 12: `GET /api/runs/{run_id}/tree`

**Files:**
- Modify: `dashboard/backend/app/routers/runs.py`
- Modify: `dashboard/backend/app/schemas.py`
- New: `dashboard/backend/tests/test_runs_tree.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_runs_tree.py`:

```python
"""GET /api/runs/{run_id}/tree (#391)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_tree_endpoint_returns_parent_and_subs(async_session_factory, client):
    from app.models import Run

    async with async_session_factory() as db:
        db.add(Run(run_id="run-pt-1", run_kind="primary",
                   started_at=datetime.now(timezone.utc)))
        db.add(Run(run_id="run-pt-1-a", run_kind="sub-of-27",
                   parent_run_id="run-pt-1",
                   started_at=datetime.now(timezone.utc),
                   verdict="APPROVE"))
        db.add(Run(run_id="run-pt-1-b", run_kind="sub-of-27",
                   parent_run_id="run-pt-1",
                   started_at=datetime.now(timezone.utc),
                   verdict="PR"))
        await db.commit()

    resp = await client.get("/api/runs/run-pt-1/tree")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == "run-pt-1"
    sub_ids = {s["run_id"] for s in body["sub_runs"]}
    assert sub_ids == {"run-pt-1-a", "run-pt-1-b"}


@pytest.mark.asyncio
async def test_tree_endpoint_404_for_unknown(client):
    resp = await client.get("/api/runs/run-does-not-exist/tree")
    assert resp.status_code == 404
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_runs_tree.py -q
```

Expected: 404 on the first test (endpoint not defined).

- [ ] **Step 3: Add the endpoint**

In `dashboard/backend/app/schemas.py`:

```python
class RunTreeSubRun(BaseModel):
    run_id: str
    run_kind: str | None = None
    verdict: str | None = None
    status: str | None = None


class RunTree(BaseModel):
    run_id: str
    run_kind: str | None
    sub_runs: list[RunTreeSubRun]
```

In `dashboard/backend/app/routers/runs.py`, after `get_run_timeline`:

```python
@router.get("/{run_id}/tree", response_model=RunTree)
async def get_run_tree(run_id: str, db: AsyncSession = Depends(get_db)) -> RunTree:
    parent = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one_or_none()
    if parent is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    subs = (
        await db.execute(select(Run).where(Run.parent_run_id == run_id))
    ).scalars().all()
    return RunTree(
        run_id=parent.run_id,
        run_kind=parent.run_kind,
        sub_runs=[
            RunTreeSubRun(
                run_id=s.run_id,
                run_kind=s.run_kind,
                verdict=s.verdict,
                status=s.status,
            )
            for s in subs
        ],
    )
```

- [ ] **Step 4: Verify it passes + commit**

```bash
cd dashboard/backend && python3 -m pytest tests/test_runs_tree.py -q
git add dashboard/backend/app/routers/runs.py dashboard/backend/app/schemas.py dashboard/backend/tests/test_runs_tree.py
git commit -m "feat(splitter): GET /api/runs/{run_id}/tree (#391)"
```

---

## Task 13: PR 3 — open

- [ ] **Step 1: Push**

```bash
git push -u origin feature/391-splitter-scheduler
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --base dev --title "feat(splitter): scheduler + decide hook + tree API (#391, PR 3/4)" --body "$(cat <<'EOF'
Part 3 of 4 for #391.

## Summary
- DB: `Run.run_kind`, `Run.parent_run_id` (indexed), `Run.split_decision_json`.
- `agent/issue_splitter/scheduler.py` (depends_on + label-gate).
- Pre-dispatch hook `maybe_run_splitter` + `execute_split_decision` in `agent/coordinator/decide.py`, behind `STATION_SPLIT_ENABLED=1` (default off).
- `GET /api/runs/{run_id}/tree`.

## Test plan
- [ ] `cd dashboard/backend && pytest tests/test_run_kind_parent.py tests/test_issue_splitter_scheduler.py tests/test_coordinator_decide_split_hook.py tests/test_execute_split_decision.py tests/test_runs_tree.py -q`

The hook is **off by default** — no production behaviour change until an operator sets `STATION_SPLIT_ENABLED=1`.
EOF
)"
```

- [ ] **Step 3-5: Wait for CI, merge, sync.**

---

# PR 4 — Frontend + integration test + docs

## Task 14: Branch — verify prerequisites

- [ ] **Step 1: Branch**

```bash
git checkout dev && git pull --ff-only origin dev && git checkout -b feature/391-splitter-ui
```

- [ ] **Step 2: Re-confirm #385 + #386 merged**

```bash
gh pr list --state merged --search "385 in:title"
gh pr list --state merged --search "386 in:title"
```

Expected: green. If still not merged, **stop and route back to the team**. The integration test in Task 16 needs both.

- [ ] **Step 3-5: (No commit)**

---

## Task 15: `RunComplete` aggregation fields (gated on #385)

**Files:**
- Modify: wherever the `RunComplete` tool schema lives (added in #385 — likely `agent/tools/run_complete.py`).
- Modify: `dashboard/backend/app/schemas.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_run_complete_aggregation.py`:

```python
"""RunComplete aggregation fields (#391, gated on #385)."""
from __future__ import annotations


def test_run_complete_schema_has_sub_runs_field():
    from agent.tools.run_complete import RunCompleteInput  # delivered by #385

    fields = RunCompleteInput.model_fields  # pydantic v2
    assert "sub_runs" in fields
    assert "parent_run" in fields
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_complete_aggregation.py -q
```

Expected: fail (field not present).

- [ ] **Step 3: Add the fields to `RunCompleteInput`**

In the file delivered by #385 (e.g. `agent/tools/run_complete.py`), append to `RunCompleteInput`:

```python
    sub_runs: list[str] = Field(default_factory=list,
        description="Sub-run IDs spawned from this run (parent runs only).")
    parent_run: str | None = Field(default=None,
        description="Parent run ID if this is a sub-run.")
```

If the file path differs, locate it via:

```bash
grep -rn "class RunCompleteInput" agent/
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_complete_aggregation.py -q
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/tools/run_complete.py dashboard/backend/tests/test_run_complete_aggregation.py
git commit -m "feat(splitter): RunComplete.sub_runs + parent_run (#391, gated on #385)"
```

---

## Task 16: Integration test — synthetic split flow

**Files:**
- New: `dashboard/backend/tests/integration/test_splitter_e2e.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/integration/test_splitter_e2e.py`:

```python
"""End-to-end splitter flow with stubbed GitHub + SDK (#391)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_synthetic_split_flow_creates_sub_issues(async_session_factory, monkeypatch):
    monkeypatch.setenv("STATION_SPLIT_ENABLED", "1")
    from agent.coordinator import decide
    from app.models import Run

    parent = {
        "number": 27,
        "title": "Auth refactor",
        "body": ("## Acceptance criteria\n"
                 "- [ ] login api\n- [ ] me endpoint\n"
                 "- [ ] oauth callback\n- [ ] route middleware\n"),
        "labels": ["backend"],
        "repo": "kenhaesler/claude-agent-station",
    }

    splitter_raw = json.dumps([
        {"title": "Login API", "body": "POST /api/auth/login",
         "labels": ["backend"], "acceptance": ["Returns 200 + JWT"], "depends_on": None},
        {"title": "/me endpoint", "body": "GET /api/me",
         "labels": ["backend"], "acceptance": ["Returns 200 when authenticated"], "depends_on": 0},
    ])

    gh = MagicMock()
    gh.label_exists.return_value = True
    gh.create_issue.side_effect = [{"number": 101}, {"number": 102}]

    async with async_session_factory() as db:
        db.add(Run(run_id="rsd-int-1", run_kind="split-decision",
                   started_at=datetime.now(timezone.utc)))
        await db.commit()

    with patch("agent.issue_splitter.runner._invoke_splitter_sdk",
               new=AsyncMock(return_value=splitter_raw)), \
         patch("agent.coordinator.decide._gh_client", return_value=gh), \
         patch("agent.coordinator.decide._ensure_integration_branch") as iib:
        decision = await decide.maybe_run_splitter(
            parent, run_id="rsd-int-1", repo_summary="", vision="",
        )
        assert decision is not None
        await decide.execute_split_decision(parent, decision, run_id="rsd-int-1")

    assert gh.create_issue.call_count == 2
    gh.create_issue_comment.assert_called_once()
    iib.assert_called_once()

    # Verify the split decision was persisted.
    from sqlalchemy import select
    async with async_session_factory() as db:
        row = (await db.execute(select(Run).where(Run.run_id == "rsd-int-1"))).scalar_one()
        assert row.run_kind == "split-decision"
        assert row.split_decision_json["sub_numbers"] == [101, 102]
```

- [ ] **Step 2: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/integration/test_splitter_e2e.py -q
```

Expected: 1 passed (across both `[sqlite]` and `[postgres]` fixtures).

- [ ] **Step 3-5: (No code change.) Commit.**

```bash
git add dashboard/backend/tests/integration/test_splitter_e2e.py
git commit -m "test(splitter): integration synthetic split flow (#391)"
```

---

## Task 17: Frontend — fan-out panel on RunDetail; nested rows on MissionControl

**Files:**
- Modify: `dashboard/frontend/src/pages/RunDetail.svelte`
- Modify: `dashboard/frontend/src/pages/MissionControl.svelte`
- New: `dashboard/frontend/e2e/splitter_fanout.spec.ts`

- [ ] **Step 1: Write the failing spec**

Create `dashboard/frontend/e2e/splitter_fanout.spec.ts`:

```ts
import { expect, test } from '@playwright/test';

test('RunDetail shows fan-out panel for parent run', async ({ page }) => {
  await page.route('**/api/runs/run-parent-1/tree', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        run_id: 'run-parent-1',
        run_kind: 'split-decision',
        sub_runs: [
          { run_id: 'run-sub-a', run_kind: 'sub-of-27', verdict: 'APPROVE', status: 'success' },
          { run_id: 'run-sub-b', run_kind: 'sub-of-27', verdict: 'PR', status: 'success' },
        ],
      }),
    });
  });
  await page.route('**/api/runs/run-parent-1/full', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        run_id: 'run-parent-1',
        status: 'success',
        run_kind: 'split-decision',
        coordinator_tasks: [],
      }),
    });
  });

  await page.goto('/runs/run-parent-1');
  await expect(page.getByText('Fan-out')).toBeVisible();
  await expect(page.getByText('run-sub-a')).toBeVisible();
  await expect(page.getByText('run-sub-b')).toBeVisible();
});
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/frontend && npx playwright test e2e/splitter_fanout.spec.ts
```

Expected: 1 failed.

- [ ] **Step 3: Add the panel**

In `dashboard/frontend/src/pages/RunDetail.svelte`, near the top of the script:

```ts
  let tree = $state<{
    run_id: string;
    run_kind: string | null;
    sub_runs: { run_id: string; run_kind: string | null; verdict: string | null; status: string | null }[];
  } | null>(null);

  async function loadTree() {
    if (!run?.run_kind || run.run_kind !== 'split-decision') return;
    const r = await fetch(`/api/runs/${run.run_id}/tree`);
    if (r.ok) tree = await r.json();
  }

  $effect(() => { loadTree(); });
```

In the Overview tab body, add:

```svelte
{#if tree && tree.sub_runs.length > 0}
  <section class="fanout">
    <h3>Fan-out</h3>
    <ul>
      {#each tree.sub_runs as sub}
        <li>
          <a href={`/runs/${sub.run_id}`}>{sub.run_id}</a>
          <span class="verdict">{sub.verdict ?? '—'}</span>
          <span class="status">{sub.status ?? '—'}</span>
        </li>
      {/each}
    </ul>
  </section>
{/if}
```

For `MissionControl.svelte`'s run list, group rows so sub-runs render indented under their parent (when `parent_run_id` is present in the row). The list endpoint already returns `parent_run_id` because it's on `RunOut` via Task 8's schema update. Add a one-level nesting CSS rule:

```svelte
{#each runs as run}
  <tr class="run" class:sub={!!run.parent_run_id}>
    <td>{run.run_id}</td>
    …
  </tr>
{/each}

<style>
  tr.sub td:first-child { padding-left: 1.5rem; opacity: 0.85; }
</style>
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/frontend && npx playwright test e2e/splitter_fanout.spec.ts
cd dashboard/frontend && npm run check
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/pages/RunDetail.svelte dashboard/frontend/src/pages/MissionControl.svelte dashboard/frontend/e2e/splitter_fanout.spec.ts
git commit -m "feat(splitter): fan-out panel + nested run rows (#391)"
```

---

## Task 18: Docs

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/configuration.md`

- [ ] **Step 1: Append failing doc-shape test**

Append to `dashboard/backend/tests/test_issue_splitter_prompt.py`:

```python
def test_architecture_doc_has_issue_decomposition_section():
    p = REPO_ROOT / "docs/architecture.md"
    text = p.read_text()
    assert "## Issue decomposition" in text
    assert "issue-splitter" in text
    assert "STATION_SPLIT_ENABLED" in text


def test_configuration_doc_documents_split_envs():
    p = REPO_ROOT / "docs/configuration.md"
    text = p.read_text()
    for token in ("STATION_SPLIT_ENABLED", "splitter-proposed", "split-me", "do-not-split"):
        assert token in text, token
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_issue_splitter_prompt.py -q
```

Expected: 2 fail.

- [ ] **Step 3: Append docs**

Append to `docs/architecture.md`:

```markdown
## Issue decomposition

The coordinator's `decide.py` runs a pre-dispatch hook `maybe_run_splitter`
(feature-gated by `STATION_SPLIT_ENABLED=1`) before spawning a specialist
team. Eligible issues — long bodies, ≥4 acceptance criteria, cross-cutting
label sets, or the explicit `split-me` label — are routed to an
issue-splitter SDK session (`agent/issue_splitter/runner.py`). The splitter
emits a JSON array of 2-5 sub-issue proposals; the harness creates them on
GitHub with a `splitter-proposed` label and `Parent: #N` back-link.

Sub-runs execute concurrently when per-project containers (#386) are in
place: one runner container per sub-run, all merging to an
`integration/issue-<N>` branch. CI on the integration branch is the
integration test. A single PR to `dev` is opened once all sub-runs land.

Failure isolation: a failed sub-run does not block its siblings; the
parent stays open with a `splitter-needs-rework` label only if *every*
sub-run fails.
```

Append to `docs/configuration.md`:

```markdown
### Issue splitter (#391)

| Env var | Default | Notes |
|---|---|---|
| `STATION_SPLIT_ENABLED` | `0` | Set to `1` to enable the issue-splitter pre-dispatch hook. Off by default during rollout. |

| Label | Purpose |
|---|---|
| `splitter-proposed` | Sub-issue created by the splitter; operator must remove the label before autonomous pickup. Mirrors `vision-suggested`. |
| `split-me` | Operator opt-in: always split this issue, even if heuristics say no. |
| `do-not-split` | Operator opt-out: never split this issue, even if heuristics say yes. Veto wins over everything. |
| `split` | Added automatically to a parent after its sub-issues are created so the router doesn't re-consider it. |
| `splitter-needs-rework` | Added to the parent when all sub-runs fail. |
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_issue_splitter_prompt.py -q
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add docs/architecture.md docs/configuration.md dashboard/backend/tests/test_issue_splitter_prompt.py
git commit -m "docs(splitter): architecture + configuration sections (#391)"
```

---

## Task 19: PR 4 — open

- [ ] **Step 1: Push**

```bash
git push -u origin feature/391-splitter-ui
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --base dev --title "feat(splitter): fan-out UI + e2e + docs (#391, PR 4/4)" --body "$(cat <<'EOF'
Part 4 of 4 for #391.

## Summary
- `RunComplete.sub_runs` + `parent_run` fields (gated on #385).
- Integration test of the full split flow with stubbed GitHub + SDK.
- RunDetail "Fan-out" panel; MissionControl nests sub-runs under parent.
- Architecture + configuration docs.

## Test plan
- [ ] `cd dashboard/backend && pytest tests/integration/test_splitter_e2e.py tests/test_run_complete_aggregation.py -q`
- [ ] `cd dashboard/frontend && npx playwright test e2e/splitter_fanout.spec.ts`
- [ ] Manual rollout: set `STATION_SPLIT_ENABLED=1` on a dev box, drop `split-me` on a synthetic issue, observe 4 sub-issues created with `splitter-proposed`; remove labels manually; observe sub-runs schedule into separate containers (#386).
EOF
)"
```

- [ ] **Step 3-5: Wait for CI, merge, sync dev.**

---

## Self-review checklist

- [x] Every acceptance criterion in `2026-05-14-issue-391-decompose-long-runs.md` maps to ≥1 task:
  - "New agent role: issue-splitter, with prompt + tests" → Task 3.
  - "Smart-router can decide 'too big, split first' before spawning teammates" → Tasks 2, 10.
  - "Sub-issues link back to parent issue" → Tasks 5, 11.
  - "Run scheduler can fan out 2-5 runs on sub-issues concurrently" → Task 9; concurrency proven by Task 16 + #386 prerequisites.
  - "Failure of one sub-issue doesn't block the others" → Task 9 (`failed_sibling_does_not_block_others`).
  - "Auth refactor in ≤4×10-min runs" → measured at rollout (Task 19's manual smoke step).
- [x] No `TBD`, `TODO`, `add error handling`, `similar to Task N` placeholders.
- [x] Real paths verified: `agent/coordinator/decide.py` (source present, confirmed in Task 0 step 4), `agent/vision_analyst.py:300+` (label-create pattern reused), `agent/agents/issue-worker.md` (role file template), `dashboard/backend/app/models.py` `Run` class at line 44.
- [x] Type / name consistency: `SubIssueProposal`, `SplitDecision`, `SplitterError`, `parse_splitter_output`, `maybe_split`, `run_splitter`, `maybe_run_splitter`, `execute_split_decision`, `create_sub_issues`, `add_backlink_comment`, `ensure_splitter_label`, `pick_eligible_subruns`, `STATION_SPLIT_ENABLED`, `splitter-proposed`, `split-me`, `do-not-split` used identically across files and tests.
- [x] Hard prerequisites declared and re-checked at Task 0 + Task 14. Tasks 15, 16 explicitly depend on #385 + #386.

## Drift / corrections vs. the spec

- The spec's open question "smart-router module path is wrong / `.pyc`-only" is resolved at Task 0 step 4: `agent/coordinator/decide.py` is the integration point. If the source file is genuinely absent in a future repo state, this plan halts at Task 0 — the plan does not paper over a missing module.
- The spec proposes a `Run.run_kind` enum with values `primary | sub-of-<parent-issue-number> | split-decision`. This plan adopts that literal vocabulary (Task 8 stores as plain `Text` rather than a DB enum to keep migrations simple across SQLite + Postgres).
- The spec mentions `RunComplete` aggregation as an open dependency on #385. This plan isolates that work to Task 15 (gated PR-4) so PRs 1-3 can ship before #385 lands.
- The spec leaves the per-sub-run integration branch flow loose. This plan commits to `integration/issue-<N>` as the merge target and a single final PR to `dev` once every sub-run resolves — chosen to match the existing `feature/<desc>` branching pattern in `CLAUDE.md`.
