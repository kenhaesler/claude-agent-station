"""Entry point for the coordinator: python3 -m agent.coordinator"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

from agent.coordinator.config import CoordinatorConfig
from agent.coordinator.dag import TaskDAG
from agent.coordinator.db import init_db
from agent.coordinator.decomposer import decompose_issue
from agent.coordinator.scheduler import run_scheduler
from agent.coordinator.reporter import post_event

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("coordinator")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Coordinated employee scheduler")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--config-file", required=True)
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--workspaces-dir", required=True)
    parser.add_argument("--assignments-file", required=True)
    parser.add_argument("--concurrent-group-id", default="")
    return parser.parse_args()


def _load_assignments(path: str) -> list[dict]:
    """Load project assignments from the JSON file written by run-manager.sh."""
    with open(path) as f:
        return json.load(f)


def _fetch_top_open_issue(repo: str, workspace: str) -> tuple[int, str] | None:
    """Best-effort: fetch the top open issue from GitHub for decomposition."""
    try:
        result = subprocess.run(
            ["gh", "issue", "list", "--repo", repo, "--state", "open",
             "--limit", "1", "--json", "number,title,body"],
            capture_output=True, text=True, timeout=15, cwd=workspace,
        )
        if result.returncode == 0:
            issues = json.loads(result.stdout)
            if issues:
                issue = issues[0]
                number = issue.get("number")
                body = f"# {issue.get('title', '')}\n\n{issue.get('body', '')}"
                if number:
                    return int(number), body
    except Exception as e:
        logger.warning("Failed to fetch open issues for %s: %s", repo, e)
    return None


def _fetch_issue_body(repo: str, issue_number: int, workspace: str) -> str:
    """Fetch issue body from GitHub."""
    try:
        result = subprocess.run(
            ["gh", "issue", "view", str(issue_number), "--repo", repo, "--json", "body,title,comments"],
            capture_output=True, text=True, timeout=15, cwd=workspace,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            body = f"# {data.get('title', '')}\n\n{data.get('body', '')}"
            # Append comments for context
            for comment in data.get("comments", [])[:3]:
                body += f"\n\n---\nComment: {comment.get('body', '')[:500]}"
            return body
    except Exception as e:
        logger.warning("Failed to fetch issue #%d: %s", issue_number, e)
    return ""


async def coordinate(config: CoordinatorConfig) -> int:
    """Main coordination flow: decompose → schedule → monitor."""
    # Initialize coordinator's own DB connection (same file as dashboard)
    session_factory = await init_db(config.db_path)

    assignments = _load_assignments(config.assignments_file)
    if not assignments:
        logger.warning("No assignments found")
        return 0

    # Normalize run_id to include "run-" prefix (matches run-manager.sh convention)
    run_id = config.run_id
    if not run_id.startswith("run-"):
        run_id = f"run-{run_id}"

    logger.info("Coordinator starting with %d project assignments (run_id=%s)", len(assignments), run_id)

    # Process each project assignment
    for assignment in assignments:
        repo = assignment["repo"]
        workspace = assignment["workspace"]
        employee_count = assignment.get("employee_count", 1)
        issue_number = assignment.get("issue_number")
        issue_body = assignment.get("issue_body", "")

        # Extract and validate project mode from assignment
        project_mode = assignment.get("mode", "full")
        if project_mode not in ("full", "plan", "analyze"):
            logger.warning("Unknown mode '%s' for %s, defaulting to 'full'", project_mode, repo)
            project_mode = "full"
        config.project_mode = project_mode
        logger.info("Project %s mode: %s", repo, project_mode)

        # Fetch issue body if we have an issue number but no body
        if issue_number and not issue_body:
            issue_body = _fetch_issue_body(repo, issue_number, workspace)

        # In full mode with multiple employees but no issue, try to fetch one
        if not issue_number and project_mode == "full" and employee_count > 1:
            fetched = _fetch_top_open_issue(repo, workspace)
            if fetched:
                issue_number, issue_body = fetched
                logger.info("Auto-selected issue #%d for %s", issue_number, repo)

        # Check for existing tasks in DB (crash recovery)
        dag = TaskDAG(run_id, repo, session_factory)
        existing = await dag.task_count()

        if existing > 0:
            logger.info("Resuming run %s: %d tasks in DB", run_id, existing)
            recovered = await dag.recover_from_crash()
            if recovered:
                logger.info("Reset %d running tasks to ready", recovered)
        else:
            # Decompose the issue into a task DAG
            logger.info("Decomposing work for %s (issue #%s, %d employees)", repo, issue_number, employee_count)
            dag = await decompose_issue(
                issue_body=issue_body,
                repo=repo,
                workspace=workspace,
                config=config,
                session_factory=session_factory,
                run_id=run_id,
                issue_number=issue_number,
                employee_count=employee_count,
            )

        # Save DAG to log directory for dashboard access
        dag_data = await dag.to_dict()
        dag_file = os.path.join(config.log_dir, f"{run_id}-{_repo_name(repo)}-dag.json")
        with open(dag_file, "w") as f:
            json.dump(dag_data, f, indent=2)
        logger.info("Task DAG saved: %s (%d tasks)", dag_file, len(dag_data["tasks"]))

        # Post DAG info to dashboard
        post_event(config, "dag_created", {
            "project": repo,
            "task_count": len(dag_data["tasks"]),
            "dag_file": dag_file,
        })

        # Run the scheduler
        await run_scheduler(dag, config)

        # Save final DAG state
        dag_data = await dag.to_dict()
        with open(dag_file, "w") as f:
            json.dump(dag_data, f, indent=2)

        # Log results
        summary = await dag.summary()
        logger.info("Project %s complete: %s", repo, summary)
        post_event(config, "dag_completed", {
            "project": repo,
            "summary": summary,
        })

    return 0


def _repo_name(repo: str) -> str:
    return repo.split("/")[-1] if "/" in repo else repo


def main() -> None:
    args = parse_args()
    config = CoordinatorConfig.from_args(
        run_id=args.run_id,
        config_file=args.config_file,
        log_dir=args.log_dir,
        workspaces_dir=args.workspaces_dir,
        assignments_file=args.assignments_file,
        concurrent_group_id=args.concurrent_group_id,
    )

    # Handle signals for graceful shutdown
    loop = asyncio.new_event_loop()

    def handle_signal(sig: int, frame) -> None:
        logger.warning("Received signal %d, shutting down...", sig)
        for task in asyncio.all_tasks(loop):
            task.cancel()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        exit_code = loop.run_until_complete(coordinate(config))
    except asyncio.CancelledError:
        logger.warning("Coordinator cancelled")
        exit_code = 1
    except Exception:
        logger.exception("Coordinator failed")
        exit_code = 1
    finally:
        loop.close()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
