# Project Vision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a co-created project vision (markdown file at `docs/vision.md` in each project repo, authored via chat with Claude) that drives three orchestrator behaviours: vision-aligned issue prioritisation, plan-time misalignment flagging, and on-demand gap detection.

**Architecture:** Source of truth is `docs/vision.md` on the project's repo. Dashboard uses GitHub Contents API for read/write, with a small DB cache. Chat uses `claude_agent_sdk.query` (bundled native CLI). Three orchestrator hooks read the vision from the local workspace clone and degrade to no-op when absent.

**Tech Stack:** FastAPI · SQLAlchemy (Column-style, async) · `claude_agent_sdk` · httpx (GitHub Contents API) · Svelte 5 (runes) · vitest · pytest · in-app additive migrations (no Alembic)

**Spec:** [`docs/superpowers/specs/2026-05-07-project-vision-design.md`](../specs/2026-05-07-project-vision-design.md)

**Phase ordering:**
1. **Phase 1** — Vision authoring foundation (UI + chat + GitHub commit). No agent behaviour change yet.
2. **Phase 2** — Hook 1: vision-aware issue prioritisation in the orchestrator.
3. **Phase 3** — Hook 2: plan-time misalignment flag in the lead's prompt.
4. **Phase 4** — Hook 3: gap-detection vision-analyst agent (manual trigger via Vision tab).

Each phase ends with all changes committed and a `[OK] Phase N done` checkpoint.

**Launcher dispatch decision (Phase 4 prerequisite, locked in this plan):** the existing launcher (`agent/launcher.py`) gains a **second, dedicated endpoint** `POST /vision-analyst` with its own `_current_analyst` slot. This keeps gap-detection independent from run-manager dispatch (option (b) from the spec).

---

## File map

**Backend — new files**

| Path | Responsibility |
|---|---|
| `dashboard/backend/app/services/github_contents.py` | Read/write `docs/vision.md` via GitHub Contents API; optimistic concurrency on sha. |
| `dashboard/backend/app/services/vision_chat.py` | Chat session state machine; SDK query wrapper; vision-meta/vision-doc parsing. |
| `dashboard/backend/app/services/vision_render.py` | JSON `vision_doc` → markdown template. |
| `dashboard/backend/app/services/vision_cleanup.py` | Periodic prune of stale `active` (24h) and old `approved`/`cancelled` (30d) sessions. |
| `dashboard/backend/app/routers/vision.py` | All vision endpoints (chat + commit). |
| `dashboard/backend/tests/test_vision_render.py` | Unit tests for the markdown renderer. |
| `dashboard/backend/tests/test_vision_chat_parser.py` | Unit tests for `vision-meta` and `vision-doc` extraction. |
| `dashboard/backend/tests/test_vision_chat_service.py` | Unit tests for the session state machine. |
| `dashboard/backend/tests/test_github_contents.py` | VCR-style tests for the GitHub Contents wrapper. |
| `dashboard/backend/tests/test_vision_router.py` | Endpoint tests, including the 409 stale-sha path and SSE basics. |
| `dashboard/backend/tests/test_vision_cleanup.py` | Tests for the prune loop. |

**Backend — modified files**

| Path | Change |
|---|---|
| `dashboard/backend/app/models.py` | Add `vision_cached_*` columns to `Project`; add `VisionChatSession` table model. |
| `dashboard/backend/app/database.py` | Add migration entries for the three new columns; add `VisionChatSession` to the `init_db()` import list. |
| `dashboard/backend/app/main.py` | Register `vision.router`; start the cleanup loop on app startup. |
| `dashboard/backend/app/schemas.py` | Pydantic schemas for `VisionDoc`, `VisionRead`, `VisionCommitIn`, `VisionChatSessionOut`. |
| `agent/prompts/vision_create.md` | System prompt for the create flow (NEW file, lives under `agent/prompts/`). |
| `agent/prompts/vision_refine.md` | System prompt for the refine flow (NEW file). |

**Frontend — new files**

| Path | Responsibility |
|---|---|
| `dashboard/frontend/src/components/vision/VisionChat.svelte` | Reusable chat panel (SSE stream, transcript, coverage indicator, approve/cancel). |
| `dashboard/frontend/src/components/vision/VisionTab.svelte` | Project-detail tab; switches between empty / read / refining states. |
| `dashboard/frontend/src/components/vision/CoverageChecklist.svelte` | Seven-pill coverage indicator. |
| `dashboard/frontend/src/lib/vision-sse.ts` | Browser SSE wrapper that yields typed events for `VisionChat`. |
| `dashboard/frontend/src/components/vision/VisionChat.test.ts` | vitest spec for SSE handling, disconnect, reconnect, vision_ready → approve flow. |

**Frontend — modified files**

| Path | Change |
|---|---|
| `dashboard/frontend/src/lib/types.ts` | Add `VisionDoc`, `VisionRead`, `VisionChatSession`, SSE event types. |
| `dashboard/frontend/src/lib/api.ts` | Add `getVision`, `commitVision`, `openVisionChat`, `getVisionChatSession`, `cancelVisionChat`, `findVisionGaps` (Phase 4). |
| `dashboard/frontend/src/pages/ProjectsPage.svelte` | `Modal show=showCreateModal` becomes a 2-step wizard with embedded `VisionChat`. |
| `dashboard/frontend/src/pages/ProjectDetail.svelte` | Add tab strip (Overview, Vision, Runs); render `VisionTab` when active. |

**Agent — Phase 2/3/4 additions**

| Path | Phase | Responsibility |
|---|---|---|
| `agent/vision.py` | 2 | `load_vision(workspace_dir) -> dict | None` — reads + parses `<workspace>/docs/vision.md`. |
| `agent/vision_scoring.py` | 2 | `score_issues_against_vision(issues, vision, model)` — Hook 1. |
| `agent/vision_analyst.py` | 4 | Standalone CLI: gap detection. |
| `agent/station_orchestrator.py` | 2, 3 | Wire scoring into `orchestrate()`; inject vision-check section into `build_team_prompt()`. |
| `agent/launcher.py` | 4 | Add `POST /vision-analyst` endpoint with separate `_current_analyst` slot. |
| `dashboard/backend/app/services/service_control.py` | 4 | New `start_vision_analyst(project_id)` that branches on deploy mode. |
| `dashboard/backend/app/routers/webhook.py` | 3 | Handle `vision_misalignment` event type, persist to `agent_events`. |

---

# Phase 1 — Vision authoring foundation

The whole of Phase 1 is shippable on its own: users can create + refine a vision; it lands at `docs/vision.md` on GitHub. No agent behaviour changes.

## Task 1.1: Schema — add `vision_cached_*` columns to `Project`

**Files:**
- Modify: `dashboard/backend/app/models.py:17-32` (Project class)
- Modify: `dashboard/backend/app/database.py:38-86` (migration list)
- Test: `dashboard/backend/tests/test_database_migrations.py` (existing file or create if missing)

- [ ] **Step 1: Write the failing test**

```python
# dashboard/backend/tests/test_database_migrations.py
import pytest
from sqlalchemy import text
from app.database import engine, init_db

@pytest.mark.asyncio
async def test_vision_cache_columns_exist():
    await init_db()
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(projects)"))
        columns = {row[1] for row in result.fetchall()}
    assert "vision_cached_sha" in columns
    assert "vision_cached_body" in columns
    assert "vision_cached_at" in columns
```

- [ ] **Step 2: Run the test and confirm it fails**

```bash
cd dashboard/backend && pytest tests/test_database_migrations.py::test_vision_cache_columns_exist -v
```

Expected: FAIL — columns missing.

- [ ] **Step 3: Add migration entries**

Append to the `migrations` list in `dashboard/backend/app/database.py:_migrate_add_columns` (just before the closing `]`):

```python
        # Project vision cache (Phase 1 — vision authoring)
        ("projects", "vision_cached_sha",  "ALTER TABLE projects ADD COLUMN vision_cached_sha TEXT"),
        ("projects", "vision_cached_body", "ALTER TABLE projects ADD COLUMN vision_cached_body TEXT"),
        ("projects", "vision_cached_at",   "ALTER TABLE projects ADD COLUMN vision_cached_at DATETIME"),
```

- [ ] **Step 4: Add columns to the model**

In `dashboard/backend/app/models.py`, inside `class Project(Base)`, add after `max_budget_usd`:

```python
    # Vision cache (Phase 1 — see docs/superpowers/specs/2026-05-07-project-vision-design.md)
    vision_cached_sha = Column(Text, nullable=True, default=None)
    vision_cached_body = Column(Text, nullable=True, default=None)
    vision_cached_at = Column(DateTime, nullable=True, default=None)
```

- [ ] **Step 5: Run the test and confirm it passes**

```bash
pytest tests/test_database_migrations.py::test_vision_cache_columns_exist -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/backend/app/models.py dashboard/backend/app/database.py dashboard/backend/tests/test_database_migrations.py
git commit -m "feat(vision): add vision cache columns to Project"
```

---

## Task 1.2: Schema — add `VisionChatSession` table

**Files:**
- Modify: `dashboard/backend/app/models.py` (add new class)
- Modify: `dashboard/backend/app/database.py:101-118` (init_db imports)
- Test: `dashboard/backend/tests/test_database_migrations.py`

- [ ] **Step 1: Write the failing test**

Append to `test_database_migrations.py`:

```python
@pytest.mark.asyncio
async def test_vision_chat_sessions_table_exists():
    await init_db()
    async with engine.begin() as conn:
        result = await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='vision_chat_sessions'"
        ))
        rows = result.fetchall()
    assert len(rows) == 1, "vision_chat_sessions table missing"

@pytest.mark.asyncio
async def test_vision_chat_session_has_required_columns():
    await init_db()
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(vision_chat_sessions)"))
        columns = {row[1] for row in result.fetchall()}
    for col in ("id", "project_id", "state", "phase", "coverage",
                "sdk_session_id", "messages", "assembled",
                "created_at", "updated_at"):
        assert col in columns, f"missing column: {col}"
```

- [ ] **Step 2: Run the tests and confirm they fail**

```bash
pytest tests/test_database_migrations.py -v
```

Expected: both new tests fail.

- [ ] **Step 3: Add the model class**

Append to `dashboard/backend/app/models.py`:

```python
class VisionChatSession(Base):
    """In-flight chat session for collaborative vision authoring.

    "One active per project" is enforced in the application layer (SQLite
    can't do partial unique indexes); historical 'approved' and 'cancelled'
    rows coexist freely. See spec 2026-05-07-project-vision-design.md.
    """
    __tablename__ = "vision_chat_sessions"

    id = Column(Text, primary_key=True)  # UUID
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    state = Column(Text, nullable=False, default="active")  # active|approved|cancelled
    phase = Column(Text, nullable=False, default="freeform")  # freeform|structured
    coverage = Column(Text, nullable=False, default="{}")  # JSON
    sdk_session_id = Column(Text, nullable=True, default=None)
    messages = Column(Text, nullable=False, default="[]")  # JSON list
    assembled = Column(Text, nullable=True, default=None)  # JSON
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
```

(Note: `coverage`, `messages`, `assembled` are TEXT columns holding JSON. Async SQLAlchemy on SQLite is happiest with manual `json.loads/dumps` at the service layer rather than the JSON column type.)

- [ ] **Step 4: Register in init_db**

In `dashboard/backend/app/database.py:init_db`, add `VisionChatSession` to the import block:

```python
        from app.models import (  # noqa: F401
            AgentEvent,
            BrainstormMessage,
            BrainstormSession,
            ConfigEntry,
            CoordinatorMessage,
            CoordinatorTask,
            IntegrationFeature,
            Notification,
            PermissionRequest,
            Plan,
            PlanUsageHistory,
            PromptVersion,
            Project,
            QueueItem,
            Run,
            TaskOutcome,
            VisionChatSession,  # ← add
        )
```

- [ ] **Step 5: Run the tests and confirm they pass**

```bash
pytest tests/test_database_migrations.py -v
```

Expected: both new tests PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/backend/app/models.py dashboard/backend/app/database.py dashboard/backend/tests/test_database_migrations.py
git commit -m "feat(vision): add vision_chat_sessions table"
```

---

## Task 1.3: GitHub Contents service — read

**Files:**
- Create: `dashboard/backend/app/services/github_contents.py`
- Test: `dashboard/backend/tests/test_github_contents.py`

- [ ] **Step 1: Write the failing test**

```python
# dashboard/backend/tests/test_github_contents.py
import base64
import pytest
import httpx
from unittest.mock import patch
from app.services.github_contents import read_file, ContentsResult, FileNotFound


@pytest.mark.asyncio
async def test_read_file_returns_decoded_body_and_sha():
    fake = {
        "sha": "abc123",
        "content": base64.b64encode(b"# hello\n").decode(),
        "encoding": "base64",
        "html_url": "https://github.com/o/r/blob/main/docs/vision.md",
    }
    with patch("app.services.github_contents._get_token", return_value="ghi"):
        async def fake_get(self, url, headers=None, params=None):
            return httpx.Response(200, json=fake, request=httpx.Request("GET", url))
        with patch.object(httpx.AsyncClient, "get", new=fake_get):
            result = await read_file(repo="o/r", path="docs/vision.md", branch="main")
    assert isinstance(result, ContentsResult)
    assert result.sha == "abc123"
    assert result.body == "# hello\n"
    assert result.html_url.endswith("/docs/vision.md")


@pytest.mark.asyncio
async def test_read_file_404_raises_FileNotFound():
    with patch("app.services.github_contents._get_token", return_value="ghi"):
        async def fake_get(self, url, headers=None, params=None):
            return httpx.Response(404, json={"message": "Not Found"}, request=httpx.Request("GET", url))
        with patch.object(httpx.AsyncClient, "get", new=fake_get):
            with pytest.raises(FileNotFound):
                await read_file(repo="o/r", path="docs/vision.md", branch="main")
