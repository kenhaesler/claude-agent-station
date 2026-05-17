# Idle-Run Semantics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce `status="skipped"` and `project_skipped_no_work` so idle runs (no eligible work) are visually and semantically distinct from genuine failures.

**Architecture:** Thread a `work_attempted: bool` from `orchestrate_project` through `iterate_projects` to `RunDriver`. Idle per-project: emit new `project_skipped_no_work`, append `SKIP` digest entry, do NOT bump exit_code, do NOT touch verdicts file. Idle run-level: `RunDriver` emits `run_complete` with `status="skipped"` iff every project was idle and no real failure occurred. `run_lifecycle.handle_finished` adds `"skipped" → "skipped"` to its status map. Frontend gets a neutral-grey badge variant.

**Tech Stack:** Python 3.11 / FastAPI / SQLite / pytest / Svelte 5 / TypeScript

**Spec:** `docs/superpowers/specs/2026-05-17-idle-run-semantics-design.md`
**Issues:** Closes #446, Closes #447
**Target branch:** PR `--base dev` (per project policy)

---

## File Structure

| File | Role |
|---|---|
| `agent/station_orchestrator.py` | Change `orchestrate_project` return to 3-tuple `(rc, state, work_attempted)`; `RunDriver.run` unpacks new bool from `iterate_projects`'s return and chooses `status="skipped"` when applicable |
| `agent/project_loop.py` | Unpack 3-tuple from `orchestrate_project`; per-project: emit `project_skipped_no_work` for the idle case, skip verdicts read, `continue`; track `any_work_attempted` / `any_real_failure`; return new 3-tuple `(exit_code, last_state, terminal_status_hint)` to `RunDriver` |
| `dashboard/backend/app/services/run_lifecycle.py` | Add `"skipped": "skipped"` to `handle_finished`'s `status_map` (line ~155) |
| `dashboard/frontend/src/lib/types.ts` | Add `"skipped"` to run-status union; add `"project_skipped_no_work"` to event-name union |
| `dashboard/frontend/src/lib/run-status.ts` (or wherever badge mapping lives — verify during Task 8) | Add `skipped` → neutral-grey badge variant |
| `docs/architecture.md` | Document new status + new event |
| `dashboard/backend/tests/test_iterate_projects_python.py` | New tests: idle emission, real-failure still emits manager_no_verdicts, run-level skipped, mixed-status precedence |
| `dashboard/backend/tests/test_run_lifecycle.py` (or appropriate existing file — verify during Task 7) | New tests: `handle_finished` maps `skipped` → `skipped`; regression that `skipped` is NOT remapped to `failed` |
| `agent/tests/test_orchestrate_project_work_attempted.py` (or `dashboard/backend/tests/` if that's the convention — verify) | New tests: `orchestrate_project` returns `work_attempted=False` for no-eligible-issues, `True` for happy path |

---

## Task 1: Add `work_attempted` to `orchestrate_project` return contract

**Files:**
- Modify: `agent/station_orchestrator.py:2011-2165` (signature + the no-eligible-issues early return at line 2165) and `:2671` (the happy-path return)
- Test: `dashboard/backend/tests/test_orchestrate_project_work_attempted.py` (create)

The signature change is contagious — `iterate_projects` is the only caller. Tasks 2-3 unpack the new tuple; this task just defines and tests the new return contract.

- [ ] **Step 1: Write the failing tests**

Create `dashboard/backend/tests/test_orchestrate_project_work_attempted.py`:

```python
"""Tests for the work_attempted bool in orchestrate_project's return.

Spec: docs/superpowers/specs/2026-05-17-idle-run-semantics-design.md
Issues: #446, #447
"""

from unittest.mock import patch, MagicMock
import pytest

# These tests verify the new return contract introduced by Task 1 of the
# idle-run-semantics plan. They mock the picker so the function returns
# from its early-exit branch (no eligible issues) without booting the
# SDK session, OR from a path that did open the session.


@pytest.mark.asyncio
async def test_orchestrate_project_returns_work_attempted_false_when_no_eligible_issues():
    """When the picker finds no eligible issues, orchestrate_project must
    return (exit_code, None, False) — no SDK session was opened."""
    from agent import station_orchestrator as so

    project = {"repo": "test/repo", "id": 1, "mode": "full"}
    config = {"projects": [project]}

    # Force the no-eligible-issues short-circuit:
    # _pick_eligible_issues returns [] → handle_empty_backlog runs →
    # function returns at line 2165.
    with patch.object(so, "_pick_eligible_issues", return_value=[]), \
         patch.object(so, "handle_empty_backlog", return_value="no-eligible-issues-no-vision"), \
         patch.object(so, "ensure_workspace", return_value="/tmp/test-ws"):
        result = await so.orchestrate_project(project, config, "test-run", "/tmp")

    assert len(result) == 3, f"Expected 3-tuple, got {len(result)}-tuple"
    exit_code, stream_state, work_attempted = result
    assert work_attempted is False, (
        "work_attempted must be False when no eligible issues found"
    )
    assert stream_state is None
    assert exit_code == 0


@pytest.mark.asyncio
async def test_orchestrate_project_returns_work_attempted_true_when_session_opened():
    """When the SDK session opens (eligible issues found), work_attempted
    must be True even if the session later errors out."""
    from agent import station_orchestrator as so

    project = {"repo": "test/repo", "id": 1, "mode": "full"}
    config = {"projects": [project]}
    fake_issue = {"number": 1, "title": "test", "body": "", "labels": []}

    # Picker returns one issue → session opens. Mock everything downstream
    # to a no-op success so we just verify the work_attempted=True signal.
    with patch.object(so, "_pick_eligible_issues", return_value=[fake_issue]), \
         patch.object(so, "ensure_workspace", return_value="/tmp/test-ws"), \
         patch.object(so, "_run_agent_session", new_callable=MagicMock,
                      return_value=(0, None)):
        result = await so.orchestrate_project(project, config, "test-run", "/tmp")

    assert len(result) == 3
    _exit_code, _stream_state, work_attempted = result
    assert work_attempted is True, (
        "work_attempted must be True once the SDK session is opened"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard/backend && python3 -m pytest tests/test_orchestrate_project_work_attempted.py -xvs`
Expected: FAIL — `ValueError: not enough values to unpack (expected 3, got 2)` or `AttributeError` depending on which mock surface drifted. Both tests must fail.

- [ ] **Step 3: Modify `orchestrate_project` signature + early-return**

Edit `agent/station_orchestrator.py:2011-2013`. Change signature from:

```python
async def orchestrate_project(
    project: dict, config: dict, run_id: str, workspaces_dir: str,
) -> tuple[int, "_StreamState | None"]:
    """Run the Agent Teams session for a single project.

    Returns ``(exit_code, stream_state)``. ``stream_state`` is ``None`` if
    the project short-circuited before the orchestrator session began
    (no eligible issues, workspace error, etc.). Callers use the returned
    state for telemetry aggregation — see :class:`RunDriver._finalize_telemetry`.
```

to:

```python
async def orchestrate_project(
    project: dict, config: dict, run_id: str, workspaces_dir: str,
) -> tuple[int, "_StreamState | None", bool]:
    """Run the Agent Teams session for a single project.

    Returns ``(exit_code, stream_state, work_attempted)``.

    - ``stream_state`` is ``None`` if the project short-circuited before
      the SDK session began (no eligible issues, workspace error, etc.).
    - ``work_attempted`` is ``False`` only when the picker returned no
      eligible issues and the SDK session was never opened. ``True`` for
      all other paths (session opened, errored, etc.) so that downstream
      failure signals (manager_no_verdicts, exit_code bumps) are preserved.
      Idle-run-semantics discriminator — see #446 / #447 / spec
      ``docs/superpowers/specs/2026-05-17-idle-run-semantics-design.md``.
```

Also fix the no-eligible-issues early return at line 2165. Change from:

```python
            return exit_code, None
```

to:

```python
            return exit_code, None, False
```

And the happy-path return at line 2671. Change from:

```python
    return exit_code, stream_state
```

to:

```python
    return exit_code, stream_state, True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard/backend && python3 -m pytest tests/test_orchestrate_project_work_attempted.py -xvs`
Expected: PASS — both tests green.

- [ ] **Step 5: Update `iterate_projects`'s call site to unpack 3-tuple (compat-only — full logic in Task 2)**

`iterate_projects` will break at runtime until it unpacks the new tuple. Make a minimal compat edit now so the existing test suite isn't red between tasks.

Edit `agent/project_loop.py:219-221`. Change from:

```python
            proj_rc, proj_state = asyncio.run(
                orchestrate_project(project, config, run_id, workspaces_dir)
            )
```

to:

```python
            proj_rc, proj_state, _work_attempted = asyncio.run(
                orchestrate_project(project, config, run_id, workspaces_dir)
            )
```

The `_` prefix signals "we'll use this in Task 2." Don't add logic on it yet.

- [ ] **Step 6: Run the broader suite to confirm no regressions**

Run: `cd dashboard/backend && python3 -m pytest tests/test_iterate_projects_python.py tests/test_project_loop*.py tests/test_orchestrator_wiring.py -x`
Expected: All previously-passing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add agent/station_orchestrator.py agent/project_loop.py dashboard/backend/tests/test_orchestrate_project_work_attempted.py
git commit -m "$(cat <<'EOF'
feat(orchestrator): return work_attempted bool from orchestrate_project (#446 #447)

Foundation for distinguishing idle runs (no eligible work) from
genuine failures. orchestrate_project now returns a 3-tuple
(exit_code, stream_state, work_attempted); the bool is False only
on the no-eligible-issues early-exit path. iterate_projects unpacks
the new value but does not yet branch on it (next task).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Per-project idle branch in `iterate_projects`

**Files:**
- Modify: `agent/project_loop.py:219-282` (the per-project loop body — branch on `work_attempted` before verdicts read)
- Test: `dashboard/backend/tests/test_iterate_projects_python.py` (extend)

This task moves the idle case off the `manager_no_verdicts` code path and onto the new `project_skipped_no_work` event. The "real failure" path (work_attempted=True + verdicts missing) is untouched.

- [ ] **Step 1: Write the failing tests**

Add to `dashboard/backend/tests/test_iterate_projects_python.py` (at the end, with the other tests):

```python
def test_iterate_projects_emits_project_skipped_no_work_when_no_work_attempted(
    tmp_path, monkeypatch
):
    """Idle case: orchestrate_project returns work_attempted=False →
    iterate_projects emits project_skipped_no_work, NOT
    manager_no_verdicts, and does not bump exit_code.

    Spec: docs/superpowers/specs/2026-05-17-idle-run-semantics-design.md
    Issues: #446 #447
    """
    from agent import project_loop

    config_path = tmp_path / "config.json"
    config_path.write_text('{"projects":[{"repo":"test/repo","enabled":true,"mode":"full"}]}')
    workspaces_dir = str(tmp_path / "workspaces")

    # Mock orchestrate_project to return the idle signal.
    captured_emits: list[tuple] = []

    def fake_emit(event, *, run_id, payload=None):
        captured_emits.append((event, run_id, payload))

    def fake_orchestrate(*args, **kwargs):
        # (exit_code, stream_state, work_attempted=False)
        return (0, None, False)

    async def fake_orchestrate_async(*args, **kwargs):
        return fake_orchestrate(*args, **kwargs)

    monkeypatch.setattr(
        "agent.station_orchestrator.orchestrate_project", fake_orchestrate_async
    )
    monkeypatch.setattr(
        "agent.project_loop.ensure_workspace", lambda *a, **kw: str(tmp_path / "ws")
    )
    monkeypatch.setattr("agent.webhook_emitter.emit", fake_emit)

    # Make purge/recover/preflight no-ops so we reach the loop.
    monkeypatch.setattr("agent.project_loop.preflight", lambda *a, **kw: None)
    monkeypatch.setattr("agent.project_loop.purge_and_recover", lambda *a, **kw: None)
    monkeypatch.setattr("agent.project_loop.resume_paused", lambda: None)

    exit_code, _last_state, _terminal_hint = project_loop.iterate_projects(
        "test-run", str(config_path), workspaces_dir
    )

    # Assertions on emits
    emit_names = [e[0] for e in captured_emits]
    assert "project_skipped_no_work" in emit_names, (
        f"Expected project_skipped_no_work emit, got: {emit_names}"
    )
    assert "manager_no_verdicts" not in emit_names, (
        f"manager_no_verdicts must NOT fire in idle case, got: {emit_names}"
    )

    skip_event = next(e for e in captured_emits if e[0] == "project_skipped_no_work")
    assert skip_event[1] == "run-test-run", f"run_id wrong: {skip_event[1]}"
    assert skip_event[2] is not None
    assert skip_event[2].get("project") == "test/repo"
    assert skip_event[2].get("reason") == "no_eligible_work"

    # Exit code must NOT be bumped — idle is not a failure.
    assert exit_code == 0


def test_iterate_projects_still_emits_manager_no_verdicts_for_real_failure(
    tmp_path, monkeypatch
):
    """Regression pin: work_attempted=True + verdicts file missing must
    still trigger the existing manager_no_verdicts path (exit_code=6).
    The work_attempted discriminator must not suppress real failures.
    """
    from agent import project_loop

    config_path = tmp_path / "config.json"
    config_path.write_text('{"projects":[{"repo":"test/repo","enabled":true,"mode":"full"}]}')
    workspaces_dir = str(tmp_path / "workspaces")

    captured_emits: list[tuple] = []

    def fake_emit(event, *, run_id, payload=None):
        captured_emits.append((event, run_id, payload))

    async def fake_orchestrate(*a, **kw):
        return (0, None, True)  # work was attempted

    monkeypatch.setattr(
        "agent.station_orchestrator.orchestrate_project", fake_orchestrate
    )
    monkeypatch.setattr(
        "agent.project_loop.ensure_workspace", lambda *a, **kw: str(tmp_path / "ws")
    )
    # _read_verdicts_file returns None → real failure branch
    monkeypatch.setattr(
        "agent.station_orchestrator._read_verdicts_file", lambda p: None
    )
    monkeypatch.setattr("agent.webhook_emitter.emit", fake_emit)
    monkeypatch.setattr("agent.project_loop.preflight", lambda *a, **kw: None)
    monkeypatch.setattr("agent.project_loop.purge_and_recover", lambda *a, **kw: None)
    monkeypatch.setattr("agent.project_loop.resume_paused", lambda: None)

    exit_code, _last_state, _terminal_hint = project_loop.iterate_projects(
        "test-run", str(config_path), workspaces_dir
    )

    emit_names = [e[0] for e in captured_emits]
    assert "manager_no_verdicts" in emit_names, (
        "manager_no_verdicts must still fire when work was attempted but verdicts missing"
    )
    assert "project_skipped_no_work" not in emit_names, (
        "project_skipped_no_work must NOT fire when work was attempted"
    )
    assert exit_code == 6, f"exit_code must be 6 on real failure, got {exit_code}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard/backend && python3 -m pytest tests/test_iterate_projects_python.py::test_iterate_projects_emits_project_skipped_no_work_when_no_work_attempted tests/test_iterate_projects_python.py::test_iterate_projects_still_emits_manager_no_verdicts_for_real_failure -xvs`
Expected: FAIL on the idle test (the new event is not yet emitted; either `project_skipped_no_work` missing or `manager_no_verdicts` present). The real-failure test may pass already (since `work_attempted` is unused) — that's fine; it just becomes a regression pin.

Also: `iterate_projects` does not yet return a 3-tuple. The test `_terminal_hint` unpack will fail with `ValueError`. Both tests will fail at unpack.

- [ ] **Step 3: Implement the idle branch + return 3-tuple**

Edit `agent/project_loop.py`. Three changes in the same edit:

**3a.** At the call site (lines 218-238, after the `try/except` around `orchestrate_project`), rename `_work_attempted` → `work_attempted` (drop underscore so we use it) and insert the idle branch BEFORE the existing `verdicts_path = ...` block:

```python
        try:
            proj_rc, proj_state, work_attempted = asyncio.run(
                orchestrate_project(project, config, run_id, workspaces_dir)
            )
            if proj_state is not None:
                last_state = proj_state
            if proj_rc != 0:
                exit_code = proj_rc
        except (KeyboardInterrupt, OrchestratorStopRequested):
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("orchestrate_project failed for %s", project["repo"])
            exit_code = 4
            results.append({"project": project["repo"], "decision": "ERROR", "error": str(exc)})
            continue

        # #446 / #447: idle case — the project had no eligible issues
        # and the SDK session was never opened. This is NOT a failure;
        # don't read verdicts, don't emit manager_no_verdicts, don't
        # bump exit_code. Emit project_skipped_no_work so the dashboard
        # can render it distinctly from a real failure.
        if not work_attempted:
            try:
                from agent.webhook_emitter import emit as _emit_skip
                _emit_skip(
                    "project_skipped_no_work",
                    run_id=f"run-{run_id}",
                    payload={
                        "project": project.get("repo", ""),
                        "reason": "no_eligible_work",
                    },
                )
            except Exception:  # noqa: BLE001 — best-effort signal
                # Same hygiene rule as the other emits in this module:
                # logger.exception so any future signature drift surfaces
                # with a traceback instead of vanishing into a one-liner.
                logger.exception("project_skipped_no_work webhook emit failed")

            results.append({
                "project": project.get("repo", ""),
                "decision": "SKIP",
                "reason": "no_eligible_work",
            })
            continue

        # #390: the manager is now a sibling agent inside the lead's SDK
        # session. It writes verdicts to this path during the run; we just
        # confirm the file exists and hand each verdict to the executor.
        verdicts_path = Path(log_dir) / f"run-{run_id}-verdicts.json"
        # ... (rest unchanged)
```

**3b.** Track run-level flags. Right before the `for project in enabled:` loop (around line 178), add:

```python
    any_work_attempted = False
    any_real_failure = False
```

In the per-project `except Exception` clause (line 232) and the verdicts-missing real-failure branch (around line 244), and the verdict-execution failure path (line 336 — `exit_code = max(exit_code, 7)`), set `any_real_failure = True`. In the `if not work_attempted:` branch above, do NOT set either flag. In the happy path (after work_attempted was True), set `any_work_attempted = True` right after the unpack:

```python
            if proj_rc != 0:
                exit_code = proj_rc
                any_real_failure = True
            if work_attempted:
                any_work_attempted = True
```

(Place `any_work_attempted = True` BEFORE the `if not work_attempted: continue` block at the spot in 3a so it captures the True case.)

**3c.** Change `iterate_projects`'s return at line 371 from:

```python
    write_digest(run_id=run_id, results=results, log_dir=log_dir)
    return exit_code, last_state
```

to:

```python
    write_digest(run_id=run_id, results=results, log_dir=log_dir)

    # #446 / #447: idle-run terminal status hint for RunDriver.
    # Only emit "skipped" if EVERY enabled project was idle AND
    # nothing genuinely failed. Conservative: anything ambiguous
    # falls through to the existing completed/failed mapping in
    # RunDriver.run().
    if enabled and not any_work_attempted and not any_real_failure and exit_code == 0:
        terminal_status_hint = "skipped"
    else:
        terminal_status_hint = None
    return exit_code, last_state, terminal_status_hint
```

**3d.** Update `iterate_projects`'s docstring (the function header around line 112) — find the `Returns` section and update it to reflect the new 3-tuple shape and the `terminal_status_hint` semantics.

- [ ] **Step 4: Update `RunDriver.run` to unpack the 3-tuple**

Edit `agent/station_orchestrator.py:2893-2899`. Change from:

```python
            exit_code, last_state = iterate_projects(
                self._clean_id, self.config_path, self.workspaces_dir,
            )
            if exit_code == 130:
                status = "interrupted"
            elif exit_code != 0:
                status = "failed"
```

to:

```python
            exit_code, last_state, terminal_status_hint = iterate_projects(
                self._clean_id, self.config_path, self.workspaces_dir,
            )
            if exit_code == 130:
                status = "interrupted"
            elif exit_code != 0:
                status = "failed"
            elif terminal_status_hint == "skipped":
                # #446 / #447: idle run — every enabled project was idle
                # and nothing failed. Distinct terminal status so the
                # dashboard can render this as "skipped" not "failed".
                status = "skipped"
```

- [ ] **Step 5: Run the two new tests to verify they pass**

Run: `cd dashboard/backend && python3 -m pytest tests/test_iterate_projects_python.py::test_iterate_projects_emits_project_skipped_no_work_when_no_work_attempted tests/test_iterate_projects_python.py::test_iterate_projects_still_emits_manager_no_verdicts_for_real_failure -xvs`
Expected: PASS — both.

- [ ] **Step 6: Run the broader suite to confirm no regressions**

Run: `cd dashboard/backend && python3 -m pytest tests/test_iterate_projects_python.py tests/test_project_loop*.py tests/test_orchestrator_wiring.py tests/test_run_lifecycle*.py -x`
Expected: All previously-passing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add agent/project_loop.py agent/station_orchestrator.py dashboard/backend/tests/test_iterate_projects_python.py
git commit -m "$(cat <<'EOF'
feat(project_loop): idle projects emit project_skipped_no_work, not manager_no_verdicts (#446 #447)

When orchestrate_project returns work_attempted=False (no eligible
issues, SDK session never opened), iterate_projects now emits a
project_skipped_no_work webhook event and appends a SKIP digest
entry instead of routing through the manager_no_verdicts path.
Exit code is not bumped; the run is not a failure.

iterate_projects now returns a 3-tuple including a terminal_status_hint
"skipped" when every enabled project was idle and nothing failed.
RunDriver maps the hint to status="skipped" on run_complete.

Regression test pins that manager_no_verdicts still fires for the
real-failure case (work_attempted=True + verdicts missing).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Run-level `skipped` status — additional coverage

**Files:**
- Test: `dashboard/backend/tests/test_iterate_projects_python.py` (extend)

Task 2's commit makes the run-level skipped status work. This task adds three more tests pinning the precedence rules.

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/backend/tests/test_iterate_projects_python.py`:

```python
def test_iterate_projects_returns_skipped_hint_when_all_projects_idle(
    tmp_path, monkeypatch
):
    """Run-level: all projects idle, no failures → terminal_status_hint='skipped'."""
    from agent import project_loop

    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"projects":['
        '{"repo":"a/a","enabled":true,"mode":"full"},'
        '{"repo":"b/b","enabled":true,"mode":"full"}'
        ']}'
    )

    async def fake_orchestrate(*a, **kw):
        return (0, None, False)  # both projects idle

    monkeypatch.setattr(
        "agent.station_orchestrator.orchestrate_project", fake_orchestrate
    )
    monkeypatch.setattr(
        "agent.project_loop.ensure_workspace", lambda *a, **kw: str(tmp_path / "ws")
    )
    monkeypatch.setattr("agent.webhook_emitter.emit", lambda *a, **kw: None)
    monkeypatch.setattr("agent.project_loop.preflight", lambda *a, **kw: None)
    monkeypatch.setattr("agent.project_loop.purge_and_recover", lambda *a, **kw: None)
    monkeypatch.setattr("agent.project_loop.resume_paused", lambda: None)

    exit_code, _last_state, terminal_status_hint = project_loop.iterate_projects(
        "test-run", str(config_path), str(tmp_path / "workspaces")
    )

    assert exit_code == 0
    assert terminal_status_hint == "skipped"


def test_iterate_projects_no_skipped_hint_when_any_project_did_work(
    tmp_path, monkeypatch
):
    """Mixed: one idle, one did work → no skipped hint (run is completed)."""
    from agent import project_loop

    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"projects":['
        '{"repo":"a/a","enabled":true,"mode":"full"},'
        '{"repo":"b/b","enabled":true,"mode":"full"}'
        ']}'
    )

    call_count = {"n": 0}

    async def fake_orchestrate(*a, **kw):
        call_count["n"] += 1
        # First call: idle; second: did work
        return (0, None, call_count["n"] != 1)

    monkeypatch.setattr(
        "agent.station_orchestrator.orchestrate_project", fake_orchestrate
    )
    monkeypatch.setattr(
        "agent.project_loop.ensure_workspace", lambda *a, **kw: str(tmp_path / "ws")
    )
    # Second project's verdicts file present, empty verdicts (happy minimal).
    monkeypatch.setattr(
        "agent.station_orchestrator._read_verdicts_file",
        lambda p: {"verdicts": []},
    )
    monkeypatch.setattr("agent.webhook_emitter.emit", lambda *a, **kw: None)
    monkeypatch.setattr("agent.project_loop.preflight", lambda *a, **kw: None)
    monkeypatch.setattr("agent.project_loop.purge_and_recover", lambda *a, **kw: None)
    monkeypatch.setattr("agent.project_loop.resume_paused", lambda: None)

    exit_code, _last_state, terminal_status_hint = project_loop.iterate_projects(
        "test-run", str(config_path), str(tmp_path / "workspaces")
    )

    assert exit_code == 0
    assert terminal_status_hint is None, (
        "Mixed idle+work runs must NOT be marked skipped"
    )


def test_iterate_projects_no_skipped_hint_when_any_real_failure(
    tmp_path, monkeypatch
):
    """Mixed: one idle, one fails → no skipped hint (run is failed)."""
    from agent import project_loop

    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"projects":['
        '{"repo":"a/a","enabled":true,"mode":"full"},'
        '{"repo":"b/b","enabled":true,"mode":"full"}'
        ']}'
    )

    call_count = {"n": 0}

    async def fake_orchestrate(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return (0, None, False)   # idle
        raise RuntimeError("simulated project failure")

    monkeypatch.setattr(
        "agent.station_orchestrator.orchestrate_project", fake_orchestrate
    )
    monkeypatch.setattr(
        "agent.project_loop.ensure_workspace", lambda *a, **kw: str(tmp_path / "ws")
    )
    monkeypatch.setattr("agent.webhook_emitter.emit", lambda *a, **kw: None)
    monkeypatch.setattr("agent.project_loop.preflight", lambda *a, **kw: None)
    monkeypatch.setattr("agent.project_loop.purge_and_recover", lambda *a, **kw: None)
    monkeypatch.setattr("agent.project_loop.resume_paused", lambda: None)

    exit_code, _last_state, terminal_status_hint = project_loop.iterate_projects(
        "test-run", str(config_path), str(tmp_path / "workspaces")
    )

    assert exit_code != 0
    assert terminal_status_hint is None, (
        "Run with a real failure must NOT be marked skipped"
    )
```

- [ ] **Step 2: Run tests**

Run: `cd dashboard/backend && python3 -m pytest tests/test_iterate_projects_python.py::test_iterate_projects_returns_skipped_hint_when_all_projects_idle tests/test_iterate_projects_python.py::test_iterate_projects_no_skipped_hint_when_any_project_did_work tests/test_iterate_projects_python.py::test_iterate_projects_no_skipped_hint_when_any_real_failure -xvs`
Expected: All PASS (Task 2's implementation already handles these).

- [ ] **Step 3: Commit**

```bash
git add dashboard/backend/tests/test_iterate_projects_python.py
git commit -m "$(cat <<'EOF'
test(project_loop): pin run-level skipped precedence rules (#446 #447)

Three regression tests covering: all-idle → skipped, mixed
idle+work → completed (not skipped), idle+failure → failed
(not skipped). Verifies the conservative-default rule from
the design spec: ambiguous cases fall through to existing logic.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Add `"skipped"` to `run_lifecycle.handle_finished` status map

**Files:**
- Modify: `dashboard/backend/app/services/run_lifecycle.py:155-163` (the `status_map` dict in `handle_finished`)
- Test: `dashboard/backend/tests/test_run_lifecycle_safe_repo.py` (extend — verify file exists) or create a new test file `test_run_lifecycle_skipped_status.py`

Without this map entry, the agent-side `status="skipped"` from Task 2 falls through to `status_map.get(raw_status, raw_status)` and lands as the literal `"skipped"` in the DB — which would actually work. But pinning it explicitly defends against drift and makes the map self-documenting.

- [ ] **Step 1: Identify the appropriate test file**

Run: `cd dashboard/backend && grep -l "handle_finished\|EVENT_STATUS_MAP\|status_map" tests/*.py | head -5`
Expected output: list of files that already test `handle_finished`. Pick the most apt one (likely `test_run_lifecycle_safe_repo.py`). If none is obviously right, create `tests/test_run_lifecycle_skipped_status.py`.

- [ ] **Step 2: Write the failing tests**

Add to the chosen test file (using the existing imports and fixtures as templates — read the file first if creating fresh, mimic the pattern):

```python
@pytest.mark.asyncio
async def test_handle_finished_maps_skipped_to_skipped(db_session):
    """Agent-side status='skipped' must persist as row status='skipped',
    not fall through to default mapping. #446 #447."""
    from app.services import run_lifecycle
    from app.schemas import WebhookRunEvent

    event = WebhookRunEvent(
        event="finished",
        event_id="evt-test-skipped",
        run_id="run-test-skipped",
        status="skipped",
    )

    run = await run_lifecycle.handle_finished(
        db_session, event, project_id=None, run=None
    )

    assert run.status == "skipped", (
        f"Expected status='skipped', got '{run.status}'. "
        "If 'skipped' is missing from the status_map, this regresses."
    )


@pytest.mark.asyncio
async def test_handle_finished_does_not_remap_skipped_to_failed(db_session):
    """Regression pin: future map changes must not collapse skipped → failed."""
    from app.services import run_lifecycle
    from app.schemas import WebhookRunEvent

    event = WebhookRunEvent(
        event="finished",
        event_id="evt-test-skipped-2",
        run_id="run-test-skipped-2",
        status="skipped",
    )

    run = await run_lifecycle.handle_finished(
        db_session, event, project_id=None, run=None
    )

    assert run.status != "failed", (
        "Map drift: 'skipped' must not be remapped to 'failed'. "
        "See spec docs/superpowers/specs/2026-05-17-idle-run-semantics-design.md"
    )
```

(If `db_session` fixture name differs in the file you chose, use whatever the surrounding tests use. If `WebhookRunEvent` requires different fields, mimic the closest existing test.)

- [ ] **Step 3: Run tests to verify they fail (or already pass via fall-through)**

Run: `cd dashboard/backend && python3 -m pytest tests/<chosen_file>.py::test_handle_finished_maps_skipped_to_skipped tests/<chosen_file>.py::test_handle_finished_does_not_remap_skipped_to_failed -xvs`
Expected: The first may PASS via fall-through (`status_map.get("skipped", "skipped")` → `"skipped"`); the second will PASS for the same reason. Either way, proceed — Task 4 makes the mapping explicit so future drift is caught.

- [ ] **Step 4: Add explicit map entry**

Edit `dashboard/backend/app/services/run_lifecycle.py:155-163`. Change from:

```python
    status_map = {
        "success": "completed",
        "finished": "completed",
        "no_reports": "completed",
        "completed": "completed",
        "rate_limited": "completed",
        "error": "failed",
        "interrupted": "interrupted",
    }
```

to:

```python
    status_map = {
        "success": "completed",
        "finished": "completed",
        "no_reports": "completed",
        "completed": "completed",
        "rate_limited": "completed",
        "skipped": "skipped",       # #446 #447: idle runs (no eligible work)
        "error": "failed",
        "interrupted": "interrupted",
    }
```

- [ ] **Step 5: Run tests to confirm they still pass**

Run: `cd dashboard/backend && python3 -m pytest tests/<chosen_file>.py -xvs -k "skipped"`
Expected: PASS — both tests.

- [ ] **Step 6: Commit**

```bash
git add dashboard/backend/app/services/run_lifecycle.py dashboard/backend/tests/<chosen_file>.py
git commit -m "$(cat <<'EOF'
feat(run_lifecycle): map agent-side 'skipped' → row status 'skipped' (#446 #447)

Adds explicit entry to handle_finished's status_map so idle-run
status persists correctly and is protected against future map drift.
Two regression tests pin the mapping and assert skipped is never
remapped to failed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Document new status + event in `docs/architecture.md`

**Files:**
- Modify: `docs/architecture.md`

Project rule (CLAUDE.md): "Keep `docs/` in sync with code. Drifted docs are a defect." This task is required.

- [ ] **Step 1: Locate the relevant sections**

Run: `grep -n "^##\|run status\|webhook event\|status values" docs/architecture.md | head -30`
Identify the section(s) listing run statuses and webhook events. There is typically a status table and an events table or list. Note the line ranges.

- [ ] **Step 2: Add `skipped` to the run-status table**

Locate the table/list of `runs.status` values. Add an entry following the existing style. Example new row:

```markdown
| `skipped` | Run finished cleanly with no eligible work to do in any configured project. Distinct from `failed`. Introduced 2026-05-17 (#446 / #447). |
```

If statuses are listed as bullets, mimic that format instead.

- [ ] **Step 3: Add `project_skipped_no_work` to the webhook-events table**

Locate the webhook-events table/list. Add an entry. Example:

```markdown
| `project_skipped_no_work` | Emitted per-project when the orchestrator found no eligible work and did not open the SDK session. Payload: `{project, reason}` where `reason` is `"no_eligible_work"`. Run-level analogue: `runs.status="skipped"`. Introduced 2026-05-17 (#447). |
```

- [ ] **Step 4: If a "decision" enum is documented (digest entries), add `SKIP`**

Grep for `decision` documentation in `docs/architecture.md`. If digest entries / per-project results are documented with an enum (APPROVE / PR / REJECT / SKIP / ERROR etc.), confirm `SKIP` is present or add it.

Run: `grep -n "SKIP\|decision" docs/architecture.md | head -10`
If `SKIP` is already documented: no action. If missing: add it alongside ERROR with description: `"SKIP: project had no eligible work; no SDK session was opened. Paired with the project_skipped_no_work webhook."`

- [ ] **Step 5: Commit**

```bash
git add docs/architecture.md
git commit -m "$(cat <<'EOF'
docs(architecture): document 'skipped' status and project_skipped_no_work event (#446 #447)

Adds the new run-status value and webhook event introduced by the
idle-run-semantics work. Keeps docs in lockstep with the agent
and dashboard changes — drifted docs are a defect per CLAUDE.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Frontend type union — add `"skipped"` and `"project_skipped_no_work"`

**Files:**
- Modify: `dashboard/frontend/src/lib/types.ts`

This task makes TypeScript aware of the new values. The badge/color mapping is Task 7.

- [ ] **Step 1: Locate the run-status union and event-name union**

Run: `grep -n "skipped\|completed\|failed\|interrupted\|RunStatus\|status:\|event:" dashboard/frontend/src/lib/types.ts | head -30`
Identify:
- The TypeScript union type for run statuses (likely `RunStatus`, `RunStatusValue`, or similar).
- The TypeScript union type for event names (likely `WebhookEvent`, `EventName`, or similar — may also include event-name string-literal unions inline).

- [ ] **Step 2: Add `"skipped"` to the run-status union**

Append `| "skipped"` to the union. Example before/after:

Before:
```ts
export type RunStatus =
  | "pending"
  | "running"
  | "reviewing"
  | "plan_reviewing"
  | "awaiting_plan_review"
  | "plan_approved"
  | "plan_rejected"
  | "completed"
  | "failed"
  | "interrupted"
  | "orphaned";
```

After:
```ts
export type RunStatus =
  | "pending"
  | "running"
  | "reviewing"
  | "plan_reviewing"
  | "awaiting_plan_review"
  | "plan_approved"
  | "plan_rejected"
  | "completed"
  | "skipped"      // #446 #447: idle runs (no eligible work)
  | "failed"
  | "interrupted"
  | "orphaned";
```

Match the actual surrounding type name and style in the file.

- [ ] **Step 3: Add `"project_skipped_no_work"` to the event-name union**

Same approach — find the event-name union and add `| "project_skipped_no_work"` with a comment referencing #447.

- [ ] **Step 4: Type-check**

Run: `cd dashboard/frontend && npx tsc --noEmit 2>&1 | head -30`
Expected: Zero new errors. (Any pre-existing errors carry through unchanged.)

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/lib/types.ts
git commit -m "$(cat <<'EOF'
feat(frontend): add 'skipped' status and project_skipped_no_work event types (#446 #447)

Extends the run-status and webhook-event TypeScript unions with
the new idle-run values. Badge/color mapping follows in next task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Frontend badge variant — render `skipped` distinctly from `failed`

**Files:**
- Modify: `dashboard/frontend/src/lib/run-status.ts` OR wherever badge mapping lives (verify in Step 1).

Goal: any page that renders a status badge for a run shows a neutral-grey (or analogous unobtrusive color) variant for `skipped`, visually distinct from `failed` (red) and `completed` (green).

- [ ] **Step 1: Locate the badge mapping**

Run: `grep -rn "failed\|completed\|interrupted" dashboard/frontend/src/lib/ dashboard/frontend/src/components/ 2>/dev/null | grep -i "color\|badge\|bg-\|class" | head -20`

Expected: one or two files own the status → color/class mapping. Common patterns:
- A `getStatusBadge(status)` or `statusBadgeClass(status)` helper.
- A constant object `STATUS_COLORS = { failed: "bg-red-...", completed: "bg-green-...", ... }`.
- Inline `class:` directives in Svelte components.

If the mapping is centralized in one file, modify there (best case). If it's duplicated across pages, modify the centralized helper if one exists, or each occurrence if not (use grep output to enumerate).

- [ ] **Step 2: Add the `skipped` variant**

Add `skipped` to whatever mapping you found. Choose a neutral color — `bg-gray-500` / `bg-slate-500` / `bg-zinc-500` (use whichever TailwindCSS palette the existing codebase prefers). Example:

```ts
const STATUS_COLORS: Record<RunStatus, string> = {
  pending: "bg-yellow-500",
  running: "bg-blue-500",
  completed: "bg-green-600",
  skipped: "bg-gray-500",       // #446 #447: neutral; distinct from failed
  failed: "bg-red-600",
  interrupted: "bg-orange-500",
  // ...
};
```

If your mapping uses a `switch` or chained ternaries, add a new branch in the same style.

- [ ] **Step 3: Verify the type-checker is happy (the type added in Task 6 should make the missing variant a compile error if your mapping is keyed by `Record<RunStatus, X>`)**

Run: `cd dashboard/frontend && npx tsc --noEmit 2>&1 | head -30`
Expected: Zero errors. If the type check FAILS with `Property 'skipped' is missing in type ...`, that's actually good news — it confirms you found the right mapping file. Add the variant and re-run.

- [ ] **Step 4: Manual visual smoke (deferred to after merge)**

Note in the PR description that visual verification requires a live `skipped` run, which Task 9 / smoke test exercises end-to-end. Not blocking.

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/
git commit -m "$(cat <<'EOF'
feat(frontend): render 'skipped' status with neutral-grey badge (#446 #447)

Distinct visual treatment for idle runs so operators can tell
'no eligible work' apart from genuine failures at a glance.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Full backend test suite + agent test sweep

**Files:** none (validation only).

- [ ] **Step 1: Run the backend test suite scope used across the recent PRs**

Run:
```bash
cd dashboard/backend && python3 -m pytest \
  tests/test_iterate_projects_python.py \
  tests/test_project_loop*.py \
  tests/test_orchestrator_wiring.py \
  tests/test_orchestrate_project_work_attempted.py \
  tests/test_run_lifecycle*.py \
  tests/test_webhook*.py \
  -xvs 2>&1 | tail -30
```

Expected: ALL pass. If a test fails, do NOT mark the task complete — fix the regression in a follow-up edit and re-run.

- [ ] **Step 2: Broader backend sweep (excluding postgres-bound files)**

Run:
```bash
cd dashboard/backend && python3 -m pytest tests/ \
  --ignore=tests/test_database.py \
  --ignore=tests/test_migration_script.py \
  --ignore=tests/test_pubsub.py \
  -x 2>&1 | tail -5
```

Expected: ALL pass (modulo any pre-existing failures unrelated to this change — note them but do NOT fix here).

- [ ] **Step 3: Frontend type-check**

Run: `cd dashboard/frontend && npx tsc --noEmit 2>&1 | tail -10`
Expected: Zero NEW errors. Pre-existing carry through.

- [ ] **Step 4: No commit** (this is validation only).

---

## Task 9: Open PR + smoke test

**Files:** none (workflow).

- [ ] **Step 1: Push branch**

Run:
```bash
git push -u origin <branch-name>
```

- [ ] **Step 2: Open PR against `dev`**

Run:
```bash
gh pr create --base dev --title "feat: introduce 'skipped' run status + project_skipped_no_work event (#446 #447)" --body "$(cat <<'EOF'
## Summary

Idle runs (no eligible work in any configured project) are now recorded as `status="skipped"` and emit a per-project `project_skipped_no_work` webhook. Real failures still use `status="failed"` + `manager_no_verdicts` as before.

## Spec

`docs/superpowers/specs/2026-05-17-idle-run-semantics-design.md`

## Changes by file

- `agent/station_orchestrator.py` — `orchestrate_project` returns `(rc, state, work_attempted)`; `RunDriver.run` maps `terminal_status_hint="skipped"` to `status="skipped"` on `run_complete`.
- `agent/project_loop.py` — idle per-project branch emits `project_skipped_no_work` instead of `manager_no_verdicts`; tracks `any_work_attempted` / `any_real_failure` for the run-level hint; returns 3-tuple.
- `dashboard/backend/app/services/run_lifecycle.py` — `status_map` has explicit `"skipped" → "skipped"`.
- `dashboard/frontend/src/lib/types.ts` — adds `"skipped"` status and `"project_skipped_no_work"` event types.
- Frontend badge mapping — `skipped` renders neutral-grey, distinct from `failed`.
- `docs/architecture.md` — documents new status, new event, optional `SKIP` digest enum entry.

## Tests

- 2 new tests for `orchestrate_project` return contract (work_attempted False/True).
- 5 new tests for `iterate_projects` (idle emit, real-failure regression pin, run-level precedence × 3).
- 2 new tests for `run_lifecycle.handle_finished` skipped mapping (forward + regression pin).
- Frontend type-check confirms the new values are valid throughout.

## Smoke test (post-merge)

1. Trigger a run while no projects have eligible issues: `curl -X POST http://localhost:8420/api/runs/trigger -H "Authorization: Bearer $STATION_API_KEY"`.
2. Verify via API: `runs.status == "skipped"` (not `failed`).
3. Verify webhook log shows `project_skipped_no_work`, NOT `manager_no_verdicts`.
4. Verify digest shows `decision="SKIP"`, not `decision="ERROR"`.
5. Verify dashboard renders the run with the neutral-grey badge.

## Closes

Closes #446
Closes #447

## Test plan

- [x] All backend tests pass (focused + broader sweep).
- [x] Frontend type-check passes.
- [ ] Smoke test against live container (post-merge): idle run → status='skipped', new event fires, dashboard renders correctly.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: After merge — rebuild and smoke**

Wait for merge approval. After merge, on the dev branch:

```bash
git fetch origin && git reset --hard origin/dev
docker compose build dashboard agent && docker compose up -d
```

Trigger an idle run:

```bash
API_KEY=$(grep '^STATION_API_KEY=' .env | cut -d= -f2)
curl -s -X POST http://localhost:8420/api/runs/trigger \
  -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json'
```

Capture the `run_id` and inspect:

```bash
RUN_ID=<captured>
sleep 10
curl -s "http://localhost:8420/api/runs/$RUN_ID" \
  -H "Authorization: Bearer $API_KEY" | python3 -m json.tool | grep -E '"status"|"skip_reason"'
```

Expected output:
```
"status": "skipped",
"skip_reason": "no-eligible-issues-proposals-pending",
```

Then check the dashboard webhook log:

```bash
docker logs cas-dashboard 2>&1 | grep "$RUN_ID" | grep -E "project_skipped|manager_no_verdicts"
```

Expected: see `project_skipped_no_work` line(s), NOT `manager_no_verdicts`.

If all four checks pass, close #446 and #447 (squash-merge to dev does not auto-close per `feedback_pr_merge_to_dev_first` memory):

```bash
MERGE_COMMIT=$(gh pr view <PR-NUMBER> --json mergeCommit -q .mergeCommit.oid | cut -c1-10)
gh issue close 446 --comment "Fixed in PR #<PR-NUMBER> (commit ${MERGE_COMMIT}), merged into dev. Verified via smoke test on $RUN_ID."
gh issue close 447 --comment "Fixed in PR #<PR-NUMBER> (commit ${MERGE_COMMIT}), merged into dev. Verified via smoke test on $RUN_ID."
```

---

## Self-Review

**Spec coverage:**
- Goal: status=skipped ✅ Task 1, 2, 4. Event project_skipped_no_work ✅ Task 2. Dashboard distinct rendering ✅ Task 6, 7. Backwards-compatible (no migration) ✅ (covered by design choice, no task required).
- Non-goals: no migration, no rename, no state-machine refactor ✅ (none of these are tasks).
- Discriminator (`work_attempted` from `orchestrate_project`) ✅ Task 1.
- Signal flow: orchestrate_project ✅ T1; project_loop branch ✅ T2; run-level status ✅ T2 + T3 (precedence pins) + T4; status_map ✅ T4; webhook routing ✅ existing pass-through via `handle_unknown` (verified during exploration; no code needed); frontend types ✅ T6; frontend badge ✅ T7; docs ✅ T5.
- Error handling: emit wrapped in try/except + logger.exception ✅ T2; work_attempted=True on uncertainty ✅ T1.
- Testing: all 9 spec tests mapped to tasks (T1: tests 8, 9; T2: tests 1, 2; T3: tests 3, 4, 5; T4: tests 6, 7; T7: test 10 via type-check; T9: smoke test 11).

**Placeholder scan:** no TBD/TODO in the plan body. All code blocks complete. All commands have expected output stated. Two "verify during Task N" notes (for test file location in T1 and T4) are explicit research steps with grep commands, not placeholders.

**Type consistency:** `work_attempted` used identically in T1 + T2. `terminal_status_hint` introduced in T2's return change and consumed in the same task at the `RunDriver.run` edit. Event name `project_skipped_no_work` identical across T2 (emit), T5 (docs), T6 (type union), T9 (smoke). Status `"skipped"` identical across T2 (terminal_status_hint and RunDriver mapping), T4 (status_map), T6 (type union), T7 (badge), T9 (smoke assertion).

Self-review clean.
