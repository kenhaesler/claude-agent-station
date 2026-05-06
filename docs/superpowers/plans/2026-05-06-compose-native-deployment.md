# Compose-Native Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the agent station fully functional under docker-compose, with no behavior regressions on the existing systemd deployment, by abstracting the systemd-coupled call sites and giving the agent container ownership of OAuth-token refresh.

**Architecture:** Introduce a single `service_control` module that branches on `STATION_DEPLOY_MODE` (`systemd` or `compose`). In compose mode it talks HTTP to the agent container's launcher, which gains `/stop` and (already has) `/status` endpoints. The reaper, the trigger endpoint, the system router, and the plans-promote button all flow through this module. Independently, the agent container's launcher gains a periodic OAuth-refresh task so credentials stay fresh without dashboard intervention. The hard-coded `claude-agent` service user in GitHub OAuth storage becomes env-driven.

**Tech Stack:** Python 3.11, FastAPI, httpx, pytest, respx (httpx mocking), docker-compose. Existing modules: `agent/launcher.py`, `agent/scripts/refresh-token.py`, `dashboard/backend/app/services/systemd.py`, `dashboard/backend/app/services/stale_run_reaper.py`.

**Sequencing:** Two PRs.
- **PR-A** = Tasks 1–10 (Phases 1+2+4): service_control abstraction, all four systemctl call-site migrations, configurable service-user.
- **PR-B** = Tasks 11–14 (Phase 3): in-container token refresh.

Each PR is independently shippable. PR-A is the larger refactor; PR-B is small and standalone.

---

## File Structure

### PR-A — service_control + reaper + chown

**Create:**
- `dashboard/backend/app/services/service_control.py` — deploy-mode-aware service control facade.
- `dashboard/backend/tests/test_service_control.py` — unit tests for the facade.
- `dashboard/backend/tests/test_launcher_endpoints.py` — unit tests for `/stop`, `/status`, plus auth.

**Modify:**
- `agent/launcher.py` — add `/stop` endpoint; keep `/run`, `/status`, `/health`.
- `dashboard/backend/app/services/systemd.py` — keep impl; just used by `service_control`.
- `dashboard/backend/app/services/stale_run_reaper.py` — replace `pgrep` + direct `systemctl status` with `service_control.get_agent_status()`.
- `dashboard/backend/app/routers/runs.py` — replace inline launcher branch with a call to `service_control.start_agent_service()`.
- `dashboard/backend/app/routers/system.py` — replace direct `systemctl()` with `service_control.run_action()`.
- `dashboard/backend/app/routers/plans.py:220` — replace direct `systemctl()` with `service_control.start_agent_service()`.
- `dashboard/backend/app/routers/github_oauth.py:89` — read `STATION_SERVICE_USER` env, default `claude-agent`.
- `dashboard/backend/tests/test_trigger_run.py` — adapt to new abstraction (env-mocked instead of inline patching).
- `compose.yml` — set `STATION_DEPLOY_MODE=compose` on dashboard; change `STATION_AGENT_LAUNCHER_URL` to base URL (`http://agent:8421`) instead of `/run`.

### PR-B — token refresh in agent

**Create:**
- `agent/token_refresh.py` — async task that periodically invokes `refresh-token.py`.
- `dashboard/backend/tests/test_token_refresh_task.py` — unit tests.

**Modify:**
- `agent/launcher.py` — schedule the refresh task on FastAPI startup.
- `dashboard/backend/app/main.py` — drop `_periodic_token_refresh` registration when `STATION_DEPLOY_MODE=compose` (agent owns it).
- `compose.yml` — bind-mount `~/.claude` rw on the agent (already there) so the refresh task can write back.

---

## Self-Review Checklist (filled in after writing)

Performed at end of plan. See bottom.

---

# PR-A: Service Control Abstraction (Phases 1, 2, 4)

## Task 1: Add `/stop` endpoint to the launcher

**Files:**
- Modify: `agent/launcher.py`
- Test: `dashboard/backend/tests/test_launcher_endpoints.py` (new file)

- [ ] **Step 1: Create the test file with a failing `/stop` test**

```python
# dashboard/backend/tests/test_launcher_endpoints.py
"""Unit tests for the agent launcher's HTTP endpoints."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app(monkeypatch):
    """Reload the launcher module so each test gets a fresh _current global
    and its own LAUNCHER_TOKEN reading."""
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "")
    monkeypatch.setenv("STATION_RUN_MANAGER", "/nonexistent/run-manager.sh")
    import importlib

    import agent.launcher as launcher_mod
    importlib.reload(launcher_mod)
    return launcher_mod.app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_stop_when_no_run_in_flight_returns_409(client):
    resp = client.post("/stop")
    assert resp.status_code == 409
    assert "no run is currently running" in resp.json()["detail"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

```
cd /home/simon/Documents/claude-agent-station
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/test_launcher_endpoints.py::test_stop_when_no_run_in_flight_returns_409 -v
```
Expected: FAIL — `404 Not Found` because `/stop` doesn't exist yet.

- [ ] **Step 3: Add `/stop` endpoint to `agent/launcher.py`**

Insert after the existing `/status` route definition:

```python
@app.post("/stop")
def stop(x_launcher_token: str | None = Header(default=None)) -> dict:
    """Send SIGTERM to the running run-manager.sh, if any.

    Returns 409 if no run is in flight. The dashboard's service_control
    module calls this in compose mode where ``systemctl stop`` is unavailable.
    """
    global _current

    if LAUNCHER_TOKEN and x_launcher_token != LAUNCHER_TOKEN:
        raise HTTPException(status_code=401, detail="invalid or missing launcher token")

    if _current is None or _current.poll() is not None:
        raise HTTPException(status_code=409, detail="No run is currently running")

    pid = _current.pid
    _current.terminate()
    logger.info("Sent SIGTERM to run-manager.sh pid=%s", pid)
    return {"status": "stopping", "pid": pid}
```

- [ ] **Step 4: Run test to verify it passes**

```
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/test_launcher_endpoints.py::test_stop_when_no_run_in_flight_returns_409 -v
```
Expected: PASS.

- [ ] **Step 5: Add the auth-token test**

Append to `dashboard/backend/tests/test_launcher_endpoints.py`:

```python
def test_stop_requires_token_when_configured(monkeypatch):
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "secret-xyz")
    import importlib

    import agent.launcher as launcher_mod
    importlib.reload(launcher_mod)
    client = TestClient(launcher_mod.app)

    # Without token
    resp = client.post("/stop")
    assert resp.status_code == 401

    # With wrong token
    resp = client.post("/stop", headers={"X-Launcher-Token": "wrong"})
    assert resp.status_code == 401


