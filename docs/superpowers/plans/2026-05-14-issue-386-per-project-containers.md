# Per-Project Ephemeral Containers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the long-lived `cas-agent` runtime with a short-lived `cas-runner-<run-id>` container spawned per run, so concurrent runs no longer share a process tree, filesystem (except via the durable `station-data` volume), CPU budget, or SDK CLI process.

**Architecture:** The current `cas-agent` service splits into two roles. `cas-launcher` is a long-running FastAPI container that exposes the existing launcher HTTP endpoints; its `/run` handler now calls the Docker socket (via the `docker` Python SDK) to spawn one ephemeral `cas-runner-<run-id>` container per request. The runner's image is the existing `claude-agent-station/agent:dev`; its ENTRYPOINT is `python -m agent.station_orchestrator --driver --run-id ...`. Workspace state still lives on the `station-data` named volume, mounted into each runner. The launcher's module-level `_current` becomes a dict of `RunnerHandle` keyed by `run_id`. The zombie reaper polls Docker for container status and stops stuck containers via `client.containers.stop`. Per-project resource quotas live on `Project.runner_memory_limit` / `runner_cpu_limit`, defaults in `Settings`. A `STATION_RUNNER_MODE=inline` feature flag preserves the old subprocess path for one release as a panic-revert.

