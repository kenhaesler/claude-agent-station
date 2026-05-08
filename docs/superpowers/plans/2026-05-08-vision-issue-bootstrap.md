# Vision-driven Issue Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-fire `vision_analyst` from two trigger points (orchestrator on empty backlog; vision commit with content-hash change) and surface the activity as a distinct `vision-bootstrap` run type in the dashboard.

**Architecture:** No new worker. Reuse `agent/vision_analyst.py`, `agent/launcher.py:/vision-analyst`, and `service_control.start_vision_analyst`. Add a `vision-bootstrap` value for `Run.mode`, four nullable DB columns, two trigger sites, one new endpoint, and four UI surfaces. Vision-analyst worker self-registers as a `Run` row by calling the existing `/api/webhook/run-event` endpoint.

**Tech Stack:** Python 3.11+ / FastAPI / SQLite / SQLAlchemy / pytest / pytest-asyncio / httpx (orchestrator → launcher); Svelte 5 / TypeScript / Vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-05-08-vision-issue-bootstrap-design.md`

**Branch:** `feature/vision-bootstrap` (already created; spec already committed).

---

## File map

### Backend

| File | Change |
|---|---|
| `dashboard/backend/app/database.py` | Append 4 ALTER TABLE entries to `migrations` list |
| `dashboard/backend/app/models.py` | Add 4 columns: `Run.skip_reason`, `Run.vision_bootstrap_count`, `Run.vision_bootstrap_proposals`, `Project.last_vision_analyzed_sha` |
| `dashboard/backend/app/schemas.py` | Extend `WebhookRunEvent` with the 3 new run fields; extend `RunRead` with `skip_reason`, `vision_bootstrap_count`; add `VisionProposalsRead` |
| `dashboard/backend/app/services/run_lifecycle.py` | `handle_finished` persists `vision_bootstrap_count` + `vision_bootstrap_proposals`; `handle_started` no change (already persists `mode`) |
| `dashboard/backend/app/routers/runs.py` | `RunRead` mapping carries the new fields out; existing `?mode=` filter already works for new value |
| `dashboard/backend/app/routers/vision.py` | Hook in `commit_vision` (Trigger B); new `GET /{project_id}/vision/proposals` endpoint |
| `agent/vision_analyst.py` | `run_for_project` posts `started` + `finished` webhooks with the new fields |
| `agent/station_orchestrator.py` | Replace lines 932–934 (`No eligible issues, skipping`) with dispatch logic + skip-reason webhook |

### Frontend

| File | Change |
|---|---|
| `dashboard/frontend/src/lib/types.ts` | Add `skip_reason` and `vision_bootstrap_count` to `Run`; add `VisionProposals` type |
| `dashboard/frontend/src/lib/api.ts` | Add `getVisionProposals(projectId)` |
| `dashboard/frontend/src/lib/format.ts` | Add `formatRunMode(mode)` returning `{ icon, label, accent }` |
| `dashboard/frontend/src/components/runs/RunStatus.svelte` (new, small) | Renders mode-aware icon + label; consumed by Runs list, Run detail, Mission Control |
| `dashboard/frontend/src/pages/RunsPage.svelte` | Render `skip_reason` hint line under the row when present |
| `dashboard/frontend/src/components/vision/VisionTab.svelte` | New "Vision analyst" info strip above existing UI |
| `dashboard/frontend/src/lib/format.test.ts` | Tests for `formatRunMode` |
| `dashboard/frontend/src/components/vision/VisionTab.test.ts` (new) | Smoke test for the info strip rendering |

### Docs

| File | Change |
|---|---|
| `docs/configuration.md` | Document `STATION_VISION_ANALYST_MODEL` env var; new triggers section under "Project config" |
| `docs/architecture.md` | One paragraph + run-type entry for `vision-bootstrap` |

---

## Task 1: DB schema migration + ORM columns

**Files:**
- Modify: `dashboard/backend/app/models.py:40-77` (Run class), `:17-37` (Project class)
- Modify: `dashboard/backend/app/database.py:38-90` (migrations list)
- Test: `dashboard/backend/tests/test_migrations.py`

- [ ] **Step 1: Write the failing migration test**

Append to `dashboard/backend/tests/test_migrations.py`:

```python
@pytest.mark.asyncio
async def test_vision_bootstrap_columns_present():
    """The four vision-bootstrap columns must exist after migration."""
    from sqlalchemy import text
    from app.database import init_db, engine

    await init_db()
    async with engine.begin() as conn:
        runs_cols = {row[1] for row in (
            await conn.execute(text("PRAGMA table_info(runs)"))
        ).fetchall()}
        projects_cols = {row[1] for row in (
            await conn.execute(text("PRAGMA table_info(projects)"))
        ).fetchall()}

    assert "skip_reason" in runs_cols
    assert "vision_bootstrap_count" in runs_cols
    assert "vision_bootstrap_proposals" in runs_cols
    assert "last_vision_analyzed_sha" in projects_cols
```

- [ ] **Step 2: Run the test — must fail**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest tests/test_migrations.py::test_vision_bootstrap_columns_present -v
```
Expected: FAIL — columns missing.

- [ ] **Step 3: Add columns to ORM models**

In `dashboard/backend/app/models.py`, add inside the `Run` class (after `max_budget_usd` at line 77):

```python
    # Vision-bootstrap (spec 2026-05-08-vision-issue-bootstrap-design.md)
    skip_reason = Column(Text, nullable=True)
    vision_bootstrap_count = Column(Integer, nullable=True)
    vision_bootstrap_proposals = Column(Text, nullable=True)  # JSON list
```

Add inside `Project` class (after `vision_cached_at` at line 35):

```python
    last_vision_analyzed_sha = Column(Text, nullable=True, default=None)
```

- [ ] **Step 4: Add migration entries**

In `dashboard/backend/app/database.py`, append to the `migrations` list (after line 89):

```python
        # Vision-bootstrap columns (spec 2026-05-08-vision-issue-bootstrap-design.md)
        ("runs", "skip_reason", "ALTER TABLE runs ADD COLUMN skip_reason TEXT"),
        ("runs", "vision_bootstrap_count", "ALTER TABLE runs ADD COLUMN vision_bootstrap_count INTEGER"),
        ("runs", "vision_bootstrap_proposals", "ALTER TABLE runs ADD COLUMN vision_bootstrap_proposals TEXT"),
        ("projects", "last_vision_analyzed_sha", "ALTER TABLE projects ADD COLUMN last_vision_analyzed_sha TEXT"),
```

- [ ] **Step 5: Run the test — must pass**

```bash
pytest tests/test_migrations.py::test_vision_bootstrap_columns_present -v
```
Expected: PASS.

- [ ] **Step 6: Run full suite to ensure no regressions**

```bash
pytest -x --tb=short -q 2>&1 | tail -8
```
Expected: 891 passed (was 890), 1 skipped — same baseline plus the new test.

- [ ] **Step 7: Commit**

```bash
git add dashboard/backend/app/models.py dashboard/backend/app/database.py dashboard/backend/tests/test_migrations.py
git commit -m "feat(db): add vision-bootstrap columns to runs and projects

Adds Run.skip_reason, Run.vision_bootstrap_count,
Run.vision_bootstrap_proposals, Project.last_vision_analyzed_sha
per spec 2026-05-08-vision-issue-bootstrap-design.md."
```

---

## Task 2: Extend webhook schema and lifecycle to persist new fields

