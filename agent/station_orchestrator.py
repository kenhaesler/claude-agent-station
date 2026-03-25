"""Station Orchestrator — Agent Teams-based multi-employee coordination.

Replaces the custom coordinator (scheduler, decomposer, foreman, etc.) with
Claude Agent SDK + Agent Teams. Each GitHub issue becomes a Task claimed by
exactly one teammate, eliminating duplicate work via atomic file-locking.

Usage:
    python3 -m agent.station_orchestrator \
        --config /path/to/manager-config.json \
        --run-id 20260325T130713Z \
        --workspaces-dir /home/claude-agent/workspaces
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import query, ClaudeAgentOptions

logger = logging.getLogger(__name__)

SKIP_LABELS = frozenset({
    "autonomous-agent/in-progress",
    "autonomous-agent/needs-help",
    "autonomous-agent/refined",
    "NO AI",
    "backlog",
    "wontfix",
})

# Priority label ordering for deterministic assignment
PRIORITY_ORDER = {
    "priority/critical": 0,
    "priority/high": 1,
    "priority/medium": 2,
    "priority/low": 3,
}


# ── Configuration ──────────────────────────────────────────────

def load_config(config_file: str) -> dict:
    """Load manager-config.json."""
    with open(config_file) as f:
        return json.load(f)


def get_limit(config: dict, key: str, default: int) -> int:
    return config.get("limits", {}).get(key, default)


def get_model(config: dict, key: str, default: str) -> str:
    return config.get("models", {}).get(key, default)


# ── Issue Fetching ─────────────────────────────────────────────

def fetch_eligible_issues(repo: str, limit: int, workspace: str | None = None) -> list[dict]:
    """Fetch open issues from GitHub, filter SKIP_LABELS, sort by priority.

    Returns at most ``limit`` issues, each with keys:
    number, title, body, labels.
    """
    cmd = [
        "gh", "issue", "list",
        "--repo", repo,
        "--state", "open",
        "--limit", str(limit + 20),
        "--json", "number,title,body,labels",
    ]
    env = os.environ.copy()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=workspace,
            env=env,
        )
        if result.returncode != 0:
            logger.warning("gh issue list failed: %s", result.stderr.strip())
            return []
        issues = json.loads(result.stdout)
    except Exception as e:
        logger.error("Failed to fetch issues for %s: %s", repo, e)
        return []

    # Filter out issues with SKIP_LABELS
    eligible: list[dict] = []
    for issue in issues:
        label_names = {label.get("name", "") for label in issue.get("labels", [])}
        if label_names & SKIP_LABELS:
            continue
        eligible.append(issue)

    # Sort by priority labels (critical first, unlabeled last)
    def priority_key(issue: dict) -> int:
        for label in issue.get("labels", []):
            name = label.get("name", "")
            if name in PRIORITY_ORDER:
                return PRIORITY_ORDER[name]
        return len(PRIORITY_ORDER)

    eligible.sort(key=priority_key)
    return eligible[:limit]


# ── Team Prompt Construction ──────────────────────────────────

def build_team_prompt(
    repo: str,
    issues: list[dict],
    config: dict,
    run_id: str,
) -> str:
    """Build the lead agent prompt that creates and manages the team."""
    issue_entries = []
    for issue in issues:
        labels_str = ", ".join(l.get("name", "") for l in issue.get("labels", []))
        issue_entries.append(
            f"- **#{issue['number']}**: {issue.get('title', 'Untitled')}"
            + (f" [{labels_str}]" if labels_str else "")
        )
    issue_list = "\n".join(issue_entries)

    max_turns = get_limit(config, "max_employee_turns", 200)
    teammate_model = get_model(config, "employee", "claude-opus-4-6")

    return f"""You are the lead of an agent team implementing GitHub issues for **{repo}**.

## Your Tasks

1. **Create a team** called "{repo.split('/')[-1]}-{run_id[:8]}"
2. **Create one Task per issue** listed below (use TaskCreate for each)
3. **Spawn one teammate per task** using the `issue-worker` agent type
4. Each teammate works on exactly ONE issue — no duplicates
5. **Require plan approval** before any teammate starts implementation
6. Review each plan — reject if it conflicts with another teammate's work
7. After all teammates complete, **synthesize a summary** of what was done
8. **Clean up** the team when finished