def test_status_does_not_require_token(monkeypatch):
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "secret-xyz")
    import importlib

    import agent.launcher as launcher_mod
    importlib.reload(launcher_mod)
    client = TestClient(launcher_mod.app)

    resp = client.get("/status")
    assert resp.status_code == 200
    assert resp.json() == {"running": False, "pid": None, "exit_code": None}
```

- [ ] **Step 6: Run all launcher endpoint tests**

```
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/test_launcher_endpoints.py -v
```
Expected: 3 PASS.

- [ ] **Step 7: Commit**

```
git add agent/launcher.py dashboard/backend/tests/test_launcher_endpoints.py
git commit -m "feat(launcher): add /stop endpoint with token auth

Mirrors systemctl stop for the compose deployment path. Returns 409
when no run is in flight, 401 when STATION_LAUNCHER_TOKEN is set and
the X-Launcher-Token header is missing or wrong. /status remains
unauthenticated so the dashboard can poll it for the reaper."
```

---

## Task 2: Create the `service_control` module skeleton

**Files:**
- Create: `dashboard/backend/app/services/service_control.py`
- Create: `dashboard/backend/tests/test_service_control.py`

- [ ] **Step 1: Write the first failing test**

```python
# dashboard/backend/tests/test_service_control.py
"""Tests for the deploy-mode-aware service control facade."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_mode_default_is_systemd(monkeypatch):
    monkeypatch.delenv("STATION_DEPLOY_MODE", raising=False)
    from app.services import service_control
    assert service_control._mode() == "systemd"


@pytest.mark.asyncio
async def test_mode_reads_env_lowercase(monkeypatch):
    monkeypatch.setenv("STATION_DEPLOY_MODE", "COMPOSE")
    from app.services import service_control
    assert service_control._mode() == "compose"
```

- [ ] **Step 2: Run, expect ImportError**

```
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/test_service_control.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.service_control'`.

- [ ] **Step 3: Create the module with minimal scaffolding**

```python
# dashboard/backend/app/services/service_control.py
"""Deploy-mode-aware service control.

In ``systemd`` mode (the default, bare-metal install), service actions are
``sudo systemctl <action> claude-agent.service`` calls. In ``compose`` mode,
they go to the agent container's HTTP launcher instead — the dashboard
container has no systemd, so it can't shell out to systemctl.

Selected by ``STATION_DEPLOY_MODE`` env (``systemd`` | ``compose``).
The launcher base URL is ``STATION_AGENT_LAUNCHER_URL`` (e.g.
``http://agent:8421``); the optional shared secret is ``STATION_LAUNCHER_TOKEN``.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_AGENT_UNIT = "claude-agent.service"


def _mode() -> str:
    return os.environ.get("STATION_DEPLOY_MODE", "systemd").lower()
```

- [ ] **Step 4: Run, expect pass**

```
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/test_service_control.py -v
```
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```
git add dashboard/backend/app/services/service_control.py dashboard/backend/tests/test_service_control.py
git commit -m "feat(service_control): add module skeleton with mode reader

STATION_DEPLOY_MODE selects between the existing systemd path and a new
compose path that goes via the launcher. Default is 'systemd' so bare-metal
deployments are unaffected when this lands."
```

---

## Task 3: Implement `start_agent_service()` for both modes

**Files:**
- Modify: `dashboard/backend/app/services/service_control.py`
- Modify: `dashboard/backend/tests/test_service_control.py`

- [ ] **Step 1: Add tests for both modes**

Append to `test_service_control.py`:

```python
from unittest.mock import AsyncMock, patch

import respx


@pytest.mark.asyncio
async def test_start_systemd_mode_calls_systemctl(monkeypatch):
    monkeypatch.setenv("STATION_DEPLOY_MODE", "systemd")
    from app.services import service_control

    mock_systemctl = AsyncMock(return_value={"success": True, "stdout": "", "stderr": "", "returncode": 0})
    with patch("app.services.service_control.systemctl", mock_systemctl):
        result = await service_control.start_agent_service()

    assert result["success"] is True
    mock_systemctl.assert_awaited_once_with("start", "claude-agent.service")


@pytest.mark.asyncio
async def test_start_compose_mode_posts_to_launcher(monkeypatch):
    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")
    monkeypatch.delenv("STATION_LAUNCHER_TOKEN", raising=False)
    from app.services import service_control

    with respx.mock() as mock:
        mock.post("http://agent:8421/run").respond(200, json={"status": "triggered", "pid": 42})
        result = await service_control.start_agent_service()

    assert result["success"] is True
    assert result["pid"] == 42


@pytest.mark.asyncio
async def test_start_compose_mode_forwards_token(monkeypatch):
    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "tok-1")
    from app.services import service_control

    with respx.mock() as mock:
        route = mock.post("http://agent:8421/run").respond(200, json={"status": "triggered"})
        await service_control.start_agent_service()

    assert route.calls[0].request.headers["X-Launcher-Token"] == "tok-1"


@pytest.mark.asyncio
async def test_start_compose_mode_unreachable_returns_error(monkeypatch):
    import httpx
    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")
    from app.services import service_control

    with respx.mock() as mock:
        mock.post("http://agent:8421/run").mock(side_effect=httpx.ConnectError("refused"))
        result = await service_control.start_agent_service()

    assert result["success"] is False
    assert "launcher unreachable" in result["error"].lower()
```

- [ ] **Step 2: Run, expect import errors / failures**

```
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/test_service_control.py -v
```
Expected: FAIL — `start_agent_service` not defined.

- [ ] **Step 3: Implement the function in `service_control.py`**

Append to `service_control.py`:

```python
import httpx

from app.services.systemd import systemctl


def _launcher_base_url() -> str | None:
    return os.environ.get("STATION_AGENT_LAUNCHER_URL")


def _launcher_token() -> str | None:
    val = os.environ.get("STATION_LAUNCHER_TOKEN", "")
    return val if val else None


def _launcher_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    token = _launcher_token()
    if token:
        headers["X-Launcher-Token"] = token
    return headers


