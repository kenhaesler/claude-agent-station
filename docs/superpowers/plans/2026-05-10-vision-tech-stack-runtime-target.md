# Vision Tech Stack + Runtime Target Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Project Vision schema from 7 to 9 H2 sections (adding `## Tech Stack` and `## Runtime Target` at positions 4 and 5), with full plumbing through the dashboard schema, render layer, chat lifecycle, the orchestrator's parser, the vision-analyst and vision-scoring agents, the lead's prompt assembly, and both chat prompt templates. Old visions keep working; backfill is user-driven.

**Architecture:** Free-text markdown sections — same shape as the existing 7. Optional Pydantic fields with empty-string defaults so old `vision-doc` JSON validates. The render layer keeps the existing `_(not specified)_` placeholder rule uniformly across all 9 sections. The new sections flow through `agent/vision.py:load_vision()` → the parsed dict → vision_analyst / vision_scoring / station_orchestrator's lead prompt.

**Tech Stack:** Python 3.11, Pydantic v2, pytest, SQLAlchemy (for fixture setup only), Svelte 5.

**Spec:** `docs/superpowers/specs/2026-05-10-vision-tech-stack-runtime-target-design.md`

**Issue:** #335

---

## File Structure

**Modified files (dashboard):**

- `dashboard/backend/app/schemas.py` — `VisionDoc` gains two optional fields.
- `dashboard/backend/app/services/vision_render.py` — `SECTIONS` list gains two entries at positions 4 and 5.
- `dashboard/backend/app/services/vision_chat.py` — `_SECTIONS` list extended.
- `dashboard/backend/tests/test_vision_render.py` — existing tests updated from 7 to 9 sections; new `test_vision_doc_optional_fields` added.

**Modified files (orchestrator):**

- `agent/vision.py` — `SECTIONS` list extended.
- `agent/vision_analyst.py` — `_PROMPT` template + named kwargs.
- `agent/vision_scoring.py` — `_PROMPT_TEMPLATE` template (kwargs already use `**vision`, no change there).
- `agent/station_orchestrator.py` — `vision_section` assembly adds Tech Stack + Runtime Target blocks.
- `agent/prompts/vision_create.md` — section list extended; "seven" → "nine" in prose.
- `agent/prompts/vision_refine.md` — section list extended.

**New files:**

- `dashboard/backend/tests/test_vision_chat_parser_required_keys.py` — pinning test that `_REQUIRED_DOC_KEYS` stays at 7.

**Modified existing test file:**

- `dashboard/backend/tests/test_agent_vision.py` — two new cases for `load_vision()` with the new sections.

**Untouched (with rationale):**

- `dashboard/backend/app/services/vision_chat_parser.py` — `_REQUIRED_DOC_KEYS` must NOT gain the new fields (old chat payloads would fail validation); Pydantic accepts the missing fields via defaults.
- `dashboard/backend/app/routers/vision.py` — passes `VisionDoc` through.
- `dashboard/frontend/src/components/vision/VisionTab.svelte` — displays markdown source verbatim.
- `dashboard/frontend/src/lib/types.ts` — `VisionRead.body: string` covers both shapes.
- DB schema / migrations.
- Manager review prompt.

---

## Conventions

- Python: type hints on every function. Tests in `dashboard/backend/tests/`. Run with `cd dashboard/backend && pytest` (the test harness lives there).
- Branch: a single feature branch `feature/vision-tech-stack-runtime` for the whole plan; PR opened in the final task against `dev`.
- Commits: each task ends with a single commit. Conventional format (`feat(vision): ...` / `test(vision): ...` / `docs(prompt): ...`).
- Bash: avoid `cd dashboard/backend` chained in `&&` — prefer absolute paths so the cwd stays at the repo root between tool calls.

---

## Task 1: Create the feature branch

**Files:** none

- [ ] **Step 1: Branch off `dev`**

```bash
cd /home/simon/Documents/claude-agent-station
git checkout dev
git pull --ff-only
git checkout -b feature/vision-tech-stack-runtime
```

- [ ] **Step 2: Verify branch**

```bash
git status
git branch --show-current
```

Expected: clean working tree, branch is `feature/vision-tech-stack-runtime`.

---

## Task 2: Extend `VisionDoc` schema (TDD)

**Files:**
- Test: `dashboard/backend/tests/test_vision_render.py` (add a new test at the end of the file)
- Modify: `dashboard/backend/app/schemas.py` lines 893–903

- [ ] **Step 1: Write the failing test**

