# Per-Project Ephemeral Containers — Design

**Status**: design
**Date**: 2026-05-14
**Author**: tier-2-architect
**Issue**: [#386](https://github.com/kenhaesler/claude-agent-station/issues/386) — *Tier 2 / Issue A* of epic [#382](https://github.com/kenhaesler/claude-agent-station/issues/382)

## Context

Today the entire Agent Teams runtime lives inside a single long-running container, `cas-agent`, declared in `compose.yml:88-153`. Inside that one process tree:

- The HTTP launcher (`agent/launcher.py`) accepts `/run` POSTs from the dashboard and spawns `python -m agent.station_orchestrator --driver` (or the legacy `run-manager.sh`) as a detached subprocess.
- The orchestrator forks the lead Claude SDK process (Sonnet 4.6), which in turn invokes the bundled CLI to spawn three teammate processes (Opus 4.7) for backend / frontend / qa.
- Filesystem state lives under `/home/claude-agent/workspaces/<repo>/` — only git worktrees keep concurrent teammates from clobbering each other.
- All four agent processes share memory, CPU, and the bundled SDK CLI's stdin/stdout pipes (pid 13's children).

When one teammate wedges (we have seen many — "Wait 2 minutes for opus agents to finish planning"), it doesn't only block its own task. A stuck CLI stream pins the SDK slot the lead is multiplexing across teammates, the launcher's zombie reaper can't distinguish "lead is reviewing a slow plan" from "process tree is dead," and the operator's only recovery is `docker compose restart agent`. Production runs with `max_concurrent_employees: 1` in part *because* of this shared-state risk — concurrent teammates inside one container compound the wedge probability.

This issue isolates each run inside its own ephemeral container. The lead + 3 specialists still share that container — the Agent Teams pattern depends on SDK in-process coordination — but two concurrent runs no longer share a process tree, filesystem (except via the durable `station-data` volume), CPU budget, or the SDK CLI process. Containers are reaped on run exit. State persists across containers via named volumes. Multi-project runs (Tier 3 work) become one container per project, but this issue stops at one container per run; project-level decomposition arrives with [[issue-391-run-decomposition]].

A prerequisite is multi-writer persistence — see [[2026-05-14-issue-393-postgres-migration]]. Two ephemeral runners writing to SQLite's single-writer lock would produce `database is locked` errors under peak event load. #393 must land first.

## Goals

- Each run executes inside its own container, spawned on `/api/runs/trigger` and torn down on run exit.
- Two concurrent runs on different projects do not share a process tree or filesystem (except the durable `station-data` volume).
- Resource quotas (`--memory`, `--cpus`) are configurable per project.
- Workspace state under `/var/lib/claude-agent-station/workspaces/` persists across container lifecycles.
- An operator can inspect a live runner container with a single documented command.

## Non-goals

- Replacing Agent Teams. The lead + 3 specialists still share the runner container; only inter-run isolation changes.
- Kubernetes / Swarm. Bare `docker compose run` (or the Docker socket directly) suffices at this scale.
- Per-teammate containers. Out of scope; the SDK couples lead and teammates in-process.
- Encrypted volumes / network policy. Out of scope; current compose-network trust model stands.

## Approach

### Architectural split: launcher vs. runner

`cas-agent` splits into two roles, mediated by the Docker socket:

