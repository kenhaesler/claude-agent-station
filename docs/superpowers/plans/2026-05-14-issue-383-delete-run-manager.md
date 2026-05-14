# Delete `run-manager.sh`, Port Phases to Python — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove `agent/scripts/run-manager.sh` (3309 LOC of bash) by porting each of its nine production phases to dedicated Python modules under `agent/`, then rewiring `agent/project_loop.py::iterate_projects` to call them in-process — eliminating the bash/Python boundary that hid PR #381's bug cascade.

**Architecture:** Each former bash phase becomes a single-responsibility Python module (`preflight.py`, `workspace_setup.py`, `queue_recovery.py`, `rate_limit.py`, `manager_review.py`, `integration_branch.py`, `digest.py`). `iterate_projects` orchestrates them top-to-bottom: preflight → queue recovery → per-project (workspace setup → `orchestrate_project` async block → manager review → verdict execution → optional merge-to-dev) → digest. `RunDriver._read_bash_telemetry` is replaced with an in-process counter passed through from the stream-state. The `STATION_LAUNCHER_USE_BASH=1` panic-revert flag is removed at the end.

**Tech Stack:** Python 3.11+, existing `agent/` package conventions (type hints, dataclasses), `subprocess.run` for git/gh shell-outs, `pytest` + `pytest-asyncio`, `unittest.mock` for boundary mocking.

---

## File Structure

| Path | Responsibility |
|---|---|
| `agent/preflight.py` | **New** — runs OS-level preconditions (config readable, deps present, OAuth token refreshable, rate-limit not tripped). Replaces `run-manager.sh::preflight` (lines 693–782). |
| `agent/workspace_setup.py` | **New** — clone/refresh/checkout/worktree-prune a project workspace. Replaces `run-manager.sh::setup_workspace` (lines 836–896). |
| `agent/queue_recovery.py` | **New** — purge completed queue items, resume paused ones, recover orphans from dead runs. Replaces `queue_complete_item`/`queue_reject_item`/`queue_fail_item` (lines 370–429) plus their callers. |
| `agent/rate_limit.py` | **New** — per-day/per-hour session caps reading the same JSON sidecar bash writes. Replaces `check_rate_limit` (612) + `record_session` (641). |
| `agent/manager_review.py` | **New** — invoke the manager via `claude -p` against a review package, parse verdicts. Replaces `run_manager_review` (1885–2024). |
| `agent/integration_branch.py` | **New** — Python port of `agent/scripts/integration-branch.sh::merge_to_dev` (lines 154–296). The bash file stays for ad-hoc cron use; the Python implementation is what runs from a live run. |
| `agent/digest.py` | **New** — markdown digest writer. Replaces `write_digest` (2624–2662). |
| `agent/project_loop.py` | Rewrite `iterate_projects` to call the new modules directly. Remove the `subprocess.Popen([str(runmgr), "--internal-iterate"])` path, the `_terminate_child` helper (lines 192–217), and the `_BASH_SIGTERM_*` constants. |
| `agent/station_orchestrator.py` | Extract `orchestrate_project(project, config, run_id, workspaces_dir)` — the per-project body of today's `orchestrate` — and add it as a public coroutine that `iterate_projects` calls. Delete `RunDriver._read_bash_telemetry` (lines 2323–2340); replace it with `_finalize_telemetry(stream_state)` that copies counters in-process. |
| `agent/launcher.py` | Remove `USE_BASH_LAUNCHER` flag (line 41) and its branch (lines 365–367); remove the `RUN_MANAGER` env var read (line 34) and the `if USE_BASH_LAUNCHER and not RUN_MANAGER.is_file()` guard (line 314). The launcher command is always the Python driver. |
| `agent/scripts/run-manager.sh` | **Delete** at the end of the migration (final task). |
| `dashboard/backend/tests/test_preflight.py` | **New** — happy + failure paths for `run_preflight`. |
| `dashboard/backend/tests/test_workspace_setup.py` | **New** — fresh-clone, refresh, prune, bad-remote. |
| `dashboard/backend/tests/test_queue_recovery.py` | **New** — purge, resume, recover-orphan, ignore-current. |
| `dashboard/backend/tests/test_rate_limit.py` | **New** — per-day cap, per-hour cap, fresh, malformed. |
| `dashboard/backend/tests/test_manager_review.py` | **New** — happy, malformed JSON, non-zero exit, empty package. |
| `dashboard/backend/tests/test_integration_branch_py.py` | **New** — push-ok, push-retry, PR-create, merge-conflict, dev-bootstrap. |
| `dashboard/backend/tests/test_digest_py.py` | **New** — empty, multi-verdict, verdict-with-error. |
| `dashboard/backend/tests/test_iterate_projects_python.py` | **New** — end-to-end mocked `iterate_projects` (no bash, no real network). |

---

## Tasks

### Task 1 — Extract `orchestrate_project` from `orchestrate` so `iterate_projects` can drive it directly

The current `orchestrate(config, run_id, workspaces_dir)` does *all* projects internally. After #383, `iterate_projects` owns the outer loop and per-project setup. We extract the body of `orchestrate`'s `for project in ...` block into a public coroutine.

**Step 1: Write the failing test.**

Create `dashboard/backend/tests/test_orchestrate_project_extraction.py`:

```python
"""Verify orchestrate_project exists and accepts the documented signature (#383)."""
from __future__ import annotations

import inspect


def test_orchestrate_project_signature():
    """orchestrate_project(project, config, run_id, workspaces_dir) -> int coroutine."""
    from agent import station_orchestrator as so

    assert hasattr(so, "orchestrate_project"), (
        "orchestrate_project must be extracted from orchestrate() (issue #383)"
    )
    assert inspect.iscoroutinefunction(so.orchestrate_project), (
        "orchestrate_project must be `async def`"
    )
    sig = inspect.signature(so.orchestrate_project)
    expected_params = ["project", "config", "run_id", "workspaces_dir"]
    assert list(sig.parameters.keys())[:4] == expected_params, (
        f"orchestrate_project signature must start with {expected_params}; "
        f"got {list(sig.parameters.keys())}"
    )
```

**Step 2: Run the test — confirm it fails.**

```
$ python -m pytest dashboard/backend/tests/test_orchestrate_project_extraction.py -v
```

Expected:

```
FAILED ... test_orchestrate_project_signature - AssertionError: orchestrate_project must be extracted ...
```

**Step 3: Implementation — extract the per-project body.**

In `agent/station_orchestrator.py`, locate the `async def orchestrate(...)` body. The per-project loop currently starts at around line 1750 (`for project in enabled_projects:`). Split it into two coroutines:

```python
async def orchestrate_project(
    project: dict, config: dict, run_id: str, workspaces_dir: str,
) -> int:
    """Run the Agent Teams session for a single project. Returns project-level exit code.

    Extracted from orchestrate() in #383 so iterate_projects (Python-only)
    can drive per-project work directly without delegating the outer loop
    to bash.
    """
    # Move the existing body of the `for project in ...` block here, verbatim,
    # adjusting indentation. All references to `repo`, `issues`, etc. inside
    # the block continue to work because we copy the whole block.
    # ... (existing per-project setup code) ...
    return 0  # or whatever exit code path the original block computes
```

Then `orchestrate(config, run_id, workspaces_dir)` becomes:

```python
async def orchestrate(config: dict, run_id: str, workspaces_dir: str) -> int:
    """Outer driver: iterate over enabled projects, run each one in sequence."""
    exit_code = 0
    for project in enabled_projects(config):
        proj_rc = await orchestrate_project(project, config, run_id, workspaces_dir)
        if proj_rc != 0:
            exit_code = proj_rc
    return exit_code
```

(`enabled_projects(config)` may need to be extracted as a small helper too — define it as `[p for p in config.get("projects", []) if p.get("enabled", True)]`.)

