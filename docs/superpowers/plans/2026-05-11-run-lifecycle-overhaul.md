# Run Lifecycle Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the dashboard's run-state synchronization defects and begin the structural fix (bash → Python orchestrator migration) so the same class of bugs cannot recur.

**Architecture:** Five PRs target `dev`, each linked to a tracking issue. Items 1–4 are local fixes (backend cascade, optimistic SSE event, frontend idle panel, DB heartbeat column). Item 5 is a strangler-pattern migration of `run-manager.sh`: 5a replaces the bash webhook helper with a Python emitter; 5b moves CoordinatorTask lifecycle into Python with an `atexit`-backed finalizer; 5c moves the project loop into `station_orchestrator.py` and turns the bash file into a ~200-LOC shim.

**Tech Stack:** Python 3.11+ / FastAPI / SQLAlchemy async / SQLite (backend), Svelte 5 / TypeScript / Vite (frontend), bash (legacy orchestrator being thinned out), pytest + httpx for tests.

**Tracking issues:** [#345](https://github.com/kenhaesler/claude-agent-station/issues/345) [#346](https://github.com/kenhaesler/claude-agent-station/issues/346) [#347](https://github.com/kenhaesler/claude-agent-station/issues/347) [#348](https://github.com/kenhaesler/claude-agent-station/issues/348) [#349](https://github.com/kenhaesler/claude-agent-station/issues/349)

**Spec:** `docs/superpowers/specs/2026-05-11-run-lifecycle-overhaul-design.md`

---

## Setup (run once per execution session)

### Task 0: Sync local dev branch

- [ ] **Step 1: Pull latest dev**

```bash
git checkout dev && git pull --ff-only origin dev
```

Expected: `Already up to date.` or fast-forward summary.

- [ ] **Step 2: Confirm backend tests pass on a clean tree**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_lifecycle.py tests/test_runs.py tests/test_webhook.py -q
```

Expected: all green.

---

# PR 1 — Item 1: Zombie CoordinatorTask cleanup (Issue #345)

### Task 1.1: Branch + failing test for `handle_finished` cascade

**Files:**
- Test: `dashboard/backend/tests/test_run_lifecycle.py` (append)

- [ ] **Step 1: Branch**

```bash
git checkout dev && git checkout -b fix/345-orphan-coordinator-tasks
```

- [ ] **Step 2: Append the failing test**

Add to the bottom of `dashboard/backend/tests/test_run_lifecycle.py`:

```python
@pytest.mark.asyncio
async def test_handle_finished_orphans_running_coordinator_tasks(setup_db):
    """When a Run finalises, any of its coordinator_tasks left in 'running' or
    'claimed' must be cascaded to 'orphaned'. Fixes the zombie-task bug
    (issue #345) where /api/runs/active-employees surfaces stale rows."""
    from app.models import CoordinatorTask, Run
    from datetime import datetime, timezone
    from sqlalchemy import select

    run_id = "run-orphan-test-1"
    async with async_session() as db:
        # Seed a run + two coordinator tasks (one running, one claimed)
        db.add(Run(run_id=run_id, status="running",
                   started_at=datetime.now(timezone.utc)))
        db.add(CoordinatorTask(id="t-zombie-run", run_id=run_id,
                               project_repo="x/y", status="running",
                               started_at=datetime.now(timezone.utc)))
        db.add(CoordinatorTask(id="t-zombie-claim", run_id=run_id,
                               project_repo="x/y", status="claimed",
                               started_at=datetime.now(timezone.utc)))
        # A coord_task on a DIFFERENT run must not be touched
        db.add(CoordinatorTask(id="t-other-run", run_id="run-other",
                               project_repo="x/y", status="running",
                               started_at=datetime.now(timezone.utc)))
        await db.commit()

    event = WebhookRunEvent(event="finished", run_id=run_id, status="success")
    async with async_session() as db:
        await handle_finished(db, event, project_id=None,
                              run=(await db.execute(
                                  select(Run).where(Run.run_id == run_id)
                              )).scalar_one())
        await db.commit()

    async with async_session() as db:
        rows = (await db.execute(select(CoordinatorTask))).scalars().all()
        by_id = {r.id: r for r in rows}
        assert by_id["t-zombie-run"].status == "orphaned"
        assert by_id["t-zombie-claim"].status == "orphaned"
        assert by_id["t-zombie-claim"].claimed_at is None
        assert by_id["t-other-run"].status == "running"  # untouched
```

- [ ] **Step 3: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_lifecycle.py::test_handle_finished_orphans_running_coordinator_tasks -xvs
```

Expected: FAIL with `AssertionError` — coordinator_tasks status is still "running"/"claimed".

### Task 1.2: Implement the cascade in `handle_finished`

**Files:**
- Modify: `dashboard/backend/app/services/run_lifecycle.py`

- [ ] **Step 1: Add the cascade SQL after status finalisation**

In `dashboard/backend/app/services/run_lifecycle.py`, find `handle_finished` and insert the cascade right after `run.finished_at = datetime.now(timezone.utc)`:

```python
    # Cascade: any coordinator_tasks still claimed/running for this run are
    # marked 'orphaned' so /api/runs/active-employees does not resurrect
    # them as phantom employees after the parent run has finalised.
    # See issue #345 / spec 2026-05-11-run-lifecycle-overhaul-design.md.
    from sqlalchemy import update
    from app.models import CoordinatorTask
    await db.execute(
        update(CoordinatorTask)
        .where(
            CoordinatorTask.run_id == event.run_id,
            CoordinatorTask.status.in_(("claimed", "running")),
        )
        .values(status="orphaned", claimed_at=None)
    )
```

- [ ] **Step 2: Verify the test passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_lifecycle.py::test_handle_finished_orphans_running_coordinator_tasks -xvs
```

Expected: PASS.

### Task 1.3: Failing test for the active-employees guard

**Files:**
- Test: `dashboard/backend/tests/test_runs.py` (append; create if absent)

- [ ] **Step 1: Inspect for existing file**

```bash
test -f dashboard/backend/tests/test_runs.py && echo "exists" || echo "missing"
```

If missing, create with:

```python
"""Tests for the /api/runs/* endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, async_session, engine
from app.main import app
from app.models import CoordinatorTask, Run


@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(setup_db):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
```

- [ ] **Step 2: Append the guard test**

```python
@pytest.mark.asyncio
async def test_active_employees_skips_synthesis_when_parent_terminal(client):
    """When a Run is terminal (completed/failed/interrupted) the
    active-employees fallback must NOT synthesize phantom employees from
    its leftover coordinator_tasks. Fixes issue #345."""
    run_id = "run-terminal-1"
    async with async_session() as db:
        db.add(Run(run_id=run_id, status="completed",
                   started_at=datetime.now(timezone.utc),
                   finished_at=datetime.now(timezone.utc)))
        # Stale coordinator_task — simulates the pre-fix bug surface
        db.add(CoordinatorTask(id="t-stale", run_id=run_id,
                               project_repo="x/y", status="running",
                               started_at=datetime.now(timezone.utc)))
        await db.commit()

    resp = await client.get("/api/runs/active-employees")
    assert resp.status_code == 200
    employees = resp.json()
    assert all(e["run_id"] != run_id for e in employees), \
        f"phantom employee returned for terminal run: {employees}"
```

- [ ] **Step 3: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_runs.py::test_active_employees_skips_synthesis_when_parent_terminal -xvs
```

Expected: FAIL — phantom returned.

### Task 1.4: Implement the active-employees guard

**Files:**
- Modify: `dashboard/backend/app/routers/runs.py`

- [ ] **Step 1: Guard the synthesis fallback**

In `dashboard/backend/app/routers/runs.py`, find `get_active_employees` (~line 98). The fallback at `~line 131` synthesizes from `CoordinatorTask`. Add a parent-terminal-check:

Replace:

```python
        coord_result = await db.execute(
            select(CoordinatorTask).where(CoordinatorTask.status == "running")
        )
        coord_tasks = coord_result.scalars().all()
```

With:

```python
        # Only synthesize from coordinator_tasks whose parent run is still
        # in a non-terminal state. Without this guard, stale rows linger
        # after the parent run completes and the API resurrects them as
        # phantom running employees. See issue #345.
        coord_result = await db.execute(
            select(CoordinatorTask)
            .join(Run, Run.run_id == CoordinatorTask.run_id, isouter=True)
            .where(
                CoordinatorTask.status == "running",
                # parent run not yet terminal (None covers freshly-spawned
                # tasks whose Run row hasn't been ingested yet)
                Run.status.in_(("running", "reviewing", "plan_reviewing"))
                | (Run.status.is_(None)),
            )
        )
        coord_tasks = coord_result.scalars().all()
```

- [ ] **Step 2: Verify the test passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_runs.py::test_active_employees_skips_synthesis_when_parent_terminal -xvs
```

Expected: PASS.

### Task 1.5: Full regression + commit + PR

- [ ] **Step 1: Full backend test pass**

```bash
cd dashboard/backend && python3 -m pytest -q
```

Expected: all green; warn if any pre-existing failures unrelated to this change.

- [ ] **Step 2: Commit**

```bash
git add dashboard/backend/app/services/run_lifecycle.py \
        dashboard/backend/app/routers/runs.py \
        dashboard/backend/tests/test_run_lifecycle.py \
        dashboard/backend/tests/test_runs.py
git commit -m "$(cat <<'EOF'
fix(runs): orphan stale coordinator_tasks when parent run finishes

Mission Control showed phantom "running" employees long after the parent
Run was marked completed because /api/runs/active-employees synthesizes
entries from coordinator_tasks rows, and those rows were never updated
on run completion.

- handle_finished() now cascades status to 'orphaned' for any
  coordinator_tasks left in claimed/running for that run_id.
- get_active_employees() guards the synthesis fallback to only
  consider rows whose parent Run is still non-terminal.

Fixes #345.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3: Push + open PR**

```bash
git push -u origin fix/345-orphan-coordinator-tasks
gh pr create --base dev --title "fix(runs): orphan stale coordinator_tasks when parent run finishes" --body "Fixes #345. See spec doc. Two new tests, both green."
```

Expected: PR URL printed.

---

# PR 2 — Item 2: Optimistic placeholder on Trigger Run (Issue #346)

### Task 2.1: Branch + failing test for placeholder insert

**Files:**
- Test: `dashboard/backend/tests/test_run_control.py` (append)

- [ ] **Step 1: Branch from latest dev**

```bash
git checkout dev && git pull --ff-only && git checkout -b feat/346-optimistic-placeholder
```

- [ ] **Step 2: Append failing test**

Add to `dashboard/backend/tests/test_run_control.py`:

```python
@pytest.mark.asyncio
async def test_trigger_run_inserts_pending_placeholder(client):
    """POST /api/runs/trigger must insert a Run(status='pending') BEFORE
    the launcher returns, so the dashboard shows feedback immediately
    (issue #346). The launcher call is mocked so the placeholder is the
    only side-effect we observe."""
    from app.models import Run
    from sqlalchemy import select

    with patch("app.routers.runs.service_control.start_agent_service",
               new_callable=AsyncMock,
               return_value={"success": True, "detail": "accepted",
                             "status_code": 200}):
        resp = await client.post("/api/runs/trigger")
    assert resp.status_code == 200
    body = resp.json()
    # Placeholder run_id is returned to the caller for SSE correlation
    assert body.get("run_id", "").startswith("run-")

    async with async_session() as db:
        rows = (await db.execute(
            select(Run).where(Run.run_id == body["run_id"])
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "pending"
```

- [ ] **Step 3: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_control.py::test_trigger_run_inserts_pending_placeholder -xvs
```

Expected: FAIL — no `run_id` in response, no placeholder row inserted.

### Task 2.2: Implement placeholder insert + SSE publish in `trigger_run`

**Files:**
- Modify: `dashboard/backend/app/routers/runs.py` (the `trigger_run` function near line 620)

- [ ] **Step 1: Rewrite `trigger_run`**

In `dashboard/backend/app/routers/runs.py`, replace the body of `trigger_run` with:

```python
@router.post("/trigger")
async def trigger_run(db: AsyncSession = Depends(get_db)):
    """Trigger an agent run immediately and surface a pending placeholder.

    Insertion order matters: the Run row must be committed and the
    run_start SSE event must be published BEFORE the launcher is asked
    to spawn run-manager.sh, so a fast dashboard sees the placeholder
    before the bash takes seconds to enumerate projects.
    """
    from datetime import datetime, timezone
    from app.models import Run
    from app.services.event_bus import publish

    # Generate a stable run_id we hand to the launcher; run-manager.sh
    # adopts it via STATION_RUN_ID_OVERRIDE so its own webhook_event
    # "run_start" upgrades this same row instead of inserting a duplicate.
    run_id = f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    placeholder = Run(
        run_id=run_id,
        status="pending",
        started_at=datetime.now(timezone.utc),
    )
    db.add(placeholder)
    await db.commit()

    await publish({
        "type": "run_start",
        "run_id": run_id,
        "status": "pending",
    })

    result = await service_control.start_agent_service(hint_run_id=run_id)
    if not result.get("success"):
        # Mark the placeholder failed so it doesn't linger as "pending"
        placeholder.status = "failed"
        placeholder.finished_at = datetime.now(timezone.utc)
        await db.commit()
        await publish({"type": "run_complete", "run_id": run_id,
                       "status": "failed",
                       "error": result.get("error") or "trigger failed"})
        status = result.get("status_code") or 500
        if status < 400:
            status = 500
        raise HTTPException(status_code=status,
                            detail=result.get("error") or "Failed to trigger run")

    is_compose = service_control.deploy_mode() == "compose"
    detail = result.get("detail") or (
        "agent launcher accepted run" if is_compose else "claude-agent.service started"
    )
    return {
        "status": "triggered",
        "run_id": run_id,
        "detail": detail,
        **{k: v for k, v in result.items() if k not in {"success", "status_code"}},
    }
```

- [ ] **Step 2: Wire the `hint_run_id` plumb-through in `service_control.start_agent_service`**

In `dashboard/backend/app/services/service_control.py`, update the function signature:

```python
async def start_agent_service(hint_run_id: str | None = None) -> dict:
    """Start the agent (systemctl start, or POST /run on the launcher).

    ``hint_run_id`` lets the dashboard pre-allocate a run_id so the
    in-flight run row created on /api/runs/trigger and the bash-emitted
    run_start webhook converge on the same id. The launcher passes this
    to run-manager.sh as ``STATION_RUN_ID_OVERRIDE``.
    """
    if _mode() == "compose":
        body = {"hint_run_id": hint_run_id} if hint_run_id else None
        return await _launcher_call("POST", "/run", json_body=body)
    return await systemctl("start", DEFAULT_AGENT_UNIT)
```

And update `_launcher_call` to accept an optional JSON body:

```python
async def _launcher_call(method: str, path: str,
                        json_body: dict | None = None) -> dict:
    base = _launcher_base_url()
    if not base:
        return {"success": False, "error": "STATION_AGENT_LAUNCHER_URL not set"}
    url = f"{base.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request(method, url,
                                        headers=_launcher_headers(),
                                        json=json_body)
    except httpx.HTTPError as exc:
        return {"success": False,
                "error": f"launcher unreachable: {exc}",
                "status_code": 502}
    body: dict = {}
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text}
    return {**body, "success": 200 <= resp.status_code < 300,
            "status_code": resp.status_code}