**Files:**
- Modify: `dashboard/backend/app/schemas.py` (find `WebhookRunEvent` and `RunRead`)
- Modify: `dashboard/backend/app/services/run_lifecycle.py:151-205` (`handle_finished`)
- Modify: `dashboard/backend/app/routers/runs.py` (`RunRead` field passthrough)
- Test: `dashboard/backend/tests/test_run_lifecycle.py`

- [ ] **Step 1: Find the schemas to extend**

```bash
grep -n "class WebhookRunEvent\|class RunRead" /home/simon/Documents/claude-agent-station/dashboard/backend/app/schemas.py
```

Read each class fully so the additions match the existing style (Optional vs `| None`, snake_case, etc.).

- [ ] **Step 2: Write the failing lifecycle test**

Add to `dashboard/backend/tests/test_run_lifecycle.py`:

```python
@pytest.mark.asyncio
async def test_handle_finished_persists_vision_bootstrap_fields(setup_db):
    from app.services.run_lifecycle import handle_finished
    from app.schemas import WebhookRunEvent
    from app.database import async_session
    from app.models import Run

    event = WebhookRunEvent(
        event="finished",
        run_id="run-vb-test-1",
        project="laboef1900/next-itsm",
        mode="vision-bootstrap",
        status="success",
        vision_bootstrap_count=3,
        vision_bootstrap_proposals=[
            {"number": 101, "title": "Add metrics dashboard", "url": "https://github.com/x/y/issues/101"},
            {"number": 102, "title": "Document API", "url": "https://github.com/x/y/issues/102"},
            {"number": 103, "title": "Add CI", "url": "https://github.com/x/y/issues/103"},
        ],
    )
    async with async_session() as db:
        run = await handle_finished(db, event, project_id=None, run=None)
        await db.commit()
        await db.refresh(run)
        assert run.mode == "vision-bootstrap"
        assert run.vision_bootstrap_count == 3
        proposals = json.loads(run.vision_bootstrap_proposals)
        assert len(proposals) == 3
        assert proposals[0]["number"] == 101
```

(Add `import json` at the top of the test file if missing.)

- [ ] **Step 3: Run the test — must fail**

```bash
pytest tests/test_run_lifecycle.py::test_handle_finished_persists_vision_bootstrap_fields -v
```
Expected: FAIL — `WebhookRunEvent` rejects unknown fields, or the values aren't persisted.

- [ ] **Step 4: Extend the schema**

In `dashboard/backend/app/schemas.py`, locate `WebhookRunEvent` and add (alphabetically grouped if the existing style is alphabetical, else at the bottom of the field list, before any model_config):

```python
    # Vision-bootstrap fields — spec 2026-05-08-vision-issue-bootstrap-design.md
    vision_bootstrap_count: int | None = None
    vision_bootstrap_proposals: list[dict] | None = None
    skip_reason: str | None = None
```

In the same file, locate `RunRead` and add the same three fields (with `int | None`, `list[dict] | None`, `str | None`).

- [ ] **Step 5: Persist in lifecycle**

In `dashboard/backend/app/services/run_lifecycle.py`, inside `handle_finished` after line 184 (`run.model = event.model or run.model`), add:

```python
    # Vision-bootstrap: only set when the event carries them so we don't
    # overwrite a regular run's NULLs with NULL-from-event.
    if event.vision_bootstrap_count is not None:
        run.vision_bootstrap_count = event.vision_bootstrap_count
    if event.vision_bootstrap_proposals is not None:
        run.vision_bootstrap_proposals = json.dumps(event.vision_bootstrap_proposals)
    if event.skip_reason is not None:
        run.skip_reason = event.skip_reason
```

(Verify `import json` is already at the top of `run_lifecycle.py` — it is, used for `employee_report`.)

- [ ] **Step 6: Map fields in the runs router response**

In `dashboard/backend/app/routers/runs.py`, find the `RunRead` construction (typically `RunRead.model_validate(run)` or a manual mapping in `list_runs` / `get_run`). If manual, add:

```python
    skip_reason=r.skip_reason,
    vision_bootstrap_count=r.vision_bootstrap_count,
    vision_bootstrap_proposals=json.loads(r.vision_bootstrap_proposals) if r.vision_bootstrap_proposals else None,
```

If `RunRead.model_validate(run)` is used, the new fields will pass through automatically — verify by reading the surrounding code.

- [ ] **Step 7: Run the test — must pass**

```bash
pytest tests/test_run_lifecycle.py::test_handle_finished_persists_vision_bootstrap_fields -v
```
Expected: PASS.

- [ ] **Step 8: Run the full suite**

```bash
pytest -x --tb=short -q 2>&1 | tail -8
```
Expected: 892 passed.

- [ ] **Step 9: Commit**

```bash
git add dashboard/backend/app/schemas.py dashboard/backend/app/services/run_lifecycle.py dashboard/backend/app/routers/runs.py dashboard/backend/tests/test_run_lifecycle.py
git commit -m "feat(api): persist vision-bootstrap fields on run-finished

Extends WebhookRunEvent + RunRead with skip_reason,
vision_bootstrap_count, vision_bootstrap_proposals.
handle_finished persists them when present."
```

---

## Task 3: Vision-analyst worker self-registers as a `vision-bootstrap` run

**Files:**
- Modify: `agent/vision_analyst.py:238-267` (`run_for_project`)
- Test: `dashboard/backend/tests/test_vision_analyst.py`

- [ ] **Step 1: Write the failing test**

Append to `dashboard/backend/tests/test_vision_analyst.py`:

```python
@pytest.mark.asyncio
async def test_run_for_project_posts_started_and_finished_webhooks(monkeypatch, tmp_path):
    """run_for_project must POST started + finished events with mode=vision-bootstrap."""
    from agent import vision_analyst as va

    posted = []

    def fake_post(url, json=None, headers=None, timeout=None):
        posted.append({"url": url, "json": json})
        class R:
            status_code = 200
            def raise_for_status(self): pass
        return R()

    monkeypatch.setattr(va.httpx, "post", fake_post, raising=False)
    monkeypatch.setattr(va, "_ensure_workspace", lambda w, r: True)
    monkeypatch.setattr(va, "load_vision", lambda w: {
        "problem": "p", "users": "u", "end_state": "e",
        "non_goals": "n", "principles": "pr", "horizons": "h",
        "anti_patterns": "a",
    })
    monkeypatch.setattr(va, "propose_gaps", lambda w, v, r, m: [
        {"title": "T1", "body": "B1", "priority": "low"},
    ])
    monkeypatch.setattr(va, "create_proposed_issues", lambda r, p: [101])
    monkeypatch.setenv("STATION_WEBHOOK_URL", "http://test/api/webhook/run-event")
    monkeypatch.setenv("STATION_WORKSPACES", str(tmp_path))

    # A Project row with id=1 needs to exist; reuse the test-db fixture pattern
    from app.database import async_session, init_db
    from app.models import Project
    await init_db()
    async with async_session() as db:
        db.add(Project(id=1, repo="x/y", branch="main"))
        await db.commit()

    result = await va.run_for_project(1)
    assert result["ok"] is True

    # Two webhook POSTs: started, finished
    assert len(posted) == 2
    started = posted[0]["json"]
    finished = posted[1]["json"]
    assert started["event"] == "started"
    assert started["mode"] == "vision-bootstrap"
    assert started["run_id"].startswith("run-vb-")
    assert finished["event"] == "finished"
    assert finished["mode"] == "vision-bootstrap"
    assert finished["status"] == "success"
    assert finished["vision_bootstrap_count"] == 1
    assert finished["vision_bootstrap_proposals"][0]["number"] == 101
```