**Step 4: Run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_orchestrate_project_extraction.py -v
```

Expected:

```
PASSED ... test_orchestrate_project_signature
```

Also run the existing orchestrator suite to confirm no regressions:

```
$ python -m pytest dashboard/backend/tests/test_orchestrator_wiring.py dashboard/backend/tests/test_orchestrator_clientsdk.py -q
```

Expected: green.

**Step 5: Commit.**

```
$ git add agent/station_orchestrator.py dashboard/backend/tests/test_orchestrate_project_extraction.py
$ git commit -m "refactor(orchestrator): extract orchestrate_project for in-process project loop"
```

---

### Task 2 — Port `preflight` from bash to `agent/preflight.py`

The bash `preflight()` function (lines 693–782) checks: config readable, `gh` and `git` on PATH, OAuth token present, rate limit not tripped, log directory writable.

**Step 1: Write the failing test.**

Create `dashboard/backend/tests/test_preflight.py`:

```python
"""Tests for agent.preflight (issue #383 bash port)."""
from __future__ import annotations

import json
import pytest


def test_preflight_passes_on_clean_config(tmp_path, monkeypatch):
    from agent.preflight import run_preflight, PreflightError

    cfg = tmp_path / "manager-config.json"
    cfg.write_text(json.dumps({
        "projects": [{"name": "owner/repo", "enabled": True}],
        "limits": {"max_concurrent_employees": 1},
    }))
    monkeypatch.setenv("CLAUDE_OAUTH_TOKEN", "sk-test")
    # Pretend gh/git are present.
    monkeypatch.setattr("agent.preflight._has_binary", lambda name: True)
    monkeypatch.setattr("agent.preflight._rate_limit_tripped", lambda: False)

    # Should not raise.
    run_preflight(str(cfg))


def test_preflight_raises_on_missing_config(tmp_path):
    from agent.preflight import run_preflight, PreflightError

    with pytest.raises(PreflightError, match="config"):
        run_preflight(str(tmp_path / "missing.json"))


def test_preflight_raises_on_missing_dependency(tmp_path, monkeypatch):
    from agent.preflight import run_preflight, PreflightError

    cfg = tmp_path / "manager-config.json"
    cfg.write_text(json.dumps({"projects": []}))
    monkeypatch.setattr("agent.preflight._has_binary", lambda name: name != "gh")

    with pytest.raises(PreflightError, match="gh"):
        run_preflight(str(cfg))


def test_preflight_raises_on_oauth_refresh_failure(tmp_path, monkeypatch):
    from agent.preflight import run_preflight, PreflightError

    cfg = tmp_path / "manager-config.json"
    cfg.write_text(json.dumps({"projects": []}))
    monkeypatch.setattr("agent.preflight._has_binary", lambda name: True)
    monkeypatch.setattr("agent.preflight._refresh_oauth_token", lambda: False)
    monkeypatch.setattr("agent.preflight._rate_limit_tripped", lambda: False)
    monkeypatch.delenv("CLAUDE_OAUTH_TOKEN", raising=False)

    with pytest.raises(PreflightError, match="OAuth"):
        run_preflight(str(cfg))


def test_preflight_raises_when_rate_limit_tripped(tmp_path, monkeypatch):
    from agent.preflight import run_preflight, PreflightError

    cfg = tmp_path / "manager-config.json"
    cfg.write_text(json.dumps({"projects": []}))
    monkeypatch.setenv("CLAUDE_OAUTH_TOKEN", "sk-test")
    monkeypatch.setattr("agent.preflight._has_binary", lambda name: True)
    monkeypatch.setattr("agent.preflight._rate_limit_tripped", lambda: True)

    with pytest.raises(PreflightError, match="rate limit"):
        run_preflight(str(cfg))
```

**Step 2: Run the test — confirm it fails.**

```
$ python -m pytest dashboard/backend/tests/test_preflight.py -v
```

Expected: 5 import errors / collection failures (`agent.preflight` does not exist yet).

**Step 3: Implementation — create `agent/preflight.py`.**

```python
"""Preflight checks executed before any project work begins.

Python port of agent/scripts/run-manager.sh::preflight (issue #383).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class PreflightError(RuntimeError):
    """Raised when a preflight check fails."""


_REQUIRED_BINARIES = ("git", "gh", "claude")


def _has_binary(name: str) -> bool:
    return shutil.which(name) is not None


def _refresh_oauth_token() -> bool:
    """Invoke agent/scripts/refresh-token.py. Returns True on success."""
    script = Path(__file__).resolve().parent / "scripts" / "refresh-token.py"
    if not script.exists():
        logger.warning("preflight: refresh-token.py not found at %s", script)
        return False
    result = subprocess.run(
        ["python3", str(script)],
        capture_output=True, text=True, timeout=30,
    )
    return result.returncode == 0


def _rate_limit_tripped() -> bool:
    from agent.rate_limit import is_tripped
    return is_tripped()


def run_preflight(config_path: str) -> None:
    """Run all preflight checks. Raises PreflightError on the first failure."""
    cfg_path = Path(config_path)
    if not cfg_path.is_file():
        raise PreflightError(f"config not readable: {cfg_path}")
    try:
        json.loads(cfg_path.read_text())
    except json.JSONDecodeError as exc:
        raise PreflightError(f"config invalid JSON: {exc}") from exc

    for binary in _REQUIRED_BINARIES:
        if not _has_binary(binary):
            raise PreflightError(f"required binary not on PATH: {binary}")

    if not os.environ.get("CLAUDE_OAUTH_TOKEN"):
        if not _refresh_oauth_token():
            raise PreflightError("OAuth token absent and refresh failed")

    if _rate_limit_tripped():
        raise PreflightError("rate limit tripped — refusing to start a new run")

    logger.info("preflight: all checks passed")
```

Also stub `agent/rate_limit.py` (full impl in Task 5) with just `def is_tripped() -> bool: return False` so the import resolves.

**Step 4: Run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_preflight.py -v
```

Expected:

```
PASSED ... test_preflight_passes_on_clean_config
PASSED ... test_preflight_raises_on_missing_config
PASSED ... test_preflight_raises_on_missing_dependency
PASSED ... test_preflight_raises_on_oauth_refresh_failure
PASSED ... test_preflight_raises_when_rate_limit_tripped
```

**Step 5: Commit.**

```
$ git add agent/preflight.py agent/rate_limit.py dashboard/backend/tests/test_preflight.py
$ git commit -m "feat(preflight): port bash preflight to agent/preflight.py"
```

---

### Task 3 — Port `setup_workspace` to `agent/workspace_setup.py`

The bash `setup_workspace()` (lines 836–896) clones the repo if absent, otherwise `git fetch` + `git checkout <base>` + `git pull`, then `git worktree prune`.

**Step 1: Write the failing test.**

Create `dashboard/backend/tests/test_workspace_setup.py`:

```python
"""Tests for agent.workspace_setup (issue #383 bash port)."""
from __future__ import annotations

import subprocess
import pytest
from unittest.mock import MagicMock, patch


def _git_ok(cmd, *a, **kw):
    return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")


def test_fresh_clone(tmp_path, monkeypatch):
    from agent.workspace_setup import ensure_workspace

    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=_git_ok))
    monkeypatch.setattr("agent.workspace_setup.Path.exists", lambda self: False)

    project = {"name": "owner/repo", "base_branch": "main"}
    path = ensure_workspace(project, str(tmp_path))

    # Should have called git clone at least once.
    calls = subprocess.run.call_args_list  # type: ignore[attr-defined]
    assert any("clone" in str(c) for c in calls), "ensure_workspace must clone when path is missing"
    assert "owner/repo" in path or "repo" in path


def test_refresh_existing(tmp_path, monkeypatch):
    from agent.workspace_setup import ensure_workspace

    (tmp_path / "owner__repo").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=_git_ok))

    project = {"name": "owner/repo", "base_branch": "main"}
    ensure_workspace(project, str(tmp_path))

    calls = subprocess.run.call_args_list  # type: ignore[attr-defined]
    cmd_strs = [str(c) for c in calls]
    assert any("fetch" in s for s in cmd_strs), "must git fetch on existing workspace"
    assert any("checkout" in s for s in cmd_strs), "must git checkout the base branch"