async def _launcher_call(method: str, path: str) -> dict:
    """Call the agent launcher and shape the response like systemctl()."""
    base = _launcher_base_url()
    if not base:
        return {"success": False, "error": "STATION_AGENT_LAUNCHER_URL not set"}
    url = f"{base.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request(method, url, headers=_launcher_headers())
    except httpx.HTTPError as exc:
        return {"success": False, "error": f"launcher unreachable: {exc}"}

    body: dict = {}
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text}
    return {
        "success": 200 <= resp.status_code < 300,
        "status_code": resp.status_code,
        **body,
    }


async def start_agent_service() -> dict:
    """Start the agent (systemctl start, or POST /run on the launcher)."""
    if _mode() == "compose":
        return await _launcher_call("POST", "/run")
    return await systemctl("start", DEFAULT_AGENT_UNIT)
```

- [ ] **Step 4: Run, expect pass**

```
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/test_service_control.py -v
```
Expected: 6 PASS (2 from earlier + 4 new).

- [ ] **Step 5: Commit**

```
git add dashboard/backend/app/services/service_control.py dashboard/backend/tests/test_service_control.py
git commit -m "feat(service_control): implement start_agent_service() for both modes"
```

---

## Task 4: Implement `stop_agent_service()` and `get_agent_status()`

**Files:**
- Modify: `dashboard/backend/app/services/service_control.py`
- Modify: `dashboard/backend/tests/test_service_control.py`

- [ ] **Step 1: Add tests for both functions in both modes**

Append to `test_service_control.py`:

```python
@pytest.mark.asyncio
async def test_stop_systemd_mode_calls_systemctl(monkeypatch):
    monkeypatch.setenv("STATION_DEPLOY_MODE", "systemd")
    from app.services import service_control
    mock_systemctl = AsyncMock(return_value={"success": True})
    with patch("app.services.service_control.systemctl", mock_systemctl):
        await service_control.stop_agent_service()
    mock_systemctl.assert_awaited_once_with("stop", "claude-agent.service")


@pytest.mark.asyncio
async def test_stop_compose_mode_posts_to_launcher(monkeypatch):
    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")
    from app.services import service_control
    with respx.mock() as mock:
        mock.post("http://agent:8421/stop").respond(200, json={"status": "stopping", "pid": 7})
        result = await service_control.stop_agent_service()
    assert result["success"] is True
    assert result["pid"] == 7


@pytest.mark.asyncio
async def test_status_systemd_uses_systemd_get_service_status(monkeypatch):
    monkeypatch.setenv("STATION_DEPLOY_MODE", "systemd")
    from app.services import service_control
    mock = AsyncMock(return_value={"service_active": True, "timer_active": False})
    with patch("app.services.service_control.systemd_get_status", mock):
        result = await service_control.get_agent_status()
    assert result["service_active"] is True


@pytest.mark.asyncio
async def test_status_compose_translates_launcher_status(monkeypatch):
    """In compose mode, /status returns {running, pid, exit_code} — translate
    to the systemd-shaped {service_active, ...} the dashboard already
    consumes so existing UI code keeps working."""
    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")
    from app.services import service_control
    with respx.mock() as mock:
        mock.get("http://agent:8421/status").respond(200, json={"running": True, "pid": 99, "exit_code": None})
        result = await service_control.get_agent_status()
    assert result["service_active"] is True
    assert result["pid"] == 99


@pytest.mark.asyncio
async def test_status_compose_when_unreachable_returns_inactive(monkeypatch):
    import httpx
    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
    monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")
    from app.services import service_control
    with respx.mock() as mock:
        mock.get("http://agent:8421/status").mock(side_effect=httpx.ConnectError("refused"))
        result = await service_control.get_agent_status()
    assert result["service_active"] is False
    assert result.get("error")
```

- [ ] **Step 2: Run, expect failures**

```
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/test_service_control.py -v
```
Expected: FAIL on the new tests — functions undefined.

- [ ] **Step 3: Implement both functions**

Append to `service_control.py`:

```python
from app.services.systemd import get_service_status as systemd_get_status


async def stop_agent_service() -> dict:
    """Stop the agent (systemctl stop, or POST /stop on the launcher)."""
    if _mode() == "compose":
        return await _launcher_call("POST", "/stop")
    return await systemctl("stop", DEFAULT_AGENT_UNIT)


async def get_agent_status() -> dict:
    """Return service-active status with a shape compatible with the existing
    systemd path: ``{"service_active": bool, "timer_active": bool, ...}``.

    In compose mode the agent has no timer (the launcher is always up), so
    ``timer_active`` is always False.
    """
    if _mode() == "compose":
        result = await _launcher_call("GET", "/status")
        running = bool(result.get("running"))
        return {
            "service_active": running,
            "timer_active": False,
            "timer_next": None,
            "service_stdout": "",
            "timer_stdout": "",
            "pid": result.get("pid"),
            "error": None if result.get("success") else result.get("error"),
        }
    return await systemd_get_status()
```

- [ ] **Step 4: Run, expect pass**

```
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/test_service_control.py -v
```
Expected: 11 PASS.

- [ ] **Step 5: Commit**

```
git add dashboard/backend/app/services/service_control.py dashboard/backend/tests/test_service_control.py
git commit -m "feat(service_control): add stop_agent_service and get_agent_status

get_agent_status normalises the launcher's /status shape to the systemd
shape the rest of the dashboard already consumes, so call-site migrations
in subsequent tasks are pure substitutions."
```

---

## Task 5: Migrate `runs.py:trigger_run` to `service_control`

**Files:**
- Modify: `dashboard/backend/app/routers/runs.py:370-400`
- Modify: `dashboard/backend/tests/test_trigger_run.py`

- [ ] **Step 1: Update existing tests to expect the new abstraction**

Replace `test_trigger_run.py` test bodies that use `STATION_AGENT_LAUNCHER_URL` directly to use `STATION_DEPLOY_MODE=compose` + base URL. Specifically, replace each `monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421/run")` with:

```python
monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
monkeypatch.setenv("STATION_AGENT_LAUNCHER_URL", "http://agent:8421")
```

And update `LAUNCHER_URL` constant at top:

```python
LAUNCHER_URL = "http://agent:8421/run"  # respx still mocks the full URL
```

The systemd-fallback test should set `monkeypatch.delenv("STATION_DEPLOY_MODE", raising=False)` (default systemd) and keep the rest.

- [ ] **Step 2: Run, expect failures (route still uses old logic)**