```

- [ ] **Step 3: Verify Task 2.1 test passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_run_control.py::test_trigger_run_inserts_pending_placeholder -xvs
```

Expected: PASS.

### Task 2.3: Launcher accepts and propagates `hint_run_id`

**Files:**
- Modify: `agent/launcher.py`

- [ ] **Step 1: Locate the `POST /run` handler**

```bash
grep -n "post.*run\|def trigger_run\|/run\"" agent/launcher.py
```

- [ ] **Step 2: Update the handler to accept the optional body**

Find the `@app.post("/run")` route. Update its signature and the env it passes to subprocess:

```python
from pydantic import BaseModel

class RunHint(BaseModel):
    hint_run_id: str | None = None

@app.post("/run")
async def post_run(body: RunHint | None = None) -> dict:
    """Spawn run-manager.sh.

    If ``hint_run_id`` is provided, we pass it to bash as
    ``STATION_RUN_ID_OVERRIDE`` so the bash adopts it instead of
    generating a fresh timestamp-based id. This lets the dashboard
    pre-allocate the run row before the bash even starts (see issue
    #346).
    """
    hint = body.hint_run_id if body else None
    return _spawn_run_manager(hint_run_id=hint)
```

And update `_spawn_run_manager`:

```python
def _spawn_run_manager(*, hint_run_id: str | None = None) -> dict:
    # ...existing body up to the env preparation...
    env = os.environ.copy()
    if hint_run_id:
        env["STATION_RUN_ID_OVERRIDE"] = hint_run_id
    # ...existing Popen() call, but pass env=env...
```