```

- [ ] **Step 2: Run the tests and confirm they fail**

```bash
pytest tests/test_github_contents.py -v
```

Expected: FAIL with ImportError (module doesn't exist).

- [ ] **Step 3: Create the service module**

```python
# dashboard/backend/app/services/github_contents.py
"""GitHub Contents API wrapper for vision document storage.

The dashboard reads/writes docs/vision.md via this module using the App
installation token already wired up in app.services.github_app.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

import httpx
from fastapi import HTTPException

from app.services.github_app import get_installation_token

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
COMMIT_AUTHOR = {"name": "Claude Station", "email": "claude-station@noreply.local"}


class FileNotFound(Exception):
    """Raised when the file does not exist on the requested ref."""


class StaleSha(Exception):
    """Raised on PUT when the supplied sha doesn't match GitHub's current sha."""

    def __init__(self, current_sha: str, current_body: str):
        self.current_sha = current_sha
        self.current_body = current_body
        super().__init__(f"stale sha; current is {current_sha}")


@dataclass
class ContentsResult:
    sha: str
    body: str
    html_url: str


async def _get_token() -> str:
    token = await get_installation_token()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="GitHub App not installed; cannot access repo contents.",
        )
    return token


async def read_file(repo: str, path: str, branch: str) -> ContentsResult:
    """Fetch a file's content + sha from a branch.

    Raises FileNotFound on 404, HTTPException on other errors.
    """
    token = await _get_token()
    url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    params = {"ref": branch}

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=headers, params=params)

    if resp.status_code == 404:
        raise FileNotFound(f"{repo}:{branch}:{path}")
    if resp.status_code >= 400:
        logger.warning("GitHub Contents read failed: %s %s", resp.status_code, resp.text[:200])
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    payload = resp.json()
    if payload.get("encoding") != "base64":
        raise HTTPException(status_code=500, detail="unexpected encoding from GitHub")
    body = base64.b64decode(payload["content"]).decode("utf-8", errors="replace")
    return ContentsResult(sha=payload["sha"], body=body, html_url=payload.get("html_url", ""))
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
pytest tests/test_github_contents.py -v
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/services/github_contents.py dashboard/backend/tests/test_github_contents.py
git commit -m "feat(vision): GitHub Contents read wrapper"
```

---

## Task 1.4: GitHub Contents service — write with optimistic concurrency

**Files:**
- Modify: `dashboard/backend/app/services/github_contents.py`
- Modify: `dashboard/backend/tests/test_github_contents.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_github_contents.py`:

```python
import json

@pytest.mark.asyncio
async def test_write_file_creates_new_when_no_sha():
    captured = {}
    async def fake_put(self, url, headers=None, json=None):
        captured["url"] = url
        captured["body"] = json
        return httpx.Response(
            201,
            json={"content": {"sha": "new-sha"}, "commit": {"sha": "commit-sha"}},
            request=httpx.Request("PUT", url),
        )
    with patch("app.services.github_contents._get_token", return_value="ghi"):
        with patch.object(httpx.AsyncClient, "put", new=fake_put):
            from app.services.github_contents import write_file
            new_sha = await write_file(
                repo="o/r", path="docs/vision.md", branch="main",
                body="# hello\n", message="docs: test", current_sha=None,
            )
    assert new_sha == "new-sha"
    assert "sha" not in captured["body"]  # no sha sent on create


@pytest.mark.asyncio
async def test_write_file_updates_when_sha_matches():
    async def fake_put(self, url, headers=None, json=None):
        # Echo back a new sha to indicate success
        return httpx.Response(200, json={"content": {"sha": "updated-sha"}}, request=httpx.Request("PUT", url))
    with patch("app.services.github_contents._get_token", return_value="ghi"):
        with patch.object(httpx.AsyncClient, "put", new=fake_put):
            from app.services.github_contents import write_file
            new_sha = await write_file(
                repo="o/r", path="docs/vision.md", branch="main",
                body="# new\n", message="docs: refine", current_sha="old-sha",
            )
    assert new_sha == "updated-sha"


@pytest.mark.asyncio
async def test_write_file_409_on_stale_sha_raises_StaleSha():
    """When GitHub returns 409 on PUT, we re-fetch and raise StaleSha with current state."""
    async def fake_put(self, url, headers=None, json=None):
        return httpx.Response(409, json={"message": "stale sha"}, request=httpx.Request("PUT", url))
    fake_current = {
        "sha": "newer-sha",
        "content": base64.b64encode(b"someone else wrote this").decode(),
        "encoding": "base64",
        "html_url": "x",
    }
    async def fake_get(self, url, headers=None, params=None):
        return httpx.Response(200, json=fake_current, request=httpx.Request("GET", url))
    with patch("app.services.github_contents._get_token", return_value="ghi"):
        with patch.object(httpx.AsyncClient, "put", new=fake_put), \
             patch.object(httpx.AsyncClient, "get", new=fake_get):
            from app.services.github_contents import write_file, StaleSha
            with pytest.raises(StaleSha) as exc:
                await write_file(
                    repo="o/r", path="docs/vision.md", branch="main",
                    body="# mine\n", message="docs: refine", current_sha="my-old-sha",
                )
    assert exc.value.current_sha == "newer-sha"
    assert exc.value.current_body == "someone else wrote this"
```

- [ ] **Step 2: Run the tests and confirm they fail**

```bash
pytest tests/test_github_contents.py -v -k write_file
```

Expected: ImportError on `write_file`.

- [ ] **Step 3: Implement `write_file`**

Append to `dashboard/backend/app/services/github_contents.py`:

```python
async def write_file(
    repo: str,
    path: str,
    branch: str,
    body: str,
    message: str,
    current_sha: str | None,
) -> str:
    """PUT a file to a branch.

    Pass current_sha=None for first-create. Pass the previously-fetched
    sha to update; on conflict, re-fetches the live state and raises
    StaleSha so callers can surface a 409 envelope.

    Returns the new blob sha on success.
    """
    token = await _get_token()
    url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {
        "message": message,
        "content": base64.b64encode(body.encode("utf-8")).decode("ascii"),
        "branch": branch,
        "committer": COMMIT_AUTHOR,
    }
    if current_sha is not None:
        payload["sha"] = current_sha

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.put(url, headers=headers, json=payload)

    if resp.status_code in (200, 201):
        return resp.json()["content"]["sha"]

    if resp.status_code == 409:
        # Re-fetch live state so the caller can surface a useful 409 envelope.
        try:
            current = await read_file(repo=repo, path=path, branch=branch)
        except FileNotFound:
            raise HTTPException(
                status_code=409,
                detail="Concurrent edit and file no longer exists; please retry.",
            )
        raise StaleSha(current_sha=current.sha, current_body=current.body)

    logger.warning("GitHub Contents write failed: %s %s", resp.status_code, resp.text[:200])
    raise HTTPException(status_code=resp.status_code, detail=resp.text)
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
pytest tests/test_github_contents.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/services/github_contents.py dashboard/backend/tests/test_github_contents.py
git commit -m "feat(vision): GitHub Contents write with optimistic concurrency"
```

---

## Task 1.5: Vision render — JSON → markdown

**Files:**
- Create: `dashboard/backend/app/services/vision_render.py`
- Test: `dashboard/backend/tests/test_vision_render.py`

- [ ] **Step 1: Write the failing test**

```python
# dashboard/backend/tests/test_vision_render.py
from datetime import datetime, timezone
from app.services.vision_render import render_vision_doc

def test_render_includes_all_seven_sections_in_order():
    doc = {
        "problem": "P", "users": "U", "end_state": "E",
        "non_goals": "N", "principles": "Pr",
        "horizons": "H", "anti_patterns": "A",
    }
    md = render_vision_doc(doc, repo="o/r", refined_at=datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc))
    # Check order
    headers = [line for line in md.splitlines() if line.startswith("## ")]
    assert headers == [
        "## Problem", "## Users", "## End-state", "## Non-goals",
        "## Principles", "## Horizons", "## Anti-patterns",
    ]
    # Check content
    assert "P" in md and "U" in md and "Pr" in md
    # Check metadata line
    assert md.startswith("# Vision — o/r\n")
    assert "*Last refined: 2026-05-07T12:00:00+00:00 via Claude Station*" in md


def test_render_handles_empty_section_with_placeholder():
    doc = {"problem": "P", "users": "", "end_state": "E", "non_goals": "",
           "principles": "", "horizons": "", "anti_patterns": ""}
    md = render_vision_doc(doc, repo="o/r", refined_at=datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc))
    assert "_(not specified)_" in md  # placeholder for empty sections
```

- [ ] **Step 2: Run the tests and confirm they fail**

```bash
pytest tests/test_vision_render.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement the renderer**

```python
# dashboard/backend/app/services/vision_render.py
"""Render a structured vision document to markdown using a fixed template."""

from __future__ import annotations

from datetime import datetime

SECTIONS = [
    ("problem", "Problem"),
    ("users", "Users"),
    ("end_state", "End-state"),
    ("non_goals", "Non-goals"),
    ("principles", "Principles"),
    ("horizons", "Horizons"),
    ("anti_patterns", "Anti-patterns"),
]


def render_vision_doc(doc: dict, repo: str, refined_at: datetime) -> str:
    """Render a vision_doc dict to the canonical markdown template.

    Empty/missing sections become a `_(not specified)_` placeholder so the
    file always has all seven H2 headings — orchestrator hooks rely on a
    consistent shape.
    """
    parts = [f"# Vision — {repo}", ""]
    parts.append(f"*Last refined: {refined_at.isoformat()} via Claude Station*")
    parts.append("")
    for key, heading in SECTIONS:
        parts.append(f"## {heading}")
        parts.append("")
        body = (doc.get(key) or "").strip()
        parts.append(body if body else "_(not specified)_")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
pytest tests/test_vision_render.py -v
```

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/services/vision_render.py dashboard/backend/tests/test_vision_render.py
git commit -m "feat(vision): markdown renderer with 7-section template"
```

---

## Task 1.6: Chat parser — extract `vision-meta` and `vision-doc` fenced blocks

**Files:**
- Create: `dashboard/backend/app/services/vision_chat_parser.py`
- Test: `dashboard/backend/tests/test_vision_chat_parser.py`

- [ ] **Step 1: Write the failing tests**

```python
# dashboard/backend/tests/test_vision_chat_parser.py
from app.services.vision_chat_parser import (
    extract_vision_meta, extract_vision_doc, strip_fenced_blocks,
)


def test_extract_vision_meta_finds_block():
    text = (
        "Hi! Tell me about your project.\n\n"
        "```vision-meta\n"
        '{"phase": "freeform", "covered": ["problem"], "ready_to_assemble": false}\n'
        "```\n"
    )
    meta = extract_vision_meta(text)
    assert meta == {"phase": "freeform", "covered": ["problem"], "ready_to_assemble": False}


def test_extract_vision_meta_returns_none_when_missing():
    assert extract_vision_meta("just prose, no fence") is None


def test_extract_vision_meta_returns_none_on_malformed_json():
    bad = "```vision-meta\n{not json}\n```"
    assert extract_vision_meta(bad) is None


def test_extract_vision_doc_finds_block():
    text = (
        "Here is the assembled vision:\n"
        "```vision-doc\n"
        '{"problem": "P", "users": "U", "end_state": "E",\n'
        ' "non_goals": "N", "principles": "Pr",\n'
        ' "horizons": "H", "anti_patterns": "A"}\n'
        "```\n"
    )
    doc = extract_vision_doc(text)
    assert doc["problem"] == "P"
    assert set(doc.keys()) == {"problem", "users", "end_state", "non_goals",
                               "principles", "horizons", "anti_patterns"}


def test_extract_vision_doc_rejects_missing_required_keys():
    bad = '```vision-doc\n{"problem": "P"}\n```'
    assert extract_vision_doc(bad) is None


def test_strip_fenced_blocks_removes_meta_and_doc_fences():
    text = (
        "Hi.\n\n"
        "```vision-meta\n{}\n```\n\n"
        "More prose.\n"
        "```vision-doc\n{}\n```\n"
    )
    out = strip_fenced_blocks(text)
    assert "vision-meta" not in out
    assert "vision-doc" not in out
    assert "Hi." in out
    assert "More prose." in out
```

- [ ] **Step 2: Run the tests and confirm they fail**

```bash
pytest tests/test_vision_chat_parser.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement the parser**

```python
# dashboard/backend/app/services/vision_chat_parser.py
"""Extract `vision-meta` and `vision-doc` fenced JSON blocks from model output.

The chat backend strips these blocks from text before forwarding `assistant_text`
SSE events to the client, so the user sees clean prose; the parsed metadata
drives `coverage_update`/`phase_change` events and the final assembly.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_META_RE = re.compile(r"```vision-meta\s*\n(.*?)\n```", re.DOTALL)
_DOC_RE = re.compile(r"```vision-doc\s*\n(.*?)\n```", re.DOTALL)
_FENCE_RE = re.compile(r"```vision-(meta|doc)\s*\n.*?\n```\n?", re.DOTALL)

_REQUIRED_DOC_KEYS = {
    "problem", "users", "end_state", "non_goals",
    "principles", "horizons", "anti_patterns",
}


def extract_vision_meta(text: str) -> dict[str, Any] | None:
    """Return the parsed vision-meta JSON, or None if missing/malformed."""
    match = _META_RE.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as e:
        logger.debug("vision-meta JSON parse failed: %s", e)
        return None


def extract_vision_doc(text: str) -> dict[str, Any] | None:
    """Return the parsed vision-doc JSON if present and structurally complete."""
    match = _DOC_RE.search(text)
    if not match:
        return None
    try:
        doc = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        logger.debug("vision-doc JSON parse failed: %s", e)
        return None
    if not isinstance(doc, dict):
        return None
    if not _REQUIRED_DOC_KEYS.issubset(doc.keys()):
        logger.debug("vision-doc missing required keys: have %s", doc.keys())
        return None
    return doc


def strip_fenced_blocks(text: str) -> str:
    """Remove vision-meta and vision-doc fences from text for client display."""
    return _FENCE_RE.sub("", text).rstrip() + "\n"
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
pytest tests/test_vision_chat_parser.py -v
```

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/services/vision_chat_parser.py dashboard/backend/tests/test_vision_chat_parser.py
git commit -m "feat(vision): parser for vision-meta/vision-doc fenced blocks"
```

---

## Task 1.7: Vision chat session state machine

**Files:**
- Create: `dashboard/backend/app/services/vision_chat.py`
- Test: `dashboard/backend/tests/test_vision_chat_service.py`

- [ ] **Step 1: Write the failing tests**

```python
# dashboard/backend/tests/test_vision_chat_service.py
import json
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import init_db, async_session_maker
from app.models import Project, VisionChatSession
from app.services.vision_chat import (
    create_session, get_active_session, append_turn, mark_approved,
    mark_cancelled, SessionAlreadyActive, SessionNotFound,
)


@pytest.fixture
async def db_session():
    await init_db()
    async with async_session_maker() as session:
        yield session


@pytest.fixture
async def project(db_session: AsyncSession):
    p = Project(repo="o/r", branch="main")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


@pytest.mark.asyncio
async def test_create_session_returns_active_session_with_uuid(db_session, project):
    s = await create_session(db_session, project.id)
    assert s.id  # UUID assigned
    assert s.state == "active"
    assert s.phase == "freeform"
    assert json.loads(s.coverage) == {}


@pytest.mark.asyncio
async def test_create_second_session_while_active_raises(db_session, project):
    await create_session(db_session, project.id)
    with pytest.raises(SessionAlreadyActive):
        await create_session(db_session, project.id)


@pytest.mark.asyncio
async def test_get_active_session_returns_only_active_state(db_session, project):
    s1 = await create_session(db_session, project.id)
    await mark_approved(db_session, s1.id)
    found = await get_active_session(db_session, project.id)
    assert found is None  # approved doesn't count


@pytest.mark.asyncio
async def test_create_session_after_previous_approved(db_session, project):
    s1 = await create_session(db_session, project.id)
    await mark_approved(db_session, s1.id)
    s2 = await create_session(db_session, project.id)
    assert s2.id != s1.id
    assert s2.state == "active"


@pytest.mark.asyncio
async def test_append_turn_adds_to_messages_and_updates_coverage(db_session, project):
    s = await create_session(db_session, project.id)
    await append_turn(
        db_session, s.id,
        user_message="Hi",
        assistant_message="Hello!",
        coverage={"problem": True},
        phase="structured",
    )
    refreshed = await db_session.get(VisionChatSession, s.id)
    msgs = json.loads(refreshed.messages)
    assert msgs == [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
    ]
    assert json.loads(refreshed.coverage) == {"problem": True}
    assert refreshed.phase == "structured"


@pytest.mark.asyncio
async def test_mark_cancelled_with_unknown_id_raises(db_session):
    with pytest.raises(SessionNotFound):
        await mark_cancelled(db_session, "no-such-id")
```

- [ ] **Step 2: Run the tests and confirm they fail**