- **`cas-launcher`** (long-running) — the existing FastAPI launcher (`agent/launcher.py`), but it no longer spawns a subprocess on `/run`. Instead it shells out to `docker run` (or talks to the Docker socket directly via the `docker` SDK) to spawn one **runner** container per call. Holds the bookkeeping today held in `_current` and `_last_webhook_at`, mapped now by `run_id` instead of a single global.
- **`cas-runner-<run-id>`** (ephemeral) — built from the same `Dockerfile.agent` as today. ENTRYPOINT is `python -m agent.station_orchestrator --driver --run-id $STATION_RUN_ID …`. Exits when the orchestrator returns. Auto-removed via `docker run --rm` (or compose's `--rm` flag).

`Dockerfile.agent` stays one image — no second image needed. The split is purely runtime.

### Launcher change: `_spawn_run_manager` → `_spawn_runner_container`

Current code at `agent/launcher.py:294-404` builds a subprocess command and calls `subprocess.Popen`. The replacement builds a `docker run` invocation:

```python
def _spawn_runner_container(hint_run_id: str) -> dict:
    name = f"cas-runner-{hint_run_id.removeprefix('run-')}"
    cmd = [
        "docker", "run",
        "--rm",                 # auto-cleanup on exit
        "--detach",
        "--name", name,
        "--network", "agent-net",
        "--init",               # PID 1 zombie reaper
        "--memory", project_quota_memory(project),
        "--cpus", project_quota_cpus(project),
        "-v", "station-data:/var/lib/claude-agent-station",
        "-v", "station-logs:/var/log/claude-agent",
        "-v", f"{HOME}/.claude:/root/.claude:z",
        "-v", f"{HOME}/.config/gh:/root/.config/gh:ro,z",
        "-e", f"STATION_RUN_ID={hint_run_id}",
        "-e", f"STATION_PROJECT_REPO={project}",
        "-e", f"STATION_DB_URL={os.environ['STATION_DB_URL']}",
        "-e", f"STATION_WEBHOOK_URL={os.environ['STATION_WEBHOOK_URL']}",
        # ...propagate the other STATION_* env vars
        "claude-agent-station/agent:dev",
        "python", "-m", "agent.station_orchestrator",
        "--driver", "--run-id", hint_run_id,
        "--config", STATION_CONFIG,
        "--workspaces-dir", STATION_WORKSPACES,
    ]
    subprocess.check_output(cmd)
    return {"status": "triggered", "container": name}
```

Two implementation paths:

1. **`docker` CLI shell-out** (above). Simple; matches today's `gh`/`git` subprocess style.
2. **`docker` Python SDK** (`pip install docker`). Cleaner error surface; cleaner inspection (`client.containers.get(name)`). Adds one dependency.

This spec proposes path 2 — the SDK's `client.containers.run(..., detach=True)` is barely more code than `subprocess.check_output`, and the inspection / log-streaming primitives are needed downstream anyway. Either way, the Docker socket gets mounted into `cas-launcher` at `/var/run/docker.sock` (compose: `volumes: - /var/run/docker.sock:/var/run/docker.sock`). This is a sharp tool — see Risks.

### State tracking: runs map

The launcher's module-level `_current: Popen | None` becomes a dict keyed by `run_id`:

```python
@dataclass
class RunnerHandle:
    run_id: str
    container_name: str
    started_at: datetime
    last_webhook_at: datetime
    project_repo: str

_runners: dict[str, RunnerHandle] = {}
```

Endpoints:

- `POST /run` → spawn one, insert into `_runners`.
- `GET /status` → list active runners (replaces today's single-container shape; old shape preserved as `{"runs": [...]}`).
- `POST /stop?run_id=…` → `docker stop` the named container.
- `POST /webhook-tick?run_id=…` → bump `last_webhook_at` for that specific run.

### Workspace persistence

Workspaces today live at `/home/claude-agent/workspaces/<repo>/` inside the agent container, backed by the `station-data` volume. The volume mount semantics survive the switch — each runner container mounts the same `station-data` volume at the same path. Two concurrent runs against *different* projects work because each project has its own subdir. Two concurrent runs against the *same* project still race the workspace dir; the orchestrator already uses git worktrees for per-teammate isolation, and `station_orchestrator.py` serializes per-project work via the queue. This issue does not change that — same-project parallelism remains a Tier 3 problem.

### Compose changes

`compose.yml` gains:

```yaml
networks:
  agent-net:
    driver: bridge

services:
  agent:
    # ... existing config ...
    volumes:
      # ... existing volumes ...
      - /var/run/docker.sock:/var/run/docker.sock
    networks:
      - agent-net
      - default       # for reaching cas-dashboard

  dashboard:
    networks:
      - agent-net
      - default
```

Runners join `agent-net` so they reach `cas-dashboard` at `http://dashboard:8420/api/webhook/run-event` (today's `STATION_WEBHOOK_URL`). Runners explicitly DO NOT mount `/var/run/docker.sock` — no Docker-in-Docker, no recursive spawn.

### Resource quotas per project

`Project` model (`dashboard/backend/app/models.py:17-43`) gains two columns:

```python
runner_memory_limit = Column(Text, nullable=True)   # "2g", "512m", null → cluster default
runner_cpu_limit    = Column(Text, nullable=True)   # "1.5", "0.5", null → cluster default
```

Migration adds them to `database.py:_migrate_add_columns` (or post-#393, as Alembic revisions). The launcher resolves the project for the active run (from the orchestrator's first webhook or the `STATION_PROJECT_REPO` env propagated through `/run`'s body), then includes `--memory` / `--cpus` flags accordingly. Defaults live in `Settings`:

```python
default_runner_memory_limit: str = "2g"
default_runner_cpu_limit:    str = "1.0"
```

### Inspecting a live runner — operator command

A documented one-liner:

```bash
docker exec -it cas-runner-<run-id> bash
```

Or, for non-interactive log tail:

```bash
docker logs -f cas-runner-<run-id>
```

These are surfaced in the dashboard's run detail page as copy-able snippets, and documented in `docs/operations.md`.

### Run lifecycle and reaper

The launcher's existing `_zombie_reaper` (line 278) becomes container-aware. The loop:

1. For each `RunnerHandle` in `_runners`, check `last_webhook_at`.
2. If silent for `>ZOMBIE_TIMEOUT_SECONDS` (default 120 s) AND the container is still running, `docker stop --time 30` followed by `docker rm -f` if needed.
3. On container exit (poll `client.containers.get(name).status`), remove from `_runners`.

The `--rm` flag handles normal exits. Forced cleanup on stuck containers is the reaper's job.

### Webhook routing

Webhooks emit `run_id` today. No change. The dashboard's webhook router (`app/routers/webhook.py`) already keys events by `run_id`; multiple concurrent runs producing interleaved events is a degree of concurrency the dashboard already supports.

## Acceptance criteria

From the issue body, expanded:

- [ ] **Launcher spawns a fresh container per run via Docker socket (or compose run).** `POST /run` on the launcher calls `docker run --rm --detach` (via the Python SDK) and returns the container name plus run_id. Smoke test: trigger two runs back-to-back, confirm two distinct `cas-runner-*` containers existed.
- [ ] **Container is destroyed on run exit.** Verified by polling `docker ps -a --filter "name=cas-runner-*"` 60 s after the run's `run_complete` webhook — no rows. Forced-stop path tested via the reaper.
- [ ] **Workspace state persists via `station-data` volume mount.** Two consecutive runs on the same project find the same `workspaces/<repo>/` tree; git state, vision caches, and audit DB rows survive.
- [ ] **Two concurrent runs on different projects do not share a process tree.** `docker top cas-runner-<a>` and `docker top cas-runner-<b>` show disjoint PID namespaces. SDK CLI pids are scoped per container.
- [ ] **Resource quotas (`--memory`, `--cpus`) configurable per project.** `Project.runner_memory_limit` / `runner_cpu_limit` honored at spawn; defaults documented; smoke test: set a low memory cap, trigger a run, observe `docker inspect` reflects the limit.
- [ ] **Documented operator command to inspect a live runner container.** `docs/operations.md` shows the `docker exec` and `docker logs` snippets. Dashboard's run detail page exposes them as one-click copy.

## Dependencies / Blocks

- **Depends on** [[2026-05-14-issue-393-postgres-migration]] — multi-writer is mandatory before two ephemeral runners write the audit log simultaneously. **#393 must ship first.**
- **Depends on** Tier 1 / Item 5 ([[2026-05-11-run-lifecycle-overhaul-design]] §Item 5) — the launcher entrypoint is now `python -m agent.station_orchestrator --driver`, which the bash → Python migration (#349) makes the production path. Trivially containerized only after that migration finishes.
- **Depends on** Tier 1B — clean teardown semantics. Without try/finally guarantees in the driver, containers can exit before writing `run_complete`, which the reaper would then mistake for a zombie.
- **Blocks** Tier 3 / #391 (run decomposition) — per-project containers are the substrate for multi-project parallel runs.
- **Independent of** [[2026-05-14-issue-388-approve-integration-verdict]] and [[2026-05-14-issue-387-run-timeline-api]].

## Risks and rollback

- **Docker socket inside `cas-launcher` = root on host.** Anyone who compromises the launcher has unrestricted Docker control on the host. Mitigation: keep the launcher's HTTP surface narrow (already three endpoints behind `STATION_LAUNCHER_TOKEN`); document the threat in `docs/security.md`; for hardened deployments, swap to a Docker socket proxy (e.g. Tecnativa/docker-socket-proxy) that whitelists `POST /containers/create` only.
- **Container spawn latency.** `docker run` adds ~500 ms–2 s vs. `subprocess.Popen`. Plays poorly with the optimistic-placeholder UI ([[2026-05-11-run-lifecycle-overhaul-design]] Item 2). Mitigation: the placeholder pattern is exactly designed for this; the dashboard inserts the `pending` row before calling the launcher, so the user sees instant feedback regardless of spawn latency.
- **Image pull on every container.** Default behaviour is "if not present, pull." Local images already present don't re-pull; in production we ship a built tag (`claude-agent-station/agent:dev`), so no remote pull happens. Document that operators must rebuild the image after pulling new code, same as today.
- **Volume mount semantics differ on Linux vs. macOS Docker Desktop.** Per-runner mounts of `${HOME}/.claude` are fast on Linux but slow under VirtioFS/gRPC-FUSE on Mac. Local-dev workflow stays "run the long-running `cas-agent` container" via a compose profile; production unconditionally uses ephemeral runners.
- **Reaper edge case: container started but `run_start` webhook never fires.** The reaper today uses `_last_webhook_at` as its heartbeat; if a runner crashes before its first webhook, `last_webhook_at` is the spawn time, so the timeout still triggers after `ZOMBIE_TIMEOUT_SECONDS`. Document and test.
- **Rollback**: set a `STATION_RUNNER_MODE=inline` env var that restores the old `_spawn_run_manager` code path. The two implementations can coexist behind a feature flag for one release cycle. After the flag is removed, rollback means reverting the launcher change — straightforward, single-file.

## Test strategy

- **Unit (`tests/test_launcher.py`)**: mock the Docker SDK client; assert `containers.run` invoked with the expected args (volume mounts, env vars, resource flags, name). Negative path: project with `runner_memory_limit` set — assert `--memory` flag value.
- **Unit (`tests/test_launcher_reaper.py`)**: mock the SDK; insert a `RunnerHandle` with stale `last_webhook_at`; run the reaper tick; assert `containers.stop` and removal from `_runners`.
- **Integration (`tests/integration/test_runner_spawn.py`, docker-required)**: marked `@pytest.mark.requires_docker`. Spawns a real runner against a dummy command (`sleep 5`), asserts the container exists, exits, and is auto-removed.
- **End-to-end (`tests/integration/test_concurrent_runs.py`)**: trigger two runs against two projects, assert two distinct containers existed simultaneously, neither saw the other's filesystem outside the `station-data` volume.
- **Manual production verification**: trigger a run via the dashboard; observe `docker ps` shows `cas-runner-<id>`; after completion, observe it's gone; verify the dashboard shows verdict + diff as today.

## Notes

- The issue body's snippet uses `docker compose run --rm --name cas-runner-<run-id> agent-runner`. That works but requires a declared `agent-runner` service in compose.yml. This spec prefers raw `docker run` via the SDK because it avoids tangling production behaviour with compose's service-definition lifecycle and gives the launcher direct knobs (per-call name, per-call resource caps) that `compose run` doesn't expose cleanly.
- The issue body claims "one container per run (not per project — keeps the lead/teammates in the same container so the SDK Agent Teams pattern still works)." That is correctly modeled here. Multi-project decomposition is Tier 3.
- The current `Dockerfile.agent` runs the launcher process as PID 1 via uvicorn. Under the new model, the launcher stays the persistent PID 1 of `cas-launcher`; each runner has the orchestrator as its PID 1 (via `--init` for proper signal handling). Worth a one-line comment in `Dockerfile.agent` next to the ENTRYPOINT.