```
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/test_trigger_run.py -v
```
Expected: existing 6 tests PASS (the old logic still works because the env var is still read), but the abstraction-mode tests will fail once the route is rewritten. Actually, both should pass since the old code path also responds to STATION_AGENT_LAUNCHER_URL — the env var rename didn't happen yet. Note this; we'll re-run after Step 4.

- [ ] **Step 3: Replace the trigger_run body with a service_control call**

In `dashboard/backend/app/routers/runs.py`, replace the entire `trigger_run` function:

```python
@router.post("/trigger")
async def trigger_run():
    """Trigger the agent service immediately.

    Delegates to :mod:`app.services.service_control` which branches on
    ``STATION_DEPLOY_MODE`` between ``sudo systemctl start`` (systemd
    deployments) and ``POST /run`` on the agent launcher (compose).
    """
    from app.services import service_control

    result = await service_control.start_agent_service()
    if not result.get("success"):
        # Compose path may set status_code to a launcher 4xx (e.g. 409
        # "already running"); systemd path returns generic 500. Preserve
        # the 4xx so the UI can show a precise message.
        status = result.get("status_code") or 500
        if status < 400:
            status = 500
        # Detail precedence: structured error fields first, then any
        # JSON ``detail`` from the launcher response, then ``raw`` for
        # plain-text 4xx bodies (the launcher's HTTPException emits JSON
        # but tests and some clients exercise the text path), then a
        # generic fallback.
        raise HTTPException(
            status_code=status,
            detail=(
                result.get("error")
                or result.get("stderr")
                or result.get("detail")
                or result.get("raw")
                or "Failed to trigger run"
            ),
        )
    detail = result.get("detail") or (
        "agent launcher accepted run" if "pid" in result else "claude-agent.service started"
    )
    return {"status": "triggered", "detail": detail, **{k: v for k, v in result.items() if k not in {"success", "status_code"}}}
```

Also drop the now-unused inline `httpx`/`os` use — but keep the top-of-file imports since other routes may use them. Verify with `grep`.

- [ ] **Step 4: Run, expect all tests pass**

```
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/test_trigger_run.py dashboard/backend/tests/test_service_control.py -v
```
Expected: 17 PASS (6 trigger + 11 service_control).

- [ ] **Step 5: Commit**

```
git add dashboard/backend/app/routers/runs.py dashboard/backend/tests/test_trigger_run.py
git commit -m "refactor(runs): trigger_run delegates to service_control

The deploy-mode branch lives in service_control now; runs.py just shapes
the response and propagates 4xx codes from the launcher path."
```

---

## Task 6: Migrate `system.py` and `plans.py` to `service_control`

**Files:**
- Modify: `dashboard/backend/app/routers/system.py:15,17,31,51`
- Modify: `dashboard/backend/app/routers/plans.py:17,220`
- Modify: `dashboard/backend/tests/test_system.py`

- [ ] **Step 1: Update test_system.py expectations**

The `test_service_action_*` tests currently patch `app.routers.system.systemctl` and `app.routers.system.get_service_status`. After migration, system.py will not import either name directly — both flow through `service_control`. Apply this transformation to **every** affected test in `test_system.py`:

```python
# Before — service-action tests:
mock_result = {"success": True, "stdout": "", "stderr": "", "returncode": 0}
with patch("app.routers.system.systemctl", new_callable=AsyncMock, return_value=mock_result):
    resp = await client.post("/api/system/service/restart")

# After:
mock_result = {"success": True, "stdout": "", "stderr": "", "returncode": 0}
with patch("app.routers.system.service_control.run_action", new_callable=AsyncMock, return_value=mock_result) as mock_action:
    resp = await client.post("/api/system/service/restart")
mock_action.assert_awaited_once_with("restart", "claude-agent.service")
```

```python
# Before — status endpoint test:
mock_status = {"service_active": True, "timer_active": True, "timer_next": "Mon 2026-03-16 04:00:00 UTC"}
with patch("app.routers.system.get_service_status", new_callable=AsyncMock, return_value=mock_status):
    resp = await client.get("/api/system/status")

# After:
mock_status = {"service_active": True, "timer_active": True, "timer_next": "Mon 2026-03-16 04:00:00 UTC"}
with patch("app.routers.system.service_control.get_agent_status", new_callable=AsyncMock, return_value=mock_status):
    resp = await client.get("/api/system/status")
```

Sweep with grep first so you don't miss any:
```
grep -n "app.routers.system\.\(systemctl\|get_service_status\)" dashboard/backend/tests/test_system.py
```
Apply the same transformation to each match — typically four service-action tests and one status test.

- [ ] **Step 2: Add a generic `run_action` to service_control**

Append to `service_control.py`:

```python
async def run_action(action: str, unit: str | None = None) -> dict:
    """Generic service action — used by the system router which exposes
    arbitrary {start|stop|restart|status|enable|disable} on a unit.

    In compose mode we only honour start/stop/status (the only verbs the
    launcher implements); other actions return a 501-shaped error so the
    UI can show a clear message instead of a 500.
    """
    if _mode() == "compose":
        if action == "start":
            return await start_agent_service()
        if action == "stop":
            return await stop_agent_service()
        if action == "status":
            status = await get_agent_status()
            return {"success": True, **status}
        return {
            "success": False,
            "status_code": 501,
            "error": f"Action '{action}' is not supported in compose mode",
        }
    return await systemctl(action, unit or DEFAULT_AGENT_UNIT)
```

Add a corresponding test:

```python
@pytest.mark.asyncio
async def test_run_action_compose_unsupported_action_returns_501(monkeypatch):
    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")
    from app.services import service_control
    result = await service_control.run_action("enable", "claude-agent.timer")
    assert result["success"] is False
    assert result["status_code"] == 501
```

- [ ] **Step 3: Update system.py**

Replace the imports and the two call sites:

```python
# At top of system.py — replace the existing systemd import block
from app.services import service_control
# Drop: from app.services.systemd import get_service_status, systemctl

# Replace line ~31 (status endpoint):
svc = await service_control.get_agent_status()

# Replace line ~51 (service action endpoint):
result = await service_control.run_action(action, unit)
if not result.get("success"):
    status = result.get("status_code") or 500
    if status < 400:
        status = 500
    raise HTTPException(status_code=status, detail=result.get("error") or "Failed")
```

Note: `system.py` may still need other things from `app.services.systemd` (like `ALLOWED_ACTIONS`, `get_system_resources`). Re-read the file and only drop what's now unused.

- [ ] **Step 4: Update plans.py:220**