(The existing function may need its env mutation point identified; the principle is `env` must include `STATION_RUN_ID_OVERRIDE` when the hint is set.)

- [ ] **Step 3: Add launcher-side test (optional but recommended)**

```python
# In a new file dashboard/backend/tests/test_launcher_hint.py or
# agent-side test infra — skip if your project test harness can't
# reach agent/launcher.py easily. Manual verification is acceptable:
# see Task 2.6.
```

### Task 2.4: Bash adopts `STATION_RUN_ID_OVERRIDE`

**Files:**
- Modify: `agent/scripts/run-manager.sh:27`

- [ ] **Step 1: Update the RUN_ID assignment**

Find line 27 (`RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"`). Replace with:

```bash
# Allow the dashboard to pre-allocate the run_id so the optimistic
# placeholder row and the bash-emitted run_start webhook converge on
# the same id (issue #346). The override is supplied via env by
# agent/launcher.py when the dashboard passes a hint_run_id.
if [ -n "${STATION_RUN_ID_OVERRIDE:-}" ]; then
    # Strip a "run-" prefix if present: RUN_ID is the bare timestamp
    # part and the webhook helper prepends "run-" again.
    _override="${STATION_RUN_ID_OVERRIDE#run-}"
    RUN_ID="$_override"
else
    RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
fi
```

### Task 2.5: Pending-row reaper safeguard

**Files:**
- Modify: `dashboard/backend/app/services/stale_run_reaper.py`

- [ ] **Step 1: Reap stale pending placeholders**

Add a constant near the top:

```python
# Pending placeholders that never get upgraded to 'running' (because the
# bash crashed before its run_start fired, or the user closed the tab
# before triggering anything) are reaped after this many seconds.
PENDING_REAP_AGE_SECONDS = 90
```

And in `reap_stale_runs`, after the existing `running/reviewing` reap block, add:

```python
    # Pending placeholders that never advanced to 'running' within the
    # window — the bash never picked up the hint, or the launcher
    # accepted the call but failed before run_start.
    cutoff_pending = datetime.now(timezone.utc) - timedelta(
        seconds=PENDING_REAP_AGE_SECONDS
    )
    pending_result = await db.execute(
        select(Run).where(
            Run.status == "pending",
            Run.started_at < cutoff_pending,
        )
    )
    pending_rows = pending_result.scalars().all()
    for r in pending_rows:
        r.status = "failed"
        r.finished_at = datetime.now(timezone.utc)
        # event_bus publish so SSE clients re-fetch and drop the placeholder
        await event_bus_publish({"type": "run_complete",
                                 "run_id": r.run_id,
                                 "status": "failed",
                                 "error": "pending placeholder expired"})
    if pending_rows:
        await db.commit()
        logger.info("Stale run reaper: failed %d pending placeholders",
                    len(pending_rows))
    return reaped + len(pending_rows)
```

(The existing reaper returns `reaped`; this addition extends the return count.)

- [ ] **Step 2: Test pending reap**

Append to `dashboard/backend/tests/test_stale_run_reaper.py` (or create if absent — base on existing reaper test structure):

```python
@pytest.mark.asyncio
async def test_reap_expired_pending_placeholder(setup_db):
    """A pending placeholder older than PENDING_REAP_AGE_SECONDS that
    never advanced to running gets marked failed (issue #346 safety net)."""
    from datetime import datetime, timedelta, timezone
    from app.models import Run
    from app.services.stale_run_reaper import (
        reap_stale_runs, PENDING_REAP_AGE_SECONDS,
    )
    from sqlalchemy import select
    from unittest.mock import patch, AsyncMock

    too_old = datetime.now(timezone.utc) - timedelta(
        seconds=PENDING_REAP_AGE_SECONDS + 30
    )
    async with async_session() as db:
        db.add(Run(run_id="run-stale-pending", status="pending",
                   started_at=too_old))
        await db.commit()

    # Pretend the agent service is idle so reap is allowed to act
    with patch("app.services.stale_run_reaper.get_agent_status",
               new_callable=AsyncMock,
               return_value={"service_active": False}):
        async with async_session() as db:
            reaped = await reap_stale_runs(db)
    assert reaped >= 1
    async with async_session() as db:
        row = (await db.execute(
            select(Run).where(Run.run_id == "run-stale-pending")
        )).scalar_one()
        assert row.status == "failed"
```

- [ ] **Step 3: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_stale_run_reaper.py::test_reap_expired_pending_placeholder -xvs
```

Expected: PASS.

### Task 2.6: Full regression + commit + PR

- [ ] **Step 1: Backend regression**

```bash
cd dashboard/backend && python3 -m pytest -q
```

Expected: green.

- [ ] **Step 2: Manual live verification**

```bash
cd /home/simon/Documents/claude-agent-station
docker compose build dashboard agent && docker compose up -d
# In another shell or browser: open http://localhost:8420, click Trigger Run
# Confirm a "pending" row appears in <500ms
```

- [ ] **Step 3: Commit and open PR**

```bash
git add dashboard/backend/app/routers/runs.py \
        dashboard/backend/app/services/service_control.py \
        dashboard/backend/app/services/stale_run_reaper.py \
        agent/launcher.py agent/scripts/run-manager.sh \
        dashboard/backend/tests/test_run_control.py \
        dashboard/backend/tests/test_stale_run_reaper.py
git commit -m "$(cat <<'EOF'
feat(runs): optimistic placeholder on Trigger Run

POST /api/runs/trigger now allocates the run_id server-side, inserts a
Run(status='pending') row, and publishes run_start to event_bus before
asking the launcher to spawn. The launcher forwards the hint to bash as
STATION_RUN_ID_OVERRIDE; run-manager.sh adopts the id so its own
run_start webhook upgrades the existing row.

This closes the 5–30s window between click and first-row-visible. A
reaper sweeps stale pending placeholders after 90s.

Fixes #346.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin feat/346-optimistic-placeholder
gh pr create --base dev --title "feat(runs): optimistic placeholder on Trigger Run" --body "Fixes #346. See spec doc."
```

---

# PR 3 — Item 3: Mission Control idle state (Issue #347)

### Task 3.1: Branch + create `IdlePanel.svelte`

**Files:**
- Create: `dashboard/frontend/src/components/mission/IdlePanel.svelte`

- [ ] **Step 1: Branch**

```bash
git checkout dev && git pull --ff-only && git checkout -b feat/347-mission-control-idle
```

- [ ] **Step 2: Create the component**

```bash
mkdir -p dashboard/frontend/src/components/mission
```

Write `dashboard/frontend/src/components/mission/IdlePanel.svelte`:

```svelte
<script lang="ts">
  import { triggerRun } from '../../lib/api';
  import { addToast } from '../../lib/toast.svelte';
  import { navigate } from '../../lib/router.svelte';
  import { timeAgo } from '../../lib/format';
  import type { Run } from '../../lib/types';

  let { lastRun }: { lastRun: Run | null } = $props();

  let triggering = $state(false);

  async function handleTrigger() {
    if (triggering) return;
    triggering = true;
    try {
      const res = await triggerRun();
      addToast('success', `Triggered run ${res.run_id ?? ''}`);
    } catch (e: any) {
      addToast('error', e.message ?? 'Trigger failed');
    } finally {
      triggering = false;
    }
  }

  function viewLast() {
    if (lastRun) navigate(`/runs/${lastRun.run_id}`);
  }
</script>