- [ ] **Step 2: Run the test — must fail**

```bash
pytest tests/test_vision_analyst.py::test_run_for_project_posts_started_and_finished_webhooks -v
```
Expected: FAIL — `httpx` import missing or no webhook POSTs happen.

- [ ] **Step 3: Add webhook posting helper to `agent/vision_analyst.py`**

At the top of `agent/vision_analyst.py`, after `import sys`, add:

```python
import uuid

import httpx
```

After `DISCLAIMER` (around line 27), add:

```python
def _webhook_url() -> str | None:
    return os.environ.get("STATION_WEBHOOK_URL") or None


def _webhook_secret() -> str | None:
    return os.environ.get("STATION_WEBHOOK_SECRET") or None


def _post_webhook(payload: dict) -> None:
    """Best-effort POST to STATION_WEBHOOK_URL. Failures are logged, not raised."""
    url = _webhook_url()
    if not url:
        logger.info("vision_analyst: STATION_WEBHOOK_URL unset, skipping webhook")
        return
    headers = {}
    secret = _webhook_secret()
    if secret:
        headers["X-Webhook-Token"] = secret
    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=5.0)
        if resp.status_code >= 400:
            logger.warning("vision_analyst webhook %s -> %s: %s",
                           payload.get("event"), resp.status_code, resp.text[:200])
    except httpx.HTTPError as exc:
        logger.warning("vision_analyst webhook POST failed: %s", exc)
```

- [ ] **Step 4: Wrap `run_for_project` with webhook calls**

Replace the existing `run_for_project` body (lines 238–267) so it registers a run and reports back. The new flow:

```python
async def run_for_project(project_id: int) -> dict:
    """Entry point: load project from DB, run analyst, return summary.

    Self-registers as a `vision-bootstrap` Run via webhooks so the dashboard
    sees the activity in Mission Control + Runs list.
    """
    from app.database import async_session, init_db
    from app.models import Project
    from agent.vision import load_vision

    await init_db()
    async with async_session() as db:
        project = await db.get(Project, project_id)
        if not project:
            return {"ok": False, "error": "project not found"}
        repo = project.repo

    workspaces_dir = os.environ.get("STATION_WORKSPACES", "/var/lib/claude-agent-station/workspaces")
    name = repo.split("/")[-1]
    workspace = os.path.join(workspaces_dir, name)

    run_id = f"run-vb-{uuid.uuid4().hex[:12]}"
    _post_webhook({
        "event": "started",
        "run_id": run_id,
        "project": repo,
        "mode": "vision-bootstrap",
    })

    def _finish(status: str, **extra) -> None:
        _post_webhook({
            "event": "finished",
            "run_id": run_id,
            "project": repo,
            "mode": "vision-bootstrap",
            "status": status,
            **extra,
        })

    if not _ensure_workspace(workspace, repo):
        _finish("error")
        return {"ok": False, "error": f"could not clone {repo}"}

    vision = load_vision(workspace)
    if vision is None:
        _finish("error")
        return {"ok": False, "error": "no vision file at docs/vision.md"}

    model = os.environ.get("STATION_VISION_ANALYST_MODEL", "claude-sonnet-4-6")
    proposals = propose_gaps(workspace, vision, repo, model)
    if not proposals:
        _finish("success", vision_bootstrap_count=0, vision_bootstrap_proposals=[])
        return {"ok": True, "proposals": [], "created": []}

    created = create_proposed_issues(repo, proposals)
    proposal_records = [
        {
            "number": num,
            "title": p.get("title", ""),
            "url": f"https://github.com/{repo}/issues/{num}",
        }
        for num, p in zip(created, proposals)
    ]
    _finish(
        "success",
        vision_bootstrap_count=len(created),
        vision_bootstrap_proposals=proposal_records,
    )
    return {"ok": True, "proposals": proposals, "created": created}
```

- [ ] **Step 5: Run the test — must pass**

```bash
pytest tests/test_vision_analyst.py -v
```
Expected: all `test_vision_analyst.py` tests pass (existing + the new one).

- [ ] **Step 6: Run full suite**

```bash
pytest -x --tb=short -q 2>&1 | tail -8
```
Expected: 893 passed.

- [ ] **Step 7: Commit**

```bash
git add agent/vision_analyst.py dashboard/backend/tests/test_vision_analyst.py
git commit -m "feat(agent): vision_analyst self-registers as vision-bootstrap run

run_for_project now POSTs started + finished webhooks so the dashboard
sees the activity end-to-end. Adds STATION_WEBHOOK_URL +
STATION_WEBHOOK_SECRET integration matching run-manager.sh."
```

---

## Task 4: Helper — `has_open_vision_proposals`

**Files:**
- Modify: `agent/station_orchestrator.py` (add helper near `fetch_eligible_issues`)
- Test: `dashboard/backend/tests/test_orchestrator_wiring.py`

- [ ] **Step 1: Write the failing test**

Append to `dashboard/backend/tests/test_orchestrator_wiring.py`:

```python
def test_has_open_vision_proposals_true_when_label_present(monkeypatch):
    from agent.station_orchestrator import has_open_vision_proposals
    fake_stdout = '[{"number": 1, "labels": [{"name": "vision-suggested"}]}]'
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": fake_stdout, "stderr": ""}),
    )
    assert has_open_vision_proposals("x/y") is True


def test_has_open_vision_proposals_false_when_no_matches(monkeypatch):
    from agent.station_orchestrator import has_open_vision_proposals
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "[]", "stderr": ""}),
    )
    assert has_open_vision_proposals("x/y") is False


def test_has_open_vision_proposals_false_on_gh_failure(monkeypatch):
    from agent.station_orchestrator import has_open_vision_proposals
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: type("R", (), {"returncode": 1, "stdout": "", "stderr": "boom"}),
    )
    assert has_open_vision_proposals("x/y") is False
```

- [ ] **Step 2: Run the test — must fail**

```bash
pytest tests/test_orchestrator_wiring.py::test_has_open_vision_proposals_true_when_label_present -v
```
Expected: FAIL — function doesn't exist.

- [ ] **Step 3: Add the helper**

In `agent/station_orchestrator.py`, immediately after `fetch_eligible_issues` (after the `eligible[:limit]` return at ~line 233), add:

```python
def has_open_vision_proposals(repo: str) -> bool:
    """True if any open issue carries the `vision-suggested` label.

    Used to skip Trigger A when the user hasn't yet processed the prior
    batch of proposals. A `gh` failure returns False — fail-safe; we'd
    rather miss a dispatch than spam the operator.
    """
    cmd = [
        "gh", "issue", "list",
        "--repo", repo,
        "--state", "open",
        "--label", "vision-suggested",
        "--limit", "1",
        "--json", "number,labels",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            logger.warning("has_open_vision_proposals: gh failed: %s", result.stderr.strip())
            return False
        return len(json.loads(result.stdout or "[]")) > 0
    except Exception as exc:
        logger.warning("has_open_vision_proposals: %s", exc)
        return False
```

- [ ] **Step 4: Run the tests — must pass**

```bash
pytest tests/test_orchestrator_wiring.py -k has_open_vision_proposals -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/station_orchestrator.py dashboard/backend/tests/test_orchestrator_wiring.py
git commit -m "feat(orchestrator): has_open_vision_proposals helper

Returns True when any open issue carries vision-suggested label.
Used by Trigger A to skip dispatch when prior proposals are pending."
```