```python
# Replace import at top of plans.py:
from app.services import service_control
# Drop: from app.services.systemd import systemctl

# Replace line 220:
trigger_result = await service_control.start_agent_service()
```

- [ ] **Step 5: Run all affected tests**

```
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/test_system.py dashboard/backend/tests/test_service_control.py -v
```
Expected: PASS for all.

- [ ] **Step 6: Commit**

```
git add dashboard/backend/app/services/service_control.py dashboard/backend/app/routers/system.py dashboard/backend/app/routers/plans.py dashboard/backend/tests/test_system.py dashboard/backend/tests/test_service_control.py
git commit -m "refactor(system,plans): route service control through service_control

Drops direct app.services.systemd imports from these routers. compose mode
gets 501 for verbs the launcher doesn't implement (enable/disable/restart),
so the UI can show a precise message instead of a 500."
```

---

## Task 7: Migrate the stale-run reaper

**Files:**
- Modify: `dashboard/backend/app/services/stale_run_reaper.py:8,30,44`
- Create: `dashboard/backend/tests/test_stale_run_reaper_compose.py`

- [ ] **Step 1: Write failing tests for compose-mode reaper behavior**

```python
# dashboard/backend/tests/test_stale_run_reaper_compose.py
"""Reaper must use service_control (not pgrep + systemctl) so it works
in compose where the orchestrator is in a sibling container."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.database import Base, async_session, engine
from app.models import Run
from app.services.stale_run_reaper import reap_stale_runs


@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def stale_run(setup_db):
    async with async_session() as s:
        r = Run(run_id="run-stale-001", status="running", started_at=datetime.now(timezone.utc))
        s.add(r)
        await s.commit()
    return "run-stale-001"


@pytest.mark.asyncio
async def test_reaper_does_nothing_when_agent_active(stale_run):
    mock_status = AsyncMock(return_value={"service_active": True})
    with patch("app.services.stale_run_reaper.get_agent_status", mock_status):
        async with async_session() as s:
            n = await reap_stale_runs(s)
    assert n == 0


@pytest.mark.asyncio
async def test_reaper_marks_runs_interrupted_when_agent_inactive(stale_run):
    mock_status = AsyncMock(return_value={"service_active": False})
    with patch("app.services.stale_run_reaper.get_agent_status", mock_status):
        async with async_session() as s:
            n = await reap_stale_runs(s)
            await s.commit()
            row = (await s.execute(select(Run).where(Run.run_id == stale_run))).scalar_one()
    assert n == 1
    assert row.status == "interrupted"
```

- [ ] **Step 2: Run, expect failures**

```
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/test_stale_run_reaper_compose.py -v
```
Expected: FAIL — patch path doesn't match (reaper still imports `get_service_status` from `systemd`).

- [ ] **Step 3: Update stale_run_reaper.py**

```python
# Replace the import at the top of stale_run_reaper.py:
from app.services.service_control import _mode, get_agent_status
# Drop: from app.services.systemd import get_service_status

# Replace line ~30 onwards
async def reap_stale_runs(db: AsyncSession) -> int:
    svc = await get_agent_status()
    if svc.get("service_active"):
        return 0  # Agent is alive — nothing to reap

    # pgrep is a useful tie-breaker for manual orchestrator invocations
    # outside the systemd unit (developer testing on the host). It's
    # noise in compose mode — the orchestrator runs in a sibling container
    # so pgrep here finds nothing, and the subprocess + 3s timeout adds
    # latency to every reaper tick. Skip it in compose.
    if _mode() == "systemd" and _is_orchestrator_process_alive():
        return 0

    # ... rest unchanged
```

- [ ] **Step 4: Run, expect pass**

```
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/test_stale_run_reaper_compose.py -v
```
Expected: 2 PASS.

- [ ] **Step 5: Verify existing reaper tests still pass**

```
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/ -k reaper -v
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```
git add dashboard/backend/app/services/stale_run_reaper.py dashboard/backend/tests/test_stale_run_reaper_compose.py
git commit -m "fix(reaper): use service_control so compose runs aren't reaped

Previously the reaper ran pgrep against the dashboard container's process
table, which never contained the orchestrator (running in the agent
container), and queried systemd directly which fails in compose. Both
checks now flow through service_control, which queries the launcher's
/status endpoint in compose mode."
```

---

## Task 8: Make the GitHub OAuth chown user configurable

**Files:**
- Modify: `dashboard/backend/app/routers/github_oauth.py:88-90`
- Create: `dashboard/backend/tests/test_github_oauth_chown.py`

- [ ] **Step 1: Write the failing test**

```python
# dashboard/backend/tests/test_github_oauth_chown.py
"""STATION_SERVICE_USER lets compose deployments override the hard-coded
'claude-agent' user name. Without it, shutil.chown raises LookupError
which is suppressed — but then the chown is silently a no-op."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


def test_write_token_uses_station_service_user_env(monkeypatch, tmp_path):
    monkeypatch.setenv("STATION_SERVICE_USER", "myappuser")
    from app.routers import github_oauth

    target = tmp_path / "github_token"

    with patch("shutil.chown") as mock_chown:
        github_oauth._write_token(target, {"access_token": "x"})

    mock_chown.assert_called_once()
    _, kwargs = mock_chown.call_args
    assert kwargs.get("user") == "myappuser"


def test_write_token_defaults_to_claude_agent(monkeypatch, tmp_path):
    monkeypatch.delenv("STATION_SERVICE_USER", raising=False)
    from app.routers import github_oauth

    target = tmp_path / "github_token"

    with patch("shutil.chown") as mock_chown:
        github_oauth._write_token(target, {"access_token": "x"})

    _, kwargs = mock_chown.call_args
    assert kwargs.get("user") == "claude-agent"


def test_write_token_swallows_lookup_error_when_user_missing(tmp_path):
    """In containers neither claude-agent nor STATION_SERVICE_USER may exist
    — the chmod 600 must still happen so the token isn't world-readable."""
    from app.routers import github_oauth

    target = tmp_path / "github_token"

    def _raise_lookup(*args, **kwargs):
        raise LookupError("no such user")

    with patch("shutil.chown", side_effect=_raise_lookup):
        github_oauth._write_token(target, {"access_token": "x"})

    assert target.exists()
    # 0o600 = 384
    assert (target.stat().st_mode & 0o777) == 0o600