def test_worktree_prune_runs(tmp_path, monkeypatch):
    from agent.workspace_setup import ensure_workspace

    (tmp_path / "owner__repo").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=_git_ok))

    ensure_workspace({"name": "owner/repo"}, str(tmp_path))

    calls = subprocess.run.call_args_list  # type: ignore[attr-defined]
    assert any("worktree" in str(c) and "prune" in str(c) for c in calls), (
        "ensure_workspace must run `git worktree prune`"
    )


def test_bad_remote_raises(tmp_path, monkeypatch):
    from agent.workspace_setup import ensure_workspace, WorkspaceError

    def _fail(cmd, *a, **kw):
        return subprocess.CompletedProcess(args=cmd, returncode=128, stdout="", stderr="fatal: ...")

    monkeypatch.setattr("agent.workspace_setup.Path.exists", lambda self: False)
    monkeypatch.setattr("agent.workspace_setup.subprocess.run", MagicMock(side_effect=_fail))

    with pytest.raises(WorkspaceError, match="clone"):
        ensure_workspace({"name": "owner/bad"}, str(tmp_path))
```

**Step 2: Run the test — confirm it fails.**

```
$ python -m pytest dashboard/backend/tests/test_workspace_setup.py -v
```

Expected: 4 import-error / collection failures.

**Step 3: Implementation — create `agent/workspace_setup.py`.**

```python
"""Workspace setup: clone/refresh/prune a project repo.

Python port of agent/scripts/run-manager.sh::setup_workspace (issue #383).
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class WorkspaceError(RuntimeError):
    """Raised when workspace setup fails."""


def _slug(name: str) -> str:
    """owner/repo → owner__repo (filesystem-safe)."""
    return name.replace("/", "__")


def ensure_workspace(project: dict, workspaces_dir: str) -> str:
    """Clone or refresh the project workspace. Returns the absolute path."""
    repo = project["name"]
    base = project.get("base_branch", "main")
    ws_root = Path(workspaces_dir)
    ws_root.mkdir(parents=True, exist_ok=True)
    workspace = ws_root / _slug(repo)

    if not workspace.exists():
        logger.info("workspace: cloning %s -> %s", repo, workspace)
        url = f"https://github.com/{repo}.git"
        result = subprocess.run(
            ["git", "clone", url, str(workspace)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise WorkspaceError(f"clone failed: {result.stderr.strip()}")
    else:
        logger.info("workspace: refreshing %s", workspace)
        for cmd in (
            ["git", "fetch", "--all", "--prune"],
            ["git", "checkout", base],
            ["git", "pull", "--ff-only"],
        ):
            result = subprocess.run(cmd, cwd=str(workspace), capture_output=True, text=True)
            if result.returncode != 0:
                logger.warning("workspace: %s exited %d: %s",
                               " ".join(cmd), result.returncode, result.stderr.strip())

    # Prune stale worktrees from prior runs.
    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=str(workspace), capture_output=True, text=True,
    )
    return str(workspace)
```

**Step 4: Run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_workspace_setup.py -v
```

Expected: 4 passes.

**Step 5: Commit.**

```
$ git add agent/workspace_setup.py dashboard/backend/tests/test_workspace_setup.py
$ git commit -m "feat(workspace): port setup_workspace to agent/workspace_setup.py"
```

---

### Task 4 — Port queue-recovery functions to `agent/queue_recovery.py`

The bash `queue_complete_item`/`queue_reject_item`/`queue_fail_item` (lines 370–429) plus their callers loop over the queue at the start of each run: complete finished items, recover orphans from dead runs, resume paused items.

**Step 1: Write the failing test.**

Create `dashboard/backend/tests/test_queue_recovery.py`:

```python
"""Tests for agent.queue_recovery (issue #383 bash port)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_purge_completed_items_calls_complete(monkeypatch):
    from agent import queue_recovery

    fake_items = [
        {"id": "q1", "status": "running", "run_id": "run-old", "issue_number": 1},
        {"id": "q2", "status": "running", "run_id": "run-cur", "issue_number": 2},
    ]

    list_calls = MagicMock(return_value=fake_items)
    complete_calls = MagicMock(return_value=None)
    is_alive = MagicMock(side_effect=lambda rid: rid == "run-cur")

    monkeypatch.setattr(queue_recovery, "_list_running_items", list_calls)
    monkeypatch.setattr(queue_recovery, "_run_is_alive", is_alive)
    monkeypatch.setattr(queue_recovery, "_mark_item", complete_calls)

    queue_recovery.purge_and_recover("run-cur")

    # q1 belongs to a dead run → marked recovered/failed.
    # q2 belongs to the current run → left alone.
    called_ids = [call.args[0] for call in complete_calls.call_args_list]
    assert "q1" in called_ids, "queue_recovery must mark orphan items"
    assert "q2" not in called_ids, "queue_recovery must not touch current-run items"


def test_resume_paused_items(monkeypatch):
    from agent import queue_recovery

    paused = [{"id": "qp1", "status": "paused", "issue_number": 5}]
    monkeypatch.setattr(queue_recovery, "_list_paused_items", MagicMock(return_value=paused))
    resume_calls = MagicMock()
    monkeypatch.setattr(queue_recovery, "_mark_item", resume_calls)

    queue_recovery.resume_paused()

    called = [c.args for c in resume_calls.call_args_list]
    assert any(args[0] == "qp1" for args in called), "must mark paused items as pending/running"


def test_ignore_current_run(monkeypatch):
    """Items belonging to the *current* run_id must never be touched by purge."""
    from agent import queue_recovery

    items = [{"id": "active", "status": "running", "run_id": "run-active"}]
    monkeypatch.setattr(queue_recovery, "_list_running_items", MagicMock(return_value=items))
    monkeypatch.setattr(queue_recovery, "_run_is_alive", MagicMock(return_value=True))
    mark = MagicMock()
    monkeypatch.setattr(queue_recovery, "_mark_item", mark)

    queue_recovery.purge_and_recover("run-active")

    assert mark.call_count == 0, "no items should be marked when run is alive and current"
```

**Step 2: Run the test — confirm it fails.**

```
$ python -m pytest dashboard/backend/tests/test_queue_recovery.py -v
```

Expected: 3 failures (`agent.queue_recovery` missing).

**Step 3: Implementation — create `agent/queue_recovery.py`.**

```python
"""Queue purge / paused-resume / orphan recovery at run start.

Python port of agent/scripts/run-manager.sh queue_* functions (issue #383).
Talks to the dashboard's queue API via HTTP.
"""
from __future__ import annotations

import logging
import os
from typing import Iterable

import httpx

logger = logging.getLogger(__name__)


_QUEUE_BASE = os.environ.get("STATION_DASHBOARD_BASE", "http://localhost:8420").rstrip("/")


def _list_running_items() -> list[dict]:
    try:
        r = httpx.get(f"{_QUEUE_BASE}/api/queue/items", params={"status": "running"}, timeout=10.0)
        r.raise_for_status()
        return r.json().get("items", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("queue_recovery: list running failed: %s", exc)
        return []


def _list_paused_items() -> list[dict]:
    try:
        r = httpx.get(f"{_QUEUE_BASE}/api/queue/items", params={"status": "paused"}, timeout=10.0)
        r.raise_for_status()
        return r.json().get("items", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("queue_recovery: list paused failed: %s", exc)
        return []


def _run_is_alive(run_id: str) -> bool:
    try:
        r = httpx.get(f"{_QUEUE_BASE}/api/runs/{run_id}", timeout=5.0)
        return r.status_code == 200 and r.json().get("status") in {"running", "queued"}
    except Exception:  # noqa: BLE001
        return False


def _mark_item(item_id: str, new_status: str, reason: str = "") -> None:
    try:
        httpx.patch(
            f"{_QUEUE_BASE}/api/queue/items/{item_id}",
            json={"status": new_status, "reason": reason},
            timeout=5.0,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("queue_recovery: mark %s -> %s failed: %s", item_id, new_status, exc)


def purge_and_recover(current_run_id: str) -> None:
    """Mark orphaned 'running' items from dead runs as failed; leave the current run alone."""
    for item in _list_running_items():
        rid = item.get("run_id", "")
        if rid == current_run_id:
            continue
        if _run_is_alive(rid):
            continue
        logger.info("queue_recovery: orphan item %s from dead run %s", item.get("id"), rid)
        _mark_item(item["id"], "failed", reason="orphaned: parent run died")


def resume_paused() -> None:
    """Flip 'paused' items back to 'pending' so the smart router will pick them up."""
    for item in _list_paused_items():
        _mark_item(item["id"], "pending", reason="resumed at run start")
```

**Step 4: Run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_queue_recovery.py -v
```

Expected: 3 passes.

**Step 5: Commit.**

```
$ git add agent/queue_recovery.py dashboard/backend/tests/test_queue_recovery.py
$ git commit -m "feat(queue): port queue purge/recovery from bash to agent/queue_recovery.py"
```

---

### Task 5 — Port rate-limit accounting to `agent/rate_limit.py`

The bash `check_rate_limit()` (612) + `record_session()` (641) read/write a JSON sidecar at `$STATION_RATE_LIMIT_PATH` (default `/var/lib/claude-agent-station/rate-limit.json`). Per-day and per-hour caps come from `config["limits"]`.

**Step 1: Write the failing test.**

Create `dashboard/backend/tests/test_rate_limit.py`:

```python
"""Tests for agent.rate_limit (issue #383 bash port)."""
from __future__ import annotations

import json
import time
import pytest


def test_fresh_state_not_tripped(tmp_path, monkeypatch):
    from agent import rate_limit
    sidecar = tmp_path / "rl.json"
    monkeypatch.setattr(rate_limit, "_SIDECAR_PATH", str(sidecar))
    assert rate_limit.is_tripped() is False


def test_per_day_cap_trips(tmp_path, monkeypatch):
    from agent import rate_limit
    sidecar = tmp_path / "rl.json"
    now = time.time()
    sidecar.write_text(json.dumps({"sessions": [now - 60 for _ in range(100)]}))
    monkeypatch.setattr(rate_limit, "_SIDECAR_PATH", str(sidecar))
    monkeypatch.setattr(rate_limit, "_PER_DAY_CAP", 50)
    assert rate_limit.is_tripped() is True


def test_per_hour_cap_trips(tmp_path, monkeypatch):
    from agent import rate_limit
    sidecar = tmp_path / "rl.json"
    now = time.time()
    sidecar.write_text(json.dumps({"sessions": [now - 60 for _ in range(10)]}))
    monkeypatch.setattr(rate_limit, "_SIDECAR_PATH", str(sidecar))
    monkeypatch.setattr(rate_limit, "_PER_HOUR_CAP", 5)
    monkeypatch.setattr(rate_limit, "_PER_DAY_CAP", 9999)
    assert rate_limit.is_tripped() is True


def test_malformed_sidecar_not_tripped(tmp_path, monkeypatch):
    from agent import rate_limit
    sidecar = tmp_path / "rl.json"
    sidecar.write_text("{not json")
    monkeypatch.setattr(rate_limit, "_SIDECAR_PATH", str(sidecar))
    # Malformed sidecar should fail open (don't block the run on a bad file).
    assert rate_limit.is_tripped() is False


def test_record_session_appends(tmp_path, monkeypatch):
    from agent import rate_limit
    sidecar = tmp_path / "rl.json"
    monkeypatch.setattr(rate_limit, "_SIDECAR_PATH", str(sidecar))

    rate_limit.record_session()
    data = json.loads(sidecar.read_text())
    assert len(data["sessions"]) == 1

    rate_limit.record_session()
    data = json.loads(sidecar.read_text())
    assert len(data["sessions"]) == 2
```

**Step 2: Run the test — confirm it fails.**

```
$ python -m pytest dashboard/backend/tests/test_rate_limit.py -v
```

Expected: 5 failures (the stub `agent.rate_limit` from Task 2 only has `is_tripped`, no real logic).

**Step 3: Implementation — replace the stub.**

Rewrite `agent/rate_limit.py`:

```python
"""Session rate limiting via a JSON sidecar (issue #383 bash port).

Mirrors the bash check_rate_limit / record_session pair so old rate-limit
state survives the migration without a data conversion step.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)


_SIDECAR_PATH = os.environ.get(
    "STATION_RATE_LIMIT_PATH",
    "/var/lib/claude-agent-station/rate-limit.json",
)

# Defaults can be overridden by tests or by config in a follow-up.
_PER_DAY_CAP = int(os.environ.get("STATION_RATE_LIMIT_PER_DAY", "200"))
_PER_HOUR_CAP = int(os.environ.get("STATION_RATE_LIMIT_PER_HOUR", "50"))


def _read_sessions() -> list[float]:
    try:
        data = json.loads(Path(_SIDECAR_PATH).read_text())
        sessions = data.get("sessions") or []
        return [float(s) for s in sessions]
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("rate_limit: sidecar unreadable (%s); failing open", exc)
        return []


def _write_sessions(sessions: list[float]) -> None:
    p = Path(_SIDECAR_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"sessions": sessions}))


def is_tripped() -> bool:
    """True iff the current rate exceeds either cap."""
    now = time.time()
    sessions = _read_sessions()
    last_day = [s for s in sessions if now - s < 86400]
    last_hour = [s for s in sessions if now - s < 3600]
    if len(last_day) >= _PER_DAY_CAP:
        logger.warning("rate_limit: per-day cap reached (%d/%d)", len(last_day), _PER_DAY_CAP)
        return True
    if len(last_hour) >= _PER_HOUR_CAP:
        logger.warning("rate_limit: per-hour cap reached (%d/%d)", len(last_hour), _PER_HOUR_CAP)
        return True
    return False


def record_session() -> None:
    """Append `now` to the sidecar."""
    sessions = _read_sessions()
    sessions.append(time.time())
    # Trim sessions older than 24h so the file doesn't grow unbounded.
    cutoff = time.time() - 86400
    sessions = [s for s in sessions if s >= cutoff]
    _write_sessions(sessions)
```

**Step 4: Run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_rate_limit.py -v
```

Expected: 5 passes.

**Step 5: Commit.**

```
$ git add agent/rate_limit.py dashboard/backend/tests/test_rate_limit.py
$ git commit -m "feat(rate-limit): port session rate-limit accounting to Python"
```

---

### Task 6 — Port `run_manager_review` to `agent/manager_review.py`

The bash `run_manager_review()` (1885–2024) writes a review package, invokes `claude -p` with the manager prompt + package, parses the JSON verdicts from stdout.

**Step 1: Write the failing test.**

Create `dashboard/backend/tests/test_manager_review.py`:

```python
"""Tests for agent.manager_review (issue #383 bash port)."""
from __future__ import annotations

import json
import subprocess
import pytest
from unittest.mock import MagicMock


def test_happy_review_returns_verdicts(tmp_path, monkeypatch):
    from agent import manager_review

    pkg = tmp_path / "review.md"
    pkg.write_text("review package contents")

    fake_stdout = json.dumps({
        "verdicts": [
            {"project": "owner/repo", "issue_number": 1, "decision": "APPROVE",
             "branch": "autonomous/issue-1", "base_branch": "main", "reasoning": "ok"},
        ],
    })
    monkeypatch.setattr(
        manager_review.subprocess, "run",
        MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout=fake_stdout, stderr="")),
    )

    verdicts = manager_review.run_manager_review(str(pkg), "run-xyz", config={"models": {}})
    assert len(verdicts) == 1
    assert verdicts[0].decision == "APPROVE"
    assert verdicts[0].issue_number == 1


def test_malformed_json_raises(tmp_path, monkeypatch):
    from agent import manager_review

    pkg = tmp_path / "review.md"
    pkg.write_text("x")
    monkeypatch.setattr(
        manager_review.subprocess, "run",
        MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="not json", stderr="")),
    )

    with pytest.raises(manager_review.ManagerReviewError, match="JSON"):
        manager_review.run_manager_review(str(pkg), "run-xyz", config={"models": {}})