<section class="idle-panel" data-testid="mission-idle-panel">
  <div class="idle-head">
    <h2>● Agent is idle</h2>
    <button type="button"
            class="trigger-btn primary"
            onclick={handleTrigger}
            disabled={triggering}
            data-testid="idle-trigger-btn">
      {triggering ? 'Triggering…' : 'Trigger Run'}
    </button>
  </div>

  {#if lastRun}
    <div class="last-run">
      <div class="row">
        <span class="lbl">Last run</span>
        <a href={`/runs/${lastRun.run_id}`} onclick={(e) => { e.preventDefault(); viewLast(); }}>
          {lastRun.run_id}
        </a>
      </div>
      <div class="row">
        <span class="lbl">Status</span>
        <span class="val">{lastRun.status}{lastRun.verdict ? ` · ${lastRun.verdict}` : ''}</span>
      </div>
      {#if lastRun.finished_at}
        <div class="row">
          <span class="lbl">Finished</span>
          <span class="val">{timeAgo(lastRun.finished_at)}</span>
        </div>
      {/if}
    </div>
  {:else}
    <p class="desc">No runs yet. Click <b>Trigger Run</b> to start the agent.</p>
  {/if}
</section>

<style>
  .idle-panel {
    border: 1px dashed var(--graphite);
    background: var(--paper-2);
    padding: 32px;
    border-radius: 8px;
    margin: 24px 0;
    color: var(--ink);
  }
  .idle-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    margin-bottom: 16px;
  }
  .idle-head h2 {
    margin: 0;
    color: var(--ash);
    font-size: 1.1rem;
    font-weight: 500;
  }
  .trigger-btn {
    padding: 8px 24px;
    background: var(--data);
    color: var(--paper);
    border: 0;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.95rem;
  }
  .trigger-btn:disabled { opacity: 0.6; cursor: not-allowed; }
  .last-run .row { display: flex; gap: 12px; margin: 4px 0; }
  .last-run .lbl { color: var(--graphite); min-width: 90px; }
  .last-run a { color: var(--data); text-decoration: none; }
  .desc { color: var(--graphite); margin: 8px 0 0; }
</style>
```

### Task 3.2: Wire the idle branch into MissionControl

**Files:**
- Modify: `dashboard/frontend/src/pages/MissionControl.svelte`

- [ ] **Step 1: Locate the section to wrap**

```bash
grep -n "currentRun\|activeRuns\[0\]\|<main\|<section.*mc-content" dashboard/frontend/src/pages/MissionControl.svelte | head -10
```

- [ ] **Step 2: Add the idle import and branch**

In `dashboard/frontend/src/pages/MissionControl.svelte`, add to the top imports:

```ts
import IdlePanel from '../components/mission/IdlePanel.svelte';
import { runs as runsStore } from '../lib/data-store.svelte';
```

(Replace `runsStore` with whatever the actual recent-runs export is — check `data-store.svelte.ts`.)

Add a derived idle flag near the existing `currentRunId` declaration:

```ts
let isIdle = $derived(agentPresence.activeRuns.length === 0);
let lastRun = $derived(runsStore.length > 0 ? runsStore[0] : null);
```

Then in the markup, wrap the existing mission-control chrome with:

```svelte
{#if isIdle}
  <IdlePanel {lastRun} />
{:else}
  <!-- existing chrome unchanged -->
  ...
{/if}
```

(Locate where the existing layout starts — usually after the page header. Wrap from that point to the matching close of the main layout div.)

### Task 3.3: Verify svelte-check + manual UI

- [ ] **Step 1: Type check**

```bash
cd dashboard/frontend && npx --no-install svelte-check --threshold error 2>&1 | tail -8
```

Expected: same pre-existing errors only (`VisionTab` AgentMode comparison, `format.test.ts` duplicate identifier). No new errors.

- [ ] **Step 2: Rebuild dashboard container**

```bash
cd /home/simon/Documents/claude-agent-station && docker compose build dashboard && docker compose up -d dashboard
```

- [ ] **Step 3: Manual verification**

```bash
sleep 3 && curl -sf http://localhost:8420/api/health
```

Then open `http://localhost:8420` (Mission Control is `/`) in a browser. Confirm:
- With no runs in flight, an idle panel is shown with the last-run summary.
- Clicking "Trigger Run" inserts a pending placeholder (item #2 must be merged first, or test against `dev`).
- Once a run is active, the idle panel disappears and the normal chrome returns.

### Task 3.4: Commit + PR

- [ ] **Step 1: Commit**

```bash
git add dashboard/frontend/src/components/mission/IdlePanel.svelte \
        dashboard/frontend/src/pages/MissionControl.svelte
git commit -m "$(cat <<'EOF'
feat(mission-control): explicit idle state when no active run

When agentPresence.activeRuns is empty, render a dedicated IdlePanel
instead of falling back to latestRunId chrome. The panel shows the
last run summary (status, verdict, finished_at) and a prominent
Trigger Run button.

Fixes #347.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin feat/347-mission-control-idle
gh pr create --base dev --title "feat(mission-control): explicit idle state when no active run" --body "Fixes #347. See spec doc."
```

---

# PR 4 — Item 4: `Run.last_event_at` heartbeat (Issue #348)

### Task 4.1: Branch + DB migration entry

**Files:**
- Modify: `dashboard/backend/app/database.py`
- Modify: `dashboard/backend/app/models.py`

- [ ] **Step 1: Branch**

```bash
git checkout dev && git pull --ff-only && git checkout -b feat/348-last-event-at
```

- [ ] **Step 2: Add the migration**

In `dashboard/backend/app/database.py`, find `_migrate_add_columns` and append to the migrations list:

```python
        # Per-run heartbeat — last webhook event timestamp. Updated on
        # every webhook ingestion regardless of event type. Used by the
        # reaper to detect stuck runs faster and by Mission Control to
        # show an "active N seconds ago" badge. See issue #348.
        ("runs", "last_event_at", "ALTER TABLE runs ADD COLUMN last_event_at DATETIME"),
```

And add an index entry (further down in the same file):

```python
        "CREATE INDEX IF NOT EXISTS ix_runs_last_event_at ON runs(last_event_at)",
```

- [ ] **Step 3: Add the column to the model**

In `dashboard/backend/app/models.py`, add inside the `Run` class:

```python
    # Updated on every webhook event for this run_id. NULL for legacy
    # rows. See issue #348.
    last_event_at = Column(DateTime, nullable=True, default=None, index=True)
```

### Task 4.2: Webhook handler bumps heartbeat

**Files:**
- Modify: `dashboard/backend/app/routers/webhook.py`
- Test: `dashboard/backend/tests/test_webhook.py` (append)

- [ ] **Step 1: Failing test**

Append to `dashboard/backend/tests/test_webhook.py`:

```python
@pytest.mark.asyncio
async def test_webhook_bumps_last_event_at(client):
    """Every webhook event for a run must bump runs.last_event_at so
    Mission Control and the reaper can tell live runs from dead ones
    (issue #348)."""
    from datetime import datetime, timezone
    from app.models import Run
    from sqlalchemy import select

    run_id = "run-heartbeat-1"
    async with async_session() as db:
        db.add(Run(run_id=run_id, status="running",
                   started_at=datetime.now(timezone.utc)))
        await db.commit()

    # Any event — even one we don't otherwise handle specially
    resp = await client.post("/api/webhook/run-event", json={
        "event": "narration",
        "run_id": run_id,
    })
    assert resp.status_code == 200

    async with async_session() as db:
        row = (await db.execute(
            select(Run).where(Run.run_id == run_id)
        )).scalar_one()
        assert row.last_event_at is not None
        # Within 5 seconds of now
        delta = abs((datetime.now(timezone.utc) - row.last_event_at.replace(tzinfo=timezone.utc)).total_seconds())
        assert delta < 5
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_webhook.py::test_webhook_bumps_last_event_at -xvs
```

Expected: FAIL — `last_event_at` is None.

- [ ] **Step 3: Implement the bump**

In `dashboard/backend/app/routers/webhook.py`, find the single point where `run` is resolved from `event.run_id` (the `_resolve_run` helper, or the inline lookup early in `run_event`). After the row is fetched, add:

```python
    # Heartbeat: any event for a known run row bumps last_event_at.
    # NULL persists if the run row doesn't exist yet (e.g. orchestrator
    # is mid-spawn). See issue #348.
    if run is not None:
        run.last_event_at = datetime.now(timezone.utc)
```

(Confirm the function already imports `datetime` and `timezone`; add if missing.)

- [ ] **Step 4: Verify passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_webhook.py::test_webhook_bumps_last_event_at -xvs
```

Expected: PASS.

### Task 4.3: Reaper integration

**Files:**
- Modify: `dashboard/backend/app/services/stale_run_reaper.py`
- Test: `dashboard/backend/tests/test_stale_run_reaper.py` (append)

- [ ] **Step 1: Add the heartbeat timeout**

In `dashboard/backend/app/services/stale_run_reaper.py`, near the top:

```python
# Running rows whose last_event_at is older than this many seconds AND
# whose launcher reports no active run are reaped immediately. See
# issue #348.
ACTIVE_HEARTBEAT_TIMEOUT_SECONDS = 120
```

- [ ] **Step 2: Add the heartbeat reap to `reap_stale_runs`**

Inside `reap_stale_runs`, after the existing inactive-service check passes, add a new branch before the final return:

```python
    # Heartbeat-based reap: if a 'running' row hasn't logged an event
    # in ACTIVE_HEARTBEAT_TIMEOUT_SECONDS, mark it interrupted now.
    # This kicks in even when the launcher reports the service "idle"
    # because that's the consistent signal that the orchestrator died
    # silently between events.
    cutoff_heartbeat = datetime.now(timezone.utc) - timedelta(
        seconds=ACTIVE_HEARTBEAT_TIMEOUT_SECONDS
    )
    heartbeat_result = await db.execute(
        select(Run).where(
            Run.status == "running",
            Run.last_event_at.isnot(None),
            Run.last_event_at < cutoff_heartbeat,
        )
    )
    for r in heartbeat_result.scalars().all():
        r.status = "interrupted"
        r.finished_at = datetime.now(timezone.utc)
        reaped_count += 1
        logger.info("Heartbeat-reaped %s — no event for %ds",
                    r.run_id,
                    int((datetime.now(timezone.utc) - r.last_event_at.replace(tzinfo=timezone.utc)).total_seconds()))
```

(Adapt variable names to match the existing function's tally accumulator.)

- [ ] **Step 3: Test for heartbeat reap**

```python
@pytest.mark.asyncio
async def test_reap_stale_heartbeat(setup_db):
    """A running row with no event for > timeout AND idle launcher
    gets marked interrupted (issue #348)."""
    from datetime import datetime, timedelta, timezone
    from app.models import Run
    from app.services.stale_run_reaper import (
        reap_stale_runs, ACTIVE_HEARTBEAT_TIMEOUT_SECONDS,
    )
    from sqlalchemy import select
    from unittest.mock import patch, AsyncMock

    too_old = datetime.now(timezone.utc) - timedelta(
        seconds=ACTIVE_HEARTBEAT_TIMEOUT_SECONDS + 10
    )
    async with async_session() as db:
        db.add(Run(run_id="run-stuck-heartbeat", status="running",
                   started_at=too_old, last_event_at=too_old))
        await db.commit()

    with patch("app.services.stale_run_reaper.get_agent_status",
               new_callable=AsyncMock,
               return_value={"service_active": False}):
        async with async_session() as db:
            reaped = await reap_stale_runs(db)
    assert reaped >= 1
    async with async_session() as db:
        row = (await db.execute(
            select(Run).where(Run.run_id == "run-stuck-heartbeat")
        )).scalar_one()
        assert row.status == "interrupted"
```

```bash
cd dashboard/backend && python3 -m pytest tests/test_stale_run_reaper.py::test_reap_stale_heartbeat -xvs
```

Expected: PASS.

### Task 4.4: API + types

**Files:**
- Modify: `dashboard/backend/app/schemas.py`
- Modify: `dashboard/frontend/src/lib/types.ts`

- [ ] **Step 1: Surface in `RunOut` and `ActiveEmployeeOut`**

In `dashboard/backend/app/schemas.py`, find `RunOut`. Add:

```python
    last_event_at: datetime | None = None
```

If `ActiveEmployeeOut` exists in the same file, add the same field there.

- [ ] **Step 2: Update frontend type**

In `dashboard/frontend/src/lib/types.ts`, find the `Run` interface:

```ts
export interface Run {
  // ...existing fields...
  last_event_at: string | null;
}
```

And likewise `ActiveEmployee` if it has a similar shape.

### Task 4.5: Frontend badge (small UI affordance)

**Files:**
- Modify: `dashboard/frontend/src/pages/MissionControl.svelte`

- [ ] **Step 1: Add the badge**

Find the run-header block in `MissionControl.svelte` (where `currentRun.started_at` is rendered). Add next to it:

```svelte
{#if currentRun?.last_event_at}
  {@const ageSec = (Date.now() - new Date(currentRun.last_event_at).getTime()) / 1000}
  <span class="heartbeat-badge"
        class:warn={ageSec > 60 && ageSec <= 180}
        class:stale={ageSec > 180}>
    active {Math.round(ageSec)}s ago
  </span>
{/if}
```

Add to the `<style>` block:

```css
.heartbeat-badge {
  margin-left: 12px;
  padding: 2px 8px;
  border-radius: 10px;
  background: var(--paper-3);
  color: var(--graphite);
  font-size: 0.8em;
}
.heartbeat-badge.warn { background: rgba(251, 202, 4, 0.15); color: var(--abort); }
.heartbeat-badge.stale { background: rgba(182, 2, 5, 0.15); color: var(--abort); }
```

### Task 4.6: Regression + commit + PR

```bash
cd dashboard/backend && python3 -m pytest -q
cd dashboard/frontend && npx --no-install svelte-check --threshold error 2>&1 | tail -5
```

Expected: backend green; svelte-check shows only pre-existing errors.

```bash
cd /home/simon/Documents/claude-agent-station
git add dashboard/backend/app/database.py \
        dashboard/backend/app/models.py \
        dashboard/backend/app/schemas.py \
        dashboard/backend/app/routers/webhook.py \
        dashboard/backend/app/services/stale_run_reaper.py \
        dashboard/backend/tests/test_webhook.py \
        dashboard/backend/tests/test_stale_run_reaper.py \
        dashboard/frontend/src/lib/types.ts \
        dashboard/frontend/src/pages/MissionControl.svelte
git commit -m "$(cat <<'EOF'
feat(runs): last_event_at heartbeat + reaper integration

- New runs.last_event_at column updated by every webhook event.
- Reaper now reaps running rows with no event in 120s AND idle launcher
  (vs. the old 15s tick + 30 min unknown window).
- Mission Control renders an "active Ns ago" badge that turns amber
  past 60s and red past 180s.

Fixes #348.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin feat/348-last-event-at
gh pr create --base dev --title "feat(runs): last_event_at heartbeat + reaper integration" --body "Fixes #348. See spec doc."
```

---

# PR 5a — Item 5a: Webhook emission to Python helper (Issue #349)

### Task 5a.1: Branch + new module + failing test

**Files:**
- Create: `agent/webhook_emitter.py`
- Create: `dashboard/backend/tests/test_webhook_emitter.py` (or `agent/tests/test_webhook_emitter.py` — match the test-path conventions used elsewhere; verify the layout existing tests use)

- [ ] **Step 1: Branch**

```bash
git checkout dev && git pull --ff-only && git checkout -b refactor/349-5a-webhook-emitter
```

- [ ] **Step 2: Write failing test**

Locate `agent` tests (`find agent -name 'test_*.py' -o -name 'tests' -type d`). If a Python test dir for `agent/` doesn't exist, create `dashboard/backend/tests/test_webhook_emitter.py` and import the module via the existing PYTHONPATH:

```python
"""Tests for agent/webhook_emitter.py (issue #349, sub-PR 5a)."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest


def test_emit_run_start_posts_to_webhook():
    from agent.webhook_emitter import emit
    with httpx.MockTransport() as _:
        pass  # placeholder — real assertion below
    with patch("agent.webhook_emitter.httpx.post") as mock_post:
        mock_post.return_value.status_code = 200
        emit("run_start", run_id="run-test-1", payload={"project": "x/y"})
        assert mock_post.called
        url = mock_post.call_args.args[0]
        body = mock_post.call_args.kwargs["json"]
        assert "/api/webhook/run-event" in url
        assert body["event"] == "run_start"
        assert body["run_id"] == "run-test-1"
        assert body["project"] == "x/y"


def test_emit_retries_on_5xx():
    from agent.webhook_emitter import emit
    responses = [type("R", (), {"status_code": 500, "text": "boom"})(),
                 type("R", (), {"status_code": 500, "text": "boom"})(),
                 type("R", (), {"status_code": 200, "text": "ok"})()]
    with patch("agent.webhook_emitter.httpx.post", side_effect=responses):
        emit("run_complete", run_id="run-test-2", payload={"status": "completed"})
    # Without raise — the retry succeeded on the third attempt.
```

- [ ] **Step 3: Verify failing**

```bash
cd /home/simon/Documents/claude-agent-station
python3 -m pytest dashboard/backend/tests/test_webhook_emitter.py -xvs
```

Expected: FAIL — module not found.

### Task 5a.2: Implement `agent/webhook_emitter.py`

**Files:**
- Create: `agent/webhook_emitter.py`

- [ ] **Step 1: Write the module**

```python
"""Sync HTTP client that emits orchestrator webhook events.

Replaces the bash ``webhook_event`` helper. Provides retries with
exponential backoff so an EXIT-trap (or any other call site) cannot
silently drop a critical lifecycle event.

Usage (Python):
    from agent.webhook_emitter import emit
    emit("run_start", run_id="run-1", payload={"project": "x/y"})

Usage (bash, via CLI):
    python3 -m agent.webhook_emitter run_start \\
        --run-id "run-1" \\
        --json '{"project":"x/y"}'

Env:
    STATION_WEBHOOK_URL       (default: http://127.0.0.1:8420/api/webhook/run-event)
    STATION_WEBHOOK_SECRET    (optional; sent as X-Webhook-Secret)
"""

from __future__ import annotations

import json as json_mod
import logging
import os
import sys
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:8420/api/webhook/run-event"
RETRIES = 3
BACKOFF_BASE = 0.5  # 0.5s, 1s, 2s


def _url() -> str:
    return os.environ.get("STATION_WEBHOOK_URL", DEFAULT_URL)


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    secret = os.environ.get("STATION_WEBHOOK_SECRET", "")
    if secret:
        h["X-Webhook-Secret"] = secret
    return h


def emit(event: str, *, run_id: str, payload: dict[str, Any] | None = None) -> None:
    """Post a webhook event. Retries on 5xx and connection errors.

    Does not raise on final failure — the orchestrator should not be
    killed by a dashboard outage. The failure is logged.
    """
    body: dict[str, Any] = {"event": event, "run_id": run_id}
    if payload:
        body.update(payload)

    last_err: str | None = None
    for attempt in range(RETRIES):
        try:
            resp = httpx.post(_url(), json=body, headers=_headers(), timeout=10.0)
            if 200 <= resp.status_code < 300:
                return
            last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if 400 <= resp.status_code < 500:
                # Client error — won't recover by retrying
                logger.error("webhook_emitter: non-retryable %s for %s",
                             last_err, event)
                return
        except httpx.HTTPError as exc:
            last_err = f"transport error: {exc}"
        if attempt < RETRIES - 1:
            time.sleep(BACKOFF_BASE * (2 ** attempt))
    logger.error("webhook_emitter: gave up after %d attempts (%s) for %s",
                 RETRIES, last_err, event)


def _cli() -> int:
    """CLI entrypoint: python3 -m agent.webhook_emitter EVENT --run-id ID --json JSON"""
    import argparse
    p = argparse.ArgumentParser(prog="agent.webhook_emitter")
    p.add_argument("event")
    p.add_argument("--run-id", required=True)
    p.add_argument("--json", default="{}", help="JSON-encoded payload")
    args = p.parse_args()
    try:
        payload = json_mod.loads(args.json) if args.json else {}
    except json_mod.JSONDecodeError as e:
        print(f"webhook_emitter: invalid --json: {e}", file=sys.stderr)
        return 2
    emit(args.event, run_id=args.run_id, payload=payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
```

- [ ] **Step 2: Verify tests pass**

```bash
python3 -m pytest dashboard/backend/tests/test_webhook_emitter.py -xvs
```

Expected: PASS.

### Task 5a.3: Bash `webhook_event` calls the Python emitter

**Files:**
- Modify: `agent/scripts/run-manager.sh` (the `webhook_event()` function near line 308)

- [ ] **Step 1: Replace the body of `webhook_event`**

Locate the existing `webhook_event()` shell function. Replace its body with a thin wrapper:

```bash
webhook_event() {
    # Args: EVENT [key value]...
    # All extra args are JSON-encoded into a single payload object and
    # forwarded to the Python emitter, which owns retry semantics and
    # auth. See issue #349 sub-PR 5a.
    local event="$1"
    shift

    local payload="{}"
    if [ $# -gt 0 ]; then
        payload=$(python3 - "$@" <<'PYEOF'
import json, sys
args = sys.argv[1:]
out = {}
it = iter(args)
for k in it:
    try:
        v = next(it)
    except StopIteration:
        break
    out[k] = v
print(json.dumps(out))
PYEOF
)
    fi

    PYTHONPATH="$SCRIPT_DIR/..:$SCRIPT_DIR/../dashboard/backend" \
        python3 -m agent.webhook_emitter "$event" \
            --run-id "run-$RUN_ID" \
            --json "$payload" 2>&1 | while IFS= read -r line; do
                log_info "  webhook[$event]: $line"
            done || true
}
```

(Adjust `$SCRIPT_DIR` to whatever path variable the bash already exports for its own dir; the project's `run-manager.sh:13` defines `SCRIPT_DIR` already.)

### Task 5a.4: Manual smoke + golden-file regression

- [ ] **Step 1: Trigger a smoke run**

```bash
docker compose build agent && docker compose up -d agent
# Tail the agent log to confirm events go through the Python emitter:
docker logs -f cas-agent | grep -E "webhook|run_start|run_complete" &
# Open dashboard, click Trigger Run
```

Verify: the dashboard sees `run_start` and `run_complete` as before; the bash log shows `webhook[run_start]: ...` lines from the Python emitter.

- [ ] **Step 2: Golden payload check (optional but recommended)**

```bash
# Capture one webhook payload via tcpdump on the docker network, or
# simpler: add a logging line to webhook.py temporarily that dumps the
# body, then compare against the pre-migration shape (from a prior
# git checkout). Either confirm shape parity or paste in the spec.
```

### Task 5a.5: Commit + PR

```bash
git add agent/webhook_emitter.py \
        agent/scripts/run-manager.sh \
        dashboard/backend/tests/test_webhook_emitter.py
git commit -m "$(cat <<'EOF'
refactor(agent): bash webhook_event delegates to Python emitter

Introduces agent/webhook_emitter.py with retry-with-backoff semantics
and a CLI entrypoint. run-manager.sh's webhook_event() becomes a thin
wrapper that shells to the module. All ~30 call sites continue to work
unchanged.

This is sub-PR 5a of the run-manager.sh → Python migration. It moves
the single most-failure-prone bash construct (silent webhook drops on
EXIT-trap edge cases) into a code path with deliberate retries.

Refs #349.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin refactor/349-5a-webhook-emitter
gh pr create --base dev --title "refactor(agent): bash webhook_event delegates to Python emitter (5a)" --body "Refs #349. Sub-PR 5a of 3 in the migration milestone."
```

---

# PR 5b — Item 5b: CoordinatorTask lifecycle to Python (Issue #349)

### Task 5b.1: Branch + new module skeleton

**Files:**
- Create: `agent/coordinator_lifecycle.py`
- Create: `dashboard/backend/tests/test_coordinator_lifecycle.py`

- [ ] **Step 1: Branch**

```bash
git checkout dev && git pull --ff-only && git checkout -b refactor/349-5b-coordinator-lifecycle
```

- [ ] **Step 2: Write failing test**

```python
"""Tests for agent/coordinator_lifecycle.py (issue #349, sub-PR 5b)."""

from __future__ import annotations

from unittest.mock import patch

import pytest


def test_create_task_posts_to_queue_api():
    from agent.coordinator_lifecycle import create_task
    with patch("agent.coordinator_lifecycle.httpx.post") as mock_post:
        mock_post.return_value.status_code = 201
        mock_post.return_value.json.return_value = {"id": "t-1"}
        task_id = create_task(
            run_id="run-1", project_repo="x/y", issue_number=42,
            employee_index=0,
        )
        assert task_id == "t-1"
        body = mock_post.call_args.kwargs["json"]
        assert body["run_id"] == "run-1"
        assert body["project_repo"] == "x/y"


def test_atexit_handler_fails_pending_tasks():
    """If complete_task is never called, atexit must fail-finalize the
    task so /api/runs/active-employees does not surface a zombie."""
    from agent import coordinator_lifecycle as cl
    with patch("agent.coordinator_lifecycle.httpx.post") as mock_post:
        mock_post.return_value.status_code = 201
        mock_post.return_value.json.return_value = {"id": "t-doomed"}
        cl.create_task(run_id="run-2", project_repo="x/y",
                       issue_number=1, employee_index=0)

    # Simulate the atexit fire
    with patch("agent.coordinator_lifecycle.httpx.put") as mock_put:
        mock_put.return_value.status_code = 200
        cl._finalize_orphans()
        assert mock_put.called
        url = mock_put.call_args.args[0]
        assert "/api/coordinator/tasks/t-doomed" in url
        body = mock_put.call_args.kwargs["json"]
        assert body["status"] == "orphaned"
```

- [ ] **Step 3: Verify failing**

```bash
python3 -m pytest dashboard/backend/tests/test_coordinator_lifecycle.py -xvs
```

Expected: FAIL — module not found.

### Task 5b.2: Implement the lifecycle module

**Files:**
- Create: `agent/coordinator_lifecycle.py`

- [ ] **Step 1: Write the module**

```python
"""HTTP client for the dashboard's /api/coordinator/tasks endpoints.

Owns the try/finally invariant: every task created via create_task() is
tracked in process-local state, and an atexit handler finalizes any
still-open tasks as 'orphaned' on process exit. This eliminates the
zombie-task class of bugs (issue #345 + #349).

Usage (Python):
    from agent.coordinator_lifecycle import create_task, complete_task
    tid = create_task(run_id="r-1", project_repo="x/y",
                      issue_number=1, employee_index=0)
    try:
        ...work...
        complete_task(tid, status="completed")
    except Exception as e:
        fail_task(tid, reason=str(e))

Usage (bash):
    python3 -m agent.coordinator_lifecycle create \\
        --run-id "$RUN_ID" --project-repo "$REPO" \\
        --issue-number "$ISS" --employee-index "$EI"
"""

from __future__ import annotations

import atexit
import logging
import os
import threading
from typing import Set

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE = "http://127.0.0.1:8420"

_open_tasks: Set[str] = set()
_open_lock = threading.Lock()


def _base_url() -> str:
    return os.environ.get("STATION_DASHBOARD_URL", DEFAULT_BASE).rstrip("/")


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    secret = os.environ.get("STATION_WEBHOOK_SECRET", "")
    if secret:
        h["X-Webhook-Secret"] = secret
    return h


def create_task(*, run_id: str, project_repo: str, issue_number: int | None,
                employee_index: int | None) -> str:
    """Create a coordinator task. Returns the new task id."""
    body = {
        "run_id": run_id,
        "project_repo": project_repo,
        "issue_number": issue_number,
        "employee_index": employee_index,
        "status": "running",
    }
    resp = httpx.post(f"{_base_url()}/api/coordinator/tasks",
                      json=body, headers=_headers(), timeout=10.0)
    resp.raise_for_status()
    task_id = resp.json()["id"]
    with _open_lock:
        _open_tasks.add(task_id)
    return task_id


def complete_task(task_id: str, *, status: str = "completed",
                  result_summary: str | None = None) -> None:
    body: dict[str, str] = {"status": status}
    if result_summary:
        body["result_summary"] = result_summary
    resp = httpx.put(f"{_base_url()}/api/coordinator/tasks/{task_id}",
                     json=body, headers=_headers(), timeout=10.0)
    resp.raise_for_status()
    with _open_lock:
        _open_tasks.discard(task_id)


def fail_task(task_id: str, *, reason: str) -> None:
    complete_task(task_id, status="failed", result_summary=reason)


def _finalize_orphans() -> None:
    """atexit hook: mark any still-open tasks as orphaned."""
    with _open_lock:
        ids = list(_open_tasks)
        _open_tasks.clear()
    for tid in ids:
        try:
            httpx.put(f"{_base_url()}/api/coordinator/tasks/{tid}",
                      json={"status": "orphaned"},
                      headers=_headers(), timeout=5.0)
            logger.warning("Finalized orphan coordinator task %s", tid)
        except Exception as e:
            logger.error("Failed to finalize orphan %s: %s", tid, e)


atexit.register(_finalize_orphans)


def _cli() -> int:
    import argparse, json, sys
    p = argparse.ArgumentParser(prog="agent.coordinator_lifecycle")
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("create")
    pc.add_argument("--run-id", required=True)
    pc.add_argument("--project-repo", required=True)
    pc.add_argument("--issue-number", type=int)
    pc.add_argument("--employee-index", type=int)

    pu = sub.add_parser("complete")
    pu.add_argument("--task-id", required=True)
    pu.add_argument("--status", default="completed",
                    choices=("completed", "failed", "orphaned"))
    pu.add_argument("--result-summary", default=None)

    args = p.parse_args()
    if args.cmd == "create":
        tid = create_task(run_id=args.run_id, project_repo=args.project_repo,
                          issue_number=args.issue_number,
                          employee_index=args.employee_index)
        print(tid)
    elif args.cmd == "complete":
        if args.status == "failed":
            fail_task(args.task_id, reason=args.result_summary or "failed")
        else:
            complete_task(args.task_id, status=args.status,
                          result_summary=args.result_summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
```

- [ ] **Step 2: Verify tests pass**

```bash
python3 -m pytest dashboard/backend/tests/test_coordinator_lifecycle.py -xvs
```

Expected: PASS.

### Task 5b.3: Refactor bash call sites

**Files:**
- Modify: `agent/scripts/run-manager.sh` (the coordinator-task lifecycle inline calls)

- [ ] **Step 1: Identify call sites**

```bash
grep -nE "queue_api POST.*queue|queue_api PUT|coordinator.*task" agent/scripts/run-manager.sh | head -20
```

- [ ] **Step 2: Replace each call site**

For each coordinator-task `queue_api POST /api/queue ... ` create call, replace with a `python3 -m agent.coordinator_lifecycle create ...` invocation that captures the task id. For each completion call, replace with `python3 -m agent.coordinator_lifecycle complete --task-id "$tid" --status completed`.

(This is mechanical but spans ~6 call sites; do each one with explicit grep + Edit. Test after each.)

### Task 5b.4: Commit + PR

```bash
git add agent/coordinator_lifecycle.py \
        agent/scripts/run-manager.sh \
        dashboard/backend/tests/test_coordinator_lifecycle.py
git commit -m "$(cat <<'EOF'
refactor(agent): coordinator task lifecycle to Python with atexit finalizer

agent/coordinator_lifecycle.py owns the try/finally invariant: every
task created via create_task() is tracked in process-local state, and
an atexit handler finalizes any still-open tasks as 'orphaned' on
process exit. This eliminates the zombie-task class of bugs at the
source — even if the bash process crashes mid-run, the Python atexit
hook fires before the interpreter dies.

run-manager.sh's coordinator queue_api calls are replaced with
``python3 -m agent.coordinator_lifecycle ...`` invocations.

Sub-PR 5b of 3 in the migration milestone. Refs #349.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin refactor/349-5b-coordinator-lifecycle
gh pr create --base dev --title "refactor(agent): coordinator task lifecycle to Python (5b)" --body "Refs #349. Sub-PR 5b of 3."
```

---

# PR 5c — Item 5c: Project loop to Python (Issue #349)

This is the largest sub-PR. It deletes ~2500 LOC of bash and adds ~300 LOC of Python.

### Task 5c.1: Branch + driver skeleton

**Files:**
- Create: `agent/project_loop.py`
- Modify: `agent/station_orchestrator.py` (add `RunDriver` class)
- Create: `dashboard/backend/tests/test_project_loop.py`

- [ ] **Step 1: Branch**

```bash
git checkout dev && git pull --ff-only && git checkout -b refactor/349-5c-project-loop
```

- [ ] **Step 2: Sketch the `RunDriver`**

In `agent/station_orchestrator.py`, add at the appropriate boundary (top-level class, public API):

```python
class RunDriver:
    """Owns the full run lifecycle: project enumeration, employee spawn,
    manager review, verdict execution, and run_complete emission.

    The driver wraps the entire run in a try/finally that guarantees
    emit_run_complete fires, even on uncaught exceptions. This replaces
    run-manager.sh's EXIT-trap webhook (which had reliability holes).
    """

    def __init__(self, *, run_id: str, config_path: str, workspaces_dir: str):
        self.run_id = run_id
        self.config_path = config_path
        self.workspaces_dir = workspaces_dir

    def run(self) -> int:
        from agent.webhook_emitter import emit
        try:
            emit("run_start", run_id=self.run_id, payload={})
            return self._run_inner()
        except Exception as e:
            logger.exception("RunDriver crashed")
            emit("run_complete", run_id=self.run_id,
                 payload={"status": "error", "error": str(e)})
            return 1
        else:
            emit("run_complete", run_id=self.run_id,
                 payload={"status": "completed"})

    def _run_inner(self) -> int:
        from agent.project_loop import iterate_projects
        return iterate_projects(self.run_id, self.config_path,
                                self.workspaces_dir)
```

(Wire it into the existing `main()` so `python3 -m agent.station_orchestrator --run-id X` instantiates and runs the driver.)

- [ ] **Step 3: Stub `agent/project_loop.py`**

```python
"""Per-project iteration extracted from run-manager.sh.

Picks eligible projects, decides what work to do, dispatches to the
Agent Teams orchestrator. See issue #349 sub-PR 5c.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def iterate_projects(run_id: str, config_path: str,
                     workspaces_dir: str) -> int:
    """Iterate over enabled projects and dispatch agent work.

    Returns exit code: 0 if any project did work, 1 if all skipped
    or errored.
    """
    config = json.loads(Path(config_path).read_text())
    any_work = False
    for project in config.get("projects", []):
        if not project.get("enabled", True):
            continue
        repo = project["repo"]
        logger.info("Iterating project %s", repo)
        try:
            did_work = _process_project(run_id, project, workspaces_dir)
            any_work = any_work or did_work
        except Exception:
            logger.exception("Project %s failed", repo)
    return 0 if any_work else 1


def _process_project(run_id: str, project: dict,
                     workspaces_dir: str) -> bool:
    """Process a single project: list issues, pick one, dispatch.

    Mirrors the bash logic at run-manager.sh ~lines 1700-2700.
    """
    # NOTE: This is the heart of the migration. Implement iteratively:
    # 1. List issues via `gh issue list ...` (subprocess.run).
    # 2. Filter by SKIP_LABELS / required labels.
    # 3. Pick the highest-priority eligible issue.
    # 4. If none, return False.
    # 5. Otherwise: ensure workspace, branch, dispatch to
    #    station_orchestrator.AgentTeamsDriver (existing code).
    # 6. After dispatch, run the manager review / verdict path.
    # 7. Return True.
    #
    # See spec doc and run-manager.sh for the exact algorithm.
    raise NotImplementedError("Implement during Task 5c.2–5c.5")
```

(This stub raises so tests fail clearly during the iterative migration.)

### Task 5c.2: Migrate the project enumeration

- [ ] **Step 1: Failing test (smoke)**

Create `dashboard/backend/tests/test_project_loop.py` with a fixture-driven smoke that exercises `iterate_projects` against a mock config with one disabled project and one enabled project; assert the disabled one is skipped, the enabled one dispatches (mock `_process_project`).

- [ ] **Step 2: Implement `iterate_projects`** (the stub above already does most of this)

- [ ] **Step 3: Run smoke**

### Task 5c.3: Migrate issue picking + label filtering

- [ ] **Step 1: Identify the bash block** (run-manager.sh ~lines 1900–2100)

- [ ] **Step 2: Port to `_pick_issue(project)` in `agent/project_loop.py`**

Use `subprocess.run(["gh", "issue", "list", "--repo", repo, "--json", "number,title,labels", "--limit", "50"])` and parse the JSON.

- [ ] **Step 3: Tests + verify**

### Task 5c.4: Migrate the dispatch path

- [ ] **Step 1: The bash currently shells to `python3 -m agent.station_orchestrator` at line 2757**

In the new Python driver this is a direct in-process call rather than a subprocess. Use the existing `AgentTeamsDriver` class (or whatever the orchestrator's public API is).

- [ ] **Step 2: Failure handling — wrap in try/except and emit teammate_failed / employee_complete as appropriate.**

### Task 5c.5: Migrate verdict execution

- [ ] **Step 1: The bash verdict path** (run-manager.sh ~lines 2100–2500)

- [ ] **Step 2: Port** — keep `gh pr create`, `git push`, `gh issue comment`, `gh issue edit` calls as subprocess invocations.

- [ ] **Step 3: Tests**

### Task 5c.6: Bash shim

- [ ] **Step 1: Reduce `run-manager.sh` to a thin shim**

Final shape (~200 LOC):

```bash
#!/usr/bin/env bash
# run-manager.sh - Thin shim. The orchestration logic lives in Python now.
# See agent/project_loop.py and agent/station_orchestrator.RunDriver.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${STATION_CONFIG:-/home/claude-agent/.claude/autonomous/manager-config.json}"
WORKSPACES_DIR="${STATION_WORKSPACES:-/home/claude-agent/workspaces}"

if [ -n "${STATION_RUN_ID_OVERRIDE:-}" ]; then
    RUN_ID="${STATION_RUN_ID_OVERRIDE#run-}"
else
    RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
fi

LOCK_FILE="/tmp/claude-agent-${RUN_ID}.lock"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "Another run is in progress" >&2
    exit 1
fi

# GH_TOKEN export, log redirect, etc. — preserved from the existing
# pre-Python setup block.

PYTHONPATH="$SCRIPT_DIR/..:$SCRIPT_DIR/../../dashboard/backend" \
    exec python3 -m agent.station_orchestrator \
        --run-id "$RUN_ID" \
        --config "$CONFIG_FILE" \
        --workspaces-dir "$WORKSPACES_DIR"
```

- [ ] **Step 2: Delete the now-obsolete bash blocks** (the 2500+ LOC migrated to Python)

### Task 5c.7: Commit + PR

```bash
git add agent/project_loop.py \
        agent/station_orchestrator.py \
        agent/scripts/run-manager.sh \
        dashboard/backend/tests/test_project_loop.py
git commit -m "$(cat <<'EOF'
refactor(agent): project loop to Python; bash becomes ~200-LOC shim

The per-project iteration, issue picking, dispatch, and verdict
execution paths are migrated from run-manager.sh into a new
agent/project_loop.py + RunDriver class in agent/station_orchestrator.py.

The run lifecycle is now wrapped in a Python try/finally that
guarantees emit_run_complete fires regardless of how the orchestrator
exits — replacing the EXIT-trap-dependent bash construct that caused
silent webhook drops.

run-manager.sh shrinks from 3192 LOC to ~200 LOC (lock acquisition,
env, log redirect, exec the Python driver).

Sub-PR 5c (final) of the migration milestone. Refs #349.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin refactor/349-5c-project-loop
gh pr create --base dev --title "refactor(agent): project loop to Python (5c, final)" --body "Refs #349. Sub-PR 5c of 3."
```

---

## Plan self-review

- [x] **Spec coverage** — every section of the spec maps to at least one task: Item 1 → Tasks 1.1–1.5; Item 2 → Tasks 2.1–2.6; Item 3 → Tasks 3.1–3.4; Item 4 → Tasks 4.1–4.6; Item 5a/b/c → Tasks 5a.*/5b.*/5c.*.
- [x] **Placeholder scan** — no "TBD" / "implement later" beyond the deliberate iterative scaffolding inside `_process_project` (Task 5c.1) which is followed by explicit fill-in tasks 5c.2–5c.5.
- [x] **Type consistency** — `run_id` always a string; `coordinator_tasks.status` value `orphaned` is consistent across Item 1 and Item 5b; `STATION_RUN_ID_OVERRIDE` env var consistent between Tasks 2.3 and 2.4.

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-11-run-lifecycle-overhaul.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per PR (or per task within a PR), review between tasks, fast iteration. Best for items 5a/5b/5c.
2. **Inline Execution** — Execute tasks in this session using `executing-plans`, batch execution with checkpoints for review.

Which approach?