```

- [ ] **Step 2: Run, expect failures**

```
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/test_github_oauth_chown.py -v
```
Expected: FAIL on the env-driven test (still hard-coded).

- [ ] **Step 3: Update github_oauth.py**

Replace lines 88–90:

```python
# Before:
import shutil
with contextlib.suppress(LookupError, OSError):
    shutil.chown(path, user="claude-agent", group="claude-agent")

# After:
import shutil
service_user = os.environ.get("STATION_SERVICE_USER", "claude-agent")
with contextlib.suppress(LookupError, OSError):
    shutil.chown(path, user=service_user, group=service_user)
```

(`os` is already imported in the file; verify with grep before adding.)

- [ ] **Step 4: Run, expect pass**

```
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/test_github_oauth_chown.py -v
```
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```
git add dashboard/backend/app/routers/github_oauth.py dashboard/backend/tests/test_github_oauth_chown.py
git commit -m "feat(github_oauth): STATION_SERVICE_USER env overrides chown user

claude-agent is the bare-metal default; compose deployments can pin it to
whatever user actually exists in the container (or leave unset and let the
LookupError suppression no-op the chown — chmod 600 still runs)."
```

---

## Task 9: Wire `STATION_DEPLOY_MODE` into compose.yml

**Files:**
- Modify: `compose.yml`

- [ ] **Step 1: Update the dashboard service env**

Add `STATION_DEPLOY_MODE: compose` and change `STATION_AGENT_LAUNCHER_URL` from a `/run` path to a base URL:

```yaml
  dashboard:
    # ...
    environment:
      STATION_HOST: "0.0.0.0"
      STATION_PORT: "8420"
      STATION_DB_PATH: /var/lib/claude-agent-station/station.db
      STATION_LOG_DIR: /var/log/claude-agent
      STATION_WORKSPACES: /var/lib/claude-agent-station/workspaces
      STATION_DEPLOY_MODE: compose
      STATION_AGENT_LAUNCHER_URL: http://agent:8421
      STATION_LAUNCHER_TOKEN: ${STATION_LAUNCHER_TOKEN:-cas-dev-launcher-token}
      STATION_SERVICE_USER: root
```

- [ ] **Step 2: Verify with `docker compose config`**

```
docker compose config | grep -E "STATION_(DEPLOY_MODE|LAUNCHER_URL|SERVICE_USER)"
```
Expected output (3 lines):
```
      STATION_DEPLOY_MODE: compose
      STATION_AGENT_LAUNCHER_URL: http://agent:8421
      STATION_SERVICE_USER: root
```

- [ ] **Step 3: Rebuild and restart**

```
docker compose build && docker compose up -d
```
Expected: dashboard reports healthy; agent launcher reachable on `:8421`.

- [ ] **Step 4: Smoke-test the trigger endpoint end-to-end**

```
curl -s -X POST http://localhost:8420/api/runs/trigger | head -c 200
```
Expected: JSON containing `"status":"triggered"` and a `pid` (since compose mode hits the launcher).

If this succeeds, immediately stop the run so it doesn't burn credits:
```
curl -s -X POST http://localhost:8420/api/system/service/stop | head -c 200
```

- [ ] **Step 5: Commit**

```
git add compose.yml
git commit -m "compose: select compose deploy-mode and use launcher base URL

Previously STATION_AGENT_LAUNCHER_URL pointed at /run; the new
service_control abstraction uses it as the base, appending /run, /stop,
/status as needed. STATION_SERVICE_USER=root matches the agent
container, so the GitHub OAuth chown actually runs."
```

---

## Task 10: Verify the systemd path still works

**Files:** none — this is a verification step.

- [ ] **Step 1: Run the full backend suite locally with no compose env vars**

```
unset STATION_DEPLOY_MODE STATION_AGENT_LAUNCHER_URL STATION_LAUNCHER_TOKEN
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/ -q
```
Expected: same pass count as before this PR.

- [ ] **Step 2: Verify `STATION_DEPLOY_MODE` defaults to systemd**

```
PYTHONPATH=dashboard/backend:. python -c "from app.services.service_control import _mode; print(_mode())"
```
Expected: `systemd`

- [ ] **Step 3: Open the PR for review**

This is a checkpoint — PR-A is done. The launcher has /stop, the service_control facade exists, all four call sites use it, the reaper works in compose, and GitHub OAuth's chown is configurable. Go to GitHub and open the PR.

- [ ] **Step 4: Commit any final cleanups**

If you found any drift, fix and commit. Otherwise this step is a no-op.

---

# PR-B: Token Refresh in Agent Container (Phase 3)

## Task 11: Add `agent/token_refresh.py` with the periodic loop

**Files:**
- Create: `agent/token_refresh.py`
- Create: `dashboard/backend/tests/test_token_refresh_task.py`

- [ ] **Step 1: Write the failing test**

```python
# dashboard/backend/tests/test_token_refresh_task.py
"""The agent's launcher schedules a periodic OAuth-refresh task. The task
shells out to refresh-token.py; we mock the subprocess and assert wiring."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_refresh_loop_invokes_subprocess_at_interval():
    from agent import token_refresh

    mock_run = AsyncMock(return_value=0)
    with patch("agent.token_refresh._run_refresh_once", mock_run):
        # interval=0.05 so the test completes quickly; cancel after one tick.
        task = asyncio.create_task(token_refresh.refresh_loop(interval=0.05))
        await asyncio.sleep(0.18)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # At interval=0.05s over 0.18s, we should have invoked the subprocess
    # at least 3 times (start, +0.05, +0.10, +0.15).
    assert mock_run.await_count >= 3


@pytest.mark.asyncio
async def test_refresh_loop_swallows_subprocess_errors():
    """A failed refresh must not break the loop — we keep retrying."""
    from agent import token_refresh

    mock_run = AsyncMock(side_effect=[RuntimeError("boom"), 0, 0])
    with patch("agent.token_refresh._run_refresh_once", mock_run):
        task = asyncio.create_task(token_refresh.refresh_loop(interval=0.05))
        await asyncio.sleep(0.18)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert mock_run.await_count >= 3
```

- [ ] **Step 2: Run, expect ImportError**

```
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/test_token_refresh_task.py -v
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the module**

```python
# agent/token_refresh.py
"""Periodic OAuth-token refresh for the agent container.