def test_nonzero_exit_raises(tmp_path, monkeypatch):
    from agent import manager_review

    pkg = tmp_path / "review.md"
    pkg.write_text("x")
    monkeypatch.setattr(
        manager_review.subprocess, "run",
        MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=2, stdout="", stderr="boom")),
    )

    with pytest.raises(manager_review.ManagerReviewError, match="claude -p"):
        manager_review.run_manager_review(str(pkg), "run-xyz", config={"models": {}})


def test_empty_package_raises(tmp_path):
    from agent import manager_review

    pkg = tmp_path / "review.md"
    pkg.write_text("")
    with pytest.raises(manager_review.ManagerReviewError, match="empty"):
        manager_review.run_manager_review(str(pkg), "run-xyz", config={"models": {}})
```

**Step 2: Run the test — confirm it fails.**

```
$ python -m pytest dashboard/backend/tests/test_manager_review.py -v
```

Expected: 4 collection failures.

**Step 3: Implementation — create `agent/manager_review.py`.**

```python
"""Manager review phase — invoke `claude -p` against a review package and parse verdicts.

Python port of agent/scripts/run-manager.sh::run_manager_review (issue #383).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

from agent.verdict_execution import Verdict

logger = logging.getLogger(__name__)


class ManagerReviewError(RuntimeError):
    """Raised when manager review fails (bad exit, bad JSON, empty input)."""


def _manager_prompt_path() -> Path:
    return Path(__file__).resolve().parent / "prompts" / "manager.md"


def run_manager_review(
    review_package_path: str, run_id: str, config: dict,
) -> list[Verdict]:
    """Invoke claude -p with the manager prompt and the review package.

    Returns the list of Verdict objects parsed from stdout. Raises
    ManagerReviewError on bad inputs / bad exits / unparseable output.
    """
    pkg = Path(review_package_path)
    if not pkg.is_file():
        raise ManagerReviewError(f"review package not found: {pkg}")
    contents = pkg.read_text()
    if not contents.strip():
        raise ManagerReviewError("review package is empty")

    model = (config.get("models") or {}).get("manager", "claude-sonnet-4-6")
    prompt_path = _manager_prompt_path()
    if not prompt_path.is_file():
        raise ManagerReviewError(f"manager prompt missing: {prompt_path}")

    cmd = [
        "claude", "-p", contents,
        "--model", model,
        "--system-prompt-file", str(prompt_path),
        "--output-format", "json",
    ]
    env = os.environ.copy()
    env["STATION_RUN_ID"] = run_id

    logger.info("manager_review: invoking claude -p (run=%s, model=%s)", run_id, model)
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=1800)
    if result.returncode != 0:
        raise ManagerReviewError(
            f"claude -p exited {result.returncode}: {result.stderr.strip()[:500]}"
        )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ManagerReviewError(f"manager output is not JSON: {exc}") from exc

    verdicts_raw = payload.get("verdicts") or []
    return [Verdict.from_dict(v) for v in verdicts_raw]
```

**Step 4: Run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_manager_review.py -v
```

Expected: 4 passes.

**Step 5: Commit.**

```
$ git add agent/manager_review.py dashboard/backend/tests/test_manager_review.py
$ git commit -m "feat(manager): port run_manager_review to agent/manager_review.py"
```

---

### Task 7 — Port `merge_to_dev` to `agent/integration_branch.py`

`agent/scripts/integration-branch.sh::merge_to_dev` (lines 154–296) pushes a feature branch and merges it into the integration branch (`dev`). Stays available in bash for cron callers; this Python port is what runs from a live run.

**Step 1: Write the failing test.**

Create `dashboard/backend/tests/test_integration_branch_py.py`:

```python
"""Tests for agent.integration_branch (issue #383 port of merge_to_dev)."""
from __future__ import annotations

import subprocess
import pytest
from unittest.mock import MagicMock


def _ok():
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def test_push_then_merge_ok(tmp_path, monkeypatch):
    from agent import integration_branch

    calls = []

    def _run(cmd, *a, **kw):
        calls.append(cmd)
        return _ok()

    monkeypatch.setattr(integration_branch.subprocess, "run", _run)
    integration_branch.merge_to_dev(
        project="owner/repo", feature_branch="autonomous/issue-1",
        base_branch="main", issue_number=1, reasoning="ok",
        workspaces_dir=str(tmp_path),
    )
    cmd_strs = [" ".join(c) for c in calls]
    assert any("push" in s for s in cmd_strs), "must push feature branch"
    assert any("merge" in s and "autonomous/issue-1" in s for s in cmd_strs), "must merge feature into dev"


def test_push_retry_after_initial_failure(tmp_path, monkeypatch):
    from agent import integration_branch

    state = {"attempts": 0}

    def _run(cmd, *a, **kw):
        if "push" in cmd:
            state["attempts"] += 1
            if state["attempts"] == 1:
                return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="rejected")
        return _ok()

    monkeypatch.setattr(integration_branch.subprocess, "run", _run)
    integration_branch.merge_to_dev(
        project="owner/repo", feature_branch="b", base_branch="main",
        issue_number=2, reasoning="r", workspaces_dir=str(tmp_path),
    )
    assert state["attempts"] >= 2, "must retry push at least once on initial failure"


