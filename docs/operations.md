# Operations

*When something is wrong or you need to act on the running system. For operators.*

## Service control

Five systemd units run the system:

| Unit | Purpose |
|------|---------|
| `claude-station-dashboard.service` | FastAPI dashboard backend (port 8420) |
| `claude-agent.service` | One-shot agent run — executes `python -m agent.station_orchestrator --driver` |
| `claude-agent.timer` | Fires `claude-agent.service` every hour (`:00`) |
| `claude-agent-validate.service` | One-shot validate-and-promote run via `promote.sh` |
| `claude-agent-validate.timer` | Fires `claude-agent-validate.service` daily at 06:00 |

Common commands:

```bash
sudo systemctl status claude-station-dashboard.service
sudo systemctl restart claude-station-dashboard.service
sudo systemctl status claude-agent.timer
sudo systemctl list-timers claude-agent.timer
sudo systemctl status claude-agent-validate.timer
```

## Log locations

| Source | Path | Read with |
|--------|------|-----------|
| Agent runs | `/var/log/claude-agent/` (override via `STATION_LOG_DIR`) | `tail -f`, or the dashboard Logs page |
| Dashboard backend | systemd journal | `journalctl -u claude-station-dashboard.service -f` |
| Audit / actions | `audit_log` table in `station.db` | dashboard Decisions page or direct SQLite query |

## Common failures and fixes

### Stuck run / orphan recovery

