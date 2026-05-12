"""HTTP client for the dashboard's /api/coordinator/tasks endpoints.

Owns the try/finally invariant: every task created via create_task() is
tracked in process-local state, and an atexit handler finalizes any
still-open tasks as 'orphaned' on process exit. This eliminates the
zombie-task class of bugs (issue #345 + #349).

Note: atexit fires on normal exits and on unhandled exceptions, but NOT on
SIGKILL. The dashboard's stale-run reaper is the second line of defense for
that case (#345).

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
    # /api/coordinator/* is gated by Depends(verify_api_key) — Bearer auth.
    # The X-Webhook-Token used by /api/webhook/* is NOT accepted here. See
    # agent/plan_review_gate.py for the canonical pattern.
    api_key = os.environ.get("STATION_API_KEY", "")
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
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
    import argparse
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