```bash
pytest tests/test_vision_chat_service.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement the service**

```python
# dashboard/backend/app/services/vision_chat.py
"""Chat session state machine for collaborative vision authoring.

Sessions are owned by Project. "One active per project" is enforced here
(SQLite has no partial unique indexes); historical approved/cancelled
rows coexist freely.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import VisionChatSession

logger = logging.getLogger(__name__)


class SessionAlreadyActive(Exception):
    def __init__(self, existing_session_id: str):
        self.existing_session_id = existing_session_id
        super().__init__(f"session {existing_session_id} already active")


class SessionNotFound(Exception):
    pass


async def get_active_session(db: AsyncSession, project_id: int) -> VisionChatSession | None:
    result = await db.execute(
        select(VisionChatSession).where(
            VisionChatSession.project_id == project_id,
            VisionChatSession.state == "active",
        ).limit(1)
    )
    return result.scalar_one_or_none()


async def create_session(db: AsyncSession, project_id: int) -> VisionChatSession:
    """Create a new active session.

    Raises SessionAlreadyActive if one already exists for this project.
    """
    existing = await get_active_session(db, project_id)
    if existing is not None:
        raise SessionAlreadyActive(existing.id)

    session = VisionChatSession(
        id=str(uuid.uuid4()),
        project_id=project_id,
        state="active",
        phase="freeform",
        coverage="{}",
        messages="[]",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def append_turn(
    db: AsyncSession,
    session_id: str,
    *,
    user_message: str,
    assistant_message: str,
    coverage: dict | None = None,
    phase: str | None = None,
    sdk_session_id: str | None = None,
) -> VisionChatSession:
    """Append a user→assistant turn and update coverage/phase if provided."""
    session = await db.get(VisionChatSession, session_id)
    if session is None:
        raise SessionNotFound(session_id)

    msgs = json.loads(session.messages)
    msgs.append({"role": "user", "content": user_message})
    msgs.append({"role": "assistant", "content": assistant_message})
    session.messages = json.dumps(msgs)

    if coverage is not None:
        session.coverage = json.dumps(coverage)
    if phase is not None:
        session.phase = phase
    if sdk_session_id is not None:
        session.sdk_session_id = sdk_session_id
    session.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(session)
    return session


async def mark_approved(db: AsyncSession, session_id: str, assembled: dict | None = None) -> None:
    session = await db.get(VisionChatSession, session_id)
    if session is None:
        raise SessionNotFound(session_id)
    session.state = "approved"
    if assembled is not None:
        session.assembled = json.dumps(assembled)
    session.updated_at = datetime.now(timezone.utc)
    await db.commit()


async def mark_cancelled(db: AsyncSession, session_id: str) -> None:
    session = await db.get(VisionChatSession, session_id)
    if session is None:
        raise SessionNotFound(session_id)
    session.state = "cancelled"
    session.updated_at = datetime.now(timezone.utc)
    await db.commit()
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
pytest tests/test_vision_chat_service.py -v
```

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/services/vision_chat.py dashboard/backend/tests/test_vision_chat_service.py
git commit -m "feat(vision): chat session state machine"
```

---

## Task 1.8: System prompts for create + refine

**Files:**
- Create: `agent/prompts/vision_create.md`
- Create: `agent/prompts/vision_refine.md`

- [ ] **Step 1: Write the create prompt**

```markdown
<!-- agent/prompts/vision_create.md -->
You are helping a user define a project vision for the Claude Station.

# How to Run This Conversation

There are two phases:

1. **Free-form** — listen first. Greet the user. Ask them to describe their project in their own words. Ask one focused follow-up at a time when something is vague. Do NOT begin the structured interview yet.

2. **Structured interview** — once the user has finished their free-form description (or asks to move on), walk the seven sections below and ask targeted questions only for ones not yet covered. Skip sections obvious from phase 1.

The seven sections, in order:

- **Problem** — what pain this tool solves
- **Users** — who it's for and who it's not for
- **End-state** — what "done" / "succeeded" looks like, concretely
- **Non-goals** — things deliberately out of scope
- **Principles** — how to choose when two good options conflict
- **Horizons** — near-term (3 mo), mid-term (12 mo), long-term direction
- **Anti-patterns** — concrete examples of *bad* outcomes

# Per-turn metadata (REQUIRED)

After every assistant reply, emit a fenced JSON block exactly like this:

````
```vision-meta
{ "phase": "freeform" | "structured",
  "covered": [<sections you have enough signal on, lowercase, snake_case>],
  "ready_to_assemble": <true when all seven sections covered, else false> }
```
````

The valid section names are: `problem`, `users`, `end_state`, `non_goals`, `principles`, `horizons`, `anti_patterns`.

# Final assembly

When the user approves and asks you to assemble, output ONLY a fenced JSON block — no prose, no preface:

````
```vision-doc
{ "problem": "...", "users": "...", "end_state": "...", "non_goals": "...",
  "principles": "...", "horizons": "...", "anti_patterns": "..." }
```
````

Each value is markdown — concise, a short paragraph, not an essay. Aim for 100–300 words per section.
```

- [ ] **Step 2: Write the refine prompt**

```markdown
<!-- agent/prompts/vision_refine.md -->
You are helping a user refine an existing project vision in the Claude Station.

The user already has a vision (inlined below). Don't start from scratch:

1. Greet the user. Ask which sections they want to change.
2. Probe only those sections with focused questions.
3. Leave untouched sections as-is in the final assembly.

Use the same `vision-meta` and `vision-doc` contracts as the create flow.

The seven sections are: problem, users, end_state, non_goals, principles, horizons, anti_patterns.

# Current vision

{{CURRENT_VISION_MARKDOWN}}
```

- [ ] **Step 3: Commit**

```bash
git add agent/prompts/vision_create.md agent/prompts/vision_refine.md
git commit -m "feat(vision): system prompts for create and refine flows"
```

---

## Task 1.9: SDK query wrapper with streaming + meta extraction

**Files:**
- Modify: `dashboard/backend/app/services/vision_chat.py` (add streaming function)
- Test: `dashboard/backend/tests/test_vision_chat_service.py` (add streaming test)

This wraps `claude_agent_sdk.query` and yields typed chunks the SSE endpoint can forward. Uses the same `_user_prompt_stream` async-iterable trick that `agent/station_orchestrator.py` uses (see commit `c69866f`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_vision_chat_service.py`:

```python
from unittest.mock import patch, AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_run_chat_turn_yields_text_meta_and_done(db_session, project):
    """run_chat_turn yields TextChunk, MetaChunk, DoneChunk for a normal turn."""
    s = await create_session(db_session, project.id)

    # Fake the SDK to emit one assistant text + meta block, then a result
    async def fake_query(prompt, options):
        # Simulate streaming text chunks then a result
        msg1 = MagicMock()
        msg1.session_id = "sdk-sid-1"
        msg1.content = [MagicMock(text="Hello!\n\n```vision-meta\n"
                                       '{"phase": "freeform", "covered": ["problem"], '
                                       '"ready_to_assemble": false}\n```\n")]
        msg1.__class__.__name__ = "AssistantMessage"
        yield msg1
        result = MagicMock()
        result.session_id = "sdk-sid-1"
        result.__class__.__name__ = "ResultMessage"
        yield result

    from app.services import vision_chat as vc
    with patch.object(vc, "query", new=fake_query):
        chunks = []
        async for chunk in vc.run_chat_turn(
            db_session, session_id=s.id, user_message="Hi",
            system_prompt="<test prompt>", model="claude-sonnet-4-6",
        ):
            chunks.append(chunk)

    kinds = [c["type"] for c in chunks]
    assert "assistant_text" in kinds
    assert "coverage_update" in kinds
    assert "phase_change" in kinds
    assert kinds[-1] == "done"
```

- [ ] **Step 2: Run the test and confirm it fails**

```bash
pytest tests/test_vision_chat_service.py::test_run_chat_turn_yields_text_meta_and_done -v
```

Expected: AttributeError on `vision_chat.query` or `run_chat_turn` not defined.

- [ ] **Step 3: Implement the wrapper**

Append to `dashboard/backend/app/services/vision_chat.py`:

```python
from typing import AsyncIterator

from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.types import AssistantMessage, ResultMessage, SystemMessage

from app.services.vision_chat_parser import (
    extract_vision_meta, extract_vision_doc, strip_fenced_blocks,
)


async def _user_prompt_stream(text: str):
    """One-shot async iterable wrapping a user message.

    Same pattern as agent/station_orchestrator.py — required when using
    can_use_tool, but harmless and simpler than maintaining two paths.
    """
    yield {
        "type": "user",
        "session_id": "",
        "message": {"role": "user", "content": text},
        "parent_tool_use_id": None,
    }


async def run_chat_turn(
    db: AsyncSession,
    *,
    session_id: str,
    user_message: str,
    system_prompt: str,
    model: str,
    sdk_session_id: str | None = None,
) -> AsyncIterator[dict]:
    """Run one chat turn against the bundled CLI; yield SSE-shaped chunks.

    Yields dicts with shape `{type, ...}` ready to serialise to SSE events:
    - `{"type": "assistant_text", "delta": "..."}` — incremental, append
    - `{"type": "coverage_update", "covered": [...], "remaining": [...]}`
    - `{"type": "phase_change", "phase": "freeform" | "structured"}`
    - `{"type": "vision_ready", "vision_doc": {...}}`
    - `{"type": "error", "code": "...", "message": "..."}`
    - `{"type": "done"}`

    Persists the turn (user + assistant text without fences) on completion.
    """
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=model,
        max_turns=1,  # one turn per call; UI loops as user types
    )
    if sdk_session_id:
        options.resume = sdk_session_id
        options.continue_conversation = True

    SECTIONS = ["problem", "users", "end_state", "non_goals",
                "principles", "horizons", "anti_patterns"]

    full_text = ""
    new_sdk_sid: str | None = None
    last_meta: dict | None = None
    final_doc: dict | None = None

    try:
        async for message in query(prompt=_user_prompt_stream(user_message), options=options):
            sid = getattr(message, "session_id", None)
            if sid:
                new_sdk_sid = sid

            if isinstance(message, AssistantMessage):
                # AssistantMessage.content is a list of blocks; we collect text blocks
                for block in getattr(message, "content", []) or []:
                    text = getattr(block, "text", None)
                    if not text:
                        continue
                    full_text += text
                    # Stream the visible portion (fences stripped) — but only
                    # the *new* visible content since the last yield.
                    visible = strip_fenced_blocks(full_text)
                    yield {"type": "assistant_text", "delta": text}

            elif isinstance(message, ResultMessage):
                # End of turn — extract metadata and final doc if present.
                last_meta = extract_vision_meta(full_text)
                final_doc = extract_vision_doc(full_text)

    except Exception as e:
        logger.exception("run_chat_turn failed")
        yield {"type": "error", "code": "sdk_error", "message": str(e)}
        return

    # Emit metadata-derived events
    if last_meta:
        covered = last_meta.get("covered", []) or []
        yield {
            "type": "coverage_update",
            "covered": covered,
            "remaining": [s for s in SECTIONS if s not in covered],
        }
        phase = last_meta.get("phase")
        if phase in ("freeform", "structured"):
            yield {"type": "phase_change", "phase": phase}

    if final_doc is not None:
        yield {"type": "vision_ready", "vision_doc": final_doc}

    # Persist the visible assistant text + state updates
    visible = strip_fenced_blocks(full_text)
    coverage_dict: dict = {}
    if last_meta:
        coverage_dict = {s: (s in (last_meta.get("covered") or [])) for s in SECTIONS}

    await append_turn(
        db, session_id,
        user_message=user_message,
        assistant_message=visible,
        coverage=coverage_dict if last_meta else None,
        phase=(last_meta.get("phase") if last_meta else None),
        sdk_session_id=new_sdk_sid,
    )

    yield {"type": "done"}
```

- [ ] **Step 4: Run the test and confirm it passes**

```bash
pytest tests/test_vision_chat_service.py::test_run_chat_turn_yields_text_meta_and_done -v
```

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/services/vision_chat.py dashboard/backend/tests/test_vision_chat_service.py
git commit -m "feat(vision): SDK query wrapper streaming chat turns"
```

---

## Task 1.10: Pydantic schemas

**Files:**
- Modify: `dashboard/backend/app/schemas.py`

- [ ] **Step 1: Append schemas**

At the end of `dashboard/backend/app/schemas.py`:

```python
# ── Vision (Phase 1) ───────────────────────────────────────────

class VisionDoc(BaseModel):
    """Structured vision payload — one field per section."""
    problem: str
    users: str
    end_state: str
    non_goals: str
    principles: str
    horizons: str
    anti_patterns: str


class VisionRead(BaseModel):
    """Response for GET /api/projects/{id}/vision."""
    sha: str
    body: str
    last_refined_at: str | None = None  # ISO timestamp from latest commit
    last_refined_by: str | None = None  # GitHub login from latest commit
    cache_age_seconds: int


class VisionCommitIn(BaseModel):
    """Body for POST /api/projects/{id}/vision."""
    vision_doc: VisionDoc


class VisionCommitOut(BaseModel):
    """Response for POST /api/projects/{id}/vision."""
    sha: str
    html_url: str


class VisionStaleSha(BaseModel):
    """409 envelope for POST /api/projects/{id}/vision."""
    code: str = "stale_sha"
    current_sha: str
    current_body: str


class VisionChatSessionOut(BaseModel):
    """Response for GET /api/projects/{id}/vision/chat."""
    id: str
    project_id: int
    state: str
    phase: str
    coverage: dict
    messages: list[dict]
    assembled: dict | None
    created_at: str
    updated_at: str


class VisionChatTurnIn(BaseModel):
    """Body for POST /api/projects/{id}/vision/chat (turn)."""
    session_id: str | None = None  # None on first turn
    message: str
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/backend/app/schemas.py
git commit -m "feat(vision): Pydantic schemas for vision endpoints"
```

---

## Task 1.11: Router — GET vision (cache-aware read)

**Files:**
- Create: `dashboard/backend/app/routers/vision.py`
- Test: `dashboard/backend/tests/test_vision_router.py`

- [ ] **Step 1: Write the failing test**

```python
# dashboard/backend/tests/test_vision_router.py
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient
from app.main import app
from app.database import init_db, async_session_maker
from app.models import Project
from app.services.github_contents import ContentsResult, FileNotFound


@pytest.fixture
async def project():
    await init_db()
    async with async_session_maker() as db:
        p = Project(repo="o/r", branch="main")
        db.add(p)
        await db.commit()
        await db.refresh(p)
        return p


@pytest.mark.asyncio
async def test_get_vision_serves_cache_when_fresh(project):
    async with async_session_maker() as db:
        proj = await db.get(Project, project.id)
        proj.vision_cached_sha = "abc"
        proj.vision_cached_body = "# cached"
        proj.vision_cached_at = datetime.now(timezone.utc)
        await db.commit()

    async with AsyncClient(app=app, base_url="http://test") as c:
        with patch("app.services.github_contents.read_file", new=AsyncMock()) as m:
            r = await c.get(f"/api/projects/{project.id}/vision")
    assert r.status_code == 200
    assert r.json()["sha"] == "abc"
    assert r.json()["body"] == "# cached"
    m.assert_not_called()  # cache was used


@pytest.mark.asyncio
async def test_get_vision_falls_through_to_github_when_stale(project):
    async with async_session_maker() as db:
        proj = await db.get(Project, project.id)
        proj.vision_cached_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        await db.commit()

    fake = ContentsResult(sha="new-sha", body="# fresh", html_url="x")
    async with AsyncClient(app=app, base_url="http://test") as c:
        with patch("app.services.github_contents.read_file", new=AsyncMock(return_value=fake)):
            r = await c.get(f"/api/projects/{project.id}/vision")
    assert r.status_code == 200
    assert r.json()["sha"] == "new-sha"


@pytest.mark.asyncio
async def test_get_vision_returns_404_when_file_absent(project):
    async with AsyncClient(app=app, base_url="http://test") as c:
        with patch("app.services.github_contents.read_file",
                   new=AsyncMock(side_effect=FileNotFound("o/r:main:docs/vision.md"))):
            r = await c.get(f"/api/projects/{project.id}/vision")
    assert r.status_code == 404
```

- [ ] **Step 2: Run the tests and confirm they fail**

```bash
pytest tests/test_vision_router.py::test_get_vision_serves_cache_when_fresh -v
```

Expected: 404 (router not registered) or import error.

- [ ] **Step 3: Implement the GET endpoint**

```python
# dashboard/backend/app/routers/vision.py
"""Vision authoring endpoints (Phase 1)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Project
from app.schemas import VisionRead
from app.services import github_contents

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["vision"])

CACHE_TTL_SECONDS = 5 * 60  # 5 minutes