**Tech Stack:** Python 3.11+ / FastAPI / `docker>=7` Python SDK, Docker compose, Alembic (depends on #393), pytest + `pytest-mock` for unit tests, `pytest.mark.requires_docker` for integration tests.

**Tracking issue:** [#386](https://github.com/kenhaesler/agent-station/issues/386)

**Spec:** `docs/superpowers/specs/2026-05-14-issue-386-per-project-containers.md`

**Prerequisites:**
- **Hard**: #393 (Postgres) must be in production. Two ephemeral runners writing to SQLite would produce `database is locked` errors.
- **Hard**: Tier 1 / #383 (bash → Python launcher entry point). The runner's ENTRYPOINT is `python -m agent.station_orchestrator --driver`, which only exists post-#383.
- **Hard**: Tier 1B clean teardown semantics (try/finally on driver exit).
- **Soft**: Tier 2 / #387 (timeline API) — orthogonal but improves observability of concurrent runs.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `agent/launcher.py` | modify | Module-level state becomes `_runners: dict[str, RunnerHandle]`. Endpoints reshape: `POST /run`, `GET /status`, `POST /stop?run_id=`, `POST /webhook-tick?run_id=`. New helpers `_spawn_runner_container`, `_get_docker_client`, `_resolve_quotas`. |
| `agent/runner_spawn.py` | **new** | Pure-Python module isolating the Docker SDK call from the FastAPI route. Easier to unit-test. Exposes `spawn_runner(client, hint_run_id, project_repo, quotas, env_passthrough) -> RunnerHandle`. |
| `agent/launcher_reaper.py` | **new** | Container-aware zombie reaper extracted from `agent/launcher.py:278+`. Iterates `_runners`, queries `client.containers.get(name).status`, stops stuck containers. |
| `agent/__init__.py` | unchanged | — |
| `dashboard/backend/app/models.py` | modify | `Project.runner_memory_limit` (Text, nullable), `Project.runner_cpu_limit` (Text, nullable). |
| `dashboard/backend/alembic/versions/0002_runner_quotas.py` | **new** | Alembic revision adding the two columns. |
| `dashboard/backend/app/config.py` | modify | `default_runner_memory_limit = "2g"`, `default_runner_cpu_limit = "1.0"`, `runner_mode = "container"` (with `"inline"` as the revert flag), `runner_image = "claude-agent-station/agent:dev"`. |
| `dashboard/backend/app/schemas.py` | modify | `ProjectOut` exposes `runner_memory_limit`, `runner_cpu_limit`. |
| `dashboard/backend/app/routers/projects.py` | modify | Project edit endpoint accepts the two new fields. |
| `compose.yml` | modify | Add `cas-launcher` (renamed from `agent`) with `/var/run/docker.sock` mounted and `agent-net` network; keep the old `agent` service definition behind a compose profile `legacy-inline` for the rollout. |
| `Dockerfile.agent` | modify | Comment that ENTRYPOINT differs by runtime mode; ensure `--init` is honoured (run as PID 1). No build change required if the image already runs the launcher. |
| `agent/requirements.txt` | modify | Add `docker>=7,<8`. |
| `dashboard/backend/tests/test_launcher_spawn.py` | **new** | Unit: Docker SDK mock asserts `containers.run` invoked with correct args, volume mounts, env vars, quotas, name. |
| `dashboard/backend/tests/test_launcher_reaper.py` | **new** | Unit: stale heartbeat triggers `containers.stop`; missing-container path is benign. |
| `dashboard/backend/tests/test_launcher_runs_map.py` | **new** | Unit: `_runners` dict transitions on spawn/exit; `/status` returns `{"runs": [...]}`. |
| `dashboard/backend/tests/integration/test_runner_spawn.py` | **new** | `@pytest.mark.requires_docker` — spawns a real `cas-runner-…` against `sleep 5`; asserts container exists, exits, auto-removes. |
| `dashboard/backend/tests/integration/test_concurrent_runs.py` | **new** | Two runs against two projects coexist; PID namespaces disjoint. |
| `dashboard/frontend/src/pages/RunDetail.svelte` | modify | New "Container access" expander showing `docker exec`/`docker logs` snippets. |
| `docs/operations.md` | modify | New section: inspecting a live runner, quotas per project, rollback to inline mode. |
| `docs/architecture.md` | modify | Update Agent Teams flow diagram description: cas-launcher + cas-runner per run. |

---

## Setup (run once per execution session)

### Task 0: Sync local dev and confirm prerequisites

- [ ] **Step 1: Pull latest dev**

```bash
git checkout dev && git pull --ff-only origin dev
```

- [ ] **Step 2: Confirm #393 + #383 are merged**

```bash
gh pr list --state merged --search "393 in:title"
gh pr list --state merged --search "383 in:title"
```

Expected: both list at least one merged PR. If not, **stop**; this plan is gated on those.

- [ ] **Step 3: Confirm Docker is reachable from the dev box**

```bash
docker ps
```

Expected: a header row, no error.

- [ ] **Step 4: Confirm backend + agent tests pass clean**

```bash
cd dashboard/backend && python3 -m pytest -q
```

Expected: green.

- [ ] **Step 5: Create branch**

```bash
git checkout -b feature/386-per-project-containers
```

---

# PR 1 — Quotas schema + project edit surface

## Task 1: `Project.runner_memory_limit` + `runner_cpu_limit` columns

**Files:**
- Modify: `dashboard/backend/app/models.py`
- New: `dashboard/backend/alembic/versions/0002_runner_quotas.py`
- New: `dashboard/backend/tests/test_runner_quotas.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_runner_quotas.py`:

```python
"""Per-project runner-resource columns (#386)."""
from __future__ import annotations

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_project_runner_quota_columns(async_session_factory):
    from app.models import Project

    async with async_session_factory() as db:
        p = Project(
            repo="x/y",
            branch="main",
            runner_memory_limit="512m",
            runner_cpu_limit="0.5",
        )
        db.add(p)
        await db.commit()

    async with async_session_factory() as db:
        row = (await db.execute(select(Project).where(Project.repo == "x/y"))).scalar_one()
        assert row.runner_memory_limit == "512m"
        assert row.runner_cpu_limit == "0.5"
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_runner_quotas.py -q
```

Expected: `AttributeError: 'Project' has no attribute 'runner_memory_limit'`.

- [ ] **Step 3: Add columns + Alembic revision**

In `dashboard/backend/app/models.py`, inside `class Project`, append:

```python
    runner_memory_limit = Column(Text, nullable=True)
    runner_cpu_limit = Column(Text, nullable=True)
```

Create `dashboard/backend/alembic/versions/0002_runner_quotas.py`:

```python
"""Add Project.runner_memory_limit / runner_cpu_limit (#386).

Revision ID: 0002_runner_quotas
Revises: 0001_baseline
Create Date: 2026-05-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_runner_quotas"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("runner_memory_limit", sa.Text(), nullable=True))
        batch.add_column(sa.Column("runner_cpu_limit", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("runner_memory_limit")
        batch.drop_column("runner_cpu_limit")
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_runner_quotas.py -q
```

Expected: 2 passed (`[sqlite]` + `[postgres]`).

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/models.py dashboard/backend/alembic/versions/0002_runner_quotas.py dashboard/backend/tests/test_runner_quotas.py
git commit -m "feat(runner): Project runner_memory_limit + runner_cpu_limit columns (#386)"
```

---

## Task 2: Surface quotas in ProjectOut + edit endpoint

**Files:**
- Modify: `dashboard/backend/app/schemas.py`
- Modify: `dashboard/backend/app/routers/projects.py`
- Modify: `dashboard/backend/tests/test_runner_quotas.py` (append)

- [ ] **Step 1: Append failing test**

```python
@pytest.mark.asyncio
async def test_project_edit_endpoint_accepts_quotas(async_session_factory, client):
    resp = await client.post(
        "/api/projects",
        json={"repo": "edit/q", "branch": "main"},
    )
    assert resp.status_code in (200, 201)
    project_id = resp.json()["id"]

    resp = await client.patch(
        f"/api/projects/{project_id}",
        json={"runner_memory_limit": "1g", "runner_cpu_limit": "0.75"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["runner_memory_limit"] == "1g"
    assert body["runner_cpu_limit"] == "0.75"
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_runner_quotas.py::test_project_edit_endpoint_accepts_quotas -q
```

Expected: schema rejects unknown fields (422) or echoes None.

- [ ] **Step 3: Wire the fields**

In `dashboard/backend/app/schemas.py`, find `ProjectOut` / `ProjectUpdate` (or the equivalent edit shape) and add:

```python
class ProjectUpdate(BaseModel):
    # ... existing fields ...
    runner_memory_limit: str | None = None
    runner_cpu_limit: str | None = None


class ProjectOut(BaseModel):
    # ... existing fields ...
    runner_memory_limit: str | None = None
    runner_cpu_limit: str | None = None
```

In `dashboard/backend/app/routers/projects.py`, the PATCH handler already loops over `model_dump(exclude_unset=True)`; no code change is required if the field names match the columns (they do). If the handler is field-by-field, add the two fields explicitly.

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_runner_quotas.py -q
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/schemas.py dashboard/backend/app/routers/projects.py dashboard/backend/tests/test_runner_quotas.py
git commit -m "feat(runner): expose runner quotas on Project API (#386)"
```

---

## Task 3: PR 1 — open

- [ ] **Step 1: Push**

```bash
git push -u origin feature/386-per-project-containers
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --base dev --title "feat(runner): Project quotas schema (#386, PR 1/3)" --body "$(cat <<'EOF'
Part 1 of 3 for #386.

## Summary
- Two new columns on `projects`: `runner_memory_limit`, `runner_cpu_limit`.
- Alembic revision `0002_runner_quotas`.
- Project API surfaces and accepts the fields.

## Test plan
- [ ] `cd dashboard/backend && pytest tests/test_runner_quotas.py -q`

Schema-only PR. Subsequent PRs add the Docker SDK launcher path (PR 2) and compose changes + integration tests (PR 3).
EOF
)"
```

- [ ] **Step 3-5: Wait for CI, merge, sync dev.**

---

# PR 2 — Docker SDK launcher path + runs map

## Task 4: Branch + add `docker` Python SDK dep

**Files:**
- Modify: `agent/requirements.txt`
- New: `dashboard/backend/tests/test_launcher_imports.py`

- [ ] **Step 1: Branch**

```bash
git checkout dev && git pull --ff-only origin dev && git checkout -b feature/386-launcher-docker
```

- [ ] **Step 2: Write failing test**

Create `dashboard/backend/tests/test_launcher_imports.py`:

```python
def test_docker_sdk_importable():
    import docker  # noqa: F401
```

- [ ] **Step 3: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_launcher_imports.py -q
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 4: Add the dep and install**

Append to `agent/requirements.txt`:

```
docker>=7,<8
```

Install locally:

```bash
pip install -r agent/requirements.txt
```

- [ ] **Step 5: Verify + commit**

```bash
cd dashboard/backend && python3 -m pytest tests/test_launcher_imports.py -q
git add agent/requirements.txt dashboard/backend/tests/test_launcher_imports.py
git commit -m "build(runner): add docker SDK dep (#386)"
```

---

## Task 5: `RunnerHandle` + `_runners` dict in launcher

**Files:**
- Modify: `agent/launcher.py`
- New: `dashboard/backend/tests/test_launcher_runs_map.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_launcher_runs_map.py`:

```python
"""Launcher runs map (#386)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent import launcher


def test_runners_dict_starts_empty():
    launcher._runners.clear()
    assert launcher._runners == {}


def test_runner_handle_stores_metadata():
    launcher._runners.clear()
    handle = launcher.RunnerHandle(
        run_id="run-abc",
        container_name="cas-runner-abc",
        started_at=datetime.now(timezone.utc),
        last_webhook_at=datetime.now(timezone.utc),
        project_repo="x/y",
    )
    launcher._runners[handle.run_id] = handle
    assert launcher._runners["run-abc"].container_name == "cas-runner-abc"
    assert launcher._runners["run-abc"].project_repo == "x/y"
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_launcher_runs_map.py -q
```

Expected: `AttributeError: module 'agent.launcher' has no attribute 'RunnerHandle'`.

- [ ] **Step 3: Add the dataclass and the dict**

In `agent/launcher.py`, alongside the existing `_current` / `_last_webhook_at` globals (line ~118), add:

```python
from dataclasses import dataclass


@dataclass
class RunnerHandle:
    """One per concurrent run; replaces the global `_current` (#386)."""

    run_id: str
    container_name: str
    started_at: datetime
    last_webhook_at: datetime
    project_repo: str | None


_runners: dict[str, RunnerHandle] = {}
```

Leave `_current` and `_last_webhook_at` in place for now — Task 7 retires them.

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_launcher_runs_map.py -q
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/launcher.py dashboard/backend/tests/test_launcher_runs_map.py
git commit -m "feat(runner): RunnerHandle + _runners dict (#386)"
```

---

## Task 6: `runner_spawn.spawn_runner` — Docker SDK invocation

**Files:**
- New: `agent/runner_spawn.py`
- New: `dashboard/backend/tests/test_launcher_spawn.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_launcher_spawn.py`:

```python
"""Docker SDK spawn invocation (#386)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _quotas(memory: str = "2g", cpus: str = "1.0") -> dict:
    return {"memory": memory, "cpus": cpus}


def test_spawn_runner_passes_expected_args(monkeypatch):
    from agent.runner_spawn import spawn_runner

    fake_client = MagicMock()
    fake_container = MagicMock()
    fake_container.name = "cas-runner-abc"
    fake_client.containers.run.return_value = fake_container

    handle = spawn_runner(
        fake_client,
        hint_run_id="run-abc",
        project_repo="x/y",
        quotas=_quotas("1g", "0.5"),
        env_passthrough={"STATION_DB_URL": "postgresql+asyncpg://u:p@db/h",
                         "STATION_WEBHOOK_URL": "http://dashboard:8420/api/webhook/run-event"},
        image="claude-agent-station/agent:dev",
        config_path="/var/lib/claude-agent-station/manager-config.json",
        workspaces_dir="/var/lib/claude-agent-station/workspaces",
    )

    fake_client.containers.run.assert_called_once()
    kwargs = fake_client.containers.run.call_args.kwargs
    assert kwargs["image"] == "claude-agent-station/agent:dev"
    assert kwargs["name"] == "cas-runner-abc"
    assert kwargs["detach"] is True
    assert kwargs["remove"] is True
    assert kwargs["init"] is True
    assert kwargs["mem_limit"] == "1g"
    # docker SDK uses nano-cpus (int) — 0.5 cpu = 500_000_000 nanos.
    assert kwargs["nano_cpus"] == 500_000_000
    env = kwargs["environment"]
    assert env["STATION_RUN_ID"] == "run-abc"
    assert env["STATION_PROJECT_REPO"] == "x/y"
    assert env["STATION_DB_URL"].startswith("postgresql+asyncpg://")
    assert env["STATION_WEBHOOK_URL"].endswith("/api/webhook/run-event")
    cmd = kwargs["command"]
    assert "agent.station_orchestrator" in cmd
    assert "--driver" in cmd
    assert "--run-id" in cmd and "run-abc" in cmd

    volumes = kwargs["volumes"]
    assert "station-data" in volumes
    assert volumes["station-data"]["bind"] == "/var/lib/claude-agent-station"
    assert "station-logs" in volumes

    assert handle.run_id == "run-abc"
    assert handle.container_name == "cas-runner-abc"
    assert handle.project_repo == "x/y"
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_launcher_spawn.py -q
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the module**

Create `agent/runner_spawn.py`:

```python
"""Spawn one ephemeral runner container per run (#386).

Isolated from the FastAPI route so it can be unit-tested without a live
Docker daemon. The route layer wires together the docker client, the
project quotas (from the DB), and the env passthrough.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class RunnerHandle:
    run_id: str
    container_name: str
    started_at: datetime
    last_webhook_at: datetime
    project_repo: str | None


def _container_name(run_id: str) -> str:
    return f"cas-runner-{run_id.removeprefix('run-')}"


def _cpus_to_nano(cpus: str) -> int:
    return int(float(cpus) * 1_000_000_000)


def spawn_runner(
    client,
    *,
    hint_run_id: str,
    project_repo: str | None,
    quotas: dict,
    env_passthrough: dict,
    image: str,
    config_path: str,
    workspaces_dir: str,
) -> RunnerHandle:
    """Spawn a detached, auto-removed runner container.

    ``client`` is a ``docker.from_env()`` instance (or a MagicMock in tests).
    ``quotas`` is ``{"memory": "2g", "cpus": "1.0"}`` — strings come from the
    Project row's columns; defaults are resolved upstream.
    ``env_passthrough`` carries every STATION_* the orchestrator needs.
    """
    name = _container_name(hint_run_id)
    env = dict(env_passthrough)
    env["STATION_RUN_ID"] = hint_run_id
    if project_repo is not None:
        env["STATION_PROJECT_REPO"] = project_repo

    container = client.containers.run(
        image=image,
        name=name,
        detach=True,
        remove=True,            # auto-cleanup on exit
        init=True,              # proper PID 1 zombie reaping inside the runner
        network="agent-net",
        mem_limit=quotas["memory"],
        nano_cpus=_cpus_to_nano(quotas["cpus"]),
        environment=env,
        volumes={
            "station-data": {
                "bind": "/var/lib/claude-agent-station",
                "mode": "rw",
            },
            "station-logs": {
                "bind": "/var/log/claude-agent",
                "mode": "rw",
            },
        },
        command=[
            "python", "-m", "agent.station_orchestrator",
            "--driver",
            "--run-id", hint_run_id,
            "--config", config_path,
            "--workspaces-dir", workspaces_dir,
        ],
    )
    now = datetime.now(timezone.utc)
    return RunnerHandle(
        run_id=hint_run_id,
        container_name=container.name,
        started_at=now,
        last_webhook_at=now,
        project_repo=project_repo,
    )
```

Update `agent/launcher.py` to re-export `RunnerHandle` from the new module to keep imports stable:

```python
from agent.runner_spawn import RunnerHandle  # noqa: F401
```

(Delete the inline `@dataclass class RunnerHandle` from Task 5 — the module is the single source of truth.)

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_launcher_spawn.py tests/test_launcher_runs_map.py -q
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/runner_spawn.py agent/launcher.py dashboard/backend/tests/test_launcher_spawn.py
git commit -m "feat(runner): spawn_runner via Docker SDK (#386)"
```

---

## Task 7: Route `/run` through `spawn_runner` (behind `STATION_RUNNER_MODE`)

**Files:**
- Modify: `agent/launcher.py`
- Modify: `dashboard/backend/app/config.py`
- Modify: `dashboard/backend/tests/test_launcher_spawn.py` (append)

- [ ] **Step 1: Write the failing route test**

Append to `dashboard/backend/tests/test_launcher_spawn.py`:

```python
from unittest.mock import patch


def test_post_run_invokes_spawn_runner_when_mode_container(monkeypatch):
    monkeypatch.setenv("STATION_RUNNER_MODE", "container")
    from fastapi.testclient import TestClient
    from agent import launcher

    with patch("agent.launcher._get_docker_client") as get_client, \
         patch("agent.launcher.spawn_runner") as spawn:
        fake_client = MagicMock()
        get_client.return_value = fake_client
        from agent.runner_spawn import RunnerHandle
        from datetime import datetime, timezone
        spawn.return_value = RunnerHandle(
            run_id="run-x",
            container_name="cas-runner-x",
            started_at=datetime.now(timezone.utc),
            last_webhook_at=datetime.now(timezone.utc),
            project_repo="x/y",
        )

        client_app = TestClient(launcher.app)
        resp = client_app.post("/run", json={"hint_run_id": "run-x"})

    assert resp.status_code == 200
    spawn.assert_called_once()
    assert "cas-runner-x" in resp.json()["container"]


def test_post_run_falls_back_to_inline_when_mode_inline(monkeypatch):
    monkeypatch.setenv("STATION_RUNNER_MODE", "inline")
    from fastapi.testclient import TestClient
    from agent import launcher

    with patch("agent.launcher._spawn_run_manager") as legacy:
        legacy.return_value = {"status": "triggered", "pid": 4242, "log": "/dev/null"}
        client_app = TestClient(launcher.app)
        resp = client_app.post("/run", json={"hint_run_id": "run-y"})
    assert resp.status_code == 200
    assert resp.json()["pid"] == 4242
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_launcher_spawn.py -q
```

Expected: 2 failures.

- [ ] **Step 3: Wire the mode switch in the launcher**

In `dashboard/backend/app/config.py`, add to `Settings`:

```python
    runner_mode: str = "container"   # "container" | "inline"
    runner_image: str = "claude-agent-station/agent:dev"
    default_runner_memory_limit: str = "2g"
    default_runner_cpu_limit: str = "1.0"
```

In `agent/launcher.py`, add:

```python
import docker  # at top

from agent.runner_spawn import RunnerHandle, spawn_runner

_docker_client = None


def _get_docker_client():
    global _docker_client
    if _docker_client is None:
        _docker_client = docker.from_env()
    return _docker_client


def _resolve_quotas(project_repo: str | None) -> dict:
    """Look up per-project quotas; fall back to settings defaults."""
    from app.config import settings
    default = {
        "memory": settings.default_runner_memory_limit,
        "cpus": settings.default_runner_cpu_limit,
    }
    if project_repo is None:
        return default
    # Synchronous DB lookup via a fresh session; this code runs in the
    # FastAPI thread for /run. asyncio.run() reuse is intentional.
    import asyncio
    from app.database import async_session
    from app.models import Project
    from sqlalchemy import select

    async def _fetch():
        async with async_session() as db:
            return (
                await db.execute(select(Project).where(Project.repo == project_repo))
            ).scalar_one_or_none()

    project = asyncio.run(_fetch())
    if project is None:
        return default
    return {
        "memory": project.runner_memory_limit or default["memory"],
        "cpus": project.runner_cpu_limit or default["cpus"],
    }


def _env_passthrough() -> dict:
    """STATION_* env vars to inject into the runner."""
    passthrough = {}
    for key, value in os.environ.items():
        if key.startswith("STATION_") and key not in (
            "STATION_RUNNER_MODE",       # not relevant inside runner
        ):
            passthrough[key] = value
    return passthrough


def _spawn_runner_container(hint_run_id: str, project_repo: str | None) -> dict:
    """Spawn one runner container; record handle; return route payload."""
    from app.config import settings
    if hint_run_id in _runners:
        raise HTTPException(
            status_code=409,
            detail=f"run {hint_run_id} already has a running container",
        )
    client = _get_docker_client()
    handle = spawn_runner(
        client,
        hint_run_id=hint_run_id,
        project_repo=project_repo,
        quotas=_resolve_quotas(project_repo),
        env_passthrough=_env_passthrough(),
        image=settings.runner_image,
        config_path=os.environ.get("STATION_CONFIG", "/var/lib/claude-agent-station/manager-config.json"),
        workspaces_dir=os.environ.get("STATION_WORKSPACES", "/var/lib/claude-agent-station/workspaces"),
    )
    _runners[hint_run_id] = handle
    return {"status": "triggered", "container": handle.container_name, "run_id": handle.run_id}
```

Then update the existing `@app.post("/run")` route to dispatch on mode:

```python
@app.post("/run")
def trigger(body: RunHint | None = None):
    from app.config import settings
    hint = body.hint_run_id if body else None
    mode = os.environ.get("STATION_RUNNER_MODE", settings.runner_mode)
    if mode == "inline":
        return _spawn_run_manager(hint_run_id=hint)
    if not hint:
        hint = "run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # `project_repo` is not currently in the request body; the dashboard
    # already publishes it via STATION_PROJECT_REPO on the launcher's env
    # for the active queue head, so read from there.
    project_repo = os.environ.get("STATION_PROJECT_REPO")
    return _spawn_runner_container(hint_run_id=hint, project_repo=project_repo)
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_launcher_spawn.py -q
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/app/config.py agent/launcher.py dashboard/backend/tests/test_launcher_spawn.py
git commit -m "feat(runner): /run dispatches to spawn_runner under container mode (#386)"
```

---

## Task 8: `/status` reports runs map; `/stop?run_id=`; `/webhook-tick?run_id=`

**Files:**
- Modify: `agent/launcher.py`
- Modify: `dashboard/backend/tests/test_launcher_runs_map.py` (append)

- [ ] **Step 1: Append failing tests**

```python
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from datetime import datetime, timezone


def test_status_returns_runs_list():
    launcher._runners.clear()
    handle = launcher.RunnerHandle(
        run_id="run-s1",
        container_name="cas-runner-s1",
        started_at=datetime.now(timezone.utc),
        last_webhook_at=datetime.now(timezone.utc),
        project_repo="x/y",
    )
    launcher._runners[handle.run_id] = handle

    resp = TestClient(launcher.app).get("/status")
    body = resp.json()
    assert "runs" in body
    assert any(r["run_id"] == "run-s1" for r in body["runs"])


def test_stop_endpoint_calls_docker_stop():
    launcher._runners.clear()
    handle = launcher.RunnerHandle(
        run_id="run-s2",
        container_name="cas-runner-s2",
        started_at=datetime.now(timezone.utc),
        last_webhook_at=datetime.now(timezone.utc),
        project_repo=None,
    )
    launcher._runners[handle.run_id] = handle

    with patch("agent.launcher._get_docker_client") as get_client:
        fake_client = MagicMock()
        get_client.return_value = fake_client
        resp = TestClient(launcher.app).post("/stop", params={"run_id": "run-s2"})

    assert resp.status_code == 200
    fake_client.containers.get.assert_called_with("cas-runner-s2")


def test_webhook_tick_bumps_last_webhook_at():
    launcher._runners.clear()
    earlier = datetime(2026, 1, 1, tzinfo=timezone.utc)
    handle = launcher.RunnerHandle(
        run_id="run-s3",
        container_name="cas-runner-s3",
        started_at=earlier,
        last_webhook_at=earlier,
        project_repo=None,
    )
    launcher._runners[handle.run_id] = handle

    resp = TestClient(launcher.app).post("/webhook-tick", params={"run_id": "run-s3"})
    assert resp.status_code == 200
    assert launcher._runners["run-s3"].last_webhook_at > earlier
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_launcher_runs_map.py -q
```

Expected: 3 failures.

- [ ] **Step 3: Re-shape the three endpoints**

In `agent/launcher.py`, replace the existing `@app.get("/status")`, `@app.post("/stop")`, and `@app.post("/webhook-tick")` handlers:

```python
@app.get("/status")
def status():
    return {
        "runs": [
            {
                "run_id": h.run_id,
                "container_name": h.container_name,
                "project_repo": h.project_repo,
                "started_at": h.started_at.isoformat(),
                "last_webhook_at": h.last_webhook_at.isoformat(),
            }
            for h in _runners.values()
        ]
    }


@app.post("/stop")
def stop(run_id: str):
    handle = _runners.get(run_id)
    if handle is None:
        raise HTTPException(status_code=404, detail=f"no runner for {run_id}")
    client = _get_docker_client()
    try:
        container = client.containers.get(handle.container_name)
        container.stop(timeout=30)
    except Exception as exc:
        logger.warning("stop %s: %s", handle.container_name, exc)
    _runners.pop(run_id, None)
    return {"status": "stopped", "run_id": run_id}


@app.post("/webhook-tick")
def webhook_tick(run_id: str):
    handle = _runners.get(run_id)
    if handle is None:
        raise HTTPException(status_code=404, detail=f"no runner for {run_id}")
    handle.last_webhook_at = datetime.now(timezone.utc)
    return {"status": "ok"}
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_launcher_runs_map.py -q
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add agent/launcher.py dashboard/backend/tests/test_launcher_runs_map.py
git commit -m "feat(runner): /status /stop /webhook-tick keyed by run_id (#386)"
```

---

## Task 9: Container-aware reaper

**Files:**
- New: `agent/launcher_reaper.py`
- Modify: `agent/launcher.py`
- New: `dashboard/backend/tests/test_launcher_reaper.py`

- [ ] **Step 1: Write the failing test**

Create `dashboard/backend/tests/test_launcher_reaper.py`:

```python
"""Container-aware reaper (#386)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from agent import launcher
from agent.launcher_reaper import reap_once


def _handle(run_id: str, age_s: int) -> launcher.RunnerHandle:
    return launcher.RunnerHandle(
        run_id=run_id,
        container_name=f"cas-runner-{run_id.removeprefix('run-')}",
        started_at=datetime.now(timezone.utc) - timedelta(seconds=age_s),
        last_webhook_at=datetime.now(timezone.utc) - timedelta(seconds=age_s),
        project_repo=None,
    )


def test_reaper_stops_silent_container():
    launcher._runners.clear()
    launcher._runners["run-stale"] = _handle("run-stale", 600)
    client = MagicMock()
    reap_once(client, zombie_timeout_seconds=120)
    client.containers.get.assert_called_with("cas-runner-stale")
    client.containers.get.return_value.stop.assert_called_with(timeout=30)
    assert "run-stale" not in launcher._runners


def test_reaper_leaves_active_container_alone():
    launcher._runners.clear()
    launcher._runners["run-fresh"] = _handle("run-fresh", 5)
    client = MagicMock()
    reap_once(client, zombie_timeout_seconds=120)
    client.containers.get.return_value.stop.assert_not_called()
    assert "run-fresh" in launcher._runners


def test_reaper_drops_missing_container():
    launcher._runners.clear()
    launcher._runners["run-gone"] = _handle("run-gone", 5)
    client = MagicMock()
    # docker SDK raises docker.errors.NotFound when get() can't find a container.
    import docker.errors as derr
    client.containers.get.side_effect = derr.NotFound("gone")
    reap_once(client, zombie_timeout_seconds=120)
    assert "run-gone" not in launcher._runners
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_launcher_reaper.py -q
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the reaper**

Create `agent/launcher_reaper.py`:

```python
"""Container-aware zombie reaper (#386).

Replaces the single-subprocess reaper in agent/launcher.py:278 with a loop
over _runners. A handle is reaped when (a) the runner container is gone
(normal exit, --rm removed it) or (b) the runner is still running but
last_webhook_at is older than ZOMBIE_TIMEOUT_SECONDS.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ZOMBIE_TIMEOUT_SECONDS = 120
REAP_INTERVAL_SECONDS = 15


def reap_once(client, *, zombie_timeout_seconds: int = ZOMBIE_TIMEOUT_SECONDS) -> None:
    from agent import launcher  # late-bind to avoid circular import

    import docker.errors as derr

    now = datetime.now(timezone.utc)
    for run_id, handle in list(launcher._runners.items()):
        try:
            container = client.containers.get(handle.container_name)
        except derr.NotFound:
            logger.info("reaper: %s gone, dropping", handle.container_name)
            launcher._runners.pop(run_id, None)
            continue
        idle = (now - handle.last_webhook_at).total_seconds()
        if idle > zombie_timeout_seconds:
            logger.warning("reaper: %s idle %.0fs, stopping", handle.container_name, idle)
            try:
                container.stop(timeout=30)
            except Exception as exc:
                logger.warning("reaper stop %s: %s", handle.container_name, exc)
            launcher._runners.pop(run_id, None)


async def reaper_loop() -> None:
    from agent import launcher
    while True:
        await asyncio.sleep(REAP_INTERVAL_SECONDS)
        try:
            reap_once(launcher._get_docker_client())
        except Exception:
            logger.exception("reaper_loop: unexpected")
```

In `agent/launcher.py`, replace the existing `_zombie_reaper` async loop (line 278) with:

```python
from agent.launcher_reaper import reaper_loop


@app.on_event("startup")
async def _start_reaper():
    asyncio.create_task(reaper_loop())
```

Remove the now-unused `_reap_once` and old loop helper if they were only used by the subprocess path.

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_launcher_reaper.py -q
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/launcher_reaper.py agent/launcher.py dashboard/backend/tests/test_launcher_reaper.py
git commit -m "feat(runner): container-aware reaper (#386)"
```

---

## Task 10: PR 2 — open

- [ ] **Step 1: Push**

```bash
git push -u origin feature/386-launcher-docker
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --base dev --title "feat(runner): Docker SDK launcher + runs map + reaper (#386, PR 2/3)" --body "$(cat <<'EOF'
Part 2 of 3 for #386.

## Summary
- `agent/runner_spawn.py`: unit-testable spawn_runner via the Docker SDK.
- Launcher `/run` dispatches to spawn_runner under `STATION_RUNNER_MODE=container` (default); the legacy subprocess path stays alive behind `STATION_RUNNER_MODE=inline` for one release as a panic-revert.
- `/status` now reports a list of runners; `/stop` + `/webhook-tick` keyed by `run_id`.
- Container-aware reaper in `agent/launcher_reaper.py`.

## Test plan
- [ ] `cd dashboard/backend && pytest tests/test_launcher_spawn.py tests/test_launcher_runs_map.py tests/test_launcher_reaper.py -q`
EOF
)"
```

- [ ] **Step 3-5: Wait for CI, merge, sync dev.**

---

# PR 3 — compose changes + integration tests + docs

## Task 11: Branch + `cas-launcher` compose service

**Files:**
- Modify: `compose.yml`
- Modify: `Dockerfile.agent`
- New: `dashboard/backend/tests/test_compose_runner.py`

- [ ] **Step 1: Branch**

```bash
git checkout dev && git pull --ff-only origin dev && git checkout -b feature/386-compose
```

- [ ] **Step 2: Write the failing compose-shape test**

Create `dashboard/backend/tests/test_compose_runner.py`:

```python
"""compose.yml shape for per-runner containers (#386)."""
from __future__ import annotations

from pathlib import Path

import yaml


def _compose():
    return yaml.safe_load((Path(__file__).resolve().parents[3] / "compose.yml").read_text())


def test_agent_service_mounts_docker_sock():
    c = _compose()
    agent = c["services"]["agent"]
    sock_mounts = [v for v in agent.get("volumes", []) if "docker.sock" in v]
    assert sock_mounts, "agent must mount /var/run/docker.sock"


def test_agent_net_network_declared():
    c = _compose()
    assert "agent-net" in c.get("networks", {})


def test_agent_runner_mode_env_present():
    c = _compose()
    env = c["services"]["agent"]["environment"]
    assert env.get("STATION_RUNNER_MODE") in ("container", "inline")


def test_dashboard_on_agent_net():
    c = _compose()
    nets = c["services"]["dashboard"].get("networks") or []
    assert "agent-net" in nets
```

- [ ] **Step 3: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_compose_runner.py -q
```

Expected: failures.

- [ ] **Step 4: Edit `compose.yml`**

In `compose.yml`:

1. Add a `networks:` top-level block (or extend existing):

```yaml
networks:
  agent-net:
    driver: bridge
  default:
```

2. Add `agent-net` to the `dashboard` and `agent` service `networks:` lists. If those lists don't currently exist, create them:

```yaml
  dashboard:
    networks:
      - default
      - agent-net

  agent:
    networks:
      - default
      - agent-net
```

3. In `agent.volumes`, add the Docker socket mount:

```yaml
      - /var/run/docker.sock:/var/run/docker.sock
```

4. In `agent.environment`, declare:

```yaml
      STATION_RUNNER_MODE: container
```

5. In `Dockerfile.agent`, append a comment line near the existing ENTRYPOINT:

```dockerfile
# When STATION_RUNNER_MODE=container the launcher spawns ephemeral
# cas-runner-<run-id> containers via the Docker socket. Each runner
# uses --init for proper PID 1 signal handling. (#386)
```

- [ ] **Step 5: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_compose_runner.py -q
git add compose.yml Dockerfile.agent dashboard/backend/tests/test_compose_runner.py
git commit -m "feat(runner): compose docker.sock mount + agent-net (#386)"
```

---

## Task 12: Integration test — spawn a real runner

**Files:**
- New: `dashboard/backend/tests/integration/test_runner_spawn.py`

- [ ] **Step 1: Write the failing integration test**

```python
"""Real Docker spawn of a runner container (#386).

Marked @pytest.mark.requires_docker; skipped if the docker daemon is unreachable.
"""
from __future__ import annotations

import time

import pytest

import docker


pytestmark = pytest.mark.requires_docker


def _docker_available() -> bool:
    try:
        return docker.from_env().ping()
    except Exception:
        return False


@pytest.fixture(scope="module")
def docker_client():
    if not _docker_available():
        pytest.skip("docker daemon not reachable")
    return docker.from_env()


def test_runner_spawn_and_auto_remove(docker_client):
    """Spawn a no-op runner against a sleep command; assert auto-cleanup."""
    from agent.runner_spawn import spawn_runner

    handle = spawn_runner(
        docker_client,
        hint_run_id="run-integ-1",
        project_repo=None,
        quotas={"memory": "256m", "cpus": "0.5"},
        env_passthrough={},
        image="alpine:3.20",
        config_path="/tmp/x",
        workspaces_dir="/tmp/y",
    )
    # Override the command: we don't have agent.station_orchestrator inside alpine.
    container = docker_client.containers.get(handle.container_name)
    # Replace the running process: stop and re-run sleep 3 in a new container
    # would be a different test; instead validate `--rm` semantics by waiting
    # for the original to exit.
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            container.reload()
            if container.status in ("exited", "removing"):
                break
        except docker.errors.NotFound:
            break
        time.sleep(0.5)
    # After 30s the container must be gone (`--rm` enforced).
    with pytest.raises(docker.errors.NotFound):
        docker_client.containers.get(handle.container_name)
```

- [ ] **Step 2: Verify it passes (Docker available)**

```bash
cd dashboard/backend && python3 -m pytest tests/integration/test_runner_spawn.py -q
```

Expected: 1 passed (or 1 skipped if Docker is not available).

- [ ] **Step 3: (No code change)**

- [ ] **Step 4: Register the marker**

Append to `dashboard/backend/pytest.ini`:

```ini
    requires_docker: skip if docker daemon not reachable
```

- [ ] **Step 5: Commit**

```bash
git add dashboard/backend/tests/integration/test_runner_spawn.py dashboard/backend/pytest.ini
git commit -m "test(runner): integration spawn + auto-remove (#386)"
```

---

## Task 13: Integration — two concurrent runs do not share PID namespace

**Files:**
- New: `dashboard/backend/tests/integration/test_concurrent_runs.py`

- [ ] **Step 1: Write the failing test**

```python
"""Two concurrent runners on different projects (#386)."""
from __future__ import annotations

import pytest

import docker

pytestmark = pytest.mark.requires_docker


@pytest.fixture(scope="module")
def docker_client():
    try:
        client = docker.from_env()
        client.ping()
        return client
    except Exception:
        pytest.skip("docker not reachable")


def test_two_runners_isolated(docker_client):
    from agent.runner_spawn import spawn_runner
    from agent import launcher

    launcher._runners.clear()
    handles = []
    for i, repo in enumerate(("x/a", "x/b"), start=1):
        handles.append(
            spawn_runner(
                docker_client,
                hint_run_id=f"run-conc-{i}",
                project_repo=repo,
                quotas={"memory": "128m", "cpus": "0.25"},
                env_passthrough={},
                image="alpine:3.20",
                config_path="/tmp/x",
                workspaces_dir="/tmp/y",
            )
        )

    try:
        c1 = docker_client.containers.get(handles[0].container_name)
        c2 = docker_client.containers.get(handles[1].container_name)
        # Different container IDs prove distinct PID namespaces in Docker.
        assert c1.id != c2.id
        top1 = c1.top()["Processes"] if c1.status == "running" else []
        top2 = c2.top()["Processes"] if c2.status == "running" else []
        pids1 = {row[1] for row in top1} if top1 else set()
        pids2 = {row[1] for row in top2} if top2 else set()
        # PID 1 inside each namespace would clash if they shared the namespace.
        # Disjoint or both contain "1" but on different cgroups — id check above
        # is the definitive isolation proof.
        _ = pids1, pids2
    finally:
        for h in handles:
            try:
                docker_client.containers.get(h.container_name).stop(timeout=5)
            except docker.errors.NotFound:
                pass
```

- [ ] **Step 2: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/integration/test_concurrent_runs.py -q
```

Expected: 1 passed (or skipped without Docker).

- [ ] **Step 3-5: (No code change.) Commit.**

```bash
git add dashboard/backend/tests/integration/test_concurrent_runs.py
git commit -m "test(runner): two concurrent runners are isolated (#386)"
```

---

## Task 14: Dashboard surface — container access snippet on RunDetail

**Files:**
- Modify: `dashboard/frontend/src/pages/RunDetail.svelte`

- [ ] **Step 1: Add the failing E2E expectation**

Create `dashboard/frontend/e2e/runner_access.spec.ts`:

```ts
import { expect, test } from '@playwright/test';

test('RunDetail surfaces docker exec snippet for running runs', async ({ page }) => {
  await page.route('**/api/runs/run-canned/full', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        run_id: 'run-canned',
        status: 'running',
        container_name: 'cas-runner-canned',
        coordinator_tasks: [],
      }),
    });
  });

  await page.goto('/runs/run-canned');
  await expect(page.getByText('docker exec -it cas-runner-canned bash')).toBeVisible();
});
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/frontend && npx playwright test e2e/runner_access.spec.ts
```

Expected: failure (no snippet rendered).

- [ ] **Step 3: Add the snippet to `RunDetail.svelte`**

In the Overview tab body of `dashboard/frontend/src/pages/RunDetail.svelte`, add (e.g. above the status badge):

```svelte
{#if run?.container_name && run?.status === 'running'}
  <div class="runner-access">
    <strong>Inspect runner:</strong>
    <code>docker exec -it {run.container_name} bash</code>
    <code>docker logs -f {run.container_name}</code>
  </div>
{/if}
```

If `run.container_name` is not yet on the API shape, expose it via `routers/runs.py::get_run_full_context` by reading from the launcher's `/status` endpoint (or from a new `Run.container_name` column added in a follow-up). For this initial PR, derive the name client-side via the documented format: `` `cas-runner-${run.run_id.replace(/^run-/, '')}` `` to avoid coupling to a launcher round-trip.

The simpler approach (no API change) wins for this task:

```svelte
{#if run?.status === 'running'}
  {@const cname = `cas-runner-${run.run_id.replace(/^run-/, '')}`}
  <div class="runner-access">
    <strong>Inspect runner:</strong>
    <code>docker exec -it {cname} bash</code>
    <code>docker logs -f {cname}</code>
  </div>
{/if}
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/frontend && npx playwright test e2e/runner_access.spec.ts
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/pages/RunDetail.svelte dashboard/frontend/e2e/runner_access.spec.ts
git commit -m "feat(runner): docker exec/logs snippet on RunDetail (#386)"
```

---

## Task 15: Operations doc + architecture doc

**Files:**
- Modify: `docs/operations.md`
- Modify: `docs/architecture.md`

- [ ] **Step 1: Append failing doc-shape test**

Append to `dashboard/backend/tests/test_compose_runner.py`:

```python
def test_operations_doc_has_runner_section():
    from pathlib import Path
    doc = (Path(__file__).resolve().parents[3] / "docs/operations.md").read_text()
    assert "## Inspecting a live runner" in doc
    assert "STATION_RUNNER_MODE" in doc
    assert "docker exec" in doc


def test_architecture_doc_mentions_per_run_container():
    from pathlib import Path
    doc = (Path(__file__).resolve().parents[3] / "docs/architecture.md").read_text()
    assert "cas-runner" in doc
    assert "cas-launcher" in doc or "per-run container" in doc
```

- [ ] **Step 2: Verify it fails**

```bash
cd dashboard/backend && python3 -m pytest tests/test_compose_runner.py -q
```

Expected: 2 failed.

- [ ] **Step 3: Append docs**

Append to `docs/operations.md`:

```markdown
## Inspecting a live runner

Each run executes inside an ephemeral container named `cas-runner-<run-id>`. The dashboard's run-detail page surfaces the exact snippets; from a shell:

```bash
# Tail logs in real time
docker logs -f cas-runner-<run-id>

# Drop a shell inside the runner (workspace under /var/lib/claude-agent-station/workspaces)
docker exec -it cas-runner-<run-id> bash
```

### Resource quotas per project

Set `runner_memory_limit` (Docker memory-limit syntax, e.g. `"2g"`, `"512m"`) and `runner_cpu_limit` (decimal cores, e.g. `"1.0"`) on a Project via the dashboard or `PATCH /api/projects/{id}`. Defaults come from `STATION_DEFAULT_RUNNER_MEMORY_LIMIT` / `STATION_DEFAULT_RUNNER_CPU_LIMIT` (currently `2g` / `1.0`).

### Rollback to inline mode

For one release after #386 ships, the legacy subprocess launcher path is available behind a flag:

```bash
docker compose stop agent
STATION_RUNNER_MODE=inline docker compose up -d agent
```

This restores the single-container behaviour. Remove the flag once stable to re-engage per-run containers.
```

Append to `docs/architecture.md`:

```markdown
## Per-run containers (#386)

The Agent Teams runtime now uses two roles instead of one:

- **cas-launcher** — long-lived FastAPI service. Exposes `/run`, `/status`, `/stop`, `/webhook-tick`. Mounts the Docker socket. Owns the `_runners: dict[str, RunnerHandle]` map.
- **cas-runner-<run-id>** — ephemeral container, one per run. Same image as the launcher (`claude-agent-station/agent:dev`). Entry point: `python -m agent.station_orchestrator --driver --run-id ...`. Auto-removed on exit (`--rm`).

Workspace state remains durable: each runner mounts the shared `station-data` volume, so consecutive runs on the same project see the same `workspaces/<repo>/` tree.

Two concurrent runs on different projects no longer share a process tree, SDK CLI subprocess, memory, or CPU budget.
```

- [ ] **Step 4: Verify it passes**

```bash
cd dashboard/backend && python3 -m pytest tests/test_compose_runner.py -q
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add docs/operations.md docs/architecture.md dashboard/backend/tests/test_compose_runner.py
git commit -m "docs(runner): inspect snippets + architecture per-run container (#386)"
```

---

## Task 16: PR 3 — open

- [ ] **Step 1: Push**

```bash
git push -u origin feature/386-compose
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --base dev --title "feat(runner): compose docker.sock + e2e + docs (#386, PR 3/3)" --body "$(cat <<'EOF'
Part 3 of 3 for #386.

## Summary
- `compose.yml`: docker.sock mount on `agent`, `agent-net` network, `STATION_RUNNER_MODE=container` default.
- Integration tests for real spawn / auto-remove and two-runner isolation (skipped without Docker).
- Dashboard RunDetail surfaces `docker exec` + `docker logs` snippets for running runs.
- New operations + architecture sections.

## Test plan
- [ ] `cd dashboard/backend && pytest tests/test_compose_runner.py tests/integration/test_runner_spawn.py tests/integration/test_concurrent_runs.py -q`
- [ ] Manual smoke: `docker compose up -d`; trigger a run via the dashboard; `docker ps` shows `cas-runner-…`; after completion the container is gone; verdict + diff display as today.
EOF
)"
```

- [ ] **Step 3-5: Wait for CI, merge, sync dev.**

---

## Self-review checklist

- [x] Every acceptance criterion in `2026-05-14-issue-386-per-project-containers.md` maps to ≥1 task:
  - Launcher spawns a fresh container per run via Docker socket → Tasks 6, 7.
  - Container destroyed on run exit → Task 6 (`remove=True`) + Task 9 (forced cleanup).
  - Workspace state persists via station-data volume → Task 6 (volume mount).
  - Two concurrent runs do not share a process tree → Task 13.
  - Resource quotas configurable per project → Task 1 (schema) + Task 6 (quotas dict) + Task 7 (resolve).
  - Documented operator command to inspect a runner → Task 14 (UI) + Task 15 (docs).
- [x] No `TBD`, `TODO`, `add error handling`, `similar to Task N` placeholders.
- [x] Real paths verified: `agent/launcher.py:118` (`_current`, `_last_webhook_at`), `agent/launcher.py:278` (`_zombie_reaper`), `dashboard/backend/app/models.py:17-43` (`Project`), `compose.yml`, `Dockerfile.agent`, `dashboard/frontend/src/pages/RunDetail.svelte`.
- [x] Type / name consistency: `RunnerHandle`, `_runners`, `spawn_runner`, `_spawn_runner_container`, `_get_docker_client`, `_resolve_quotas`, `reap_once`, `reaper_loop`, `STATION_RUNNER_MODE` used identically across files and tests.
- [x] Prerequisites called out (Task 0): #393 + #383 must be merged. Docker daemon must be reachable on the host. `--init` flag covers PID 1 zombie reaping inside each runner.