Append to `dashboard/backend/tests/test_vision_render.py`:

```python
def test_vision_doc_optional_fields():
    """VisionDoc accepts payloads missing tech_stack / runtime_target — they
    default to empty string. Locks the back-compat behaviour for chats that
    were authored before issue #335 added the two fields.
    """
    from app.schemas import VisionDoc

    payload = {
        "problem": "P", "users": "U", "end_state": "E",
        "non_goals": "N", "principles": "Pr",
        "horizons": "H", "anti_patterns": "A",
    }
    doc = VisionDoc.model_validate(payload)
    assert doc.tech_stack == ""
    assert doc.runtime_target == ""
    dumped = doc.model_dump()
    assert dumped["tech_stack"] == ""
    assert dumped["runtime_target"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest tests/test_vision_render.py::test_vision_doc_optional_fields -v
```

Expected: `FAIL` with `AttributeError: ... 'tech_stack'` (the field doesn't exist yet).

- [ ] **Step 3: Add the two fields to `VisionDoc`**

In `dashboard/backend/app/schemas.py`, find:

```python
class VisionDoc(BaseModel):
    """Structured vision payload — one field per section."""
    problem: str
    users: str
    end_state: str
    non_goals: str
    principles: str
    horizons: str
    anti_patterns: str
```

Replace with:

```python
class VisionDoc(BaseModel):
    """Structured vision payload — one field per section.

    Issue #335: tech_stack and runtime_target are optional with empty-string
    defaults so old vision-doc JSON (from chats predating the change) keeps
    validating.
    """
    problem: str
    users: str
    end_state: str
    tech_stack: str = ""
    runtime_target: str = ""
    non_goals: str
    principles: str
    horizons: str
    anti_patterns: str
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest tests/test_vision_render.py::test_vision_doc_optional_fields -v
```

Expected: 1 PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station
git add dashboard/backend/app/schemas.py \
        dashboard/backend/tests/test_vision_render.py
git -c commit.gpgsign=false commit -m "feat(vision): add optional tech_stack and runtime_target to VisionDoc (#335)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Update the render layer (TDD — extend existing tests)

**Files:**
- Modify: `dashboard/backend/tests/test_vision_render.py:5-26` (both existing tests)
- Modify: `dashboard/backend/app/services/vision_render.py:7-15`

- [ ] **Step 1: Rewrite the existing render tests to expect 9 sections**

In `dashboard/backend/tests/test_vision_render.py`, replace the entire body before the `test_vision_doc_optional_fields` you added in Task 2. The new contents (keep imports and the new test at the bottom):

```python
from datetime import datetime, timezone
from app.services.vision_render import render_vision_doc


def test_render_includes_all_nine_sections_in_order():
    doc = {
        "problem": "P", "users": "U", "end_state": "E",
        "tech_stack": "TS", "runtime_target": "RT",
        "non_goals": "N", "principles": "Pr",
        "horizons": "H", "anti_patterns": "A",
    }
    md = render_vision_doc(doc, repo="o/r", refined_at=datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc))
    headers = [line for line in md.splitlines() if line.startswith("## ")]
    assert headers == [
        "## Problem", "## Users", "## End-state",
        "## Tech Stack", "## Runtime Target",
        "## Non-goals", "## Principles", "## Horizons", "## Anti-patterns",
    ]
    assert "P" in md and "U" in md and "Pr" in md and "TS" in md and "RT" in md
    assert md.startswith("# Vision — o/r\n")
    assert "*Last refined: 2026-05-07T12:00:00+00:00 via Claude Station*" in md


def test_render_handles_empty_section_with_placeholder():
    """Empty new fields use the same `_(not specified)_` placeholder
    as the original seven (issue #335 backward-compat)."""
    doc = {
        "problem": "P", "users": "", "end_state": "E",
        "tech_stack": "", "runtime_target": "",
        "non_goals": "", "principles": "",
        "horizons": "", "anti_patterns": "",
    }
    md = render_vision_doc(doc, repo="o/r", refined_at=datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc))
    assert "_(not specified)_" in md
    # All nine headings always present, regardless of body.
    assert "## Tech Stack" in md
    assert "## Runtime Target" in md
```

(The `test_vision_doc_optional_fields` from Task 2 stays as-is.)

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest tests/test_vision_render.py -v
```

Expected: `test_render_includes_all_nine_sections_in_order` FAILS (renderer emits only 7 headings); `test_render_handles_empty_section_with_placeholder` FAILS on the `## Tech Stack` assertion.

- [ ] **Step 3: Extend `SECTIONS` in `vision_render.py`**

In `dashboard/backend/app/services/vision_render.py`, find:

```python
SECTIONS = [
    ("problem", "Problem"),
    ("users", "Users"),
    ("end_state", "End-state"),
    ("non_goals", "Non-goals"),
    ("principles", "Principles"),
    ("horizons", "Horizons"),
    ("anti_patterns", "Anti-patterns"),
]
```

Replace with:

```python
SECTIONS = [
    ("problem", "Problem"),
    ("users", "Users"),
    ("end_state", "End-state"),
    # Issue #335: tech stack and runtime target slot between End-state and
    # Non-goals. Both are optional on VisionDoc; empty bodies render as
    # `_(not specified)_` like the existing seven.
    ("tech_stack", "Tech Stack"),
    ("runtime_target", "Runtime Target"),
    ("non_goals", "Non-goals"),
    ("principles", "Principles"),
    ("horizons", "Horizons"),
    ("anti_patterns", "Anti-patterns"),
]
```

Also update the docstring of `render_vision_doc`. Find:

```
Empty/missing sections become a `_(not specified)_` placeholder so the
file always has all seven H2 headings — orchestrator hooks rely on a
consistent shape.
```

Replace `seven` with `nine`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest tests/test_vision_render.py -v
```

Expected: 3 PASS (both updated tests + the optional-fields test from Task 2).

- [ ] **Step 5: Commit**

```bash
cd /home/simon/Documents/claude-agent-station
git add dashboard/backend/app/services/vision_render.py \
        dashboard/backend/tests/test_vision_render.py
git -c commit.gpgsign=false commit -m "feat(vision): render tech_stack and runtime_target sections (#335)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Pin `_REQUIRED_DOC_KEYS` to the original 7 (TDD — new test file)

The chat parser must continue to validate 7-key JSON blocks for back-compat. Lock this with a pinning test.

**Files:**
- Create: `dashboard/backend/tests/test_vision_chat_parser_required_keys.py`

- [ ] **Step 1: Write the failing test**

```python
# dashboard/backend/tests/test_vision_chat_parser_required_keys.py
"""Pinning test for the vision-doc parser's required-keys contract.

Issue #335 added tech_stack and runtime_target as OPTIONAL fields on
VisionDoc with empty-string defaults. The chat parser's
``_REQUIRED_DOC_KEYS`` set, however, must stay at the original seven so
that vision-doc JSON blocks predating the change (e.g. resumed from
cache, or hand-edited by an operator) keep validating. If the two new
keys are ever added here, old chats fail with "missing required key" —
silently breaking the back-compat we promised.
"""

from app.services.vision_chat_parser import _REQUIRED_DOC_KEYS


def test_required_doc_keys_is_exactly_seven_original_fields():
    assert _REQUIRED_DOC_KEYS == {
        "problem", "users", "end_state",
        "non_goals", "principles", "horizons", "anti_patterns",
    }, (
        f"REGRESSION (#335): _REQUIRED_DOC_KEYS changed to {_REQUIRED_DOC_KEYS}. "
        "tech_stack and runtime_target must remain optional in the chat "
        "parser to preserve back-compat with pre-#335 chat payloads."
    )
```

- [ ] **Step 2: Run test to verify it passes (the contract should already be intact)**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest tests/test_vision_chat_parser_required_keys.py -v
```

Expected: 1 PASS (the parser hasn't been changed, so `_REQUIRED_DOC_KEYS` is still the original 7).

- [ ] **Step 3: Commit**

```bash
cd /home/simon/Documents/claude-agent-station
git add dashboard/backend/tests/test_vision_chat_parser_required_keys.py
git -c commit.gpgsign=false commit -m "test(vision): pin _REQUIRED_DOC_KEYS to original 7 for back-compat (#335)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Update the chat lifecycle's `_SECTIONS` list

**Files:**
- Modify: `dashboard/backend/app/services/vision_chat.py:144-147`

- [ ] **Step 1: Find the existing `_SECTIONS` list**

```bash
grep -n "_SECTIONS = \[" dashboard/backend/app/services/vision_chat.py
```

Expected: one match around line 144.

- [ ] **Step 2: Extend the list**

In `dashboard/backend/app/services/vision_chat.py`, find:

```python
_SECTIONS = [
    "problem", "users", "end_state", "non_goals",
    "principles", "horizons", "anti_patterns",
]
```

Replace with:

```python
# Order matches dashboard/backend/app/services/vision_render.py:SECTIONS.
# Issue #335 inserted tech_stack and runtime_target between end_state and
# non_goals.
_SECTIONS = [
    "problem", "users", "end_state",
    "tech_stack", "runtime_target",
    "non_goals", "principles", "horizons", "anti_patterns",
]
```

- [ ] **Step 3: Run the related chat tests to make sure nothing broke**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest tests/test_vision_chat_service.py tests/test_vision_chat_parser.py -q
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
cd /home/simon/Documents/claude-agent-station
git add dashboard/backend/app/services/vision_chat.py
git -c commit.gpgsign=false commit -m "feat(vision): extend chat _SECTIONS list with tech_stack and runtime_target (#335)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Update orchestrator-side parser (`agent/vision.py`) (TDD)

This is the load-bearing fix for the issue's stated goal. Without it, the orchestrator silently drops the new sections.

**Files:**
- Modify: `dashboard/backend/tests/test_agent_vision.py` (find the file; if absent, create it)
- Modify: `agent/vision.py:17-25`

- [ ] **Step 1: Locate the test file**

```bash
ls /home/simon/Documents/claude-agent-station/dashboard/backend/tests/test_agent_vision.py
```

Expected: file exists. (If it doesn't, the engineer creates one with the appropriate imports.)

- [ ] **Step 2: Append the failing tests**

Append to `dashboard/backend/tests/test_agent_vision.py`:

```python
def test_load_vision_parses_new_sections(tmp_path):
    """load_vision() parses the issue-#335 sections into the dict."""
    from agent.vision import load_vision

    repo = tmp_path
    (repo / "docs").mkdir()
    (repo / "docs" / "vision.md").write_text(
        "# Vision — o/r\n\n"
        "## Problem\nP\n\n## Users\nU\n\n## End-state\nE\n\n"
        "## Tech Stack\nPython + FastAPI + Svelte\n\n"
        "## Runtime Target\nContainer on Linux\n\n"
        "## Non-goals\nN\n\n## Principles\nPr\n\n"
        "## Horizons\nH\n\n## Anti-patterns\nA\n"
    )
    vision = load_vision(str(repo))
    assert vision is not None
    assert vision["tech_stack"] == "Python + FastAPI + Svelte"
    assert vision["runtime_target"] == "Container on Linux"


def test_load_vision_old_file_defaults_new_keys_to_empty(tmp_path):
    """Pre-#335 vision files with only 7 sections still parse — the new
    keys default to empty strings in the returned dict."""
    from agent.vision import load_vision

    repo = tmp_path
    (repo / "docs").mkdir()
    (repo / "docs" / "vision.md").write_text(
        "# Vision — o/r\n\n"
        "## Problem\nP\n\n## Users\nU\n\n## End-state\nE\n\n"
        "## Non-goals\nN\n\n## Principles\nPr\n\n"
        "## Horizons\nH\n\n## Anti-patterns\nA\n"
    )
    vision = load_vision(str(repo))
    assert vision is not None
    assert "tech_stack" in vision
    assert "runtime_target" in vision
    assert vision["tech_stack"] == ""
    assert vision["runtime_target"] == ""
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest tests/test_agent_vision.py::test_load_vision_parses_new_sections \
       tests/test_agent_vision.py::test_load_vision_old_file_defaults_new_keys_to_empty -v
```

Expected: both FAIL — `KeyError: 'tech_stack'` (the parser's SECTIONS list doesn't define the key yet, so the returned dict has no `tech_stack` key).

- [ ] **Step 4: Extend `SECTIONS` in `agent/vision.py`**

In `agent/vision.py`, find:

```python
SECTIONS = [
    ("problem", "Problem"),
    ("users", "Users"),
    ("end_state", "End-state"),
    ("non_goals", "Non-goals"),
    ("principles", "Principles"),
    ("horizons", "Horizons"),
    ("anti_patterns", "Anti-patterns"),
]
```

Replace with:

```python
# Order must match dashboard/backend/app/services/vision_render.py:SECTIONS.
# Issue #335 inserted tech_stack and runtime_target between end_state and
# non_goals so the orchestrator-side parser returns them in the dict for
# vision_analyst, vision_scoring, and the lead-prompt assembly to consume.
SECTIONS = [
    ("problem", "Problem"),
    ("users", "Users"),
    ("end_state", "End-state"),
    ("tech_stack", "Tech Stack"),
    ("runtime_target", "Runtime Target"),
    ("non_goals", "Non-goals"),
    ("principles", "Principles"),
    ("horizons", "Horizons"),
    ("anti_patterns", "Anti-patterns"),
]
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest tests/test_agent_vision.py -v
```

Expected: all tests pass (including the two new ones).

- [ ] **Step 6: Commit**

```bash
cd /home/simon/Documents/claude-agent-station
git add agent/vision.py dashboard/backend/tests/test_agent_vision.py
git -c commit.gpgsign=false commit -m "feat(vision): orchestrator parser returns tech_stack and runtime_target (#335)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Update `vision_analyst.py` (template + kwargs)

`vision_analyst.py` builds a prompt for the analyst agent that scores repo state against the vision. The new sections need slots in the format-string template AND in the explicit kwargs at the `.format(...)` call.

**Files:**
- Modify: `agent/vision_analyst.py:118-142` (template)
- Modify: `agent/vision_analyst.py:214-224` (kwargs)

- [ ] **Step 1: Extend the template**

In `agent/vision_analyst.py`, find:

```python
_PROMPT = """You are a project analyst. Given a project vision and the current
state of the repository, propose {max} new GitHub issues that would help close
the gap between today's state and the vision.

# Vision
## Problem
{problem}

## Users
{users}

## End-state
{end_state}

## Non-goals
{non_goals}
```

Replace with:

```python
_PROMPT = """You are a project analyst. Given a project vision and the current
state of the repository, propose {max} new GitHub issues that would help close
the gap between today's state and the vision.

# Vision
## Problem
{problem}

## Users
{users}

## End-state
{end_state}

## Tech Stack
{tech_stack}

## Runtime Target
{runtime_target}

## Non-goals
{non_goals}
```

- [ ] **Step 2: Extend the kwargs**

In `agent/vision_analyst.py`, find:

```python
    prompt = _PROMPT.format(
        max=MAX_PROPOSALS,
        problem=vision["problem"], users=vision["users"], end_state=vision["end_state"],
        non_goals=vision["non_goals"], principles=vision["principles"],
        horizons=vision["horizons"], anti_patterns=vision["anti_patterns"],
        tree="\n".join(state["tree"][:80]),
```

Replace with:

```python
    prompt = _PROMPT.format(
        max=MAX_PROPOSALS,
        problem=vision["problem"], users=vision["users"], end_state=vision["end_state"],
        tech_stack=vision.get("tech_stack", ""),
        runtime_target=vision.get("runtime_target", ""),
        non_goals=vision["non_goals"], principles=vision["principles"],
        horizons=vision["horizons"], anti_patterns=vision["anti_patterns"],
        tree="\n".join(state["tree"][:80]),
```

Use `.get(..., "")` rather than `[...]` so the analyst still works on workspaces with old (pre-#335) vision files where the keys may not exist if the parser ran before Task 6 landed (defensive — once Task 6 ships, the keys are always present, but `.get` is cheap insurance).

- [ ] **Step 3: Verify the file parses**

```bash
python3 -c "import ast; ast.parse(open('agent/vision_analyst.py').read())"
echo OK
```

- [ ] **Step 4: Commit**

```bash
git add agent/vision_analyst.py
git -c commit.gpgsign=false commit -m "feat(vision): vision_analyst sees tech_stack and runtime_target (#335)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Update `vision_scoring.py` template

`vision_scoring.py:90` already uses `**vision` to spread all dict keys into `.format(...)`. So adding the keys to the parsed dict (Task 6) + adding the placeholders to the template is sufficient — no kwargs edit needed here.

**Files:**
- Modify: `agent/vision_scoring.py:17-39` (template)

- [ ] **Step 1: Extend the template**

In `agent/vision_scoring.py`, find:

```python
_PROMPT_TEMPLATE = """You are scoring open issues against a project vision.

# Vision
## Problem
{problem}

## Users
{users}

## End-state
{end_state}

## Non-goals
{non_goals}
```

Replace with:

```python
_PROMPT_TEMPLATE = """You are scoring open issues against a project vision.

# Vision
## Problem
{problem}

## Users
{users}

## End-state
{end_state}

## Tech Stack
{tech_stack}

## Runtime Target
{runtime_target}

## Non-goals
{non_goals}
```

- [ ] **Step 2: Verify the file parses**

```bash
python3 -c "import ast; ast.parse(open('agent/vision_scoring.py').read())"
echo OK
```

- [ ] **Step 3: Run vision-scoring tests if they exist**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest tests/test_vision_scoring.py -q
```

Expected: all pass. (If the test file mocks the model call, it should be insensitive to template content. If a test breaks because it builds a vision dict without the new keys, fix the fixture to include `tech_stack` and `runtime_target` as empty strings.)

- [ ] **Step 4: Commit**

```bash
git add agent/vision_scoring.py
git -c commit.gpgsign=false commit -m "feat(vision): vision_scoring template includes tech_stack and runtime_target (#335)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Update lead-prompt assembly in `agent/station_orchestrator.py`

The lead agent's prompt has a "Vision check" section that currently surfaces only non-goals and anti-patterns (used for plan misalignment review). Add Tech Stack and Runtime Target as informational context blocks so the lead — and the spawned teammates — see the new sections.

**Files:**
- Modify: `agent/station_orchestrator.py` around line 778

- [ ] **Step 1: Find the existing `vision_section` assembly**

```bash
grep -n "non_goals = (vision.get" agent/station_orchestrator.py
```

Expected: one match (around line 778).

- [ ] **Step 2: Add the two new context variables**

In `agent/station_orchestrator.py`, find:

```python
    if vision is not None:
        non_goals = (vision.get("non_goals") or "").strip() or "_(not specified)_"
        anti_patterns = (vision.get("anti_patterns") or "").strip() or "_(not specified)_"
```

Replace with:

```python
    if vision is not None:
        non_goals = (vision.get("non_goals") or "").strip() or "_(not specified)_"
        anti_patterns = (vision.get("anti_patterns") or "").strip() or "_(not specified)_"
        # Issue #335: surface tech_stack and runtime_target as informational
        # context so the lead — and the teammates it spawns — pick the right
        # frameworks, base images, and runtime patterns.
        tech_stack_text = (vision.get("tech_stack") or "").strip() or "_(not specified)_"
        runtime_target_text = (vision.get("runtime_target") or "").strip() or "_(not specified)_"
```

- [ ] **Step 3: Find the end of the existing `vision_section` block**

```bash
grep -n "### Vision — Anti-patterns" agent/station_orchestrator.py
```

Expected: one match. Read the block from there to the closing triple-quote of `vision_section`.

- [ ] **Step 4: Append the new context blocks to `vision_section`**

In `agent/station_orchestrator.py`, find:

```python
### Vision — Non-goals
{non_goals}

### Vision — Anti-patterns
{anti_patterns}
```

Replace with:

```python
### Vision — Non-goals
{non_goals}

### Vision — Anti-patterns
{anti_patterns}

### Vision — Tech Stack
{tech_stack_text}

### Vision — Runtime Target
{runtime_target_text}
```

The f-string interpolation picks up `tech_stack_text` and `runtime_target_text` from the variables set in Step 2 of this task.

- [ ] **Step 5: Verify the file parses**

```bash
python3 -c "import ast; ast.parse(open('agent/station_orchestrator.py').read())"
echo OK
```

- [ ] **Step 6: Run any orchestrator tests that build the vision_section**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest tests/test_orchestrator_wiring.py -q 2>&1 | tail -10
```

Expected: the test_frontend_dropdown_values_match_backend_modes failure is pre-existing (unrelated to vision); everything else passes. If a new failure appears in the vision-section path, it's caused by this task — investigate.

- [ ] **Step 7: Commit**

```bash
cd /home/simon/Documents/claude-agent-station
git add agent/station_orchestrator.py
git -c commit.gpgsign=false commit -m "feat(vision): expose tech_stack and runtime_target to the lead prompt (#335)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Update `agent/prompts/vision_create.md`

Three text edits in the create prompt: the section list, the prose count, the valid-names list, and the `vision-doc` example block.

**Files:**
- Modify: `agent/prompts/vision_create.md`

- [ ] **Step 1: Replace the seven-section list**

In `agent/prompts/vision_create.md`, find:

```markdown
The seven sections, in order:

- **Problem** — what pain this tool solves
- **Users** — who it's for and who it's not for
- **End-state** — what "done" / "succeeded" looks like, concretely
- **Non-goals** — things deliberately out of scope
- **Principles** — how to choose when two good options conflict
- **Horizons** — near-term (3 mo), mid-term (12 mo), long-term direction
- **Anti-patterns** — concrete examples of *bad* outcomes
```

Replace with:

```markdown
The nine sections, in order:

- **Problem** — what pain this tool solves
- **Users** — who it's for and who it's not for
- **End-state** — what "done" / "succeeded" looks like, concretely
- **Tech Stack** — the languages, frameworks, and key libraries
- **Runtime Target** — where the application is intended to run (Linux host, container, serverless, edge, embedded)
- **Non-goals** — things deliberately out of scope
- **Principles** — how to choose when two good options conflict
- **Horizons** — near-term (3 mo), mid-term (12 mo), long-term direction
- **Anti-patterns** — concrete examples of *bad* outcomes
```

- [ ] **Step 2: Update the valid-names enumeration**

In the same file, find:

```markdown
The valid section names are: `problem`, `users`, `end_state`, `non_goals`, `principles`, `horizons`, `anti_patterns`.
```

Replace with:

```markdown
The valid section names are: `problem`, `users`, `end_state`, `tech_stack`, `runtime_target`, `non_goals`, `principles`, `horizons`, `anti_patterns`.
```

- [ ] **Step 3: Update the `vision-doc` example block**

In the same file, find:

````markdown
{ "problem": "...", "users": "...", "end_state": "...", "non_goals": "...",
  "principles": "...", "horizons": "...", "anti_patterns": "..." }
````

Replace with:

````markdown
{ "problem": "...", "users": "...", "end_state": "...",
  "tech_stack": "...", "runtime_target": "...",
  "non_goals": "...", "principles": "...", "horizons": "...",
  "anti_patterns": "..." }
````

- [ ] **Step 4: Check for remaining "seven" prose references**

```bash
grep -n "seven" agent/prompts/vision_create.md
```

Expected: no matches. (If any remain, update to "nine".)

- [ ] **Step 5: Verify prompt-contract tests still pass**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest tests/test_prompt_contracts.py -q
```

Expected: all pass — `_NON_ROLE_PROMPTS` already excludes the vision-chat prompts so the role-count test is unaffected.

- [ ] **Step 6: Commit**

```bash
cd /home/simon/Documents/claude-agent-station
git add agent/prompts/vision_create.md
git -c commit.gpgsign=false commit -m "docs(prompt): vision_create now interviews 9 sections (#335)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Update `agent/prompts/vision_refine.md`

The refine prompt enumerates the section names so the model can route "I want to update X" requests.

**Files:**
- Modify: `agent/prompts/vision_refine.md`

- [ ] **Step 1: Locate the section list**

```bash
grep -n "seven sections\|problem, users, end_state\|tech_stack" agent/prompts/vision_refine.md
```

Expected: at least one match around line 11.

- [ ] **Step 2: Update the section enumeration**

In `agent/prompts/vision_refine.md`, find:

```markdown
The seven sections are: problem, users, end_state, non_goals, principles, horizons, anti_patterns.
```

Replace with:

```markdown
The nine sections are: problem, users, end_state, tech_stack, runtime_target, non_goals, principles, horizons, anti_patterns.
```

- [ ] **Step 3: Check for any other "seven" references**

```bash
grep -n "seven" agent/prompts/vision_refine.md
```

Expected: no remaining matches.

- [ ] **Step 4: Commit**

```bash
git add agent/prompts/vision_refine.md
git -c commit.gpgsign=false commit -m "docs(prompt): vision_refine knows 9 sections (#335)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Run full backend test suite + push + open PR

**Files:** none for verification; PR opened at the end.

- [ ] **Step 1: Run the full backend test suite**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest -q 2>&1 | tail -10
```

Expected: all passing except the pre-existing `test_frontend_dropdown_values_match_backend_modes` failure (unrelated to this PR — confirm by checking it fails on `dev` too if uncertain).

- [ ] **Step 2: Type-check Python (parse-check is sufficient for this PR's scope)**

```bash
cd /home/simon/Documents/claude-agent-station
python3 -c "
import ast
for f in [
    'dashboard/backend/app/schemas.py',
    'dashboard/backend/app/services/vision_render.py',
    'dashboard/backend/app/services/vision_chat.py',
    'agent/vision.py',
    'agent/vision_analyst.py',
    'agent/vision_scoring.py',
    'agent/station_orchestrator.py',
]:
    ast.parse(open(f).read())
print('all python parses OK')
"
```

Expected: `all python parses OK`.

- [ ] **Step 3: Push the branch**

```bash
git push -u origin feature/vision-tech-stack-runtime
```

- [ ] **Step 4: Open the PR against `dev`**

```bash
gh pr create --base dev --title "feat(vision): collect Tech Stack and Runtime Target in Vision wizard (#335)" --body "$(cat <<'EOF'
## Summary
Extends the Project Vision schema from 7 to 9 H2 sections by inserting \`## Tech Stack\` and \`## Runtime Target\` at positions 4 and 5 (between End-state and Non-goals). Both are free-text markdown like the existing seven. The Vision chat wizard now collects them; the dashboard renders them; the orchestrator parses them; vision_analyst, vision_scoring, and the lead's prompt assembly all see them.

Old visions continue to work without changes. Backfill is user-driven — open Vision chat, ask Claude to add tech stack / runtime target, refine.

## Spec & plan
- Spec: \`docs/superpowers/specs/2026-05-10-vision-tech-stack-runtime-target-design.md\`
- Plan: \`docs/superpowers/plans/2026-05-10-vision-tech-stack-runtime-target.md\`

Closes #335

## Test plan
- [x] \`VisionDoc\` accepts payloads missing the new fields (back-compat with pre-#335 chat output)
- [x] Renderer emits all 9 H2 headings in the right order; empty new sections render with the existing \`_(not specified)_\` placeholder rule
- [x] \`_REQUIRED_DOC_KEYS\` pinned to original 7 strings (regression guard)
- [x] \`agent.vision.load_vision()\` returns the new keys; old 7-section files default the new keys to empty strings
- [x] Python parse-check clean on all modified files
- [ ] Manual: open Vision chat on a project with no vision; complete the interview; confirm \`docs/vision.md\` ends up with 9 sections in the right order
- [ ] Manual: open Vision chat on a project with an existing 7-section vision; ask "add tech stack and runtime target"; confirm refine flow lands the two new sections
- [ ] Manual: trigger a run with a 9-section vision; confirm the lead's prompt log shows the Tech Stack and Runtime Target context blocks

## Out of scope
- Auto-detection of tech stack from \`package.json\` / \`pyproject.toml\` / \`Cargo.toml\` / \`go.mod\` (issue body punts on this).
- Canonical vocabulary or chip-style UI. Free-text prose only.
- Migration of existing \`docs/vision.md\` files. User-driven backfill.

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
| Storage (9 sections, positions 4 and 5) | 3 (render), 6 (orchestrator parser), 10 (vision_create.md) |
| Data model (`VisionDoc` optional fields) | 2 |
| Render layer (placeholder rule preserved) | 3 |
| Parser & section lists in the dashboard (`_REQUIRED_DOC_KEYS`, `_SECTIONS`) | 4 (pinning test), 5 (chat lifecycle list) |
| Orchestrator-side parser (`agent/vision.py`) | 6 |
| Agent prompts that consume parsed dict (vision_analyst, vision_scoring, station_orchestrator) | 7, 8, 9 |
| Chat prompts (vision_create.md, vision_refine.md) | 10, 11 |
| Frontend (no change) | not a task — explicitly no-change in spec |
| Backward compatibility | covered transitively by tests in 2, 3, 4, 6 |
| Testing | 2, 3, 4, 6 |

**Gaps:** none — every spec section has at least one task.

**Placeholder scan:**

- No "TBD" / "TODO" / "similar to Task N" / "fill in details" / generic "add validation" patterns. All code edits are shown verbatim. All tests are spelled out.
- One soft spot: Task 6 step 1 says "if the file doesn't exist, the engineer creates one with the appropriate imports." That's a hedge but acceptable — the implementer pattern-matches against the existing test files in the same directory. Could tighten by inspecting now, but the existing `test_agent_vision.py` is visible in the file listing I confirmed during plan-writing, so this branch shouldn't fire.

**Type consistency:**

- Section IDs are uniformly `tech_stack` (snake_case) and `runtime_target` everywhere they appear: schema field, render `SECTIONS` tuple, chat `_SECTIONS` list, orchestrator parser `SECTIONS`, vision_analyst template + kwargs, vision_scoring template (via `**vision`), station_orchestrator local variables, both markdown prompts, all tests.
- Display labels are uniformly `Tech Stack` and `Runtime Target` (proper case, space) in: render `SECTIONS` heading tuple, the literal `## Tech Stack` / `## Runtime Target` markdown headings the tests assert on, vision_analyst + vision_scoring template heading lines, station_orchestrator `### Vision — Tech Stack` / `### Vision — Runtime Target`, the vision_create and vision_refine prompt section descriptions.
- Field-list order is uniformly: problem, users, end_state, tech_stack, runtime_target, non_goals, principles, horizons, anti_patterns — verified in tasks 2, 3, 5, 6, 7, 8, 10, 11.
- `tech_stack_text` / `runtime_target_text` local-variable names in Task 9 step 2 match the f-string placeholders used in step 4.

No remaining issues.