@router.get("/{project_id}/vision", response_model=VisionRead)
async def get_vision(project_id: int, db: AsyncSession = Depends(get_db)) -> VisionRead:
    """Return the current vision document, cache-aware."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    now = datetime.now(timezone.utc)
    cache_fresh = (
        project.vision_cached_body is not None
        and project.vision_cached_at is not None
        and (now - project.vision_cached_at.replace(tzinfo=timezone.utc)).total_seconds() < CACHE_TTL_SECONDS
    )
    if cache_fresh:
        age = int((now - project.vision_cached_at.replace(tzinfo=timezone.utc)).total_seconds())
        return VisionRead(
            sha=project.vision_cached_sha,
            body=project.vision_cached_body,
            cache_age_seconds=age,
        )

    # Fall through to GitHub
    try:
        result = await github_contents.read_file(
            repo=project.repo, path="docs/vision.md", branch=project.branch or "main",
        )
    except github_contents.FileNotFound:
        raise HTTPException(status_code=404, detail="docs/vision.md not found on base branch")

    project.vision_cached_sha = result.sha
    project.vision_cached_body = result.body
    project.vision_cached_at = now
    await db.commit()

    return VisionRead(sha=result.sha, body=result.body, cache_age_seconds=0)
```

- [ ] **Step 4: Register the router**

In `dashboard/backend/app/main.py`, add to imports:

```python
from app.routers import (
    # ... existing imports ...
    vision as vision_router,
)
```

And in the include_router calls:

```python
app.include_router(vision_router.router, dependencies=_auth)
```

- [ ] **Step 5: Run the tests and confirm they pass**

```bash
pytest tests/test_vision_router.py::test_get_vision_serves_cache_when_fresh tests/test_vision_router.py::test_get_vision_falls_through_to_github_when_stale tests/test_vision_router.py::test_get_vision_returns_404_when_file_absent -v
```

- [ ] **Step 6: Commit**

```bash
git add dashboard/backend/app/routers/vision.py dashboard/backend/app/main.py dashboard/backend/tests/test_vision_router.py
git commit -m "feat(vision): GET /api/projects/{id}/vision (cache-aware)"
```

---

## Task 1.12: Router — POST vision (commit to GitHub)

**Files:**
- Modify: `dashboard/backend/app/routers/vision.py`
- Modify: `dashboard/backend/tests/test_vision_router.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_vision_router.py`:

```python
@pytest.mark.asyncio
async def test_post_vision_renders_and_commits(project):
    fake_sha = "new-blob-sha"
    fake_html = "https://github.com/o/r/blob/main/docs/vision.md"
    async with async_session_maker() as db:
        proj = await db.get(Project, project.id)
        proj.vision_cached_sha = "old-sha"
        await db.commit()

    body = {
        "vision_doc": {
            "problem": "P", "users": "U", "end_state": "E", "non_goals": "N",
            "principles": "Pr", "horizons": "H", "anti_patterns": "A",
        }
    }
    async with AsyncClient(app=app, base_url="http://test") as c:
        with patch("app.services.github_contents.write_file", new=AsyncMock(return_value=fake_sha)) as m, \
             patch("app.services.github_contents.read_file",
                   new=AsyncMock(return_value=ContentsResult(sha=fake_sha, body="# Vision — o/r\n...", html_url=fake_html))):
            r = await c.post(f"/api/projects/{project.id}/vision", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["sha"] == fake_sha
    assert r.json()["html_url"] == fake_html

    # Verify the rendered body was sent
    args, kwargs = m.call_args
    assert "## Problem" in kwargs["body"]
    assert "## Anti-patterns" in kwargs["body"]
    assert kwargs["current_sha"] == "old-sha"


@pytest.mark.asyncio
async def test_post_vision_409_on_stale_sha_returns_envelope(project):
    from app.services.github_contents import StaleSha
    async with async_session_maker() as db:
        proj = await db.get(Project, project.id)
        proj.vision_cached_sha = "stale-sha"
        await db.commit()

    body = {"vision_doc": {k: "x" for k in [
        "problem", "users", "end_state", "non_goals",
        "principles", "horizons", "anti_patterns",
    ]}}
    async with AsyncClient(app=app, base_url="http://test") as c:
        with patch("app.services.github_contents.write_file",
                   new=AsyncMock(side_effect=StaleSha(current_sha="newer-sha", current_body="# external"))):
            r = await c.post(f"/api/projects/{project.id}/vision", json=body)
    assert r.status_code == 409
    payload = r.json()["detail"]
    assert payload["code"] == "stale_sha"
    assert payload["current_sha"] == "newer-sha"
    assert payload["current_body"] == "# external"
```

- [ ] **Step 2: Run the tests and confirm they fail**

```bash
pytest tests/test_vision_router.py::test_post_vision_renders_and_commits -v
```

Expected: 405 (POST not allowed) or 404 (no such route).

- [ ] **Step 3: Implement the POST endpoint**

Append to `dashboard/backend/app/routers/vision.py`:

```python
from app.schemas import VisionCommitIn, VisionCommitOut, VisionStaleSha
from app.services.vision_render import render_vision_doc
from app.services import vision_chat as vc_service


COMMIT_MESSAGE = "docs(vision): refine via Claude Station"


@router.post("/{project_id}/vision", response_model=VisionCommitOut)
async def commit_vision(
    project_id: int,
    body: VisionCommitIn,
    db: AsyncSession = Depends(get_db),
) -> VisionCommitOut:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    now = datetime.now(timezone.utc)
    md = render_vision_doc(
        body.vision_doc.model_dump(),
        repo=project.repo,
        refined_at=now,
    )

    try:
        new_sha = await github_contents.write_file(
            repo=project.repo,
            path="docs/vision.md",
            branch=project.branch or "main",
            body=md,
            message=COMMIT_MESSAGE,
            current_sha=project.vision_cached_sha,
        )
    except github_contents.StaleSha as exc:
        raise HTTPException(
            status_code=409,
            detail=VisionStaleSha(
                current_sha=exc.current_sha,
                current_body=exc.current_body,
            ).model_dump(),
        )

    # Re-fetch to get html_url; also updates the cache
    fresh = await github_contents.read_file(
        repo=project.repo, path="docs/vision.md", branch=project.branch or "main",
    )
    project.vision_cached_sha = fresh.sha
    project.vision_cached_body = fresh.body
    project.vision_cached_at = now

    # Mark any active chat session as approved with the assembled doc
    active = await vc_service.get_active_session(db, project_id)
    if active:
        await vc_service.mark_approved(db, active.id, assembled=body.vision_doc.model_dump())

    await db.commit()
    return VisionCommitOut(sha=new_sha, html_url=fresh.html_url)
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
pytest tests/test_vision_router.py -v -k post_vision
```

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/routers/vision.py dashboard/backend/tests/test_vision_router.py
git commit -m "feat(vision): POST /api/projects/{id}/vision (commit + 409 envelope)"
```

---

## Task 1.13: Router — chat SSE endpoint

**Files:**
- Modify: `dashboard/backend/app/routers/vision.py`
- Modify: `dashboard/backend/tests/test_vision_router.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_vision_router.py`:

```python
@pytest.mark.asyncio
async def test_post_vision_chat_streams_sse_events(project):
    """SSE endpoint yields events for assistant text, coverage, done."""
    async def fake_run_chat_turn(db, *, session_id, user_message, system_prompt, model, sdk_session_id):
        yield {"type": "assistant_text", "delta": "hi"}
        yield {"type": "coverage_update", "covered": ["problem"], "remaining": []}
        yield {"type": "done"}

    async with AsyncClient(app=app, base_url="http://test") as c:
        with patch("app.services.vision_chat.run_chat_turn", new=fake_run_chat_turn):
            async with c.stream(
                "POST",
                f"/api/projects/{project.id}/vision/chat",
                json={"session_id": None, "message": "hi"},
            ) as r:
                assert r.status_code == 200
                lines = []
                async for line in r.aiter_lines():
                    lines.append(line)
                    if len(lines) > 12: break
    text = "\n".join(lines)
    assert "event: assistant_text" in text
    assert "event: coverage_update" in text
    assert "event: done" in text


@pytest.mark.asyncio
async def test_post_vision_chat_409_when_session_already_active(project):
    from app.services import vision_chat as vc_service
    async with async_session_maker() as db:
        await vc_service.create_session(db, project.id)

    async with AsyncClient(app=app, base_url="http://test") as c:
        r = await c.post(
            f"/api/projects/{project.id}/vision/chat",
            json={"session_id": None, "message": "hi"},
        )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "session_exists"
    assert detail["session_id"]
```

- [ ] **Step 2: Run the tests and confirm they fail**

```bash
pytest tests/test_vision_router.py -v -k post_vision_chat
```

- [ ] **Step 3: Implement the SSE endpoint**

Append to `dashboard/backend/app/routers/vision.py`:

```python
import json
from pathlib import Path
from fastapi import Request
from fastapi.responses import StreamingResponse

from app.schemas import VisionChatTurnIn
from app.services.vision_chat import (
    create_session, get_active_session, mark_cancelled,
    SessionAlreadyActive, SessionNotFound,
)

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "agent" / "prompts"


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _sse_format(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")


@router.post("/{project_id}/vision/chat")
async def chat_turn(
    project_id: int,
    body: VisionChatTurnIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Stream a chat turn as SSE events."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    # Resolve session: existing if session_id supplied, else create
    if body.session_id:
        session = await db.get(VisionChatSession, body.session_id)
        if not session or session.project_id != project_id or session.state != "active":
            raise HTTPException(status_code=404, detail="active session not found")
    else:
        try:
            session = await create_session(db, project_id)
        except SessionAlreadyActive as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "session_exists", "session_id": exc.existing_session_id},
            )

    # Pick the right system prompt
    if project.vision_cached_body:
        prompt_template = _load_prompt("vision_refine.md")
        system_prompt = prompt_template.replace(
            "{{CURRENT_VISION_MARKDOWN}}", project.vision_cached_body,
        )
    else:
        system_prompt = _load_prompt("vision_create.md")

    # Pick the model — read the station config JSON directly (the same
    # source the config router serves at GET /api/config).
    import asyncio as _asyncio
    from app.services.config_sync import _read_config_json
    config = await _asyncio.to_thread(_read_config_json)
    model = (config.get("models") or {}).get("planner") or "claude-sonnet-4-6"

    async def event_stream():
        from app.services.vision_chat import run_chat_turn
        async for chunk in run_chat_turn(
            db,
            session_id=session.id,
            user_message=body.message,
            system_prompt=system_prompt,
            model=model,
            sdk_session_id=session.sdk_session_id,
        ):
            kind = chunk.pop("type")
            yield _sse_format(kind, chunk)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable proxy buffering
            "Connection": "keep-alive",
        },
    )
```

(Also add `from app.models import VisionChatSession` to the top of `vision.py`.)

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
pytest tests/test_vision_router.py -v -k post_vision_chat
```

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/routers/vision.py dashboard/backend/tests/test_vision_router.py
git commit -m "feat(vision): SSE chat turn endpoint with session-conflict 409"
```

---

## Task 1.14: Router — GET active session (resume) + DELETE (cancel)

**Files:**
- Modify: `dashboard/backend/app/routers/vision.py`
- Modify: `dashboard/backend/tests/test_vision_router.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
@pytest.mark.asyncio
async def test_get_active_chat_session_returns_404_when_none(project):
    async with AsyncClient(app=app, base_url="http://test") as c:
        r = await c.get(f"/api/projects/{project.id}/vision/chat")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_active_chat_session_returns_session(project):
    from app.services import vision_chat as vc_service
    async with async_session_maker() as db:
        await vc_service.create_session(db, project.id)
    async with AsyncClient(app=app, base_url="http://test") as c:
        r = await c.get(f"/api/projects/{project.id}/vision/chat")
    assert r.status_code == 200
    assert r.json()["state"] == "active"
    assert r.json()["phase"] == "freeform"
    assert r.json()["coverage"] == {}


@pytest.mark.asyncio
async def test_delete_chat_session_marks_cancelled(project):
    from app.services import vision_chat as vc_service
    async with async_session_maker() as db:
        s = await vc_service.create_session(db, project.id)
    async with AsyncClient(app=app, base_url="http://test") as c:
        r = await c.delete(f"/api/projects/{project.id}/vision/chat")
    assert r.status_code == 204
    async with async_session_maker() as db:
        from app.models import VisionChatSession
        refreshed = await db.get(VisionChatSession, s.id)
        assert refreshed.state == "cancelled"
```

- [ ] **Step 2: Run the tests and confirm they fail**

```bash
pytest tests/test_vision_router.py -v -k chat_session
```

- [ ] **Step 3: Implement GET and DELETE**

Append to `vision.py`:

```python
from fastapi import status as http_status
from app.schemas import VisionChatSessionOut


@router.get("/{project_id}/vision/chat", response_model=VisionChatSessionOut)
async def get_chat_session(project_id: int, db: AsyncSession = Depends(get_db)):
    session = await get_active_session(db, project_id)
    if not session:
        raise HTTPException(status_code=404, detail="no active session")
    return VisionChatSessionOut(
        id=session.id,
        project_id=session.project_id,
        state=session.state,
        phase=session.phase,
        coverage=json.loads(session.coverage),
        messages=json.loads(session.messages),
        assembled=json.loads(session.assembled) if session.assembled else None,
        created_at=session.created_at.isoformat() if session.created_at else "",
        updated_at=session.updated_at.isoformat() if session.updated_at else "",
    )


@router.delete("/{project_id}/vision/chat", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_chat_session(project_id: int, db: AsyncSession = Depends(get_db)):
    session = await get_active_session(db, project_id)
    if not session:
        raise HTTPException(status_code=404, detail="no active session")
    await mark_cancelled(db, session.id)
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
pytest tests/test_vision_router.py -v -k chat_session
```

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/routers/vision.py dashboard/backend/tests/test_vision_router.py
git commit -m "feat(vision): GET/DELETE /api/projects/{id}/vision/chat"
```

---

## Task 1.15: Stale session cleanup loop

**Files:**
- Create: `dashboard/backend/app/services/vision_cleanup.py`
- Test: `dashboard/backend/tests/test_vision_cleanup.py`
- Modify: `dashboard/backend/app/main.py` (start the loop on startup)

- [ ] **Step 1: Write the failing tests**

```python
# dashboard/backend/tests/test_vision_cleanup.py
import json
import pytest
from datetime import datetime, timezone, timedelta
from app.database import init_db, async_session_maker
from app.models import Project, VisionChatSession
from app.services.vision_cleanup import sweep_stale_sessions


@pytest.fixture
async def project():
    await init_db()
    async with async_session_maker() as db:
        p = Project(repo="o/r", branch="main")
        db.add(p)
        await db.commit()
        await db.refresh(p)
        return p


@pytest.mark.asyncio
async def test_sweep_cancels_active_older_than_24h(project):
    async with async_session_maker() as db:
        s = VisionChatSession(
            id="old-active", project_id=project.id, state="active",
            phase="freeform", coverage="{}", messages="[]",
            updated_at=datetime.now(timezone.utc) - timedelta(hours=25),
        )
        db.add(s); await db.commit()
    async with async_session_maker() as db:
        cancelled, deleted = await sweep_stale_sessions(db)
    assert cancelled == 1
    async with async_session_maker() as db:
        refreshed = await db.get(VisionChatSession, "old-active")
        assert refreshed.state == "cancelled"


@pytest.mark.asyncio
async def test_sweep_deletes_completed_older_than_30d(project):
    async with async_session_maker() as db:
        old = VisionChatSession(
            id="old-approved", project_id=project.id, state="approved",
            phase="structured", coverage="{}", messages="[]",
            updated_at=datetime.now(timezone.utc) - timedelta(days=31),
        )
        db.add(old); await db.commit()
    async with async_session_maker() as db:
        cancelled, deleted = await sweep_stale_sessions(db)
    assert deleted == 1
    async with async_session_maker() as db:
        assert await db.get(VisionChatSession, "old-approved") is None


@pytest.mark.asyncio
async def test_sweep_leaves_recent_active_alone(project):
    async with async_session_maker() as db:
        s = VisionChatSession(
            id="recent", project_id=project.id, state="active",
            phase="freeform", coverage="{}", messages="[]",
            updated_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        db.add(s); await db.commit()
    async with async_session_maker() as db:
        cancelled, deleted = await sweep_stale_sessions(db)
    assert cancelled == 0
    async with async_session_maker() as db:
        refreshed = await db.get(VisionChatSession, "recent")
        assert refreshed.state == "active"
```

- [ ] **Step 2: Run and confirm fail**

```bash
pytest tests/test_vision_cleanup.py -v
```

- [ ] **Step 3: Implement the sweep + loop**

```python
# dashboard/backend/app/services/vision_cleanup.py
"""Periodic cleanup of stale vision chat sessions.

Same surface as app/services/stale_run_reaper.py — startup hook in main.py
launches an asyncio task that calls sweep_stale_sessions() every 30 min.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker
from app.models import VisionChatSession

logger = logging.getLogger(__name__)

ACTIVE_TTL = timedelta(hours=24)
COMPLETED_TTL = timedelta(days=30)
SWEEP_INTERVAL_SECONDS = 30 * 60


async def sweep_stale_sessions(db: AsyncSession) -> tuple[int, int]:
    """Cancel active>24h, delete approved/cancelled>30d. Returns (cancelled, deleted)."""
    now = datetime.now(timezone.utc)
    active_cutoff = now - ACTIVE_TTL
    completed_cutoff = now - COMPLETED_TTL

    # Cancel stale active
    result = await db.execute(
        select(VisionChatSession).where(
            VisionChatSession.state == "active",
            VisionChatSession.updated_at < active_cutoff,
        )
    )
    stale_active = result.scalars().all()
    for s in stale_active:
        s.state = "cancelled"
        s.updated_at = now

    # Delete old completed/cancelled
    delete_result = await db.execute(
        delete(VisionChatSession).where(
            VisionChatSession.state.in_(["approved", "cancelled"]),
            VisionChatSession.updated_at < completed_cutoff,
        )
    )
    await db.commit()
    return len(stale_active), delete_result.rowcount or 0


async def run_cleanup_loop():
    """Background task: sweep every 30 min, log results, never crash."""
    while True:
        try:
            async with async_session_maker() as db:
                cancelled, deleted = await sweep_stale_sessions(db)
                if cancelled or deleted:
                    logger.info(
                        "vision_cleanup: cancelled=%d deleted=%d", cancelled, deleted,
                    )
        except Exception:
            logger.exception("vision_cleanup sweep failed")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
```

- [ ] **Step 4: Wire startup hook**

In `dashboard/backend/app/main.py`, find the existing `@app.on_event("startup")` (or `lifespan`) hook, add:

```python
import asyncio
from app.services.vision_cleanup import run_cleanup_loop

@app.on_event("startup")
async def _start_vision_cleanup():
    asyncio.create_task(run_cleanup_loop())
```

(If the existing pattern uses `lifespan`, fold the task creation there instead.)

- [ ] **Step 5: Run the tests and confirm they pass**

```bash
pytest tests/test_vision_cleanup.py -v
```

- [ ] **Step 6: Commit**

```bash
git add dashboard/backend/app/services/vision_cleanup.py dashboard/backend/app/main.py dashboard/backend/tests/test_vision_cleanup.py
git commit -m "feat(vision): periodic cleanup loop for stale chat sessions"
```

---

## Task 1.16: SDK resume smoke test

**Files:**
- Create: `dashboard/backend/tests/test_vision_sdk_resume.py`

This is the spec-mandated smoke test — verify SDK resume works for chat-style queries before the wizard ships. **Marked `@pytest.mark.integration`** because it spawns the actual CLI; runs in CI on demand, not by default.

- [ ] **Step 1: Write the smoke test**

```python
# dashboard/backend/tests/test_vision_sdk_resume.py
"""Smoke test: claude_agent_sdk.query resume works for chat-style usage.

If this test fails, the chat backend MUST fall back to transcript-replay
(see spec § Resume strategy).

Skipped by default; run with `pytest -m integration`.
"""

import pytest
from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.types import AssistantMessage, ResultMessage

pytestmark = pytest.mark.integration


async def _user_msg(text: str):
    yield {"type": "user", "session_id": "",
           "message": {"role": "user", "content": text}, "parent_tool_use_id": None}


@pytest.mark.asyncio
async def test_query_resume_chat_style_remembers_context():
    """First turn establishes context; second turn (resumed) recalls it."""
    options1 = ClaudeAgentOptions(
        system_prompt="Reply concisely. Remember anything I tell you.",
        model="claude-haiku-4-5-20251001",
        max_turns=1,
    )
    sid = None
    async for msg in query(prompt=_user_msg("My name is Sam."), options=options1):
        sid = getattr(msg, "session_id", None) or sid
    assert sid, "no session_id captured"

    options2 = ClaudeAgentOptions(
        system_prompt="Reply concisely.",
        model="claude-haiku-4-5-20251001",
        max_turns=1,
        resume=sid,
        continue_conversation=True,
    )
    final = ""
    async for msg in query(prompt=_user_msg("What is my name?"), options=options2):
        if isinstance(msg, AssistantMessage):
            for b in getattr(msg, "content", []) or []:
                final += getattr(b, "text", "")
    assert "Sam" in final, f"resume failed to recall name; got: {final!r}"