def test_merge_conflict_raises(tmp_path, monkeypatch):
    from agent import integration_branch

    def _run(cmd, *a, **kw):
        if "merge" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="CONFLICT (content)")
        return _ok()

    monkeypatch.setattr(integration_branch.subprocess, "run", _run)
    with pytest.raises(integration_branch.IntegrationBranchError, match="CONFLICT"):
        integration_branch.merge_to_dev(
            project="owner/repo", feature_branch="b", base_branch="main",
            issue_number=3, reasoning="r", workspaces_dir=str(tmp_path),
        )


def test_dev_bootstrap_on_missing(tmp_path, monkeypatch):
    """If the dev branch doesn't exist, the function creates it from base before merging."""
    from agent import integration_branch

    branches: set[str] = {"main"}

    def _run(cmd, *a, **kw):
        if cmd[:3] == ["git", "rev-parse", "--verify"]:
            ref = cmd[-1]
            return subprocess.CompletedProcess(args=cmd, returncode=0 if ref in branches else 1, stdout="", stderr="")
        if cmd[:2] == ["git", "checkout"] and "-b" in cmd:
            branches.add(cmd[-1])
        return _ok()

    monkeypatch.setattr(integration_branch.subprocess, "run", _run)
    integration_branch.merge_to_dev(
        project="owner/repo", feature_branch="b", base_branch="main",
        issue_number=4, reasoning="r", workspaces_dir=str(tmp_path),
    )
    assert "dev" in branches, "merge_to_dev must bootstrap the dev branch when absent"