The Claude CLI's access token expires roughly daily; the existing
``agent/scripts/refresh-token.py`` script knows how to refresh it. In
compose mode the dashboard container has no access to ``~/.claude``, so
the agent's launcher schedules this task instead. Default interval is 30
minutes — well below the token's typical lifetime — and ``refresh-token.py``
is itself idempotent (it returns early if the token still has plenty of
life left), so a tight default is safe.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

logger = logging.getLogger(__name__)

REFRESH_SCRIPT = os.environ.get(
    "STATION_REFRESH_SCRIPT",
    "/app/agent/scripts/refresh-token.py",
)
DEFAULT_INTERVAL = float(os.environ.get("STATION_TOKEN_REFRESH_INTERVAL", "1800"))  # 30 min


async def _run_refresh_once() -> int:
    """Invoke refresh-token.py as a subprocess. Returns its exit code."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        REFRESH_SCRIPT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode == 0:
        logger.info("token refresh ok: %s", stdout.decode(errors="replace").strip())
    else:
        logger.warning(
            "token refresh exit=%s stdout=%r stderr=%r",
            proc.returncode,
            stdout.decode(errors="replace").strip(),
            stderr.decode(errors="replace").strip(),
        )
    return proc.returncode or 0


async def refresh_loop(interval: float = DEFAULT_INTERVAL) -> None:
    """Run :func:`_run_refresh_once` every ``interval`` seconds, forever.

    The loop swallows exceptions so a transient network blip can't kill
    the launcher; the next tick will retry. Cancel this task to stop the
    loop (the launcher does this on shutdown).
    """
    logger.info("token-refresh loop starting (interval=%.0fs)", interval)
    while True:
        try:
            await _run_refresh_once()
        except asyncio.CancelledError:
            logger.info("token-refresh loop cancelled")
            raise
        except Exception as exc:  # pragma: no cover — defensive; tests cover the success path
            logger.warning("token-refresh tick failed: %s", exc)
        await asyncio.sleep(interval)
```

- [ ] **Step 4: Run, expect pass**

```
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/test_token_refresh_task.py -v
```
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```
git add agent/token_refresh.py dashboard/backend/tests/test_token_refresh_task.py
git commit -m "feat(agent): periodic OAuth-token refresh loop

Wraps the existing refresh-token.py script in an asyncio loop suitable
for embedding in the launcher process. Default interval 30 min keeps
the token well ahead of its ~24h expiry; refresh-token.py is itself
idempotent so the cadence isn't sensitive."
```

---

## Task 12: Wire the refresh loop into the launcher

**Files:**
- Modify: `agent/launcher.py`

- [ ] **Step 1: Add a startup-event test**

Append to `dashboard/backend/tests/test_launcher_endpoints.py`:

```python
def test_startup_schedules_refresh_loop(monkeypatch):
    """The launcher's startup hook must schedule the refresh loop so it
    runs for the lifetime of the launcher process."""
    monkeypatch.setenv("STATION_LAUNCHER_TOKEN", "")
    import importlib

    import agent.launcher as launcher_mod
    importlib.reload(launcher_mod)

    from unittest.mock import patch
    with patch("agent.token_refresh.refresh_loop") as mock_loop:
        with TestClient(launcher_mod.app) as client:  # triggers startup
            client.get("/health")  # ensure the lifespan ran
        assert mock_loop.called, "launcher must call refresh_loop on startup"
```

- [ ] **Step 2: Run, expect fail**

```
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/test_launcher_endpoints.py::test_startup_schedules_refresh_loop -v
```
Expected: FAIL.

- [ ] **Step 3: Add startup hook to launcher.py**

**Ordering matters.** `app = FastAPI(lifespan=lifespan)` evaluates the `lifespan` name at module-load time, so the `lifespan` function must be **defined before** the FastAPI() call. Today's launcher.py has the FastAPI() call near the top, before `_current`. Move it.

Edit `agent/launcher.py` so the order becomes:

1. Existing imports — add these lines:
   ```python
   import asyncio
   import contextlib

   from agent import token_refresh
   ```

2. Existing constants and `_current` declaration (unchanged).

3. **New** lifespan definition (insert here, before the FastAPI() call):
   ```python
   _refresh_task: asyncio.Task | None = None


   @contextlib.asynccontextmanager
   async def lifespan(app):
       global _refresh_task
       _refresh_task = asyncio.create_task(token_refresh.refresh_loop())
       try:
           yield
       finally:
           if _refresh_task is not None:
               _refresh_task.cancel()
               try:
                   await _refresh_task
               except (asyncio.CancelledError, Exception):
                   pass
   ```

4. **Replace** the existing `app = FastAPI(title="claude-agent-station launcher")` line with:
   ```python
   app = FastAPI(title="claude-agent-station launcher", lifespan=lifespan)
   ```

5. The route decorators (`@app.get(...)`, `@app.post(...)`) stay in place — they reference `app`, which is defined above them by the time they evaluate.

Verify the result:
```
grep -nE "^(async def lifespan|app = FastAPI|_current:)" agent/launcher.py
```
Expected output (line numbers will differ):
```
agent/launcher.py:30:_current: subprocess.Popen | None = None
agent/launcher.py:35:async def lifespan(app):
agent/launcher.py:50:app = FastAPI(title="claude-agent-station launcher", lifespan=lifespan)
```
The line for `lifespan` must come **before** the line for `app = FastAPI(...)`.

- [ ] **Step 4: Run, expect pass**

```
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/test_launcher_endpoints.py -v
```
Expected: 4 PASS (3 prior + new).

- [ ] **Step 5: Commit**

```
git add agent/launcher.py dashboard/backend/tests/test_launcher_endpoints.py
git commit -m "feat(launcher): schedule token refresh on startup

The refresh loop runs for the lifetime of the launcher process; cancelled
cleanly on shutdown so the agent container exits without dangling tasks."
```

---

## Task 13: Drop dashboard's `_periodic_token_refresh` in compose mode

**Files:**
- Modify: `dashboard/backend/app/main.py`

- [ ] **Step 1: Locate the existing task registration**

```
grep -n "_periodic_token_refresh\|TOKEN_REFRESH_INTERVAL" dashboard/backend/app/main.py
```

- [ ] **Step 2: Add a guard so the task is skipped in compose mode**

Find the lifespan handler that creates the task (around line 80–110). Wrap the create_task call:

```python
import os

# Existing:
# token_task = asyncio.create_task(_periodic_token_refresh())

# Replace with:
token_task = None
if os.environ.get("STATION_DEPLOY_MODE", "systemd").lower() != "compose":
    token_task = asyncio.create_task(_periodic_token_refresh())
else:
    logger.info("Skipping dashboard token-refresh task — agent owns it in compose mode")
```

And in the cleanup block:

```python
if token_task is not None:
    token_task.cancel()
    with suppress(asyncio.CancelledError):
        await token_task
```

- [ ] **Step 3: Add a test**

Append to `dashboard/backend/tests/test_token_refresh_task.py`:

```python
@pytest.mark.asyncio
async def test_dashboard_skips_token_refresh_in_compose_mode(monkeypatch):
    """When the dashboard runs in compose mode, the agent owns refresh
    so the dashboard's lifespan must NOT schedule its own task."""
    monkeypatch.setenv("STATION_DEPLOY_MODE", "compose")

    # Reload to pick up env
    import importlib
    import app.main as main_mod
    importlib.reload(main_mod)

    # In the lifespan handler, _periodic_token_refresh should not be called.
    with patch("app.main._periodic_token_refresh", new_callable=AsyncMock) as mock_refresh:
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=main_mod.app), base_url="http://test") as ac:
            await ac.get("/api/health")  # triggers lifespan
        assert not mock_refresh.called, "dashboard must not refresh tokens in compose mode"