```

- [ ] **Step 2: Run it**

```bash
pytest tests/test_vision_sdk_resume.py -v -m integration
```

Expected: PASS, confirming SDK resume is viable. If it fails, see fallback note in spec § Resume strategy and adjust `vision_chat.run_chat_turn` to replay `messages` instead of using `options.resume`.

- [ ] **Step 3: Commit**

```bash
git add dashboard/backend/tests/test_vision_sdk_resume.py
git commit -m "test(vision): SDK resume smoke test (integration-marked)"
```

---

## Task 1.17: Frontend types + API client

**Files:**
- Modify: `dashboard/frontend/src/lib/types.ts`
- Modify: `dashboard/frontend/src/lib/api.ts`

- [ ] **Step 1: Add types**

Append to `dashboard/frontend/src/lib/types.ts`:

```typescript
// ── Vision (Phase 1) ───────────────────────────────────────────

export interface VisionDoc {
  problem: string;
  users: string;
  end_state: string;
  non_goals: string;
  principles: string;
  horizons: string;
  anti_patterns: string;
}

export interface VisionRead {
  sha: string;
  body: string;
  last_refined_at?: string | null;
  last_refined_by?: string | null;
  cache_age_seconds: number;
}

export interface VisionCommitOut {
  sha: string;
  html_url: string;
}

export interface VisionStaleSha {
  code: 'stale_sha';
  current_sha: string;
  current_body: string;
}

export interface VisionChatSession {
  id: string;
  project_id: number;
  state: 'active' | 'approved' | 'cancelled';
  phase: 'freeform' | 'structured';
  coverage: Record<string, boolean>;
  messages: { role: 'user' | 'assistant'; content: string }[];
  assembled: VisionDoc | null;
  created_at: string;
  updated_at: string;
}

export type VisionSseEvent =
  | { type: 'assistant_text'; delta: string }
  | { type: 'coverage_update'; covered: string[]; remaining: string[] }
  | { type: 'phase_change'; phase: 'freeform' | 'structured' }
  | { type: 'vision_ready'; vision_doc: VisionDoc }
  | { type: 'error'; code: string; message: string }
  | { type: 'done' };
```

- [ ] **Step 2: Add API client functions**

Append to `dashboard/frontend/src/lib/api.ts`:

```typescript
import type { VisionRead, VisionDoc, VisionCommitOut, VisionChatSession } from './types';

export const getVision = (projectId: number) =>
  request<VisionRead>(`/api/projects/${projectId}/vision`);

export const commitVision = (projectId: number, vision_doc: VisionDoc) =>
  request<VisionCommitOut>(`/api/projects/${projectId}/vision`, {
    method: 'POST', body: JSON.stringify({ vision_doc }),
  });

export const getVisionChatSession = (projectId: number) =>
  request<VisionChatSession>(`/api/projects/${projectId}/vision/chat`);

export const cancelVisionChat = (projectId: number) =>
  request<void>(`/api/projects/${projectId}/vision/chat`, { method: 'DELETE' });

// SSE chat turn — see lib/vision-sse.ts for the streaming wrapper
export const visionChatTurnUrl = (projectId: number) =>
  `${BASE}/api/projects/${projectId}/vision/chat`;
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/frontend/src/lib/types.ts dashboard/frontend/src/lib/api.ts
git commit -m "feat(vision): frontend types and API client functions"
```

---

## Task 1.18: SSE wrapper for the chat turn

**Files:**
- Create: `dashboard/frontend/src/lib/vision-sse.ts`
- Test: `dashboard/frontend/src/lib/vision-sse.test.ts`

We use `fetch` + `ReadableStream` rather than `EventSource` because the Auth header (`Bearer <api-key>`) can't be set on `EventSource`.

- [ ] **Step 1: Write the failing test**

```typescript
// dashboard/frontend/src/lib/vision-sse.test.ts
import { describe, it, expect, vi } from 'vitest';
import { streamVisionChat } from './vision-sse';

describe('streamVisionChat', () => {
  it('parses event-stream into typed events', async () => {
    const sse =
      'event: assistant_text\ndata: {"delta":"hi"}\n\n' +
      'event: coverage_update\ndata: {"covered":["problem"],"remaining":[]}\n\n' +
      'event: done\ndata: {}\n\n';

    const fakeFetch = vi.fn(async () => ({
      ok: true,
      body: new ReadableStream({
        start(c) { c.enqueue(new TextEncoder().encode(sse)); c.close(); },
      }),
    }) as any);

    const events = [];
    for await (const e of streamVisionChat({
      url: 'http://test/chat',
      headers: {},
      payload: { session_id: null, message: 'hi' },
      fetchImpl: fakeFetch,
    })) {
      events.push(e);
    }

    expect(events.map(e => e.type)).toEqual([
      'assistant_text', 'coverage_update', 'done',
    ]);
    expect((events[0] as any).delta).toBe('hi');
  });

  it('yields error event on non-200 status', async () => {
    const fakeFetch = vi.fn(async () => ({
      ok: false, status: 500, body: null, statusText: 'fail',
    }) as any);

    const events = [];
    for await (const e of streamVisionChat({
      url: 'http://test/chat',
      headers: {},
      payload: { session_id: null, message: 'hi' },
      fetchImpl: fakeFetch,
    })) {
      events.push(e);
    }
    expect(events).toEqual([{ type: 'error', code: 'http_500', message: 'fail' }]);
  });

  it('aborts via AbortController and ends gracefully', async () => {
    const ctrl = new AbortController();
    const fakeFetch = vi.fn(async (url: string, init: any) => {
      // simulate hanging response
      return { ok: true, body: new ReadableStream({ start() {} }) } as any;
    });
    const it = streamVisionChat({
      url: 'http://test/chat',
      headers: {},
      payload: { session_id: null, message: 'hi' },
      fetchImpl: fakeFetch,
      signal: ctrl.signal,
    });
    setTimeout(() => ctrl.abort(), 5);
    const events: any[] = [];
    try {
      for await (const e of it) events.push(e);
    } catch (e) { /* aborted */ }
    expect(events.length).toBeLessThanOrEqual(1);
  });
});
```

- [ ] **Step 2: Run and confirm fail**

```bash
cd dashboard/frontend && npx vitest run src/lib/vision-sse.test.ts
```

- [ ] **Step 3: Implement the wrapper**

```typescript
// dashboard/frontend/src/lib/vision-sse.ts
import type { VisionSseEvent } from './types';

interface StreamArgs {
  url: string;
  headers: Record<string, string>;
  payload: { session_id: string | null; message: string };
  signal?: AbortSignal;
  fetchImpl?: typeof fetch;
}

export async function* streamVisionChat(args: StreamArgs): AsyncIterable<VisionSseEvent> {
  const fetchFn = args.fetchImpl ?? fetch;
  let resp: Response;
  try {
    resp = await fetchFn(args.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...args.headers },
      body: JSON.stringify(args.payload),
      signal: args.signal,
    });
  } catch (e: any) {
    yield { type: 'error', code: 'network', message: e?.message ?? 'network error' };
    return;
  }

  if (!resp.ok) {
    yield { type: 'error', code: `http_${resp.status}`, message: resp.statusText };
    return;
  }
  if (!resp.body) {
    yield { type: 'error', code: 'no_body', message: 'response has no body' };
    return;
  }

  const decoder = new TextDecoder();
  const reader = resp.body.getReader();
  let buf = '';

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      // SSE separator is two newlines
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, idx);
        buf = buf.slice(idx + 2);

        let eventName = 'message';
        let dataStr = '';
        for (const line of block.split('\n')) {
          if (line.startsWith('event:')) eventName = line.slice(6).trim();
          else if (line.startsWith('data:')) dataStr += line.slice(5).trim();
        }
        if (!dataStr) continue;
        try {
          const data = JSON.parse(dataStr);
          yield { type: eventName, ...data } as VisionSseEvent;
        } catch {
          // Skip malformed
        }
      }
    }
  } catch (e: any) {
    if (e?.name !== 'AbortError') {
      yield { type: 'error', code: 'stream_read', message: e?.message ?? 'stream error' };
    }
  } finally {
    try { reader.releaseLock(); } catch {}
  }
}
```

- [ ] **Step 4: Run and confirm pass**

```bash
npx vitest run src/lib/vision-sse.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/lib/vision-sse.ts dashboard/frontend/src/lib/vision-sse.test.ts
git commit -m "feat(vision): SSE streaming wrapper with abort support"
```

---

## Task 1.19: CoverageChecklist component

**Files:**
- Create: `dashboard/frontend/src/components/vision/CoverageChecklist.svelte`

- [ ] **Step 1: Write the component**

```svelte
<!-- dashboard/frontend/src/components/vision/CoverageChecklist.svelte -->
<script lang="ts">
  let { covered = [] }: { covered: string[] } = $props();

  const SECTIONS = [
    { key: 'problem', label: 'Problem' },
    { key: 'users', label: 'Users' },
    { key: 'end_state', label: 'End-state' },
    { key: 'non_goals', label: 'Non-goals' },
    { key: 'principles', label: 'Principles' },
    { key: 'horizons', label: 'Horizons' },
    { key: 'anti_patterns', label: 'Anti-patterns' },
  ];

  function isCovered(key: string) {
    return covered.includes(key);
  }
</script>