```

**Step 2: Run the test — confirm it fails.**

```
$ python -m pytest dashboard/backend/tests/test_integration_branch_py.py -v
```

Expected: 4 collection failures.

**Step 3: Implementation — create `agent/integration_branch.py`.**

```python
"""Integration-branch merge: push feature → merge into dev.

Python port of agent/scripts/integration-branch.sh::merge_to_dev (issue #383).
The bash file remains for ad-hoc cron / dashboard callers.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class IntegrationBranchError(RuntimeError):
    """Raised when push/merge fails irrecoverably."""


_INTEGRATION_BRANCH = "dev"
_PUSH_MAX_RETRIES = 3


def _slug(name: str) -> str:
    return name.replace("/", "__")


def _git(cwd: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _branch_exists(cwd: str, name: str) -> bool:
    return _git(cwd, "rev-parse", "--verify", f"refs/heads/{name}").returncode == 0


def merge_to_dev(
    *, project: str, feature_branch: str, base_branch: str,
    issue_number: int, reasoning: str, workspaces_dir: str,
) -> None:
    """Push the feature branch and merge it into the integration branch."""
    workspace = Path(workspaces_dir) / _slug(project)
    if not workspace.is_dir():
        raise IntegrationBranchError(f"workspace not found: {workspace}")
    cwd = str(workspace)

    # Push the feature branch with retry.
    for attempt in range(1, _PUSH_MAX_RETRIES + 1):
        r = _git(cwd, "push", "origin", feature_branch)
        if r.returncode == 0:
            break
        logger.warning("integration: push attempt %d failed: %s", attempt, r.stderr.strip())
        if attempt == _PUSH_MAX_RETRIES:
            raise IntegrationBranchError(f"push failed after {attempt} attempts: {r.stderr.strip()}")
        # Try a fetch + rebase before retrying.
        _git(cwd, "fetch", "origin", feature_branch)

    # Bootstrap dev if missing.
    if not _branch_exists(cwd, _INTEGRATION_BRANCH):
        logger.info("integration: bootstrapping %s from %s", _INTEGRATION_BRANCH, base_branch)
        _git(cwd, "checkout", "-b", _INTEGRATION_BRANCH, base_branch)
        _git(cwd, "push", "-u", "origin", _INTEGRATION_BRANCH)

    # Checkout dev, merge feature.
    _git(cwd, "checkout", _INTEGRATION_BRANCH)
    _git(cwd, "pull", "--ff-only")
    msg = f"Merge {feature_branch} (issue #{issue_number}): {reasoning[:200]}"
    r = _git(cwd, "merge", "--no-ff", "-m", msg, feature_branch)
    if r.returncode != 0:
        raise IntegrationBranchError(f"merge failed: {r.stderr.strip()}")

    r = _git(cwd, "push", "origin", _INTEGRATION_BRANCH)
    if r.returncode != 0:
        raise IntegrationBranchError(f"push of {_INTEGRATION_BRANCH} failed: {r.stderr.strip()}")
    logger.info("integration: merged %s into %s", feature_branch, _INTEGRATION_BRANCH)
```

**Step 4: Run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_integration_branch_py.py -v
```

Expected: 4 passes.

**Step 5: Commit.**

```
$ git add agent/integration_branch.py dashboard/backend/tests/test_integration_branch_py.py
$ git commit -m "feat(integration): port merge_to_dev to agent/integration_branch.py"
```

---

### Task 8 — Port `write_digest` to `agent/digest.py`

`run-manager.sh::write_digest` (lines 2624–2662) writes a markdown summary of the run.

**Step 1: Write the failing test.**

Create `dashboard/backend/tests/test_digest_py.py`:

```python
"""Tests for agent.digest (issue #383 bash port)."""
from __future__ import annotations


def test_empty_run_digest(tmp_path):
    from agent.digest import write_digest
    path = write_digest(run_id="run-empty", results=[], log_dir=str(tmp_path))
    txt = (tmp_path / "run-empty-digest.md").read_text()
    assert "run-empty" in txt
    assert "No verdicts" in txt or "0 verdicts" in txt


def test_multi_verdict_digest(tmp_path):
    from agent.digest import write_digest
    results = [
        {"project": "owner/a", "issue_number": 1, "decision": "APPROVE", "branch": "autonomous/issue-1"},
        {"project": "owner/a", "issue_number": 2, "decision": "REJECT", "branch": "autonomous/issue-2", "reasoning": "tests fail"},
        {"project": "owner/b", "issue_number": 3, "decision": "PR", "branch": "autonomous/issue-3"},
    ]
    write_digest(run_id="run-multi", results=results, log_dir=str(tmp_path))
    txt = (tmp_path / "run-multi-digest.md").read_text()
    assert "owner/a" in txt and "owner/b" in txt
    assert "APPROVE" in txt and "REJECT" in txt and "PR" in txt


def test_verdict_with_error_recorded(tmp_path):
    from agent.digest import write_digest
    results = [
        {"project": "owner/a", "issue_number": 1, "decision": "ERROR", "error": "rebase failed"},
    ]
    write_digest(run_id="run-err", results=results, log_dir=str(tmp_path))
    txt = (tmp_path / "run-err-digest.md").read_text()
    assert "ERROR" in txt
    assert "rebase failed" in txt
```

**Step 2: Run the test — confirm it fails.**

```
$ python -m pytest dashboard/backend/tests/test_digest_py.py -v
```

Expected: 3 import/collection errors.

**Step 3: Implementation — create `agent/digest.py`.**

```python
"""Run-digest markdown writer.

Python port of agent/scripts/run-manager.sh::write_digest (issue #383).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def write_digest(*, run_id: str, results: list[dict], log_dir: str) -> str:
    """Write a markdown digest for the run. Returns the absolute output path."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    out = Path(log_dir) / f"{run_id}-digest.md"

    lines: list[str] = [
        f"# Run Digest — {run_id}",
        "",
        f"_Generated: {datetime.now(timezone.utc).isoformat()}_",
        "",
    ]
    if not results:
        lines += ["## Verdicts", "", "_No verdicts produced this run._", ""]
    else:
        lines += ["## Verdicts", ""]
        by_project: dict[str, list[dict]] = {}
        for r in results:
            by_project.setdefault(r.get("project", "?"), []).append(r)
        for project, items in by_project.items():
            lines.append(f"### {project}")
            lines.append("")
            for item in items:
                num = item.get("issue_number")
                dec = item.get("decision", "?")
                branch = item.get("branch", "")
                reason = item.get("reasoning") or item.get("error") or ""
                lines.append(f"- **#{num}** — `{dec}`" + (f" — `{branch}`" if branch else "")
                             + (f" — {reason}" if reason else ""))
            lines.append("")

    out.write_text("\n".join(lines))
    logger.info("digest: wrote %s (%d verdicts)", out, len(results))
    return str(out)
```

**Step 4: Run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_digest_py.py -v
```

Expected: 3 passes.

**Step 5: Commit.**

```
$ git add agent/digest.py dashboard/backend/tests/test_digest_py.py
$ git commit -m "feat(digest): port write_digest to agent/digest.py"
```

---

### Task 9 — Replace `RunDriver._read_bash_telemetry` with in-process `_finalize_telemetry`

`RunDriver._read_bash_telemetry` reads `run-<id>-telemetry.json` written by the bash EXIT trap. With no bash, no JSON file is written. The replacement reads counters directly from the orchestrator's `_StreamState`.

**Step 1: Write the failing test.**

Create `dashboard/backend/tests/test_rundriver_telemetry.py`:

```python
"""Tests for RunDriver telemetry without the bash JSON file (issue #383)."""
from __future__ import annotations


def test_rundriver_has_no_read_bash_telemetry():
    from agent import station_orchestrator as so
    assert not hasattr(so.RunDriver, "_read_bash_telemetry"), (
        "_read_bash_telemetry is removed in #383 — bash no longer writes the JSON dump"
    )


def test_rundriver_has_finalize_telemetry():
    from agent import station_orchestrator as so
    assert hasattr(so.RunDriver, "_finalize_telemetry"), (
        "RunDriver must provide _finalize_telemetry to copy counters in-process"
    )


def test_finalize_telemetry_copies_stream_state_counters():
    from agent import station_orchestrator as so

    class _State:
        tokens_in = 100
        tokens_out = 50
        turns = 7

    driver = so.RunDriver(run_id="run-x", config_path="/dev/null", workspaces_dir="/tmp")
    driver._finalize_telemetry(_State())
    assert driver.telemetry.tokens_input == 100
    assert driver.telemetry.tokens_output == 50
    assert driver.telemetry.turns == 7
    assert driver.telemetry.tokens_total == 150
```

**Step 2: Run the test — confirm it fails.**

```
$ python -m pytest dashboard/backend/tests/test_rundriver_telemetry.py -v
```

Expected: 3 failures.

**Step 3: Implementation — replace `_read_bash_telemetry` with `_finalize_telemetry`.**

In `agent/station_orchestrator.py`, delete the `_read_bash_telemetry` method (lines 2323–2340). Add:

```python
    def _finalize_telemetry(self, stream_state) -> None:
        """Copy in-process counters from the orchestrator's stream state.

        Replaces the bash telemetry JSON hand-off after #383. iterate_projects
        is responsible for passing its accumulated _StreamState here in its
        return path; if it doesn't, counters remain at zero.
        """
        self.telemetry.tokens_input = int(getattr(stream_state, "tokens_in", 0) or 0)
        self.telemetry.tokens_output = int(getattr(stream_state, "tokens_out", 0) or 0)
        self.telemetry.tokens_total = self.telemetry.tokens_input + self.telemetry.tokens_output
        self.telemetry.turns = int(getattr(stream_state, "turns", 0) or 0)
```

And in the `run()` method's `finally` block:

```python
        finally:
            # Pull the last stream-state in-process. iterate_projects exposes
            # the active state via a thread-local / module variable populated
            # during the run. _finalize_telemetry tolerates missing state.
            from agent.project_loop import get_last_stream_state
            state = get_last_stream_state()
            if state is not None:
                self._finalize_telemetry(state)
            self._emit_run_complete(status=status, exit_code=exit_code, error=error)
```

Add to `agent/project_loop.py`:

```python
_LAST_STREAM_STATE = None


def get_last_stream_state():
    return _LAST_STREAM_STATE


def _set_last_stream_state(state) -> None:
    global _LAST_STREAM_STATE
    _LAST_STREAM_STATE = state
```

`iterate_projects` (which Task 10 rewrites) will call `_set_last_stream_state(...)` once per project so the driver can read it.

**Step 4: Run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_rundriver_telemetry.py -v
```

Expected: 3 passes.

**Step 5: Commit.**

```
$ git add agent/station_orchestrator.py agent/project_loop.py dashboard/backend/tests/test_rundriver_telemetry.py
$ git commit -m "refactor(driver): replace _read_bash_telemetry with in-process _finalize_telemetry"
```

---

### Task 10 — Rewrite `iterate_projects` to call the new modules directly (no bash)

This is the core wiring task. After this lands, the launcher's Python driver runs end-to-end without `bash run-manager.sh`.

**Step 1: Write the failing test.**

Create `dashboard/backend/tests/test_iterate_projects_python.py`:

```python
"""End-to-end mock of iterate_projects with all external boundaries mocked (issue #383)."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch


def test_iterate_projects_calls_each_phase(tmp_path, monkeypatch):
    """Run iterate_projects against a sandbox config; assert every phase fires."""
    from agent import project_loop as pl

    cfg = tmp_path / "manager-config.json"
    cfg.write_text(json.dumps({
        "projects": [{"name": "owner/repo", "enabled": True, "base_branch": "main"}],
        "limits": {"max_concurrent_employees": 1},
        "models": {"manager": "claude-sonnet-4-6"},
    }))

    call_log: list[str] = []

    monkeypatch.setattr("agent.preflight.run_preflight", lambda *a, **k: call_log.append("preflight"))
    monkeypatch.setattr("agent.queue_recovery.purge_and_recover", lambda *a, **k: call_log.append("purge"))
    monkeypatch.setattr("agent.queue_recovery.resume_paused", lambda: call_log.append("resume"))
    monkeypatch.setattr("agent.workspace_setup.ensure_workspace", lambda p, w: (call_log.append("workspace"), str(tmp_path))[1])

    async def _fake_orchestrate(project, config, run_id, workspaces_dir):
        call_log.append("orchestrate_project")
        return 0

    monkeypatch.setattr("agent.station_orchestrator.orchestrate_project", _fake_orchestrate)
    monkeypatch.setattr(
        "agent.manager_review.run_manager_review",
        lambda *a, **k: (call_log.append("manager_review"), [])[1],
    )
    monkeypatch.setattr("agent.digest.write_digest", lambda **kw: call_log.append("digest") or "")

    rc = pl.iterate_projects("run-test", str(cfg), str(tmp_path))

    assert rc == 0
    assert call_log == [
        "preflight",
        "purge",
        "resume",
        "workspace",
        "orchestrate_project",
        "manager_review",
        "digest",
    ], f"Unexpected phase order: {call_log}"


def test_iterate_projects_does_not_spawn_bash(tmp_path, monkeypatch):
    """The post-#383 iterate_projects must NEVER invoke run-manager.sh."""
    import inspect
    from agent import project_loop as pl

    src = inspect.getsource(pl)
    assert "run-manager.sh" not in src, "iterate_projects must not reference run-manager.sh"
    assert "subprocess.Popen" not in src or "popen" not in src.lower(), (
        "iterate_projects must not Popen any bash child"
    )
```

**Step 2: Run the test — confirm it fails.**

```
$ python -m pytest dashboard/backend/tests/test_iterate_projects_python.py -v
```

Expected: 2 failures (current `iterate_projects` shells to `run-manager.sh`).

**Step 3: Implementation — rewrite `iterate_projects`.**

Replace the body of `iterate_projects` in `agent/project_loop.py`:

```python
def iterate_projects(run_id: str, config_path: str, workspaces_dir: str) -> int:
    """Drive a full run in-process: preflight → recovery → per-project → digest.

    Replaces the bash run-manager.sh --internal-iterate shell-out (issue #383).
    Each former bash phase is a Python module; this function composes them.
    """
    import asyncio
    import json
    from pathlib import Path

    from agent.preflight import run_preflight, PreflightError
    from agent.queue_recovery import purge_and_recover, resume_paused
    from agent.workspace_setup import ensure_workspace, WorkspaceError
    from agent.station_orchestrator import orchestrate_project
    from agent.manager_review import run_manager_review, ManagerReviewError
    from agent.verdict_execution import execute as execute_verdict
    from agent.integration_branch import merge_to_dev, IntegrationBranchError
    from agent.digest import write_digest

    try:
        run_preflight(config_path)
    except PreflightError as exc:
        logger.error("preflight: %s", exc)
        return 2

    purge_and_recover(run_id)
    resume_paused()

    config = json.loads(Path(config_path).read_text())
    enabled = [p for p in config.get("projects", []) if p.get("enabled", True)]

    results: list[dict] = []
    exit_code = 0
    log_dir = "/var/log/claude-agent"

    for project in enabled:
        try:
            ensure_workspace(project, workspaces_dir)
        except WorkspaceError as exc:
            logger.error("workspace: %s", exc)
            exit_code = 3
            results.append({"project": project["name"], "decision": "ERROR", "error": str(exc)})
            continue

        try:
            proj_rc = asyncio.run(orchestrate_project(project, config, run_id, workspaces_dir))
            if proj_rc != 0:
                exit_code = proj_rc
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("orchestrate_project failed for %s", project["name"])
            exit_code = 4
            results.append({"project": project["name"], "decision": "ERROR", "error": str(exc)})
            continue

        # Manager review consumes the review package the orchestrator wrote.
        from pathlib import Path as _P
        pkg = _P(log_dir) / f"{run_id}-{project['name'].replace('/', '__')}-review.md"
        if pkg.is_file():
            try:
                verdicts = run_manager_review(str(pkg), run_id, config)
            except ManagerReviewError as exc:
                logger.error("manager_review: %s", exc)
                verdicts = []
        else:
            verdicts = []

        for verdict in verdicts:
            result = execute_verdict(verdict, run_id=run_id)
            results.append({
                "project": verdict.project,
                "issue_number": verdict.issue_number,
                "decision": verdict.decision,
                "branch": getattr(verdict, "branch", ""),
                "reasoning": getattr(verdict, "reasoning", ""),
            })
            if getattr(result, "action", "") == "merge_dev":
                try:
                    merge_to_dev(
                        project=verdict.project,
                        feature_branch=verdict.branch,
                        base_branch=verdict.base_branch,
                        issue_number=verdict.issue_number,
                        reasoning=verdict.reasoning or "",
                        workspaces_dir=workspaces_dir,
                    )
                except IntegrationBranchError as exc:
                    logger.error("merge_to_dev failed: %s", exc)
                    results.append({
                        "project": verdict.project,
                        "issue_number": verdict.issue_number,
                        "decision": "ERROR",
                        "error": f"merge_to_dev: {exc}",
                    })

    write_digest(run_id=run_id, results=results, log_dir=log_dir)
    return exit_code
```

Also delete the bash-only helpers in `agent/project_loop.py`: `_terminate_child`, the `_BASH_SIGTERM_GRACE_SECONDS` and `_BASH_SIGKILL_GRACE_SECONDS` constants, and the module docstring sentence about shelling to `run-manager.sh`.

**Step 4: Run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_iterate_projects_python.py -v
```

Expected: 2 passes.

Also run the full backend suite to confirm nothing else broke:

```
$ python -m pytest dashboard/backend/tests/ -q
```

Expected: green.

**Step 5: Commit.**

```
$ git add agent/project_loop.py dashboard/backend/tests/test_iterate_projects_python.py
$ git commit -m "refactor(project-loop): drive run in-process (Python phases, no bash)"
```

---

### Task 11 — Remove `STATION_LAUNCHER_USE_BASH` and the bash entry point from `agent/launcher.py`

**Step 1: Write the failing test.**

Create `dashboard/backend/tests/test_launcher_no_bash.py`:

```python
"""Per #383, the launcher no longer offers a bash entry point."""
from __future__ import annotations


def test_launcher_does_not_reference_use_bash_launcher():
    import inspect
    from agent import launcher
    src = inspect.getsource(launcher)
    assert "STATION_LAUNCHER_USE_BASH" not in src
    assert "USE_BASH_LAUNCHER" not in src
    assert "RUN_MANAGER" not in src


def test_launcher_command_is_python_only():
    import inspect
    from agent import launcher
    src = inspect.getsource(launcher._spawn_run_manager)
    assert "bash" not in src.lower() or "run-manager.sh" not in src
    assert "station_orchestrator" in src and "--driver" in src
```

**Step 2: Run the test — confirm it fails.**

```
$ python -m pytest dashboard/backend/tests/test_launcher_no_bash.py -v
```

Expected: 2 failures.

**Step 3: Implementation — delete bash flag and branch.**

In `agent/launcher.py`:

- Delete the `RUN_MANAGER = Path(...)` line (currently line 34).
- Delete the `USE_BASH_LAUNCHER = ...` line (currently line 41) and its surrounding comments.
- Delete the `if USE_BASH_LAUNCHER and not RUN_MANAGER.is_file(): raise HTTPException(...)` guard (currently around lines 314–318).
- Delete the `if USE_BASH_LAUNCHER: cmd = ["bash", str(RUN_MANAGER)]; entry_kind = ...` branch (currently lines 365–367) and unconditionally use the Python driver:

```python
    driver_run_id = hint_run_id or "run-" + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    cmd = [
        sys.executable, "-m", "agent.station_orchestrator",
        "--driver",
        "--run-id", driver_run_id,
        "--config", STATION_CONFIG,
        "--workspaces-dir", STATION_WORKSPACES,
    ]
    entry_kind = "station_orchestrator --driver (python)"
```

**Step 4: Run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_launcher_no_bash.py -v
```

Expected: 2 passes.

**Step 5: Commit.**

```
$ git add agent/launcher.py dashboard/backend/tests/test_launcher_no_bash.py
$ git commit -m "refactor(launcher): drop STATION_LAUNCHER_USE_BASH panic-revert flag"
```

---

### Task 12 — Delete `agent/scripts/run-manager.sh`, update docs

**Step 1: Write the failing test.**

Append to `dashboard/backend/tests/test_iterate_projects_python.py`:

```python
def test_run_manager_sh_is_deleted():
    """Issue #383: the bash file is removed from the tree."""
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[3]
    assert not (repo_root / "agent" / "scripts" / "run-manager.sh").exists(), (
        "agent/scripts/run-manager.sh must be deleted (issue #383)"
    )
```

**Step 2: Run the test — confirm it fails.**

```
$ python -m pytest dashboard/backend/tests/test_iterate_projects_python.py::test_run_manager_sh_is_deleted -v
```

Expected:

```
FAILED ... test_run_manager_sh_is_deleted - AssertionError: agent/scripts/run-manager.sh must be deleted (issue #383)
```

**Step 3: Implementation — delete the bash file and update docs.**

```
$ git rm agent/scripts/run-manager.sh
```

Then update docs. Grep first:

```
$ grep -rn "run-manager.sh\|STATION_LAUNCHER_USE_BASH" docs/
```

For each match in `docs/architecture.md`, `docs/configuration.md`, or any other living doc, replace references with the Python-driver equivalent. Sample substitution:

> The launcher spawns `bash agent/scripts/run-manager.sh` …

→

> The launcher spawns `python -m agent.station_orchestrator --driver` in-process. Each former bash phase is a Python module under `agent/` (preflight, workspace_setup, queue_recovery, rate_limit, manager_review, integration_branch, digest).

In `docs/configuration.md`, remove the `STATION_LAUNCHER_USE_BASH` and `STATION_RUN_MANAGER` env-var rows. Add a line stating `agent/scripts/run-manager.sh` was removed in PR for #383 and the project-iteration phases now live in dedicated Python modules.

**Step 4: Run the test — confirm it passes.**

```
$ python -m pytest dashboard/backend/tests/test_iterate_projects_python.py::test_run_manager_sh_is_deleted -v
```

Expected:

```
PASSED ... test_run_manager_sh_is_deleted
```

**Step 5: Commit and tag the PR-ready state.**

```
$ git add docs/ dashboard/backend/tests/test_iterate_projects_python.py
$ git rm agent/scripts/run-manager.sh
$ git commit -m "refactor(scripts): delete run-manager.sh + update docs for Python-only project loop"
```

Open the PR:

```
$ gh pr create --base dev --title "refactor: delete run-manager.sh and port phases to Python (#383)" --body "Closes #383. Replaces the 3309-LOC bash with seven new Python modules (preflight, workspace_setup, queue_recovery, rate_limit, manager_review, integration_branch, digest). Per memory, PRs target dev."
```

---

## Verification checklist

- [ ] `git ls-files agent/scripts/run-manager.sh` → empty.
- [ ] `grep -rn "run-manager.sh\|STATION_LAUNCHER_USE_BASH\|USE_BASH_LAUNCHER\|RUN_MANAGER" agent/` → zero matches.
- [ ] `grep -rn "run-manager.sh\|STATION_LAUNCHER_USE_BASH" docs/` → zero matches outside historical labels.
- [ ] `python -m pytest dashboard/backend/tests/ -q` → suite green.
- [ ] `python -c "from agent import preflight, workspace_setup, queue_recovery, rate_limit, manager_review, integration_branch, digest; print('OK')"` → prints `OK`.
- [ ] All seven new modules have a sibling `dashboard/backend/tests/test_*.py` file with at least 4 cases each (happy + 3 failure paths).
- [ ] `agent/project_loop.py::iterate_projects` contains no `subprocess.Popen` and no `run-manager.sh` reference.
- [ ] `agent/launcher.py` builds the spawn command as `[sys.executable, "-m", "agent.station_orchestrator", "--driver", ...]` unconditionally.