## Issues to Implement ({len(issues)} total)

{issue_list}

## Teammate Configuration

- Agent type: `issue-worker`
- Model: `{teammate_model}`
- Max turns: {max_turns}
- Each teammate works in an isolated git worktree (automatic)
- Teammates must commit locally — NEVER push

## Rules

- Spawn exactly {len(issues)} teammates
- One teammate per issue — verify no two teammates claim the same issue
- If a teammate's plan modifies the same files as another, coordinate or reject
- Track progress and nudge teammates that appear stuck after 10 minutes
- After all work is done, provide a JSON summary with:
  - issues_completed: list of issue numbers
  - issues_failed: list of issue numbers with reasons
  - total_turns: sum across all teammates
  - conflicts_detected: any file conflicts found

## Environment

- Repository: {repo}
- Run ID: {run_id}
- GH_TOKEN is available for GitHub CLI operations
"""


# ── Dashboard Webhook ──────────────────────────────────────────

def _message_to_dict(message) -> dict:
    """Convert an SDK stream message to a JSON-serializable dict."""
    result: dict = {}
    for attr in ("type", "subtype", "session_id", "is_error", "duration_ms",
                 "num_turns", "result", "stop_reason"):
        val = getattr(message, attr, None)
        if val is not None:
            result[attr] = val
    # Handle assistant message content
    if getattr(message, "type", None) == "assistant":
        msg = getattr(message, "message", None)
        if msg:
            content = getattr(msg, "content", None)
            if content:
                result["content_types"] = []
                for block in (content if isinstance(content, list) else [content]):
                    bt = getattr(block, "type", None) if hasattr(block, "type") else (block.get("type") if isinstance(block, dict) else None)
                    if bt:
                        result["content_types"].append(bt)
                    if bt == "tool_use":
                        name = getattr(block, "name", None) if hasattr(block, "name") else (block.get("name") if isinstance(block, dict) else None)
                        if name:
                            result.setdefault("tool_calls", []).append(name)
                    elif bt == "text":
                        text = getattr(block, "text", None) if hasattr(block, "text") else (block.get("text") if isinstance(block, dict) else None)
                        if text:
                            result["text_preview"] = text[:200]
    return result


def post_webhook(config: dict, event: str, data: dict | None = None) -> None:
    """Send an event to the dashboard webhook (best-effort)."""
    webhook_url = config.get("dashboard", {}).get(
        "webhook_url", "http://127.0.0.1:8420/api/webhook/run-event"
    )
    webhook_secret = os.environ.get("STATION_WEBHOOK_SECRET", "") or config.get(
        "dashboard", {}
    ).get("webhook_secret", "")

    payload = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if data:
        payload.update(data)

    try:
        body = json.dumps(payload).encode()
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if webhook_secret:
            headers["X-Webhook-Token"] = webhook_secret
        req = urllib.request.Request(webhook_url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=3):
            pass
    except Exception:
        pass  # Best-effort


def handle_stream_event(message, config: dict, run_id: str, log_file=None) -> None:
    """Forward SDK stream messages to the dashboard and write to log file."""
    # Write every message to the stream log file (for the Logs tab)
    if log_file is not None:
        try:
            line = json.dumps(_message_to_dict(message))
            log_file.write(line + "\n")
            log_file.flush()
        except Exception:
            pass

    msg_type = getattr(message, "type", None)

    if msg_type == "system":
        subtype = getattr(message, "subtype", "")
        if subtype == "init":
            post_webhook(config, "orchestrator_start", {
                "run_id": f"run-{run_id}",
                "mode": "agent-teams",
            })
    elif msg_type == "assistant":
        # Log assistant messages (tool calls, thinking) at debug level
        content = getattr(message, "content", None)
        if content:
            for block in (content if isinstance(content, list) else [content]):
                block_type = getattr(block, "type", "") if hasattr(block, "type") else block.get("type", "") if isinstance(block, dict) else ""
                if block_type == "tool_use":
                    tool_name = getattr(block, "name", "") if hasattr(block, "name") else block.get("name", "")
                    logger.info("Lead agent tool call: %s", tool_name)
    elif msg_type == "result":
        post_webhook(config, "orchestrator_complete", {
            "run_id": f"run-{run_id}",
            "is_error": getattr(message, "is_error", False),
            "duration_ms": getattr(message, "duration_ms", 0),
            "num_turns": getattr(message, "num_turns", 0),
        })
        # Log the final result
        result_text = getattr(message, "result", "")
        if result_text:
            logger.info("Orchestrator result:\n%s", result_text[:2000])


# ── Main Orchestration ─────────────────────────────────────────

async def orchestrate(config: dict, run_id: str, workspaces_dir: str) -> int:
    """Run the Agent Teams orchestration for all configured projects."""
    projects = config.get("projects", [])
    max_per_project = get_limit(config, "max_employees_per_project", 3)
    manager_model = get_model(config, "manager", "claude-sonnet-4-6")
    manager_turns = get_limit(config, "max_manager_turns", 30)

    exit_code = 0

    for project in projects:
        if not project.get("enabled", True):
            continue

        repo = project["repo"]
        repo_name = repo.split("/")[-1] if "/" in repo else repo
        workspace = os.path.join(workspaces_dir, repo_name)

        logger.info("Processing project: %s", repo)

        # Fetch and filter issues
        issues = fetch_eligible_issues(repo, max_per_project, workspace)
        if not issues:
            logger.info("No eligible issues for %s, skipping", repo)
            continue

        logger.info(
            "Found %d eligible issues for %s: %s",
            len(issues),
            repo,
            [f"#{i['number']}" for i in issues],
        )

        # Notify dashboard
        post_webhook(config, "run_start", {
            "run_id": f"run-{run_id}",
            "project": repo,
            "mode": "agent-teams",
            "employee_count": len(issues),
            "concurrent_group_id": f"group-{run_id}",
        })

        # Build the team prompt
        team_prompt = build_team_prompt(repo, issues, config, run_id)

        # Determine agent directory for custom agents
        agent_dir = Path(__file__).parent / "agents"

        # Construct agents JSON for the issue-worker if the file exists
        agents_config: dict | None = None
        worker_file = agent_dir / "issue-worker.md"
        if worker_file.exists():
            logger.info("Found issue-worker agent at %s", worker_file)

        # Open stream log file for the Logs tab
        log_dir = config.get("logging", {}).get("log_dir", "/var/log/claude-agent")
        stream_log_path = os.path.join(log_dir, f"run-{run_id}-orchestrator.stream.jsonl")
        logger.info("Stream log: %s", stream_log_path)

        # Also update the Run record with the log file path
        post_webhook(config, "employee_start", {
            "run_id": f"run-{run_id}",
            "project": repo,
            "mode": "agent-teams",
            "employee_index": 0,
            "concurrent_group_id": f"group-{run_id}",
        })

        # Run the lead agent via SDK
        try:
            logger.info("Starting Agent Teams lead for %s (%d issues, model=%s)", repo, len(issues), manager_model)
            with open(stream_log_path, "w") as log_file:
                async for message in query(
                    prompt=team_prompt,
                    options=ClaudeAgentOptions(
                        cwd=workspace,
                        env={
                            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
                            "GITHUB_REPO": repo,
                        },
                        allowed_tools=["Read", "Bash", "Glob", "Grep", "Edit", "Write", "Agent"],
                        max_turns=manager_turns,
                        model=manager_model,
                    ),
                ):
                    handle_stream_event(message, config, run_id, log_file=log_file)

        except Exception as e:
            logger.exception("Agent Teams orchestration failed for %s: %s", repo, e)
            post_webhook(config, "orchestrator_error", {
                "run_id": f"run-{run_id}",
                "project": repo,
                "error": str(e)[:500],
            })
            exit_code = 1

    return exit_code


# ── CLI Entry Point ────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Agent Teams station orchestrator")
    parser.add_argument("--config", required=True, help="Path to manager-config.json")
    parser.add_argument("--run-id", required=True, help="Unique run identifier")
    parser.add_argument("--workspaces-dir", required=True, help="Directory for project workspaces")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    args = parse_args()
    config = load_config(args.config)

    exit_code = asyncio.run(orchestrate(config, args.run_id, args.workspaces_dir))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