```

- [ ] **Step 4: Run, expect pass**

```
PYTHONPATH=dashboard/backend:. python -m pytest dashboard/backend/tests/test_token_refresh_task.py -v
```
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```
git add dashboard/backend/app/main.py dashboard/backend/tests/test_token_refresh_task.py
git commit -m "fix(main): skip dashboard token-refresh task in compose mode

The agent container's launcher owns refresh in compose; running both
would race on the credentials file (which lives only in the agent's
~/.claude bind mount, so the dashboard would fail anyway)."
```

---

## Task 14: Verify end-to-end refresh works in compose

**Files:** none — verification.

- [ ] **Step 1: Rebuild and restart**

```
docker compose build agent && docker compose up -d agent
```

- [ ] **Step 2: Confirm the refresh task is alive in the agent**

```
docker compose logs agent | grep -i "token-refresh"
```
Expected: a line like `INFO agent.token_refresh: token-refresh loop starting (interval=1800s)`.

- [ ] **Step 3: Manually trigger one refresh and check the log**

```
docker compose exec agent python /app/agent/scripts/refresh-token.py
```
Expected: prints `Token expires in <N>s ...` then either `Token still valid` or `Refreshed successfully`. Exit 0.

- [ ] **Step 4: Confirm credentials file is writable from within the container**

```
docker compose exec agent ls -la /root/.claude/.credentials.json
```
Expected: file exists, owned by root (or whoever the host user is, after :z relabel).

- [ ] **Step 5: Final commit (only if anything was tweaked)**

```
git status
# If empty, no commit; if not, commit what's there with a "verify" message.
```

---

# Self-Review

(Performed against the spec — Phases 1, 2, 3, 4 from `docs/superpowers/plans/...` and the prior conversation.)

## Spec coverage

| Phase | Requirement | Task(s) |
|---|---|---|
| 1 | Replace systemctl callers with deploy-mode-aware helpers | T2, T3, T4, T5, T6, T9 |
| 1 | Extend launcher with /stop and /status (status already existed) | T1 |
| 1 | `routers/runs.py:trigger_run` uses the abstraction | T5 |
| 1 | `routers/system.py:51` uses the abstraction | T6 |
| 1 | `routers/plans.py:220` uses the abstraction | T6 |
| 1 | `STATION_DEPLOY_MODE` env defaulting to systemd | T2, T9 |
| 2 | Replace reaper's `pgrep` + `systemctl status` with launcher /status | T7 |
| 2 | Compose-mode reaper still detects dead runs | T7 (test_reaper_marks_runs_interrupted_when_agent_inactive) |
| 2 | Systemd-mode reaper unchanged | T7 (existing tests still pass) |
| 3 | Periodic token refresh in agent container | T11, T12 |
| 3 | Dashboard's redundant refresh task disabled in compose | T13 |
| 3 | End-to-end verification in compose | T14 |
| 4 | Hard-coded `claude-agent` user → env-driven | T8 |
| 4 | LookupError still suppressed when user missing | T8 (test_write_token_swallows_lookup_error_when_user_missing) |

All requirements have a task. No gaps.

## Placeholder scan

- No "TBD", "implement later", "fill in details" found in the plan body.
- Every `Step` with code has a code block containing the actual code.
- Every `Step` with a command has the exact command and expected output.
- "Similar to Task N" — not used; each task is self-contained.

One area to flag: **Task 6, Step 3** says "Note: `system.py` may still need other things from `app.services.systemd` ... Re-read the file and only drop what's now unused." This is a judgment call rather than a literal instruction. Reasonable for a refactor of unknown scope, but the engineer should treat it as "grep first, drop what's safe" rather than blanket replace.

## Type & name consistency

- `start_agent_service`, `stop_agent_service`, `get_agent_status`, `run_action` defined in T2/T3/T4/T6, used unchanged in T5/T6/T7.
- `service_control._mode()` returns lowercase string (`"systemd"` | `"compose"`) — checked for that exact format in T3, T6.
- `_launcher_call` shape (`{"success", "status_code", **body}`) is consistent across T3 → T5 (`trigger_run`).
- `STATION_DEPLOY_MODE` env name is consistent (T2, T6, T9, T13).
- `STATION_AGENT_LAUNCHER_URL` semantics changed (path → base URL) — T9 has the explicit migration step.
- `STATION_LAUNCHER_TOKEN` name unchanged from existing code.
- `STATION_SERVICE_USER` introduced cleanly in T8.
- Test fixture names (`stale_run`, `setup_db`, `client`) match patterns used elsewhere in `dashboard/backend/tests/`.

No drift detected.

---

## Out of scope (deferred to later phases per the plan-overview discussion)

- Phase 5 — sweeping the hardcoded `/var/log/claude-agent/...` paths in `run-manager.sh`.
- Phase 6 — image trimming, project-registration UX, multi-stack support.
- Phase 7 — full e2e Playwright test of the compose path; updating CLAUDE.md to list compose as a supported deployment.
- Moving credentials into a named volume (separate from the bind-mount) — was discussed as part of Phase 3 but deferred; the plan above keeps the existing `~/.claude` bind-mount on the agent and just adds the refresh loop. This is the simpler/safer scope; the volume migration can be a follow-up if the rw bind-mount blast radius becomes a concern.