Symptom: a run shows `running` indefinitely after the agent has crashed or been killed. Or a run shows `unknown` indefinitely after the orchestrator exited without firing a terminal webhook (issue #268 — for example, no eligible issues + dashboard's importer ingested the stream file before the launcher's terminal webhook landed).

The dashboard runs a background reaper that checks every 15 seconds. On startup it also runs once immediately. If the agent service is inactive and a run is still in `running` or `reviewing` state, the reaper marks it `interrupted` and pushes a live SSE update to the frontend. The same reaper also catches `unknown` rows whose `started_at` is older than 30 minutes (`UNKNOWN_RUN_REAP_AGE_MINUTES` in `dashboard/backend/app/services/stale_run_reaper.py`) — the conservative threshold prevents racing the launcher's normal `finished` webhook.

No manual command is needed in normal operation. If the dashboard itself was down while the agent crashed, restarting the dashboard triggers the startup reaper:

```bash
sudo systemctl restart claude-station-dashboard.service
```

The reaper also recovers orphaned queue items and stale coordinator tasks from the same event.

### OAuth token expired

Symptom: agent runs fail at start with auth errors from Claude.

Fix:

```bash
sudo -u claude-agent /opt/claude-agent-station/agent/scripts/refresh-token.py
```

### Circuit breaker tripped

Symptom: agent timer fires but no run starts; logs mention the circuit breaker.

`agent/scripts/circuit-breaker.sh` tracks consecutive failures per agent name in a JSON state file at `/var/lib/claude-agent-station/circuit-breaker.json` (override via `STATION_CIRCUIT_FILE`). After **3** consecutive failures the circuit opens and blocks further attempts.

`<agent-name>` below is whatever string the caller passed to the script — inspect the JSON file to see the live keys: `sudo cat /var/lib/claude-agent-station/circuit-breaker.json`. Each top-level key is a tracked agent name.

To inspect the current state:

```bash
/opt/claude-agent-station/agent/scripts/circuit-breaker.sh <agent-name> status
```

To reset after investigating the root cause:

```bash
/opt/claude-agent-station/agent/scripts/circuit-breaker.sh <agent-name> reset
```

Before resetting, review the logs for the failing runs to understand why the agent failed — the circuit breaker exists to prevent infinite retry loops on a broken task or broken environment.

### Plan-tier throttle

Symptom: runs don't start when expected.

Per [`concepts.md`](concepts.md#plan-usage-throttling), the throttle short-circuits new runs when weekly usage crosses the threshold. Verify on the dashboard Command Center page; current usage and active throttle state are displayed there.

### Dashboard returns 401

Cause: `STATION_API_KEY` is set on the server but the request is missing the `Authorization: Bearer <key>` header (or `?token=<key>` query parameter). Either pass auth or unset `STATION_API_KEY` (only on isolated hosts).

## Audit trail

Every agent action is recorded in the `audit_log` table. To filter by run (replace `<run-id>` with the actual run ID before running):

```bash
sqlite3 /var/lib/claude-agent-station/station.db \
  "SELECT actor, action_kind, status, started_at FROM audit_log WHERE run_id = '<run-id>' ORDER BY started_at;"
```

Available columns: `actor`, `action_kind`, `action_detail`, `status`, `exit_code`, `stdout_tail`, `stderr_tail`, `started_at`, `finished_at`, `idempotency_key`, `run_id`, `trace_id`.

## Upgrade procedure

```bash
cd /opt/claude-agent-station
sudo -u claude-agent git pull --ff-only
cd dashboard/backend
sudo -u claude-agent ../../venv/bin/pip install -r requirements-lock.txt
sudo systemctl restart claude-station-dashboard.service
```

CI runs a dependency drift check; if local installs disagree with the lock file, the dashboard service will fail to start with an import error.

## Disaster recovery

### Database backup

```bash
sqlite3 /var/lib/claude-agent-station/station.db ".backup /tmp/station.db.bak"
```

### Database restore

```bash
sudo systemctl stop claude-station-dashboard.service
sudo cp /tmp/station.db.bak /var/lib/claude-agent-station/station.db
sudo chown claude-agent:claude-agent /var/lib/claude-agent-station/station.db
sudo systemctl start claude-station-dashboard.service
```

### Workspace cleanup

Workspaces should be cleaned up automatically; to free space manually:

```bash
sudo -u claude-agent find /home/claude-agent/workspaces/ -mindepth 1 -maxdepth 1 -mtime +7 -exec rm -rf {} +
```

Keep `-maxdepth 1` — descending into a repo would risk matching old subdirectories inside a live clone.

## Inspecting a live runner

Each run executes inside an ephemeral container named `cas-runner-<run-id>` (the `run-` prefix is stripped — e.g. run `run-20260515T120000Z` lives in container `cas-runner-20260515T120000Z`). The dashboard's run-detail page surfaces the exact snippets for the currently-active run; from a shell:

```bash
# Tail logs in real time
docker logs -f cas-runner-<run-id>

# Drop a shell inside the runner. Workspace state is under
# /var/lib/claude-agent-station/workspaces on the shared station-data volume.
docker exec -it cas-runner-<run-id> bash
```

`docker ps --filter "name=cas-runner-"` lists every active runner — useful when multiple projects are running concurrently.

### Resource quotas per project

Set `runner_memory_limit` (Docker memory-limit syntax, e.g. `"2g"`, `"512m"`) and `runner_cpu_limit` (decimal cores, e.g. `"1.0"`) on a Project via the dashboard's project-edit form or `PATCH /api/projects/{id}`. Defaults come from `STATION_DEFAULT_RUNNER_MEMORY_LIMIT` / `STATION_DEFAULT_RUNNER_CPU_LIMIT` (currently `2g` / `1.0`). The launcher resolves the per-project value first and falls back to the env default; out-of-range or malformed values fall through to the default so a bad project row never blocks a run.

### Rollback to inline mode

For one release after #386 ships, the legacy single-subprocess launcher path remains available behind a flag for emergency rollback:

```bash
docker compose stop agent
STATION_RUNNER_MODE=inline docker compose up -d agent
```

This restores the pre-#386 single-container behaviour (one orchestrator subprocess inside the agent container, no per-run isolation). Remove the override once container mode is proven stable to re-engage per-run containers as the default.

### Security implications of mounting `/var/run/docker.sock`

The `agent` service mounts `/var/run/docker.sock` so the launcher can spawn `cas-runner-<run-id>` containers. The Docker socket grants **root-equivalent access to the host** — any process that reaches it can `docker run --privileged --pid=host --network=host …` and escape the agent container.

This means the agent container's blast radius now includes the host. Concretely:

- Any code-execution vulnerability in the launcher or orchestrator (including a malicious repo's CI / setup script that ends up running inside a runner) can take over the host, not just the agent container.
- For shared infrastructure or multi-tenant deployments, gate the socket through a Docker-in-Docker proxy (e.g. `tecnativa/docker-socket-proxy`) restricting the launcher to `containers.run` / `containers.get` / `containers.stop` and nothing else.
- Single-tenant developer machines and dedicated VMs are the intended deployment target; the existing trust model (the operator already trusts the agent with `gh` auth and Claude credentials) makes the additional socket exposure proportional.

Run on dedicated hardware or a disposable VM if the threat model includes hostile repository contents.