---

## Task 5: Helper — `dispatch_vision_bootstrap`

**Files:**
- Modify: `agent/station_orchestrator.py` (add helper near `has_open_vision_proposals`)
- Test: `dashboard/backend/tests/test_orchestrator_wiring.py`

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/backend/tests/test_orchestrator_wiring.py`:

```python
def test_dispatch_vision_bootstrap_returns_dispatched_on_200(monkeypatch):
    import httpx
    from agent.station_orchestrator import dispatch_vision_bootstrap
    class R:
        status_code = 200
        text = ""
    monkeypatch.setattr(httpx, "post", lambda *a, **k: R())
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://launcher:8421")
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "tok")
    assert dispatch_vision_bootstrap(42) == "dispatched"


def test_dispatch_vision_bootstrap_returns_already_running_on_409(monkeypatch):
    import httpx
    from agent.station_orchestrator import dispatch_vision_bootstrap
    class R:
        status_code = 409
        text = "vision-analyst already running"
    monkeypatch.setattr(httpx, "post", lambda *a, **k: R())
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://launcher:8421")
    assert dispatch_vision_bootstrap(42) == "already-running"


def test_dispatch_vision_bootstrap_falls_back_to_subprocess(monkeypatch):
    """When launcher is unreachable, spawn directly via subprocess."""
    import httpx
    from agent.station_orchestrator import dispatch_vision_bootstrap
    def boom(*a, **k):
        raise httpx.RequestError("connection refused")
    monkeypatch.setattr(httpx, "post", boom)
    spawned = []
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda cmd, *a, **k: spawned.append(cmd) or type("P", (), {"pid": 1234})(),
    )
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://launcher:8421")
    assert dispatch_vision_bootstrap(42) == "dispatched"
    assert spawned == [["python", "-m", "agent.vision_analyst", "--project-id", "42"]]
