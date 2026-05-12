# Spec — Issue #361: Wire RunDriver as production launcher path

**Status**: spec
**Design**: [`docs/design/milestone-2.md`](../design/milestone-2.md)
**Issue**: [#361](https://github.com/kenhaesler/claude-agent-station/issues/361)
**Targets**: `dev`
**Depends on**: none (#349 M1 already merged)
**Blocks**: #362, #363

## Acceptance (from issue)

- [ ] `docker compose up` runs orchestration through the Python driver. `run-manager.sh` is either gone or stripped to ~200 LOC.
- [ ] All `run_start`/`run_complete` payload fields the bash used to send are present in driver-emitted events (pytest with a golden-file comparison).
- [ ] Triggering a run, then `docker compose kill --signal SIGINT cas-agent`, results in the dashboard marking the run `interrupted` (not completed/failed).

## Files changed

| Path | Action | Approx LOC |
|---|---|---|
| `agent/launcher.py` | edit `_spawn_run_manager` | ~+15 / −10 |
| `agent/station_orchestrator.py` | enrich `RunDriver` payloads + signal handling | ~+80 |
| `agent/scripts/run-manager.sh` | gut launcher invocation path (or remove entirely; see decision) | ~−2500 |
| `dashboard/backend/app/services/launcher_client.py` | maybe — adjust `hint_run_id` passthrough if needed | small |
| `tests/test_run_driver_payload.py` | new | ~+150 |
| `tests/test_run_driver_signals.py` | new | ~+80 |
| `docs/configuration.md` | update launcher path documentation | small |

## Implementation steps

### 1. Decide the bash shim fate

Choice in design's open question 2 — recommendation: **remove bash from the launcher path entirely**. The env/log/lock plumbing moves into Python:
- Log redirect: `os.dup2` onto `/var/log/claude-agent/run-<RUN_ID>-launcher.out` in `RunDriver.run()`.
- Lock acquisition: `fcntl.flock` on `/var/run/claude-agent-station/run-manager.lock`.
- Env setup (`GH_TOKEN`, etc.): already in launcher; pass through directly.

If revert-safety is preferred for one release, keep a panic-revert env flag `STATION_LAUNCHER_USE_BASH=1` that re-execs the legacy script. Recommend gating to one release and deleting in #362's PR.

### 2. Enrich `RunDriver`

Add private helpers and a telemetry dataclass:

```python
@dataclass
class RunTelemetry:
    started_at: datetime
    project_count: int = 0
    max_concurrent: int = 1
    concurrent_group_id: str = ""
    log_file: str = ""
    tokens_input: int = 0
    tokens_output: int = 0
    turns: int = 0
```

In `RunDriver.run()`:

1. Construct telemetry from config at startup: `project_count = len(config.projects)`, `max_concurrent = config.limits.max_concurrent_employees`, `concurrent_group_id = f"run-{run_id}"`, `log_file = "/var/log/claude-agent/run-{run_id}-launcher.out"`.
2. `run_start` payload now includes `project_count`, `max_concurrent`, `concurrent_group_id`, `log_file`.
3. After `iterate_projects` returns, telemetry has been updated by the orchestrator (mechanism: pass the telemetry instance into `iterate_projects`, which threads it into the SDK callbacks). Update `run_complete` payload to include `tokens_input`, `tokens_output`, `tokens_total` (= sum), `turns`, `duration_ms` (= now − started_at), `exit_code`.

### 3. Signal handling

```python
def run(self) -> int:
    self._emit_run_start()
    status = "completed"
    exit_code = 0
    error: str | None = None

    try:
        exit_code = iterate_projects(...)
        if exit_code == 130:
            status = "interrupted"
        elif exit_code != 0:
            status = "failed"
    except KeyboardInterrupt:
        status = "interrupted"
        exit_code = 130
        # Do NOT re-raise — emit run_complete first via the finally clause.
        # Re-raise after the emit for the systemd exit-code contract.
        _interrupted = True
    except Exception as e:
        logger.exception("RunDriver: iterate_projects raised")
        status = "error"
        exit_code = 1
        error = f"{type(e).__name__}: {e}"
    finally:
        payload = {"status": status, ...telemetry...}
        if error: payload["error"] = error
        emit("run_complete", run_id=self.run_id, payload=payload)

    if locals().get("_interrupted"):
        raise KeyboardInterrupt
    return exit_code
```

### 4. Launcher integration

In `agent/launcher.py::_spawn_run_manager`, replace the `run-manager.sh` invocation:

```python
cmd = [
    sys.executable, "-m", "agent.station_orchestrator",
    "--driver",
    "--run-id", run_id,
    "--config", str(config_path),
    "--workspaces-dir", str(workspaces_dir),
]
env = os.environ.copy()
env.setdefault("PYTHONUNBUFFERED", "1")
_current = subprocess.Popen(
    cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    start_new_session=False,  # share the process group for signal propagation
)
```

If the panic-revert flag is implemented: `if os.environ.get("STATION_LAUNCHER_USE_BASH") == "1": exec the bash`. Document and time-bound.

### 5. Tests

- `tests/test_run_driver_payload.py`: build a config with N projects, run `RunDriver` against a mocked `iterate_projects` (returns 0, populates telemetry). Capture emitted webhooks via a fake `emit` and golden-file compare to `tests/fixtures/run_driver_payloads.json`.
- `tests/test_run_driver_signals.py`: 
  - SIGINT subtest: spawn the driver as a subprocess, send SIGINT, assert exit code 130 and `run_complete` with `status="interrupted"`.
  - Exception subtest: make `iterate_projects` raise; assert `run_complete` with `status="error"` fires.
  - Exit-130 subtest: `iterate_projects` returns 130; assert `status="interrupted"`.

### 6. Documentation

Update `docs/configuration.md`:
- Launcher path: now `python3 -m agent.station_orchestrator --driver …`.
- Remove or shorten the `run-manager.sh` section to whatever survives.
- Note the SIGINT → `interrupted` mapping.

Update `docs/architecture.md` if it has a sequence diagram showing the bash path.

## Risks

- **Telemetry plumbing.** The orchestrator already tracks tokens — find the accumulator (check `station_orchestrator.py` for `usage` / `tokens` references) and surface it on the telemetry dataclass. If the data lives only in per-iteration locals today, refactor minimally to return it from `orchestrate`/`iterate_projects`.
- **Process group / signal handling.** Verify the launcher's `Popen` uses `start_new_session=False` so SIGINT propagates. The existing launcher kills via `_current.terminate()` which sends SIGTERM, not SIGINT — confirm behavior under both.
- **`hint_run_id` flow.** Verify the optimistic run-placeholder path (#346) still threads `STATION_RUN_ID_OVERRIDE` to the Python entry point.

## Rollback

If `STATION_LAUNCHER_USE_BASH` panic flag is implemented: flip env var, redeploy. Otherwise: `git revert` the launcher commit and re-deploy.

## Out of scope

- Issue picking / dispatch port (#362).
- Verdict execution port (#363).
- Async I/O conversion.