<div class="flex flex-wrap gap-2 text-xs">
  <span class="text-tertiary">Coverage:</span>
  {#each SECTIONS as s}
    <span
      class="px-2 py-0.5 rounded-full border"
      style={isCovered(s.key)
        ? 'background: rgba(46,125,50,0.10); color: #2E7D32; border-color: rgba(46,125,50,0.30);'
        : 'background: var(--color-surface-1); color: var(--color-tertiary); border-color: var(--color-border);'}
    >
      {isCovered(s.key) ? '▣' : '□'} {s.label}
    </span>
  {/each}
</div>
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/frontend/src/components/vision/CoverageChecklist.svelte
git commit -m "feat(vision): CoverageChecklist component"
```

---

## Task 1.20: VisionChat component (the heart of the UI)

**Files:**
- Create: `dashboard/frontend/src/components/vision/VisionChat.svelte`

This component is reused by both the wizard step 2 and the Project Detail tab.

- [ ] **Step 1: Write the component**

```svelte
<!-- dashboard/frontend/src/components/vision/VisionChat.svelte -->
<script lang="ts">
  import { onDestroy } from 'svelte';
  import { streamVisionChat } from '../../lib/vision-sse';
  import {
    visionChatTurnUrl, getVisionChatSession, cancelVisionChat,
    commitVision, getStoredApiKey,
  } from '../../lib/api';
  import { toastError, toastSuccess } from '../../lib/toast.svelte';
  import type { VisionDoc, VisionSseEvent } from '../../lib/types';
  import CoverageChecklist from './CoverageChecklist.svelte';

  let {
    projectId,
    onApproved = () => {},
    onCancelled = () => {},
  }: {
    projectId: number;
    onApproved?: () => void;
    onCancelled?: () => void;
  } = $props();

  type Msg = { role: 'user' | 'assistant'; content: string };

  let messages = $state<Msg[]>([]);
  let covered = $state<string[]>([]);
  let phase = $state<'freeform' | 'structured'>('freeform');
  let assembledDoc = $state<VisionDoc | null>(null);
  let input = $state('');
  let streaming = $state(false);
  let sessionId = $state<string | null>(null);
  let abortCtrl: AbortController | null = null;

  // Try to resume an existing session on mount
  $effect(() => { resume(); });

  onDestroy(() => abortCtrl?.abort());

  async function resume() {
    try {
      const s = await getVisionChatSession(projectId);
      sessionId = s.id;
      messages = s.messages.map(m => ({ role: m.role, content: m.content }));
      covered = Object.entries(s.coverage).filter(([, v]) => v).map(([k]) => k);
      phase = s.phase;
      if (s.assembled) assembledDoc = s.assembled;
    } catch {
      // 404 = no active session, normal fresh-start case
    }
  }

  async function send() {
    const text = input.trim();
    if (!text || streaming) return;
    input = '';
    streaming = true;
    messages = [...messages, { role: 'user', content: text }, { role: 'assistant', content: '' }];

    abortCtrl = new AbortController();
    const headers: Record<string, string> = {};
    const apiKey = getStoredApiKey();
    if (apiKey) headers['Authorization'] = `Bearer ${apiKey}`;

    try {
      for await (const ev of streamVisionChat({
        url: visionChatTurnUrl(projectId),
        headers,
        payload: { session_id: sessionId, message: text },
        signal: abortCtrl.signal,
      })) {
        handleEvent(ev);
      }
    } finally {
      streaming = false;
      abortCtrl = null;
    }
  }

  function handleEvent(ev: VisionSseEvent) {
    if (ev.type === 'assistant_text') {
      const i = messages.length - 1;
      if (i >= 0 && messages[i].role === 'assistant') {
        messages[i] = { role: 'assistant', content: messages[i].content + ev.delta };
        messages = [...messages];
      }
    } else if (ev.type === 'coverage_update') {
      covered = ev.covered;
    } else if (ev.type === 'phase_change') {
      phase = ev.phase;
    } else if (ev.type === 'vision_ready') {
      assembledDoc = ev.vision_doc;
    } else if (ev.type === 'error') {
      toastError(`Chat error: ${ev.message} (${ev.code})`);
    }
    // After the first send the backend has a session; refresh the id
    if (!sessionId) refreshSessionId();
  }

  async function refreshSessionId() {
    try {
      const s = await getVisionChatSession(projectId);
      sessionId = s.id;
    } catch { /* ignore */ }
  }

  async function approveAndCommit() {
    if (!assembledDoc) return;
    try {
      await commitVision(projectId, assembledDoc);
      toastSuccess('Vision saved to GitHub');
      onApproved();
    } catch (e: any) {
      toastError(e.message);
    }
  }

  async function cancel() {
    abortCtrl?.abort();
    try { await cancelVisionChat(projectId); } catch { /* ignore */ }
    onCancelled();
  }
</script>

<div class="space-y-3">
  <CoverageChecklist {covered} />

  <!-- Transcript -->
  <div class="card p-4 max-h-96 overflow-y-auto space-y-3" data-testid="vision-chat-transcript">
    {#if messages.length === 0}
      <p class="text-xs text-tertiary">
        Hi — describe your project in your own words. I'll listen, then walk
        through the seven sections of the vision.
      </p>
    {/if}
    {#each messages as m, i (i)}
      <div class="text-sm">
        <div class="text-[10px] font-semibold text-tertiary mb-1">{m.role === 'user' ? 'You' : 'Claude'}</div>
        <div class="whitespace-pre-wrap text-secondary">{m.content || (streaming && i === messages.length - 1 ? '…' : '')}</div>
      </div>
    {/each}
  </div>

  <!-- Input -->
  <div class="flex gap-2">
    <input
      type="text"
      bind:value={input}
      placeholder="Type a message…"
      class="input flex-1 text-sm"
      disabled={streaming}
      onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter') send(); }}
      data-testid="vision-chat-input"
    />
    <button
      type="button"
      onclick={send}
      disabled={streaming || !input.trim()}
      class="btn btn-primary btn-sm text-xs"
    >Send</button>
  </div>

  <!-- Terminal actions -->
  <div class="flex justify-between items-center">
    <button type="button" onclick={cancel} class="btn btn-ghost btn-sm text-xs">Cancel</button>
    <button
      type="button"
      onclick={approveAndCommit}
      disabled={!assembledDoc}
      data-testid="vision-chat-approve-btn"
      class="btn btn-primary btn-sm text-xs"
    >{assembledDoc ? '✓ Approve & commit' : 'Continue the conversation…'}</button>
  </div>
</div>
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/frontend/src/components/vision/VisionChat.svelte
git commit -m "feat(vision): VisionChat component with SSE, coverage, approve/cancel"
```

---

## Task 1.21: VisionTab component

**Files:**
- Create: `dashboard/frontend/src/components/vision/VisionTab.svelte`

- [ ] **Step 1: Write the component**

```svelte
<!-- dashboard/frontend/src/components/vision/VisionTab.svelte -->
<script lang="ts">
  import { getVision } from '../../lib/api';
  import type { VisionRead, Project } from '../../lib/types';
  import VisionChat from './VisionChat.svelte';

  let { project }: { project: Project } = $props();

  let vision = $state<VisionRead | null>(null);
  let loading = $state(true);
  let mode = $state<'view' | 'chat'>('view');

  $effect(() => { load(); });

  async function load() {
    loading = true;
    try {
      vision = await getVision(project.id);
      mode = 'view';
    } catch (e: any) {
      // 404 = no vision yet — show empty state
      vision = null;
    } finally {
      loading = false;
    }
  }

  function startChat() { mode = 'chat'; }
  function onApproved() { mode = 'view'; load(); }
  function onCancelled() { mode = 'view'; }

  const githubBaseUrl = $derived(
    `https://github.com/${project.repo}/blob/${project.branch || 'main'}/docs/vision.md`,
  );
</script>

{#if loading}
  <div class="text-sm text-tertiary">Loading…</div>
{:else if mode === 'chat'}
  <VisionChat projectId={project.id} {onApproved} {onCancelled} />
{:else if vision === null}
  <!-- Empty state -->
  <div class="card p-6 text-center space-y-3">
    <h3 class="font-heading text-base">No vision yet</h3>
    <p class="text-xs text-tertiary max-w-md mx-auto">
      A vision describes what this project is for and where it's headed.
      Claude will help you author it through a short conversation, then
      commit it to <code class="text-accent-orange">docs/vision.md</code>
      on the project's base branch.
    </p>
    <button type="button" onclick={startChat} data-testid="vision-start-btn"
            class="btn btn-primary btn-sm text-xs">Start vision chat</button>
  </div>
{:else}
  <!-- Read state -->
  <div class="card p-5 space-y-4">
    <div class="flex justify-between items-start gap-3 pb-2 border-b border-tertiary/15">
      <div class="text-xs text-tertiary">
        {#if vision.last_refined_at}
          Last refined {new Date(vision.last_refined_at).toLocaleDateString()}
          {#if vision.last_refined_by} by {vision.last_refined_by}{/if}
        {:else}
          docs/vision.md on {project.branch || 'main'}
        {/if}
      </div>
      <div class="flex gap-2">
        <button type="button" onclick={startChat} class="btn btn-primary btn-sm text-xs">Refine via chat</button>
        <a href={githubBaseUrl} target="_blank" rel="noopener" class="btn btn-ghost btn-sm text-xs">View on GitHub →</a>
      </div>
    </div>
    <pre class="whitespace-pre-wrap font-mono text-xs text-secondary">{vision.body}</pre>
  </div>
{/if}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/frontend/src/components/vision/VisionTab.svelte
git commit -m "feat(vision): VisionTab with empty/read/chat states"
```

---

## Task 1.22: Make Add Project a 2-step wizard

**Files:**
- Modify: `dashboard/frontend/src/pages/ProjectsPage.svelte`

- [ ] **Step 1: Locate the existing modal**

The existing modal is around `dashboard/frontend/src/pages/ProjectsPage.svelte:226`. It's a single-step `<Modal>` with form fields and a Save button.

- [ ] **Step 2: Add wizard state and modify the modal**

In the `<script>` block of `ProjectsPage.svelte`, add:

```typescript
import VisionChat from '../components/vision/VisionChat.svelte';

let wizardStep = $state<1 | 2>(1);
let savedProjectId = $state<number | null>(null);
```

Modify the existing `createProject()` function so that, after successful save, it sets `savedProjectId = result.id` and `wizardStep = 2` instead of closing the modal:

```typescript
async function createProject() {
  // ... existing project payload assembly ...
  try {
    const created = await postProject(payload);  // existing call
    savedProjectId = created.id;
    wizardStep = 2;  // advance to vision step
    // Don't close the modal yet
  } catch (e: any) { toastError(e.message); }
}

function closeWizard() {
  showCreateModal = false;
  wizardStep = 1;
  savedProjectId = null;
  // Refresh project list so the newly created project appears
  loadProjects();
}
```

- [ ] **Step 3: Wrap the modal body**

Replace the inner content of `<Modal show={showCreateModal} ...>` with:

```svelte
<Modal show={showCreateModal} onClose={closeWizard} title={wizardStep === 1 ? 'Add Project' : 'Project Vision'}>
  {#if wizardStep === 1}
    <!-- ↓ existing form here, unchanged ↓ -->
    <!-- ... -->
    <!-- ↑ existing form ↑ -->
    <div class="flex justify-end gap-2 mt-4">
      <button type="button" onclick={closeWizard} class="btn btn-ghost btn-sm">Cancel</button>
      <button type="button" onclick={createProject} class="btn btn-primary btn-sm">Next →</button>
    </div>
  {:else if wizardStep === 2 && savedProjectId !== null}
    <p class="text-xs text-tertiary mb-3">
      Step 2 of 2 — Define the project's vision so Claude knows the end goal.
    </p>
    <VisionChat
      projectId={savedProjectId}
      onApproved={closeWizard}
      onCancelled={closeWizard}
    />
    <div class="flex justify-start mt-3">
      <button type="button" onclick={closeWizard} class="btn btn-ghost btn-sm text-xs">
        Skip for now
      </button>
    </div>
  {/if}
</Modal>
```

- [ ] **Step 4: Smoke test in browser**

Start the dev server and verify the wizard:

```bash
cd dashboard/frontend && npm run dev
# In another terminal:
cd dashboard/backend && uvicorn app.main:app --reload --port 8420
```

Open the Add Project modal, fill step 1, click Next, confirm step 2 shows the chat. Type a message and confirm streaming text appears.

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/pages/ProjectsPage.svelte
git commit -m "feat(vision): 2-step Add Project wizard with embedded VisionChat"
```

---

## Task 1.23: Add Vision tab to Project Detail

**Files:**
- Modify: `dashboard/frontend/src/pages/ProjectDetail.svelte`

- [ ] **Step 1: Add tab state and import VisionTab**

In the `<script>` block:

```typescript
import VisionTab from '../components/vision/VisionTab.svelte';

let activeTab = $state<'overview' | 'vision' | 'runs'>('overview');
```

- [ ] **Step 2: Add a tab strip above the existing content**

Just after `<h1>{project.repo}</h1>`, before the Configuration card:

```svelte
<div class="flex gap-1" style="border-bottom: 1px solid var(--color-border);">
  {#each ['overview', 'vision', 'runs'] as t}
    <button
      class="px-4 py-2.5 text-xs font-medium capitalize transition-colors cursor-pointer"
      style="{activeTab === t ? 'color: var(--color-primary); border-bottom: 2px solid var(--color-violet);' : 'color: var(--color-tertiary); border-bottom: 2px solid transparent;'}"
      onclick={() => activeTab = t}
    >{t}</button>
  {/each}
</div>
```

- [ ] **Step 3: Wrap existing content under `overview` and add `vision` tab**

```svelte
{#if activeTab === 'overview'}
  <!-- ↓ existing Configuration + Recent Runs cards here ↓ -->
{:else if activeTab === 'vision'}
  <VisionTab {project} />
{:else if activeTab === 'runs'}
  <!-- existing Recent Runs section -->
{/if}
```

(If "Recent Runs" was previously rendered alongside Configuration, split them between `overview` and `runs` tabs.)

- [ ] **Step 4: Wire the optional `?tab=vision` query param**

If the page accepts query params via the existing router, allow `tab=vision` to set `activeTab = 'vision'` on mount. The pattern from `SettingsPage.svelte` already handles this with `let { tab = null }: { tab?: string | null } = $props();` — replicate.

- [ ] **Step 5: Smoke test**

In the browser, navigate to `/projects/<id>` and click the **Vision** tab. Confirm empty state if no `docs/vision.md` exists; click **Start vision chat** and verify the chat opens inline.

- [ ] **Step 6: Commit**

```bash
git add dashboard/frontend/src/pages/ProjectDetail.svelte
git commit -m "feat(vision): Project Detail tabs with Vision tab"
```

---

## Task 1.24: Phase 1 e2e smoke

Manually verify the full flow before declaring Phase 1 done:

- [ ] **Step 1: Pick a real test project repo on GitHub** (you can use a sandbox). Confirm the GitHub App is installed on it with `Contents: Write` permission.

- [ ] **Step 2: Run** the full stack via `docker compose up -d --build` (or local `uvicorn` + `npm run dev`).

- [ ] **Step 3: Add the project** via the wizard. In step 2, hold a brief conversation with Claude. Confirm:
  - Streaming text appears in the transcript.
  - The coverage checklist updates as sections get covered.
  - Phase changes from `freeform` to `structured` after enough context.
  - Eventually **Approve & commit** becomes enabled.

- [ ] **Step 4: Approve & commit.** Confirm the toast says "Vision saved to GitHub", then check the project repo on GitHub and verify `docs/vision.md` exists with all seven H2 sections.

- [ ] **Step 5: Refresh the page** → Project Detail → Vision tab. Confirm the read view shows the rendered markdown.

- [ ] **Step 6: Click Refine via chat.** Confirm the chat reopens with the current vision pre-loaded and the system prompt asks what to change.

- [ ] **Step 7: Commit a checkpoint marker (no code change)**

```bash
git commit --allow-empty -m "chore(vision): Phase 1 — e2e verified, foundation done"
```

**[OK] Phase 1 done.**

---

# Phase 2 — Issue prioritisation (Hook 1)

Adds vision-aware ranking to the orchestrator's issue selection. No UI change.

## Task 2.1: `agent/vision.py` — load + parse vision file

**Files:**
- Create: `agent/vision.py`
- Test: `dashboard/backend/tests/test_agent_vision.py` (tests live in the backend's pytest tree because that's where pytest is configured; agent code is on PYTHONPATH)

- [ ] **Step 1: Write the failing tests**

```python
# dashboard/backend/tests/test_agent_vision.py
import os
import tempfile
from agent.vision import load_vision

SAMPLE = """\
# Vision — owner/repo

*Last refined: 2026-05-07T12:00:00+00:00 via Claude Station*

## Problem
The pain.

## Users
The audience.

## End-state
Done looks like this.

## Non-goals
Out of scope.

## Principles
How to choose.

## Horizons
Near, mid, long.

## Anti-patterns
Bad shapes.
"""


def test_load_vision_parses_all_seven_sections(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "vision.md").write_text(SAMPLE)
    v = load_vision(str(workspace))
    assert v is not None
    assert v["problem"].strip() == "The pain."
    assert v["non_goals"].strip() == "Out of scope."
    assert v["anti_patterns"].strip() == "Bad shapes."


def test_load_vision_returns_none_when_missing(tmp_path):
    assert load_vision(str(tmp_path)) is None


def test_load_vision_tolerates_partial_sections(tmp_path):
    """If some H2s are missing, return what we got and log a warning."""
    partial = "# Vision — o/r\n\n## Problem\nP\n\n## Users\nU\n"
    workspace = tmp_path / "ws"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "vision.md").write_text(partial)
    v = load_vision(str(workspace))
    assert v is not None
    assert v["problem"] == "P"
    assert v["users"] == "U"
    # Missing sections present as empty strings (not absent keys)
    assert v["end_state"] == ""
```

- [ ] **Step 2: Run and confirm fail**

```bash
cd dashboard/backend && pytest tests/test_agent_vision.py -v
```

- [ ] **Step 3: Implement the loader**

```python
# agent/vision.py
"""Load and parse docs/vision.md from a project workspace.

Returns a dict shaped like the VisionDoc Pydantic model in the dashboard.
Missing file → None. Missing sections → empty strings (orchestrator hooks
treat empty sections as "no signal").
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

SECTIONS = [
    ("problem", "Problem"),
    ("users", "Users"),
    ("end_state", "End-state"),
    ("non_goals", "Non-goals"),
    ("principles", "Principles"),
    ("horizons", "Horizons"),
    ("anti_patterns", "Anti-patterns"),
]
_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def load_vision(workspace_dir: str) -> dict[str, Any] | None:
    """Return the parsed vision doc, or None if docs/vision.md is missing."""
    path = os.path.join(workspace_dir, "docs", "vision.md")
    if not os.path.isfile(path):
        return None

    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        logger.warning("could not read vision file %s: %s", path, e)
        return None

    sections: dict[str, str] = {key: "" for key, _ in SECTIONS}

    # Find each H2 heading and capture text until the next H2 or EOF
    matches = list(_HEADING_RE.finditer(text))
    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end].strip()
        # Match heading to canonical section key
        for key, label in SECTIONS:
            if heading.lower() == label.lower():
                sections[key] = body
                break

    return sections
```

- [ ] **Step 4: Run and confirm pass**

```bash
pytest tests/test_agent_vision.py -v
```

- [ ] **Step 5: Commit**

```bash
git add agent/vision.py dashboard/backend/tests/test_agent_vision.py
git commit -m "feat(vision-hook1): load_vision helper for orchestrator"
```

---

## Task 2.2: Vision-aware issue scoring

**Files:**
- Create: `agent/vision_scoring.py`
- Test: `dashboard/backend/tests/test_vision_scoring.py`

- [ ] **Step 1: Write the failing tests**

```python
# dashboard/backend/tests/test_vision_scoring.py
import json
import pytest
from unittest.mock import patch, MagicMock
from agent.vision_scoring import score_issues_against_vision

VISION = {
    "problem": "Self-host a Claude agent.",
    "users": "Solo devs.",
    "end_state": "A daily autonomous agent.",
    "non_goals": "Multi-tenant.",
    "principles": "Solo simplicity.",
    "horizons": "Near-term: stability.",
    "anti_patterns": "Enterprise complexity.",
}


def test_score_issues_returns_input_with_added_fields():
    issues = [
        {"number": 1, "title": "Add daily run", "body": ""},
        {"number": 2, "title": "Add SSO", "body": ""},
    ]
    fake_response = json.dumps([
        {"number": 1, "score": 0.9, "why": "advances daily autonomy"},
        {"number": 2, "score": 0.1, "why": "violates non-goal"},
    ])

    with patch("agent.vision_scoring._call_model", return_value=fake_response):
        scored = score_issues_against_vision(issues, VISION, model="claude-sonnet-4-6")

    by_num = {i["number"]: i for i in scored}
    assert by_num[1]["vision_score"] == 0.9
    assert by_num[2]["vision_score"] == 0.1
    assert "advances daily autonomy" in by_num[1]["vision_reason"]


def test_score_issues_falls_back_to_neutral_on_model_error():
    issues = [{"number": 1, "title": "x", "body": ""}]
    with patch("agent.vision_scoring._call_model", side_effect=RuntimeError("nope")):
        scored = score_issues_against_vision(issues, VISION, model="claude-sonnet-4-6")
    assert scored[0]["vision_score"] == 0.5
    assert scored[0]["vision_reason"] == ""


def test_score_issues_falls_back_on_malformed_json():
    issues = [{"number": 1, "title": "x", "body": ""}]
    with patch("agent.vision_scoring._call_model", return_value="not json"):
        scored = score_issues_against_vision(issues, VISION, model="claude-sonnet-4-6")
    assert scored[0]["vision_score"] == 0.5
```

- [ ] **Step 2: Run and confirm fail**

```bash
pytest tests/test_vision_scoring.py -v
```

- [ ] **Step 3: Implement the scorer**

```python
# agent/vision_scoring.py
"""Hook 1: vision-aware issue prioritisation.

One LLM call per orchestrator run. Falls back to neutral 0.5 on any
failure so the orchestrator still runs with label-only priority.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


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

## Principles
{principles}

## Horizons
{horizons}

## Anti-patterns
{anti_patterns}

# Issues to score

{issues_block}

# Task

For each issue, output a score in [0, 1] (higher = more aligned with the
vision) plus a one-sentence reason. Output ONLY a JSON array, no prose:

[{{"number": <int>, "score": <float>, "why": "<one sentence>"}}]
"""


def _format_issues(issues: list[dict]) -> str:
    parts = []
    for issue in issues:
        body = (issue.get("body") or "")[:500]
        parts.append(f"## #{issue['number']}: {issue.get('title', '')}\n{body}")
    return "\n\n".join(parts)


def _call_model(prompt: str, model: str) -> str:
    """Invoke the bundled `claude` CLI for one-shot inference.

    Uses --print mode (no streaming) since we just need the final JSON.
    """
    proc = subprocess.run(
        ["claude", "--print", "--model", model, "--no-session-persistence",
         "--dangerously-skip-permissions", prompt],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def score_issues_against_vision(
    issues: list[dict], vision: dict, model: str,
) -> list[dict]:
    """Add vision_score (0-1) and vision_reason fields to each issue.

    On any failure, all issues get vision_score=0.5 (neutral) so the
    orchestrator's combined ranking falls back to pure priority labels.
    """
    if not issues:
        return issues

    prompt = _PROMPT_TEMPLATE.format(
        issues_block=_format_issues(issues),
        **vision,
    )

    try:
        raw = _call_model(prompt, model)
    except Exception as e:
        logger.warning("vision scoring model call failed: %s", e)
        return [{**i, "vision_score": 0.5, "vision_reason": ""} for i in issues]

    # Strip code fences if the model added them
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.rstrip("` \n")

    try:
        scored = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("vision scoring response not JSON: %s", e)
        return [{**i, "vision_score": 0.5, "vision_reason": ""} for i in issues]

    score_by_num = {item["number"]: item for item in scored if isinstance(item, dict)}
    out = []
    for issue in issues:
        match = score_by_num.get(issue["number"], {})
        out.append({
            **issue,
            "vision_score": float(match.get("score", 0.5)),
            "vision_reason": match.get("why", ""),
        })
    return out
```

- [ ] **Step 4: Run and confirm pass**

```bash
pytest tests/test_vision_scoring.py -v
```

- [ ] **Step 5: Commit**

```bash
git add agent/vision_scoring.py dashboard/backend/tests/test_vision_scoring.py
git commit -m "feat(vision-hook1): score_issues_against_vision"
```

---

## Task 2.3: Wire scoring into the orchestrator

**Files:**
- Modify: `agent/station_orchestrator.py:617` (after `fetch_eligible_issues`)
- Modify: `agent/station_orchestrator.py:170-175` (`PRIORITY_ORDER`) — make `N` referenceable
- Test: `dashboard/backend/tests/test_orchestrator_scoring.py`

- [ ] **Step 1: Write the failing test**

```python
# dashboard/backend/tests/test_orchestrator_scoring.py
"""Test that the orchestrator's combined ranking honours vision scores."""

import pytest
from unittest.mock import patch
from agent.station_orchestrator import _combined_rank_issues


VISION = {"problem": "P", "users": "U", "end_state": "E", "non_goals": "N",
          "principles": "Pr", "horizons": "H", "anti_patterns": "A"}


def test_combined_rank_no_vision_preserves_priority_order():
    issues = [
        {"number": 1, "title": "x", "body": "", "labels": [{"name": "low"}]},
        {"number": 2, "title": "x", "body": "", "labels": [{"name": "critical"}]},
    ]
    out = _combined_rank_issues(issues, vision=None, weight=0.4, model="m")
    assert out[0]["number"] == 2  # critical first


def test_combined_rank_vision_can_promote_aligned_issue():
    issues = [
        {"number": 1, "title": "x", "body": "", "labels": []},  # unlabeled
        {"number": 2, "title": "x", "body": "", "labels": [{"name": "high"}]},
    ]
    fake_scored = [
        {**issues[0], "vision_score": 0.95, "vision_reason": "very aligned"},
        {**issues[1], "vision_score": 0.20, "vision_reason": "off-mission"},
    ]
    with patch("agent.station_orchestrator.score_issues_against_vision",
               return_value=fake_scored):
        out = _combined_rank_issues(issues, vision=VISION, weight=0.6, model="m")
    # vision-strong (0.95) should win over priority-strong (high) when w=0.6
    assert out[0]["number"] == 1
```

- [ ] **Step 2: Run and confirm fail**

```bash
pytest tests/test_orchestrator_scoring.py -v
```

- [ ] **Step 3a: Promote `priority_key` to module level**

Currently `priority_key` is a nested function inside `fetch_eligible_issues` (around line 183). Move it to module scope so the new combined-rank helper can reference it.

In `agent/station_orchestrator.py`, just below the `PRIORITY_ORDER = { ... }` block (around line 89), add:

```python
def priority_key(issue: dict) -> int:
    """Return the priority rank for an issue (lower = higher priority)."""
    for label in issue.get("labels", []) or []:
        name = label.get("name", "")
        if name in PRIORITY_ORDER:
            return PRIORITY_ORDER[name]
    return len(PRIORITY_ORDER)  # unlabeled = lowest
```

Then delete the nested `def priority_key(...)` inside `fetch_eligible_issues` (around lines 183–188); `eligible.sort(key=priority_key)` will pick up the module-level one automatically.

- [ ] **Step 3b: Add the combined-rank helper to the orchestrator**

Below the new module-level `priority_key`, add:

```python
from agent.vision import load_vision  # noqa: E402
from agent.vision_scoring import score_issues_against_vision  # noqa: E402


def _combined_rank_issues(
    issues: list[dict],
    vision: dict | None,
    weight: float,
    model: str,
) -> list[dict]:
    """Combine label-priority and vision-alignment into a single sort.

    No vision (or weight=0) → pure priority. Returns issues with
    vision_score / vision_reason fields (0.5 / "" when no vision).
    """
    N = len(PRIORITY_ORDER)  # number of priority labels
    if not issues:
        return issues

    if vision is None or weight <= 0:
        scored = [{**i, "vision_score": 0.5, "vision_reason": ""} for i in issues]
        weight = 0.0
    else:
        scored = score_issues_against_vision(issues, vision, model)

    def combined(issue: dict) -> float:
        # priority_label_rank: 0=critical … N-1=unlabeled. Convert to score:
        # 1.0 for critical, 0.0 for unlabeled.
        rank = priority_key(issue)  # 0..N (or N if no label)
        prio_score = 1.0 - (min(rank, N - 1) / max(N - 1, 1))
        v = float(issue.get("vision_score", 0.5))
        return prio_score * (1.0 - weight) + v * weight

    return sorted(scored, key=combined, reverse=True)
```

- [ ] **Step 4: Modify `orchestrate()` to use it**

Find `agent/station_orchestrator.py:617` (the `issues = fetch_eligible_issues(...)` call) and replace its body with:

```python
        # Fetch and filter issues
        issues = fetch_eligible_issues(repo, max_per_project, workspace)
        if not issues:
            logger.info("No eligible issues for %s, skipping", repo)
            continue

        # Hook 1: vision-aware prioritisation
        vision = load_vision(workspace)
        weight = float((config.get("vision") or {}).get("scoring_weight", 0.4))
        analyst_model = get_model(config, "analyst", "claude-sonnet-4-6")
        issues = _combined_rank_issues(issues, vision=vision, weight=weight, model=analyst_model)

        if vision is not None:
            for issue in issues:
                logger.info(
                    "Picked #%s (vision_score=%.2f): %s",
                    issue["number"], issue.get("vision_score", 0.5), issue.get("vision_reason", ""),
                )
```

- [ ] **Step 5: Run the test and confirm it passes**

```bash
pytest tests/test_orchestrator_scoring.py -v
```

- [ ] **Step 6: Commit**

```bash
git add agent/station_orchestrator.py dashboard/backend/tests/test_orchestrator_scoring.py
git commit -m "feat(vision-hook1): combined-rank ordering in orchestrator"
```

---

## Task 2.4: Inject "why this was picked" into team prompt

**Files:**
- Modify: `agent/station_orchestrator.py:199-230` (`build_team_prompt`)

- [ ] **Step 1: Modify the prompt builder**

In `build_team_prompt`, change the `issue_entries` build (around line 208):

```python
    issue_entries = []
    for issue in issues:
        labels_str = ", ".join(l.get("name", "") for l in issue.get("labels", []))
        why = issue.get("vision_reason", "")
        line = f"- **#{issue['number']}**: {issue.get('title', 'Untitled')}"
        if labels_str:
            line += f" [{labels_str}]"
        if why:
            line += f"\n    *Why this advances the vision:* {why}"
        issue_entries.append(line)
    issue_list = "\n".join(issue_entries)
```

- [ ] **Step 2: Smoke test**

Trigger a run on a project with a vision and inspect the orchestrator's stream log:

```bash
docker exec cas-agent ls /var/log/claude-agent/run-*-orchestrator.stream.jsonl | tail -1
docker exec cas-agent grep "Why this advances the vision" $(...)
```

The lead's prompt (visible in the early `system` events) should include the per-issue why lines.

- [ ] **Step 3: Commit**

```bash
git add agent/station_orchestrator.py
git commit -m "feat(vision-hook1): inject vision reasons into team prompt"
```

---

## Task 2.5: Phase 2 e2e + checkpoint

- [ ] **Step 1: With a vision committed (from Phase 1), trigger a run.** Confirm the run log shows "Picked #N (vision_score=…)" lines for each issue, and the order matches the scores.

- [ ] **Step 2: Toggle scoring weight.** Set `config.vision.scoring_weight = 0.0` in the dashboard config, trigger again — issues should reorder back to pure label priority.

- [ ] **Step 3: Checkpoint commit**

```bash
git commit --allow-empty -m "chore(vision): Phase 2 — vision-aware prioritisation done"
```

**[OK] Phase 2 done.**

---

# Phase 3 — Misalignment flag (Hook 2)

Adds vision-check instructions to the lead's prompt and wires a new webhook event.

## Task 3.1: Inject vision-check section into team prompt

**Files:**
- Modify: `agent/station_orchestrator.py:199-310` (`build_team_prompt`)

- [ ] **Step 1: Modify the prompt builder signature to accept a vision**

```python
def build_team_prompt(
    repo: str,
    issues: list[dict],
    config: dict,
    run_id: str,
    workspace: str = "",
    worktree_paths: dict[str, str] | None = None,
    vision: dict | None = None,  # ← new
) -> str:
```

- [ ] **Step 2: Build the vision-check block**

Just before the final `return f"""..."""`:

```python
    vision_section = ""
    if vision is not None:
        non_goals = (vision.get("non_goals") or "").strip() or "_(not specified)_"
        anti_patterns = (vision.get("anti_patterns") or "").strip() or "_(not specified)_"
        vision_section = f"""
## Vision check (when reviewing teammate plans)

This project has a vision. Before approving ANY teammate plan, verify the
plan does not violate the non-goals or anti-patterns below. If it does:

1. Reject the plan with a specific quote from the violated section.
2. Apply label `autonomous-agent/needs-help` to the issue:
   `gh issue edit <number> --add-label autonomous-agent/needs-help`
3. POST a misalignment event to the dashboard:
   `curl -s -X POST http://dashboard:8420/api/webhook/run-event \\
       -H "Content-Type: application/json" \\
       -d '{{"event":"vision_misalignment","run_id":"run-{run_id}",
            "issue_number":<number>,"violated_section":"<non_goals|anti_patterns>",
            "quote":"<exact quote>","plan_excerpt":"<short excerpt>"}}'`
4. Reassign the teammate to a different task or stop them.

### Vision — Non-goals
{non_goals}

### Vision — Anti-patterns
{anti_patterns}

(Full vision available at `{workspace}/docs/vision.md` if you need other context.)
"""
```

- [ ] **Step 3: Append it to the returned prompt**

In the f-string near the end, add `{vision_section}` after the existing `## Issues to Work On` section.

- [ ] **Step 4: Update the call site**

In `orchestrate()` (around line 736):

```python
                        prompt = build_team_prompt(
                            repo, issues, config, run_id, workspace, worktree_paths,
                            vision=vision,  # ← pass it
                        )
```

- [ ] **Step 5: Commit**

```bash
git add agent/station_orchestrator.py
git commit -m "feat(vision-hook2): vision-check section in team prompt"
```

---

## Task 3.2: Webhook event handling for `vision_misalignment`

**Files:**
- Modify: `dashboard/backend/app/routers/webhook.py`
- Test: `dashboard/backend/tests/test_webhook_vision_misalignment.py`

- [ ] **Step 1: Write the failing test**

```python
# dashboard/backend/tests/test_webhook_vision_misalignment.py
import pytest
from httpx import AsyncClient
from app.main import app
from app.database import init_db, async_session_maker
from app.models import AgentEvent


@pytest.mark.asyncio
async def test_vision_misalignment_event_persists_to_agent_events():
    await init_db()
    async with AsyncClient(app=app, base_url="http://test") as c:
        r = await c.post("/api/webhook/run-event", json={
            "event": "vision_misalignment",
            "run_id": "run-test-001",
            "issue_number": 42,
            "violated_section": "non_goals",
            "quote": "Multi-tenant is out of scope.",
            "plan_excerpt": "I'll add tenant isolation…",
        })
    assert r.status_code in (200, 202)
    async with async_session_maker() as db:
        from sqlalchemy import select
        rows = (await db.execute(select(AgentEvent).where(AgentEvent.event_type == "vision_misalignment"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].run_id == "run-test-001"
```

- [ ] **Step 2: Run and confirm fail**

```bash
pytest tests/test_webhook_vision_misalignment.py -v
```

(May fail because the webhook router doesn't recognise the event type yet — depends on existing dispatch.)

- [ ] **Step 3: Add event handling**

Inspect `dashboard/backend/app/routers/webhook.py` for how existing event types are dispatched (typically a big `match` or `if/elif` chain). Add a branch for `vision_misalignment` that creates an `AgentEvent`:

```python
elif event == "vision_misalignment":
    db.add(AgentEvent(
        run_id=payload.get("run_id"),
        event_type="vision_misalignment",
        agent_id="lead",
        payload=json.dumps({
            "issue_number": payload.get("issue_number"),
            "violated_section": payload.get("violated_section"),
            "quote": payload.get("quote"),
            "plan_excerpt": payload.get("plan_excerpt"),
        }),
    ))
    await db.commit()
```

- [ ] **Step 4: Run the test and confirm it passes**

```bash
pytest tests/test_webhook_vision_misalignment.py -v
```

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/routers/webhook.py dashboard/backend/tests/test_webhook_vision_misalignment.py
git commit -m "feat(vision-hook2): persist vision_misalignment events"
```

---

## Task 3.3: Render misalignment in the run timeline

**Files:**
- Modify: `dashboard/frontend/src/pages/RunDetail.svelte` (or whichever page renders agent events)

- [ ] **Step 1: Locate the event-row rendering**

```bash
grep -n "event_type\|AgentEvent" dashboard/frontend/src/pages/RunDetail.svelte
```

- [ ] **Step 2: Add a case for `vision_misalignment`**

Inside the per-event renderer (a switch/match on `event.event_type`), add a branch:

```svelte
{:else if e.event_type === 'vision_misalignment'}
  <div class="card p-3" style="border-left: 3px solid #B06030;">
    <div class="flex items-center gap-2 text-xs text-[#B06030] font-semibold mb-1">
      ⚠ Vision misalignment — issue #{e.payload?.issue_number}
    </div>
    <div class="text-xs text-secondary mb-1">
      Violated: <code class="text-accent-orange">{e.payload?.violated_section}</code>
    </div>
    <blockquote class="text-xs text-tertiary italic border-l-2 border-tertiary/40 pl-2 my-1">
      "{e.payload?.quote}"
    </blockquote>
    <div class="text-xs text-tertiary">Plan excerpt: {e.payload?.plan_excerpt}</div>
  </div>
{/if}
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/frontend/src/pages/RunDetail.svelte
git commit -m "feat(vision-hook2): render vision_misalignment events in timeline"
```

---

## Task 3.4: Phase 3 e2e + checkpoint

- [ ] **Step 1: Write a contrived issue** that violates a stated non-goal. Trigger a run.

- [ ] **Step 2: Verify** that the lead rejects the teammate's plan, applies `autonomous-agent/needs-help`, and the dashboard shows a `vision_misalignment` row.

- [ ] **Step 3: Checkpoint commit**

```bash
git commit --allow-empty -m "chore(vision): Phase 3 — misalignment flagging done"
```

**[OK] Phase 3 done.**

---

# Phase 4 — Gap detection (Hook 3)

Adds an on-demand "Find gaps" agent that proposes new GitHub issues based on the vision + repo state. Compose-mode dispatch uses **option (b)**: a dedicated `/vision-analyst` endpoint on the launcher with its own slot.

## Task 4.1: `agent/vision_analyst.py` — the gap-finding agent

**Files:**
- Create: `agent/vision_analyst.py`
- Test: `dashboard/backend/tests/test_vision_analyst.py`

- [ ] **Step 1: Write the failing tests**

```python
# dashboard/backend/tests/test_vision_analyst.py
import json
import pytest
from unittest.mock import patch, MagicMock
from agent.vision_analyst import propose_gaps, format_proposal_body


VISION = {"problem": "P", "users": "U", "end_state": "E", "non_goals": "N",
          "principles": "Pr", "horizons": "H", "anti_patterns": "A"}


def test_propose_gaps_returns_parsed_proposals():
    fake = json.dumps([
        {"title": "Add daily digest", "body": "Send a daily summary email", "labels": ["feature"], "priority": "medium"},
        {"title": "Cron resilience", "body": "Retry failed cron runs", "labels": ["enhancement"], "priority": "high"},
    ])
    with patch("agent.vision_analyst._gather_repo_state", return_value={"tree": [], "readme": "", "commits": [], "open_issues": [], "closed_issues": []}):
        with patch("agent.vision_analyst._call_model", return_value=fake):
            proposals = propose_gaps(workspace="/x", vision=VISION, repo="o/r", model="m")
    assert len(proposals) == 2
    assert proposals[0]["title"] == "Add daily digest"


def test_propose_gaps_caps_at_5():
    huge = json.dumps([{"title": f"x{i}", "body": "", "labels": [], "priority": "low"} for i in range(20)])
    with patch("agent.vision_analyst._gather_repo_state", return_value={"tree": [], "readme": "", "commits": [], "open_issues": [], "closed_issues": []}):
        with patch("agent.vision_analyst._call_model", return_value=huge):
            proposals = propose_gaps(workspace="/x", vision=VISION, repo="o/r", model="m")
    assert len(proposals) <= 5


def test_format_proposal_body_includes_disclaimer():
    body = format_proposal_body("The feature explanation.")
    assert "Proposed by Claude Station" in body
    assert "vision-suggested" in body
    assert "The feature explanation." in body
```

- [ ] **Step 2: Run and confirm fail**

```bash
pytest tests/test_vision_analyst.py -v
```

- [ ] **Step 3: Implement the agent**

```python
# agent/vision_analyst.py
"""Hook 3: gap detection.

Analyses a project's vision against the current repo state and proposes
new GitHub issues to fill gaps. Issues land with the `vision-suggested`
label so the orchestrator's SKIP_LABELS prevents autonomous implementation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
from typing import Any

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

MAX_PROPOSALS = 5
DISCLAIMER = (
    "*Proposed by Claude Station based on the project vision. "
    "Review and accept by removing the `vision-suggested` label, "
    "or close to reject.*\n\n---\n\n"
)


def _gather_repo_state(workspace: str, repo: str) -> dict:
    """Snapshot the repo: file tree top-N, README, last 50 commits, issues."""
    state: dict[str, Any] = {"tree": [], "readme": "", "commits": [], "open_issues": [], "closed_issues": []}

    # File tree
    for root, _dirs, files in os.walk(workspace):
        if any(part.startswith(".") for part in os.path.relpath(root, workspace).split(os.sep)):
            continue
        for f in files:
            p = os.path.relpath(os.path.join(root, f), workspace)
            state["tree"].append(p)
            if len(state["tree"]) >= 200:
                break
        if len(state["tree"]) >= 200:
            break

    # README
    for fn in ("README.md", "README.rst", "README.txt"):
        p = os.path.join(workspace, fn)
        if os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                state["readme"] = f.read()[:5000]
            break

    # Recent commits
    try:
        result = subprocess.run(
            ["git", "-C", workspace, "log", "--oneline", "-50"],
            capture_output=True, text=True, timeout=15,
        )
        state["commits"] = [line for line in result.stdout.splitlines() if line]
    except Exception as e:
        logger.warning("git log failed: %s", e)

    # Issues via gh
    try:
        result = subprocess.run(
            ["gh", "issue", "list", "--repo", repo, "--state", "open", "--limit", "100",
             "--json", "number,title,labels"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            state["open_issues"] = json.loads(result.stdout)
        result = subprocess.run(
            ["gh", "issue", "list", "--repo", repo, "--state", "closed", "--limit", "100",
             "--json", "number,title"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            state["closed_issues"] = json.loads(result.stdout)
    except Exception as e:
        logger.warning("gh issue list failed: %s", e)

    return state


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

## Principles
{principles}

## Horizons
{horizons}

## Anti-patterns
{anti_patterns}

# Repo state

## File tree (sample)
{tree}

## README (truncated)
{readme}

## Recent commits
{commits}

## Open issues
{open_issues}

## Recently closed issues
{closed_issues}

# Task

Propose at most {max} new issues that would advance toward the vision. Skip
ideas that are already covered by existing open or closed issues. Skip
anything that violates a non-goal or anti-pattern.

Output ONLY a JSON array, no prose:

[{{"title": "...", "body": "...", "labels": ["..."], "priority": "low|medium|high|critical"}}]
"""


def _call_model(prompt: str, model: str) -> str:
    proc = subprocess.run(
        ["claude", "--print", "--model", model, "--no-session-persistence",
         "--dangerously-skip-permissions", prompt],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def format_proposal_body(body: str) -> str:
    """Wrap a proposal body with the user-facing disclaimer."""
    return DISCLAIMER + body.strip() + "\n"


def propose_gaps(workspace: str, vision: dict, repo: str, model: str) -> list[dict]:
    """Return a list of proposal dicts, capped at MAX_PROPOSALS. Empty on failure."""
    state = _gather_repo_state(workspace, repo)

    open_titles = [f"#{i['number']}: {i['title']}" for i in state["open_issues"]][:50]
    closed_titles = [f"#{i['number']}: {i['title']}" for i in state["closed_issues"]][:50]

    prompt = _PROMPT.format(
        max=MAX_PROPOSALS,
        problem=vision["problem"], users=vision["users"], end_state=vision["end_state"],
        non_goals=vision["non_goals"], principles=vision["principles"],
        horizons=vision["horizons"], anti_patterns=vision["anti_patterns"],
        tree="\n".join(state["tree"][:80]),
        readme=state["readme"][:3000],
        commits="\n".join(state["commits"][:30]),
        open_issues="\n".join(open_titles),
        closed_issues="\n".join(closed_titles),
    )

    try:
        raw = _call_model(prompt, model)
    except Exception as e:
        logger.error("vision_analyst model call failed: %s", e)
        return []

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.rstrip("` \n")
    try:
        proposals = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("vision_analyst response not JSON: %s", e)
        return []
    if not isinstance(proposals, list):
        return []
    return proposals[:MAX_PROPOSALS]


def create_proposed_issues(repo: str, proposals: list[dict]) -> list[int]:
    """Create issues via `gh`. Returns list of created issue numbers."""
    created = []
    for p in proposals:
        labels = ["vision-suggested"]
        priority = (p.get("priority") or "low").lower()
        if priority in ("low", "medium", "high", "critical"):
            labels.append(priority)
        labels.extend([l for l in (p.get("labels") or []) if l != "vision-suggested"])

        body = format_proposal_body(p.get("body") or "")
        cmd = ["gh", "issue", "create", "--repo", repo,
               "--title", p["title"], "--body", body,
               "--label", ",".join(labels)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                logger.warning("gh issue create failed: %s", result.stderr)
                continue
            # Output is like "https://github.com/o/r/issues/123"
            url = result.stdout.strip()
            num = int(url.rstrip("/").rsplit("/", 1)[1])
            created.append(num)
            logger.info("Proposed issue #%d: %s", num, p["title"])
        except Exception as e:
            logger.warning("gh issue create failed: %s", e)
    return created


def _ensure_workspace(workspace: str, repo: str) -> bool:
    """Clone the repo into workspace if not already present."""
    if os.path.isdir(os.path.join(workspace, ".git")):
        return True
    parent = os.path.dirname(workspace)
    name = os.path.basename(workspace)
    os.makedirs(parent, exist_ok=True)
    result = subprocess.run(
        ["gh", "repo", "clone", repo, name],
        cwd=parent, capture_output=True, text=True, timeout=120,
    )
    return result.returncode == 0


async def run_for_project(project_id: int) -> dict:
    """Entry point: load project from DB, run analyst, return summary."""
    # Late imports so this module can be imported from tests without DB setup
    from app.database import async_session_maker, init_db
    from app.models import Project
    from agent.vision import load_vision

    await init_db()
    async with async_session_maker() as db:
        project = await db.get(Project, project_id)
        if not project:
            return {"ok": False, "error": "project not found"}

    workspaces_dir = os.environ.get("STATION_WORKSPACES", "/var/lib/claude-agent-station/workspaces")
    name = project.repo.split("/")[-1]
    workspace = os.path.join(workspaces_dir, name)

    if not _ensure_workspace(workspace, project.repo):
        return {"ok": False, "error": f"could not clone {project.repo}"}

    vision = load_vision(workspace)
    if vision is None:
        return {"ok": False, "error": "no vision file at docs/vision.md"}

    model = os.environ.get("STATION_VISION_ANALYST_MODEL", "claude-sonnet-4-6")
    proposals = propose_gaps(workspace, vision, project.repo, model)
    if not proposals:
        return {"ok": True, "proposals": [], "created": []}

    created = create_proposed_issues(project.repo, proposals)
    return {"ok": True, "proposals": proposals, "created": created}


def _main():
    parser = argparse.ArgumentParser(description="Run vision-analyst gap detection for a project")
    parser.add_argument("--project-id", type=int, required=True)
    args = parser.parse_args()
    result = asyncio.run(run_for_project(args.project_id))
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    _main()
```

- [ ] **Step 4: Run and confirm pass**

```bash
pytest tests/test_vision_analyst.py -v
```

- [ ] **Step 5: Commit**

```bash
git add agent/vision_analyst.py dashboard/backend/tests/test_vision_analyst.py
git commit -m "feat(vision-hook3): vision_analyst gap-detection agent"
```

---

## Task 4.2: Add `vision-suggested` to orchestrator's SKIP_LABELS

**Files:**
- Modify: `agent/station_orchestrator.py:58-65` (`SKIP_LABELS`)

- [ ] **Step 1: Add the label**

```python
SKIP_LABELS = frozenset({
    "autonomous-agent/in-progress",
    "autonomous-agent/needs-help",
    "autonomous-agent/refined",
    "NO AI",
    "backlog",
    "wontfix",
    "vision-suggested",  # ← Hook 3: proposed by vision_analyst, awaits human acceptance
})
```

- [ ] **Step 2: Commit**

```bash
git add agent/station_orchestrator.py
git commit -m "feat(vision-hook3): skip vision-suggested issues until accepted"
```

---

## Task 4.3: Launcher gains `/vision-analyst` endpoint

**Files:**
- Modify: `agent/launcher.py`

- [ ] **Step 1: Add the dedicated slot + endpoint**

Append to `agent/launcher.py`:

```python
_current_analyst: subprocess.Popen | None = None


@app.get("/vision-analyst/status")
def vision_analyst_status() -> dict:
    running = _current_analyst is not None and _current_analyst.poll() is None
    return {
        "running": running,
        "pid": _current_analyst.pid if running else None,
        "exit_code": _current_analyst.returncode if (_current_analyst and not running) else None,
    }


@app.post("/vision-analyst")
def trigger_vision_analyst(
    project_id: int,
    x_launcher_token: str | None = Header(default=None),
) -> dict:
    """Spawn `python -m agent.vision_analyst --project-id <id>` detached."""
    global _current_analyst

    if LAUNCHER_TOKEN and x_launcher_token != LAUNCHER_TOKEN:
        raise HTTPException(status_code=401, detail="invalid or missing launcher token")

    if _current_analyst is not None and _current_analyst.poll() is None:
        raise HTTPException(
            status_code=409,
            detail=f"vision-analyst already running (pid={_current_analyst.pid})",
        )

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"vision-analyst-{project_id}.out"
    log_fh = log_path.open("ab")

    env = os.environ.copy()
    gh_token = _fetch_gh_token()
    if gh_token:
        env["GH_TOKEN"] = gh_token

    _current_analyst = subprocess.Popen(
        ["python", "-m", "agent.vision_analyst", "--project-id", str(project_id)],
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=WORKDIR,
        env=env,
    )
    logger.info(
        "Spawned vision_analyst pid=%s, project_id=%s, log=%s, app_auth=%s",
        _current_analyst.pid, project_id, log_path, "yes" if gh_token else "no",
    )
    return {"status": "triggered", "pid": _current_analyst.pid, "log": str(log_path)}
```

- [ ] **Step 2: Commit**

```bash
git add agent/launcher.py
git commit -m "feat(vision-hook3): launcher /vision-analyst endpoint with separate slot"
```

---

## Task 4.4: Backend trigger endpoint

**Files:**
- Modify: `dashboard/backend/app/services/service_control.py`
- Modify: `dashboard/backend/app/routers/vision.py`
- Test: `dashboard/backend/tests/test_vision_router.py`

- [ ] **Step 1: Add a dispatch helper**

Append to `dashboard/backend/app/services/service_control.py`:

```python
async def start_vision_analyst(project_id: int) -> dict:
    """Trigger the vision_analyst (compose: launcher; systemd: transient unit)."""
    if _mode() == "compose":
        base = _launcher_base_url()
        if not base:
            return {"success": False, "error": "STATION_AGENT_LAUNCHER_URL not set"}
        url = f"{base.rstrip('/')}/vision-analyst?project_id={project_id}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, headers=_launcher_headers())
        except httpx.HTTPError as exc:
            return {"success": False, "error": f"launcher unreachable: {exc}", "status_code": 502}
        body = {}
        try: body = resp.json()
        except Exception: body = {"raw": resp.text}
        return {**body, "success": 200 <= resp.status_code < 300, "status_code": resp.status_code}

    # systemd: spawn a transient unit
    cmd = [
        "sudo", "systemd-run", "--unit", f"claude-agent-vision-analyst-{project_id}",
        "--user", os.environ.get("STATION_SERVICE_USER", "claude-agent"),
        "python", "-m", "agent.vision_analyst", "--project-id", str(project_id),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    return {
        "success": proc.returncode == 0,
        "stdout": proc.stdout, "stderr": proc.stderr,
        "status_code": 200 if proc.returncode == 0 else 500,
    }
```

- [ ] **Step 2: Add the endpoint**

Append to `dashboard/backend/app/routers/vision.py`:

```python
from app.services import service_control


@router.post("/{project_id}/vision/find-gaps")
async def find_gaps(project_id: int, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    if not project.vision_cached_body:
        raise HTTPException(status_code=400, detail="project has no vision yet")

    result = await service_control.start_vision_analyst(project_id)
    if not result.get("success"):
        raise HTTPException(
            status_code=result.get("status_code") or 500,
            detail=result.get("error") or result.get("stderr") or "failed to start vision-analyst",
        )
    return {"status": "triggered", **{k: v for k, v in result.items() if k not in {"success", "status_code"}}}
```

- [ ] **Step 3: Write a smoke test**

Append to `tests/test_vision_router.py`:

```python
@pytest.mark.asyncio
async def test_find_gaps_calls_service_control(project):
    async with async_session_maker() as db:
        proj = await db.get(Project, project.id)
        proj.vision_cached_body = "# Vision — o/r\n\n## Problem\nP\n"
        await db.commit()

    async with AsyncClient(app=app, base_url="http://test") as c:
        with patch("app.services.service_control.start_vision_analyst",
                   new=AsyncMock(return_value={"success": True, "status_code": 200, "pid": 99})):
            r = await c.post(f"/api/projects/{project.id}/vision/find-gaps")
    assert r.status_code == 200
    assert r.json()["status"] == "triggered"
```

- [ ] **Step 4: Run and confirm pass**

```bash
pytest tests/test_vision_router.py -v -k find_gaps
```

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/services/service_control.py dashboard/backend/app/routers/vision.py dashboard/backend/tests/test_vision_router.py
git commit -m "feat(vision-hook3): /find-gaps endpoint dispatching to launcher"
```

---

## Task 4.5: Frontend — "Find gaps" button on Vision tab

**Files:**
- Modify: `dashboard/frontend/src/lib/api.ts`
- Modify: `dashboard/frontend/src/components/vision/VisionTab.svelte`

- [ ] **Step 1: Add the API client function**

Append to `dashboard/frontend/src/lib/api.ts`:

```typescript
export const findVisionGaps = (projectId: number) =>
  request<{ status: string; pid?: number; log?: string }>(`/api/projects/${projectId}/vision/find-gaps`, { method: 'POST' });
```

- [ ] **Step 2: Add the button**

In `VisionTab.svelte`, in the read state header strip (next to **Refine via chat**):

```svelte
<button type="button" onclick={findGaps} disabled={findingGaps}
        data-testid="vision-find-gaps-btn"
        class="btn btn-ghost btn-sm text-xs">
  {findingGaps ? 'Finding…' : 'Find gaps'}
</button>
```

And in the script block:

```typescript
import { findVisionGaps } from '../../lib/api';
import { toastSuccess, toastError } from '../../lib/toast.svelte';

let findingGaps = $state(false);

async function findGaps() {
  findingGaps = true;
  try {
    await findVisionGaps(project.id);
    toastSuccess('Gap analysis started — proposed issues will appear on GitHub shortly');
  } catch (e: any) {
    toastError(e.message);
  } finally {
    findingGaps = false;
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/frontend/src/lib/api.ts dashboard/frontend/src/components/vision/VisionTab.svelte
git commit -m "feat(vision-hook3): Find gaps button on Vision tab"
```

---

## Task 4.6: Phase 4 e2e + final checkpoint

- [ ] **Step 1: With a vision committed and a real project,** click **Find gaps**. Wait ~30 s.

- [ ] **Step 2: Verify** that 1–5 new issues appear on GitHub with the `vision-suggested` label and the disclaimer body.

- [ ] **Step 3: Trigger an orchestrator run.** Confirm the `vision-suggested` issues are NOT picked up (they're in `SKIP_LABELS`). Remove the label from one issue, trigger again, confirm it now gets picked up and ranked normally.

- [ ] **Step 4: Final checkpoint commit**

```bash
git commit --allow-empty -m "chore(vision): Phase 4 — gap detection done; all four phases complete"
```

**[OK] Phase 4 done. All four phases complete.**

---

# Self-review

After implementing, run a final pass against the spec:

- [ ] **Spec coverage:** open `docs/superpowers/specs/2026-05-07-project-vision-design.md` side-by-side with the diff. For each section/requirement in the spec, verify a task implemented it. Common gaps to watch for:
  - SDK resume smoke test ran (Task 1.16) — if it failed, transcript-replay fallback in `vision_chat.run_chat_turn` was wired up.
  - 409 envelope shape (`{code, current_sha, current_body}`) verified in Task 1.12 test.
  - SSE proxy headers present in `vision.py` chat endpoint (Task 1.13).
  - Cleanup loop deletes 30-day-old `approved`/`cancelled` rows (Task 1.15).

- [ ] **Linter / typecheck:**

```bash
cd dashboard/backend && pip install -r requirements.txt && pytest -q
cd dashboard/frontend && npx svelte-check && npx vitest run
```

- [ ] **Final spec self-review:** check for placeholders, contradictions, and any task whose code references symbols not defined in earlier tasks.

---

# Notes for the implementer

- **TDD discipline:** the test-first ordering in every task is deliberate. If you find yourself writing implementation before the test, stop and write the test.
- **Commit cadence:** every task ends with a commit. Don't roll up commits across tasks.
- **`docker compose up -d --build`** is the only way to test agent-side changes (the agent image bakes the script). The dashboard image bakes the SPA.
- **Don't change the agent's `claude_agent_sdk` import path or model defaults** — those align with the existing TOS-compliant flow.
- **Phase 4 systemd-mode dispatch** (the `systemd-run` branch in `service_control.start_vision_analyst`) is best-effort — verify it on a real systemd host before relying on it. The compose path via the launcher is the primary tested route.