```

- [ ] **Step 2: Run the tests — must fail**

```bash
pytest tests/test_orchestrator_wiring.py -k dispatch_vision_bootstrap -v
```
Expected: 3 FAIL — function not defined.

- [ ] **Step 3: Add the helper**

At the top of `agent/station_orchestrator.py`, add (if not already present):

```python
import httpx
```

Below `has_open_vision_proposals`, add:

```python
def dispatch_vision_bootstrap(project_id: int) -> str:
    """Trigger a vision-analyst run for ``project_id``.

    Tries the in-container launcher first (compose-mode path) and falls
    back to spawning the worker via subprocess (systemd-mode path or any
    failure where the launcher is unreachable).

    Returns one of:
      - "dispatched"      — analyst was started
      - "already-running" — launcher reported 409
    """
    launcher_url = os.environ.get(
        "STATION_AGENT_LAUNCHER_URL", "http://localhost:8421",
    ).rstrip("/")
    token = os.environ.get("STATION_LAUNCHER_TOKEN", "")
    headers = {"X-Launcher-Token": token} if token else {}

    try:
        resp = httpx.post(
            f"{launcher_url}/vision-analyst",
            params={"project_id": project_id},
            headers=headers,
            timeout=5.0,
        )
        if resp.status_code == 409:
            logger.info("vision-analyst already running (409)")
            return "already-running"
        if 200 <= resp.status_code < 300:
            return "dispatched"
        logger.warning(
            "launcher /vision-analyst returned %s: %s",
            resp.status_code, resp.text[:200],
        )
    except httpx.RequestError as exc:
        logger.info("launcher unreachable (%s); falling back to subprocess", exc)

    # Fallback: spawn the worker directly. No cross-process lock; best
    # effort. systemd path or compose with launcher down.
    subprocess.Popen(
        ["python", "-m", "agent.vision_analyst", "--project-id", str(project_id)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    return "dispatched"
```

- [ ] **Step 4: Run the tests — must pass**

```bash
pytest tests/test_orchestrator_wiring.py -k dispatch_vision_bootstrap -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/station_orchestrator.py dashboard/backend/tests/test_orchestrator_wiring.py
git commit -m "feat(orchestrator): dispatch_vision_bootstrap helper

POSTs to in-container launcher with subprocess fallback for systemd.
Returns 'dispatched' or 'already-running' for the per-project loop."
```

---

## Task 6: Trigger A — orchestrator integrates the helpers

**Files:**
- Modify: `agent/station_orchestrator.py:931-934` (the `if not issues` skip block)
- Test: `dashboard/backend/tests/test_orchestrator_wiring.py`

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/backend/tests/test_orchestrator_wiring.py`:

```python
def test_handle_empty_backlog_dispatches_when_vision_and_no_proposals(monkeypatch, tmp_path):
    """Trigger A: dispatches and reports skip_reason=bootstrap-dispatched."""
    from agent import station_orchestrator as so

    # Create a fake workspace with docs/vision.md
    ws = tmp_path / "repo"
    ws.mkdir()
    (ws / "docs").mkdir()
    (ws / "docs" / "vision.md").write_text("## Problem\np\n## Users\nu\n## End-state\ne\n## Non-goals\nn\n## Principles\npr\n## Horizons\nh\n## Anti-patterns\na\n")

    monkeypatch.setattr(so, "has_open_vision_proposals", lambda r: False)
    dispatched = []
    monkeypatch.setattr(so, "dispatch_vision_bootstrap", lambda pid: dispatched.append(pid) or "dispatched")
    posted = []
    monkeypatch.setattr(so, "post_webhook", lambda cfg, ev, data: posted.append((ev, data)))

    skip_reason = so.handle_empty_backlog(
        config={}, repo="x/y", project_id=42, workspace=str(ws), run_id="r-1",
    )
    assert skip_reason == "no-eligible-issues-bootstrap-dispatched"
    assert dispatched == [42]
    # The orchestrator emits a finished webhook for the regular run row
    assert any(ev == "finished" and d.get("skip_reason") == skip_reason for ev, d in posted)


def test_handle_empty_backlog_no_vision(monkeypatch, tmp_path):
    from agent import station_orchestrator as so
    ws = tmp_path / "repo"
    ws.mkdir()  # no docs/vision.md
    monkeypatch.setattr(so, "has_open_vision_proposals", lambda r: False)
    dispatched = []
    monkeypatch.setattr(so, "dispatch_vision_bootstrap", lambda pid: dispatched.append(pid) or "dispatched")
    monkeypatch.setattr(so, "post_webhook", lambda *a, **k: None)
    skip_reason = so.handle_empty_backlog(
        config={}, repo="x/y", project_id=42, workspace=str(ws), run_id="r-1",
    )
    assert skip_reason == "no-eligible-issues-no-vision"
    assert dispatched == []


def test_handle_empty_backlog_proposals_pending(monkeypatch, tmp_path):
    from agent import station_orchestrator as so
    ws = tmp_path / "repo"
    ws.mkdir()
    (ws / "docs").mkdir()
    (ws / "docs" / "vision.md").write_text("## Problem\np\n## Users\nu\n## End-state\ne\n## Non-goals\nn\n## Principles\npr\n## Horizons\nh\n## Anti-patterns\na\n")
    monkeypatch.setattr(so, "has_open_vision_proposals", lambda r: True)
    dispatched = []
    monkeypatch.setattr(so, "dispatch_vision_bootstrap", lambda pid: dispatched.append(pid) or "dispatched")
    monkeypatch.setattr(so, "post_webhook", lambda *a, **k: None)
    skip_reason = so.handle_empty_backlog(
        config={}, repo="x/y", project_id=42, workspace=str(ws), run_id="r-1",
    )
    assert skip_reason == "no-eligible-issues-proposals-pending"
    assert dispatched == []


def test_handle_empty_backlog_already_running(monkeypatch, tmp_path):
    from agent import station_orchestrator as so
    ws = tmp_path / "repo"
    ws.mkdir()
    (ws / "docs").mkdir()
    (ws / "docs" / "vision.md").write_text("## Problem\np\n## Users\nu\n## End-state\ne\n## Non-goals\nn\n## Principles\npr\n## Horizons\nh\n## Anti-patterns\na\n")
    monkeypatch.setattr(so, "has_open_vision_proposals", lambda r: False)
    monkeypatch.setattr(so, "dispatch_vision_bootstrap", lambda pid: "already-running")
    monkeypatch.setattr(so, "post_webhook", lambda *a, **k: None)
    skip_reason = so.handle_empty_backlog(
        config={}, repo="x/y", project_id=42, workspace=str(ws), run_id="r-1",
    )
    assert skip_reason == "no-eligible-issues-bootstrap-already-running"
```

- [ ] **Step 2: Run the tests — must fail**

```bash
pytest tests/test_orchestrator_wiring.py -k handle_empty_backlog -v
```
Expected: 4 FAIL — `handle_empty_backlog` doesn't exist.

- [ ] **Step 3: Add `handle_empty_backlog` and wire it into the loop**

In `agent/station_orchestrator.py`, add the new function (place it next to `fetch_eligible_issues`):

```python
def handle_empty_backlog(
    config: dict,
    repo: str,
    project_id: int | None,
    workspace: str,
    run_id: str,
) -> str:
    """Decide what to do when a project's backlog is empty.

    Returns the skip_reason string. Side-effects:
      - posts a `finished` webhook for the regular run with skip_reason
      - dispatches the vision_analyst when conditions match (Trigger A)
    """
    has_vision = os.path.isfile(os.path.join(workspace, "docs", "vision.md"))
    proposals_pending = has_vision and has_open_vision_proposals(repo)

    if not has_vision:
        skip_reason = "no-eligible-issues-no-vision"
    elif proposals_pending:
        skip_reason = "no-eligible-issues-proposals-pending"
    elif project_id is None:
        # Can't dispatch without a project_id (manager-config drift).
        skip_reason = "no-eligible-issues-no-vision"
    else:
        outcome = dispatch_vision_bootstrap(project_id)
        skip_reason = (
            "no-eligible-issues-bootstrap-dispatched"
            if outcome == "dispatched"
            else "no-eligible-issues-bootstrap-already-running"
        )

    post_webhook(config, "finished", {
        "run_id": f"run-{run_id}",
        "project": repo,
        "mode": "agent-teams",
        "status": "completed",
        "skip_reason": skip_reason,
    })
    logger.info("Empty backlog for %s: %s", repo, skip_reason)
    return skip_reason
```

In the per-project loop at lines 931–934, replace:

```python
issues = fetch_eligible_issues(repo, max_per_project, workspace)
if not issues:
    logger.info("No eligible issues for %s, skipping", repo)
    continue
```

with:

```python
issues = fetch_eligible_issues(repo, max_per_project, workspace)
if not issues:
    handle_empty_backlog(
        config=config,
        repo=repo,
        project_id=project.get("id"),
        workspace=workspace,
        run_id=run_id,
    )
    continue
```

- [ ] **Step 4: Run the tests — must pass**

```bash
pytest tests/test_orchestrator_wiring.py -k handle_empty_backlog -v
```
Expected: 4 passed.

- [ ] **Step 5: Run full suite**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest -x --tb=short -q 2>&1 | tail -8
```
Expected: 900 passed (was 893 + 4 new + 3 from Task 4 already counted = 900).

- [ ] **Step 6: Commit**

```bash
git add agent/station_orchestrator.py dashboard/backend/tests/test_orchestrator_wiring.py
git commit -m "feat(orchestrator): Trigger A — dispatch vision-analyst on empty backlog

When a triggered run finds no eligible issues, branches by:
  - no docs/vision.md            -> no-eligible-issues-no-vision
  - vision-suggested issues open -> no-eligible-issues-proposals-pending
  - otherwise                    -> dispatch + bootstrap-(dispatched|already-running)

The regular run terminates with skip_reason set."
```

---

## Task 7: Trigger B — vision commit fires the analyst with content-hash gate

**Files:**
- Modify: `dashboard/backend/app/routers/vision.py:71-121` (`commit_vision`)
- Test: `dashboard/backend/tests/test_vision_router.py`

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/backend/tests/test_vision_router.py`:

```python
@pytest.mark.asyncio
async def test_commit_vision_fires_analyst_when_sha_changes(client, setup_db, monkeypatch):
    """Two commits with different SHAs -> two dispatches."""
    from app.services import service_control
    from app.routers import vision as vision_router

    calls = []
    async def fake_dispatch(project_id):
        calls.append(project_id)
        return {"success": True, "status_code": 200}
    monkeypatch.setattr(service_control, "start_vision_analyst", fake_dispatch)
    monkeypatch.setattr(vision_router.service_control, "start_vision_analyst", fake_dispatch)

    # ... arrange a project, post commit_vision with two different SHAs
    # ... assert calls == [project_id, project_id]


@pytest.mark.asyncio
async def test_commit_vision_skips_analyst_when_sha_unchanged(client, setup_db, monkeypatch):
    """Commit twice with same SHA -> only one dispatch."""
    # ... similar arrangement; assert len(calls) == 1


@pytest.mark.asyncio
async def test_commit_vision_treats_409_as_success(client, setup_db, monkeypatch):
    """Launcher 409 -> commit_vision still returns 200."""
    from app.services import service_control
    from app.routers import vision as vision_router
    async def fake_dispatch(project_id):
        return {"success": False, "status_code": 409, "error": "already running"}
    monkeypatch.setattr(service_control, "start_vision_analyst", fake_dispatch)
    monkeypatch.setattr(vision_router.service_control, "start_vision_analyst", fake_dispatch)
    # ... post commit_vision; assert response.status_code == 200
```

(Read the surrounding `test_vision_router.py` to find the existing `client` and `setup_db` fixtures and the pattern for stubbing `github_contents.write_file` / `read_file`. Reuse them; don't reinvent.)

- [ ] **Step 2: Run the tests — must fail**

```bash
pytest tests/test_vision_router.py -k commit_vision -v
```
Expected: 3 FAIL — analyst not yet wired.

- [ ] **Step 3: Add the post-commit hook**

In `dashboard/backend/app/routers/vision.py`, in `commit_vision` after line 113 (`project.vision_cached_at = now`), add:

```python
    # Trigger B (spec 2026-05-08-vision-issue-bootstrap-design.md):
    # fire the analyst when the vision SHA actually changed. We set
    # last_vision_analyzed_sha at *dispatch* time (not on completion) so a
    # failed analyst doesn't loop on identical re-commits.
    if fresh.sha != project.last_vision_analyzed_sha:
        try:
            result = await service_control.start_vision_analyst(project_id)
            if not result.get("success") and result.get("status_code") != 409:
                logger.warning(
                    "vision commit B-trigger dispatch failed: %s",
                    result.get("error") or result.get("stderr"),
                )
            else:
                # 200 or 409 — both mean "an analyst run will happen"
                project.last_vision_analyzed_sha = fresh.sha
        except Exception as exc:
            logger.warning("vision commit B-trigger dispatch exception: %s", exc)
```

Verify `service_control` is already imported at the top (it is — used by `find_gaps`).

Add `import logging; logger = logging.getLogger(__name__)` if not already present.

- [ ] **Step 4: Run the tests — must pass**

```bash
pytest tests/test_vision_router.py -k commit_vision -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/routers/vision.py dashboard/backend/tests/test_vision_router.py
git commit -m "feat(vision): Trigger B — auto-fire analyst on vision commit

Content-hash gate via Project.last_vision_analyzed_sha. 409 from
launcher is treated as success (analyst is already running)."
```

---

## Task 8: New endpoint — `GET /api/projects/{id}/vision/proposals`

**Files:**
- Modify: `dashboard/backend/app/routers/vision.py` (add endpoint)
- Modify: `dashboard/backend/app/schemas.py` (add `VisionProposalsRead`)
- Test: `dashboard/backend/tests/test_vision_router.py`

- [ ] **Step 1: Write the failing test**

Append to `dashboard/backend/tests/test_vision_router.py`:

```python
@pytest.mark.asyncio
async def test_vision_proposals_endpoint(client, setup_db, monkeypatch):
    """GET /api/projects/{id}/vision/proposals returns counts."""
    import subprocess
    open_payload = '[{"number": 1}, {"number": 2}, {"number": 3}]'

    def fake_run(cmd, *a, **k):
        if "--state" in cmd and "open" in cmd:
            return type("R", (), {"returncode": 0, "stdout": open_payload, "stderr": ""})
        return type("R", (), {"returncode": 0, "stdout": "[]", "stderr": ""})

    monkeypatch.setattr(subprocess, "run", fake_run)

    # ... arrange a project (id known), then:
    resp = await client.get(f"/api/projects/{project_id}/vision/proposals")
    assert resp.status_code == 200
    body = resp.json()
    assert body["open"] == 3
    assert body["accepted_recent"] == 0
```

- [ ] **Step 2: Run the test — must fail**

```bash
pytest tests/test_vision_router.py::test_vision_proposals_endpoint -v
```
Expected: FAIL — 404.

- [ ] **Step 3: Add the schema**

In `dashboard/backend/app/schemas.py` (alphabetically near other Vision* schemas):

```python
class VisionProposalsRead(BaseModel):
    open: int
    accepted_recent: int
```

- [ ] **Step 4: Add the endpoint with 60-second cache**

In `dashboard/backend/app/routers/vision.py`, append:

```python
# Module-level cache: {project_id: (timestamp, payload)}.
# 60-second TTL is enough to absorb dashboard re-renders without
# overwhelming the rate-limited gh CLI.
_PROPOSALS_CACHE: dict[int, tuple[float, dict]] = {}
_PROPOSALS_TTL_S = 60


@router.get("/{project_id}/vision/proposals", response_model=VisionProposalsRead)
async def vision_proposals(project_id: int, db: AsyncSession = Depends(get_db)):
    """Return open + recently-accepted proposal counts for the Vision tab."""
    import time

    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    cached = _PROPOSALS_CACHE.get(project_id)
    if cached and (time.time() - cached[0]) < _PROPOSALS_TTL_S:
        return VisionProposalsRead(**cached[1])

    open_count = _count_issues(project.repo, state="open", label="vision-suggested")
    # Accepted = closed within last 7 days that previously had vision-suggested
    # is hard to query cheaply via gh; approximate with closed+7d as a proxy.
    accepted = _count_issues(
        project.repo, state="closed", label="vision-suggested", days_back=7,
    )

    payload = {"open": open_count, "accepted_recent": accepted}
    _PROPOSALS_CACHE[project_id] = (time.time(), payload)
    return VisionProposalsRead(**payload)


def _count_issues(repo: str, *, state: str, label: str, days_back: int | None = None) -> int:
    """Run `gh issue list` and count results. Returns 0 on any failure."""
    import subprocess
    cmd = [
        "gh", "issue", "list",
        "--repo", repo,
        "--state", state,
        "--label", label,
        "--limit", "100",
        "--json", "number",
    ]
    if days_back is not None:
        cmd += ["--search", f"closed:>=$(date -u -d '-{days_back} days' '+%Y-%m-%d')"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return 0
        return len(json.loads(result.stdout or "[]"))
    except Exception:
        return 0
```

- [ ] **Step 5: Run the test — must pass**

```bash
pytest tests/test_vision_router.py::test_vision_proposals_endpoint -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/backend/app/routers/vision.py dashboard/backend/app/schemas.py dashboard/backend/tests/test_vision_router.py
git commit -m "feat(vision): GET /vision/proposals endpoint

Returns open + recently-accepted vision-suggested issue counts.
60-second server-side cache to limit gh calls."
```

---

## Task 9: Frontend types and API client

**Files:**
- Modify: `dashboard/frontend/src/lib/types.ts`
- Modify: `dashboard/frontend/src/lib/api.ts`

- [ ] **Step 1: Extend the `Run` type**

Find `interface Run` (or `type Run`) in `dashboard/frontend/src/lib/types.ts`. Add at the bottom of its fields:

```typescript
  skip_reason?: string | null;
  vision_bootstrap_count?: number | null;
  vision_bootstrap_proposals?: { number: number; title: string; url: string }[] | null;
```

- [ ] **Step 2: Add the `VisionProposals` type**

Append to `types.ts`:

```typescript
export interface VisionProposals {
  open: number;
  accepted_recent: number;
}
```

- [ ] **Step 3: Add `getVisionProposals` to the API client**

Find the existing vision API helpers in `dashboard/frontend/src/lib/api.ts`. Add next to them:

```typescript
export async function getVisionProposals(projectId: number): Promise<VisionProposals> {
  const res = await fetchAuth(`/api/projects/${projectId}/vision/proposals`);
  if (!res.ok) throw new Error(`getVisionProposals: ${res.status}`);
  return res.json();
}
```

(Use the same `fetchAuth` / fetch pattern used by adjacent functions — don't introduce a new pattern.)

Add `VisionProposals` to the import block at the top:

```typescript
import type { ..., VisionProposals } from './types';
```

- [ ] **Step 4: Verify the frontend build passes**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/frontend
npm run build 2>&1 | tail -5
```
Expected: build succeeds (warnings allowed; no errors in `types.ts` or `api.ts`).

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/lib/types.ts dashboard/frontend/src/lib/api.ts
git commit -m "feat(frontend): types + API client for vision proposals

Extends Run with skip_reason, vision_bootstrap_count, and
vision_bootstrap_proposals; adds getVisionProposals client."
```

---

## Task 10: `formatRunMode` utility

**Files:**
- Modify: `dashboard/frontend/src/lib/format.ts`
- Modify: `dashboard/frontend/src/lib/format.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `dashboard/frontend/src/lib/format.test.ts`:

```typescript
import { formatRunMode } from './format';

describe('formatRunMode', () => {
  it('returns vision-bootstrap descriptor', () => {
    const m = formatRunMode('vision-bootstrap');
    expect(m.label).toBe('Vision bootstrap');
    expect(m.icon).toBe('✨');
    expect(m.accent).toBe('violet');
  });

  it('returns agent-teams descriptor', () => {
    const m = formatRunMode('agent-teams');
    expect(m.label).toBe('Agent Teams');
    expect(m.accent).toBe('default');
  });

  it('falls back for unknown modes', () => {
    const m = formatRunMode(null);
    expect(m.label).toBe('Run');
    expect(m.accent).toBe('default');
  });
});
```

- [ ] **Step 2: Run — must fail**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/frontend
npm test -- --run format.test.ts 2>&1 | tail -10
```
Expected: FAIL — `formatRunMode` not exported.

- [ ] **Step 3: Implement `formatRunMode`**

Append to `dashboard/frontend/src/lib/format.ts`:

```typescript
export interface RunModeDescriptor {
  label: string;
  icon: string;
  accent: 'default' | 'violet';
}

/** Map a Run.mode value to its UI descriptor. */
export function formatRunMode(mode: string | null | undefined): RunModeDescriptor {
  switch (mode) {
    case 'vision-bootstrap':
      return { label: 'Vision bootstrap', icon: '✨', accent: 'violet' };
    case 'agent-teams':
      return { label: 'Agent Teams', icon: '◆', accent: 'default' };
    default:
      return { label: 'Run', icon: '◆', accent: 'default' };
  }
}
```

- [ ] **Step 4: Run — must pass**

```bash
npm test -- --run format.test.ts 2>&1 | tail -10
```
Expected: 3 new tests pass; total `format.test.ts` count goes from 16 to 19.

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/lib/format.ts dashboard/frontend/src/lib/format.test.ts
git commit -m "feat(frontend): formatRunMode utility for vision-bootstrap rendering"
```

---

## Task 11: Render vision-bootstrap mode in run list / detail

**Files:**
- Modify: `dashboard/frontend/src/pages/RunsPage.svelte` (or wherever runs render — verify with `grep -rn 'mode' src/pages/RunsPage.svelte`)

- [ ] **Step 1: Read the existing run-row template**

```bash
grep -n "mode\|run.status" /home/simon/Documents/claude-agent-station/dashboard/frontend/src/pages/RunsPage.svelte | head -10
```
Read enough surrounding code to understand how the badge currently renders.

- [ ] **Step 2: Apply `formatRunMode` to the row's mode label**

Wherever `run.mode` is displayed, replace with the descriptor. Conceptually:

```svelte
<script lang="ts">
  import { formatRunMode } from '../lib/format';
</script>

{#each runs as run}
  {@const m = formatRunMode(run.mode)}
  <div class="row" class:accent-violet={m.accent === 'violet'}>
    <span class="icon">{m.icon}</span>
    <span class="label">{m.label}</span>
    <!-- existing status badge etc. -->
    {#if run.skip_reason}
      <div class="text-xs text-tertiary mt-1">
        {skipReasonText(run.skip_reason, run)}
      </div>
    {/if}
  </div>
{/each}
```

- [ ] **Step 3: Add the `skipReasonText` helper**

In the same `<script>` block (or in `format.ts` if preferred — the spec calls these "UI hints" so they could live as a util):

```typescript
function skipReasonText(reason: string, run: Run): string {
  switch (reason) {
    case 'no-eligible-issues-no-vision':
      return 'No vision yet — define one in the Vision tab.';
    case 'no-eligible-issues-bootstrap-dispatched':
      return 'Vision analyst dispatched.';
    case 'no-eligible-issues-bootstrap-already-running':
      return 'Vision analyst is already running.';
    case 'no-eligible-issues-proposals-pending':
      return 'Vision-suggested issues await your acceptance.';
    default:
      return reason;
  }
}
```

(Adapt to the `formatRunMode`/utility location; if multiple pages share the helper, put it in `format.ts` and export it.)

- [ ] **Step 4: Add the violet accent class**

Look for the existing CSS pattern (border-l-violet etc.). If unclear, use inline style — match conventions in the surrounding code. Skip mockup-pixel work; the goal is a visible, recognizable distinction, not a finished design.

- [ ] **Step 5: Verify the build passes**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/frontend
npm run build 2>&1 | tail -5
```
Expected: build succeeds.

- [ ] **Step 6: Commit**

```bash
git add dashboard/frontend/src/pages/RunsPage.svelte
git commit -m "feat(frontend): render vision-bootstrap rows with skip-reason hint"
```

---

## Task 12: Vision tab info strip

**Files:**
- Modify: `dashboard/frontend/src/components/vision/VisionTab.svelte`
- Test: `dashboard/frontend/src/components/vision/VisionTab.test.ts` (new)

- [ ] **Step 1: Read the existing `VisionTab.svelte`**

```bash
sed -n '1,80p' /home/simon/Documents/claude-agent-station/dashboard/frontend/src/components/vision/VisionTab.svelte
```

Understand the existing top-of-tab structure so the info strip slots in cleanly.

- [ ] **Step 2: Write the smoke test**

Create `dashboard/frontend/src/components/vision/VisionTab.test.ts`:

```typescript
import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/svelte';
import VisionTab from './VisionTab.svelte';

vi.mock('../../lib/api', () => ({
  getVisionProposals: vi.fn().mockResolvedValue({ open: 3, accepted_recent: 1 }),
}));

describe('VisionTab info strip', () => {
  it('renders proposal counts when API resolves', async () => {
    const { findByText } = render(VisionTab, {
      props: { project: { id: 7, repo: 'x/y', branch: 'main' } as any },
    });
    expect(await findByText(/3 proposals open/)).toBeTruthy();
  });
});
```

If `@testing-library/svelte` isn't installed:

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/frontend
npm install --save-dev @testing-library/svelte @testing-library/jest-dom jsdom
```

And update `vite.config.ts` `test.environment` to `'jsdom'` (was `'node'`). If the existing tests need the node environment, set the environment per-file with `// @vitest-environment jsdom` at the top of `VisionTab.test.ts`.

- [ ] **Step 3: Run the test — must fail**

```bash
npm test -- --run VisionTab.test.ts 2>&1 | tail -10
```
Expected: FAIL.

- [ ] **Step 4: Add the info strip**

In `VisionTab.svelte`, near the top of the existing layout, add:

```svelte
<script lang="ts">
  import { onMount } from 'svelte';
  import { getVisionProposals } from '../../lib/api';
  import type { Project, VisionProposals } from '../../lib/types';

  let { project }: { project: Project } = $props();
  let proposals = $state<VisionProposals | null>(null);

  onMount(async () => {
    try { proposals = await getVisionProposals(project.id); }
    catch { /* silent — info strip just hides the count */ }
  });

  function rerunAnalyst() {
    fetch(`/api/projects/${project.id}/vision/find-gaps`, { method: 'POST' })
      .then(r => r.ok || console.warn('rerun failed', r.status));
  }
</script>

<div class="vision-info-strip" style="border:1px solid var(--color-border); border-radius:8px; padding:12px; margin-bottom:12px; display:flex; gap:12px; align-items:center;">
  <strong>Vision analyst</strong>
  <span class="text-xs text-tertiary">
    {#if proposals}
      {proposals.open} proposals open · {proposals.accepted_recent} accepted last week
    {:else}
      Loading…
    {/if}
  </span>
  <span style="flex:1"></span>
  <button class="btn btn-sm" onclick={rerunAnalyst}>Re-run analyst</button>
  <a class="btn btn-sm" href="https://github.com/{project.repo}/issues?q=is:open+label:vision-suggested" target="_blank" rel="noopener">View on GitHub</a>
</div>
```

- [ ] **Step 5: Run the test — must pass**

```bash
npm test -- --run VisionTab.test.ts 2>&1 | tail -10
```
Expected: PASS.

- [ ] **Step 6: Verify the build**

```bash
npm run build 2>&1 | tail -5
```

- [ ] **Step 7: Commit**

```bash
git add dashboard/frontend/src/components/vision/VisionTab.svelte dashboard/frontend/src/components/vision/VisionTab.test.ts dashboard/frontend/package.json dashboard/frontend/package-lock.json dashboard/frontend/vite.config.ts
git commit -m "feat(frontend): Vision tab info strip with proposal counts"
```

---

## Task 13: Toast notifications

**Files:**
- Modify: wherever vision-commit submission happens (likely `VisionTab.svelte` or a sibling)
- Modify: SSE consumer for run events (likely `dashboard/frontend/src/lib/agent-presence.svelte.ts`)

- [ ] **Step 1: Find the commit-vision submit handler**

```bash
grep -rn "POST.*vision\|commitVision\|/vision\b" /home/simon/Documents/claude-agent-station/dashboard/frontend/src --include='*.ts' --include='*.svelte' | head
```

- [ ] **Step 2: Add success toast**

After a successful vision commit, call:

```typescript
addToast('info', 'Vision analyst running — proposals will appear in a few minutes.');
```

(Use the existing `addToast` from `lib/toast.svelte`.)

- [ ] **Step 3: Find the SSE run-event consumer**

```bash
grep -rn "run_event\|runEvent\|EventSource" /home/simon/Documents/claude-agent-station/dashboard/frontend/src/lib --include='*.ts' | head
```

- [ ] **Step 4: Add completion toast for vision-bootstrap mode**

Where finished events are processed, after persisting the run:

```typescript
if (event.mode === 'vision-bootstrap' && event.event === 'finished' && event.status === 'success') {
  const n = event.vision_bootstrap_count ?? 0;
  addToast(
    'success',
    n === 0
      ? 'Vision analyzed — no gaps found.'
      : `${n} issue${n === 1 ? '' : 's'} created from vision.`,
  );
}
```

- [ ] **Step 5: Verify the build**

```bash
npm run build 2>&1 | tail -5
```

- [ ] **Step 6: Commit**

```bash
git add dashboard/frontend/src/components/vision/VisionTab.svelte dashboard/frontend/src/lib/agent-presence.svelte.ts
git commit -m "feat(frontend): toasts for vision-commit dispatch + bootstrap completion"
```

---

## Task 14: Documentation

**Files:**
- Modify: `docs/configuration.md`
- Modify: `docs/architecture.md`

- [ ] **Step 1: Document `STATION_VISION_ANALYST_MODEL` and triggers**

In `docs/configuration.md`, under the Models section (line 28+) add a row to the table for the analyst model:

```
| Vision analyst | `claude-sonnet-4-6` | env: `STATION_VISION_ANALYST_MODEL` |
```

Under "Project config" (line 95+) add a subsection:

```markdown
### Vision-driven issue bootstrap

When a project has `docs/vision.md`, two automatic triggers fire the vision analyst:

- **Trigger A (orchestrator):** triggered runs that find no eligible issues dispatch the analyst when no `vision-suggested` issues are already open. The triggering run terminates with `Run.skip_reason = no-eligible-issues-bootstrap-dispatched`.
- **Trigger B (vision commit):** committing a new vision via the dashboard fires the analyst when the document SHA changes. Idempotent on identical re-commits.

Both produce `Run.mode = vision-bootstrap` rows that surface in the Runs list and Mission Control. Issues land with the `vision-suggested` label; remove the label to accept (the orchestrator's `SKIP_LABELS` blocks autonomous implementation until then).
```

- [ ] **Step 2: Document the run type**

In `docs/architecture.md`, in the section that lists run modes / agent flows, add:

```markdown
- **`vision-bootstrap`** — single-shot run that dispatches `agent/vision_analyst.py` to propose new issues from `docs/vision.md`. Triggered automatically (orchestrator empty backlog, or vision commit with content-hash change) or manually from the Vision tab. Never spawns teammates, never opens PRs.
```

- [ ] **Step 3: Verify links resolve**

```bash
cd /home/simon/Documents/claude-agent-station
grep -n "vision-bootstrap\|vision-issue-bootstrap" docs/*.md
```

- [ ] **Step 4: Commit**

```bash
git add docs/configuration.md docs/architecture.md
git commit -m "docs: document vision-bootstrap triggers and run type

Per CLAUDE.md project rule: docs stay in sync with code."
```

---

## Final verification

- [ ] **Step 1: Full backend suite**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/backend
pytest --tb=short -q 2>&1 | tail -8
```
Expected: ~905 passed, 1 skipped (was 890; ~15 new tests added).

- [ ] **Step 2: Full frontend suite**

```bash
cd /home/simon/Documents/claude-agent-station/dashboard/frontend
npm test 2>&1 | tail -10
```
Expected: all passes; new VisionTab + formatRunMode tests pass.

- [ ] **Step 3: Frontend build**

```bash
npm run build 2>&1 | tail -5
```

- [ ] **Step 4: Rebuild compose dashboard image and restart**

```bash
cd /home/simon/Documents/claude-agent-station
docker compose build dashboard agent
docker compose up -d
docker compose ps
```

- [ ] **Step 5: Manual smoke test (operator playthrough)**

Reload http://localhost:8420. With a project that has `docs/vision.md` but zero open issues:

1. Trigger a run → expect a regular run with `skip_reason = bootstrap-dispatched` plus a new `vision-bootstrap` run row appearing in the Runs list with the violet accent.
2. Wait for the bootstrap run to complete → expect a toast and N new GitHub issues with the `vision-suggested` label.
3. Edit the vision in the Vision tab and commit → expect the toast and a second bootstrap run.
4. Edit the vision again with no content change → expect no toast and no new run.

- [ ] **Step 6: Push and open the PR**

```bash
git push -u origin feature/vision-bootstrap
gh pr create --base dev --title "feat: vision-driven issue bootstrap" --body-file docs/superpowers/specs/2026-05-08-vision-issue-bootstrap-design.md
```

---

## Self-review notes

Coverage check against the spec:

| Spec section | Task(s) |
|---|---|
| Run-type contract (mode, columns) | 1, 2 |
| Trigger A — orchestrator | 4, 5, 6 |
| Trigger B — vision commit | 7 |
| Skip-reason hints | 6 (backend), 11 (UI) |
| `/vision/proposals` endpoint | 8 |
| Run rendering (icon, label, color) | 10, 11 |
| Vision tab info strip | 12 |
| Toasts | 13 |
| SSE event surfacing | 13 (re-uses existing run_event flow — no new event type) |
| Worker self-registers as run | 3 |
| Edge cases (409, no vision, proposals pending, fallback) | 5, 6, 7 |
| DB migration | 1 |
| Tests | every task |
| Docs | 14 |

Type-consistency check:

- `Run.mode` values used: `"vision-bootstrap"`, `"agent-teams"` — match across orchestrator, worker webhook, frontend `formatRunMode`.
- `skip_reason` values used: 4 strings — defined in Task 6, consumed in Task 11. Verified identical.
- Webhook payload field names: `vision_bootstrap_count`, `vision_bootstrap_proposals`, `skip_reason` — defined in Task 2 schema, populated in Tasks 3 + 6, persisted by `handle_finished` in Task 2.
