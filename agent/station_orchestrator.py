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
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.types import (
    AgentDefinition,
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
)

logger = logging.getLogger(__name__)


@dataclass
class _StreamState:
    """Accumulates stream data for batched webhook delivery."""
    tokens_in: int = 0
    tokens_out: int = 0
    tool_calls: int = 0
    turns: int = 0
    last_webhook_time: float = 0.0
    BATCH_INTERVAL: float = 15.0  # seconds between progress webhooks


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


# Map full model names to SDK short names
MODEL_MAP = {
    "claude-opus-4-6": "opus",
    "claude-sonnet-4-6": "sonnet",
    "claude-haiku-4-5": "haiku",
}


def load_agent_definition(path: Path) -> tuple[str, AgentDefinition]:
    """Parse an agent markdown file (with YAML frontmatter) into an AgentDefinition."""
    text = path.read_text()
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"Invalid agent definition (missing --- delimiters): {path}")

    # Parse frontmatter manually (avoid yaml dependency)
    meta: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip()

    body = parts[2].strip()
    name = meta.get("name", path.stem)
    tools_str = meta.get("tools", "")
    tools = [t.strip() for t in tools_str.split(",")] if tools_str else None
    model_raw = meta.get("model", "")
    model = MODEL_MAP.get(model_raw, "opus")

    return name, AgentDefinition(
        description=meta.get("description", ""),
        prompt=body,
        tools=tools,
        model=model,
    )


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
    workspace: str = "",
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
7. **Actively monitor** teammates until ALL have completed (see monitoring rules below)
8. After all teammates complete, **synthesize a final JSON summary**

## Issues to Implement ({len(issues)} total)

{issue_list}

## Teammate Configuration

- Agent type: `issue-worker`
- Model: `{teammate_model}`
- Max turns: {max_turns}
- Each teammate works in an isolated git worktree (automatic)
- Teammates must commit locally — NEVER push

## CRITICAL: Active Monitoring Rules

After spawning teammates, you MUST actively monitor their progress using tool calls.
**NEVER end your turn while any teammate is still working.**

Follow this monitoring loop:
1. After spawning all teammates, run: `sleep 60` (Bash tool)
2. Check for completed reports: `find {workspace} -name ".claude-employee-report.json" -type f 2>/dev/null`
3. For each report found, read it and record the status
4. If any teammate has not yet reported, **go back to step 1**
5. Only end your turn and provide the final JSON summary AFTER:
   - All teammates have written their `.claude-employee-report.json`, OR
   - 20 minutes have elapsed since spawning (report timeout for remaining)

**Why this matters**: If you say "I'm waiting" and end your turn, the session terminates
and your teammates lose their work. You must keep making tool calls to stay alive.

## Rules

- Spawn exactly {len(issues)} teammates
- One teammate per issue — verify no two teammates claim the same issue
- If a teammate's plan modifies the same files as another, coordinate or reject
- After all work is done, provide a JSON summary with:
  - issues_completed: list of issue numbers
  - issues_failed: list of issue numbers with reasons
  - total_turns: sum across all teammates
  - conflicts_detected: any file conflicts found

## Environment

- Repository: {repo}
- Run ID: {run_id}
- Workspace: {workspace}
- GH_TOKEN is available for GitHub CLI operations
"""


def build_followup_prompt(workspace: str) -> str:
    """Build a follow-up prompt for re-entering the lead agent session."""
    return (
        "Your previous session ended but teammates may still be working.\n\n"
        "Check their status now:\n"
        f"1. Run: `find {workspace} -name '.claude-employee-report.json' -type f`\n"
        "2. Read any reports found and record results\n"
        "3. If workers haven't finished yet, `sleep 60` and check again\n"
        "4. Provide the final JSON summary (issues_completed, issues_failed, "
        "total_turns, conflicts_detected) only when ALL workers are done or timed out.\n\n"
        "Do NOT shut down the team or end your turn until all work is accounted for."
    )


# ── Dashboard Webhook ──────────────────────────────────────────

def _message_to_dict(message) -> dict:
    """Convert an SDK stream message to a JSON-serializable dict.

    Uses isinstance() checks against SDK dataclass types — getattr-based
    type detection returns None for all SDK messages.
    """
    result: dict = {}

    if isinstance(message, AssistantMessage):
        result["type"] = "assistant"
        if message.usage:
            result["usage"] = message.usage
        if message.content:
            result["content_types"] = []
            for block in (message.content if isinstance(message.content, list) else [message.content]):
                bt = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
                if bt:
                    result["content_types"].append(bt)
                if bt == "tool_use":
                    name = getattr(block, "name", None) or (block.get("name") if isinstance(block, dict) else None)
                    if name:
                        result.setdefault("tool_calls", []).append(name)
                elif bt == "text":
                    text = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else None)
                    if text:
                        result["text_preview"] = text[:200]

    elif isinstance(message, ResultMessage):
        result["type"] = "result"
        result["subtype"] = getattr(message, "subtype", "success")
        for attr in ("session_id", "is_error", "duration_ms", "num_turns", "result", "stop_reason"):
            val = getattr(message, attr, None)
            if val is not None:
                result[attr] = val
        if message.usage:
            result["usage"] = message.usage

    elif isinstance(message, TaskStartedMessage):
        result["type"] = "system"
        result["subtype"] = "task_started"
        result["task_id"] = message.task_id
        result["description"] = message.description
        result["session_id"] = message.session_id
        result["task_type"] = getattr(message, "task_type", None)

    elif isinstance(message, TaskProgressMessage):
        result["type"] = "system"
        result["subtype"] = "task_progress"
        result["task_id"] = message.task_id
        result["last_tool_name"] = message.last_tool_name
        if message.usage:
            result["usage"] = {
                "total_tokens": message.usage.total_tokens,
                "tool_uses": message.usage.tool_uses,
                "duration_ms": message.usage.duration_ms,
            }

    elif isinstance(message, TaskNotificationMessage):
        result["type"] = "system"
        result["subtype"] = "task_notification"
        result["task_id"] = message.task_id
        result["status"] = message.status
        result["summary"] = message.summary
        if message.usage:
            result["usage"] = {
                "total_tokens": message.usage.total_tokens,
                "tool_uses": message.usage.tool_uses,
                "duration_ms": message.usage.duration_ms,
            }

    elif isinstance(message, SystemMessage):
        result["type"] = "system"
        result["subtype"] = getattr(message, "subtype", "")
        sid = getattr(message, "session_id", None) if hasattr(message, "session_id") else None
        if sid:
            result["session_id"] = sid

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


def handle_stream_event(
    message, config: dict, run_id: str, log_file=None, state: _StreamState | None = None,
) -> None:
    """Forward SDK stream messages to the dashboard and write to log file."""
    # Write structured data to JSONL log (skip empty dicts)
    if log_file is not None:
        try:
            d = _message_to_dict(message)
            if d:
                log_file.write(json.dumps(d) + "\n")
                log_file.flush()
        except Exception:
            pass

    # --- Forward meaningful events to dashboard ---

    if isinstance(message, AssistantMessage):
        # Accumulate tokens
        if state and message.usage:
            state.tokens_in += message.usage.get("input_tokens", 0)
            state.tokens_out += message.usage.get("output_tokens", 0)
        # Count tool calls and log them
        if message.content:
            for block in (message.content if isinstance(message.content, list) else [message.content]):
                bt = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
                if bt == "tool_use":
                    if state:
                        state.tool_calls += 1
                    name = getattr(block, "name", None) or (block.get("name") if isinstance(block, dict) else None)
                    logger.info("Lead agent tool call: %s", name)
        # Batch-send progress webhook every BATCH_INTERVAL seconds
        if state:
            now = time.monotonic()
            if now - state.last_webhook_time >= state.BATCH_INTERVAL:
                state.last_webhook_time = now
                post_webhook(config, "progress_update", {
                    "run_id": f"run-{run_id}",
                    "tokens_input": state.tokens_in,
                    "tokens_output": state.tokens_out,
                    "tokens_total": state.tokens_in + state.tokens_out,
                    "turns": state.turns,
                })

    elif isinstance(message, TaskStartedMessage):
        logger.info("Teammate spawned: task=%s desc=%s", message.task_id, message.description)
        post_webhook(config, "teammate_spawned", {
            "run_id": f"run-{run_id}",
            "task_id": message.task_id,
            "agent_name": message.description,
        })

    elif isinstance(message, TaskProgressMessage):
        if state and message.usage:
            state.turns = message.usage.tool_uses
        logger.info(
            "Teammate progress: task=%s tools=%s last=%s",
            message.task_id,
            message.usage.tool_uses if message.usage else "?",
            message.last_tool_name,
        )

    elif isinstance(message, TaskNotificationMessage):
        logger.info("Teammate finished: task=%s status=%s", message.task_id, message.status)
        post_webhook(config, "teammate_completed", {
            "run_id": f"run-{run_id}",
            "task_id": message.task_id,
            "status": message.status,
            "agent_name": message.summary[:100] if message.summary else "",
            "tokens_total": message.usage.total_tokens if message.usage else 0,
            "turns": message.usage.tool_uses if message.usage else 0,
        })

    elif isinstance(message, ResultMessage):
        # Final flush of accumulated tokens
        if state:
            post_webhook(config, "progress_update", {
                "run_id": f"run-{run_id}",
                "tokens_input": state.tokens_in,
                "tokens_output": state.tokens_out,
                "tokens_total": state.tokens_in + state.tokens_out,
                "turns": state.turns,
            })
        post_webhook(config, "orchestrator_complete", {
            "run_id": f"run-{run_id}",
            "is_error": getattr(message, "is_error", False),
            "duration_ms": getattr(message, "duration_ms", 0),
            "num_turns": getattr(message, "num_turns", 0),
        })
        result_text = getattr(message, "result", "")
        if result_text:
            logger.info("Orchestrator result:\n%s", result_text[:2000])


# ── Completion Detection ──────────────────────────────────────

def _is_work_complete(result_text: str) -> bool:
    """Check if the lead agent's result indicates all work is done."""
    if not result_text:
        return False
    # Look for the structured JSON summary markers
    if "issues_completed" in result_text and "issues_failed" in result_text:
        return True
    lower = result_text.lower()
    return any(phrase in lower for phrase in [
        "all teammates have completed",
        "all workers have completed",
        "final report",
        "final summary",
    ])


# ── Main Orchestration ─────────────────────────────────────────

async def orchestrate(config: dict, run_id: str, workspaces_dir: str) -> int:
    """Run the Agent Teams orchestration for all configured projects."""
    projects = config.get("projects", [])
    max_per_project = get_limit(config, "max_employees_per_project", 3)
    manager_model = get_model(config, "manager", "claude-sonnet-4-6")
    manager_turns = get_limit(config, "max_manager_turns", 30)

    # Load issue-worker agent definition for SDK discovery
    agent_dir = Path(__file__).parent / "agents"
    worker_file = agent_dir / "issue-worker.md"
    agents_dict: dict[str, AgentDefinition] | None = None
    if worker_file.exists():
        try:
            worker_name, worker_def = load_agent_definition(worker_file)
            agents_dict = {worker_name: worker_def}
            logger.info("Loaded agent definition: %s from %s", worker_name, worker_file)
        except Exception as e:
            logger.warning("Failed to load agent definition %s: %s", worker_file, e)

    exit_code = 0
    max_reentries = 6  # Up to 6 re-entries if lead exits prematurely

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
            len(issues), repo, [f"#{i['number']}" for i in issues],
        )

        # Notify dashboard
        post_webhook(config, "run_start", {
            "run_id": f"run-{run_id}",
            "project": repo,
            "mode": "agent-teams",
            "employee_count": len(issues),
            "concurrent_group_id": f"group-{run_id}",
        })

        # Open stream log file
        log_dir = config.get("logging", {}).get("log_dir", "/var/log/claude-agent")
        stream_log_path = os.path.join(log_dir, f"run-{run_id}-orchestrator.stream.jsonl")
        logger.info("Stream log: %s", stream_log_path)

        post_webhook(config, "employee_start", {
            "run_id": f"run-{run_id}",
            "project": repo,
            "mode": "agent-teams",
            "employee_index": 0,
            "concurrent_group_id": f"group-{run_id}",
        })

        # ---- Retry loop: re-enter the lead session if it exits prematurely ----
        session_id: str | None = None
        work_complete = False
        first_init_sent = False
        stream_state = _StreamState(last_webhook_time=time.monotonic())

        try:
            logger.info("Starting Agent Teams lead for %s (%d issues, model=%s)", repo, len(issues), manager_model)
            with open(stream_log_path, "a") as log_file:
                for iteration in range(max_reentries):
                    is_followup = iteration > 0

                    if is_followup:
                        prompt = build_followup_prompt(workspace)
                        logger.info(
                            "Re-entering lead session (iteration %d/%d, session=%s)",
                            iteration + 1, max_reentries, session_id,
                        )
                    else:
                        prompt = build_team_prompt(repo, issues, config, run_id, workspace)

                    # Build options — use resume for follow-up iterations
                    options = ClaudeAgentOptions(
                        cwd=workspace,
                        env={
                            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
                            "GITHUB_REPO": repo,
                        },
                        allowed_tools=["Read", "Bash", "Glob", "Grep", "Edit", "Write", "Agent"],
                        max_turns=manager_turns,
                        model=manager_model,
                        agents=agents_dict,
                    )
                    if is_followup and session_id:
                        options.resume = session_id
                        options.continue_conversation = True

                    async for message in query(prompt=prompt, options=options):
                        # Capture session_id for resume
                        sid = getattr(message, "session_id", None)
                        if sid:
                            session_id = sid

                        # Only send orchestrator_start webhook on the very first init
                        if isinstance(message, SystemMessage) and getattr(message, "subtype", "") == "init":
                            if not first_init_sent:
                                post_webhook(config, "orchestrator_start", {
                                    "run_id": f"run-{run_id}",
                                    "mode": "agent-teams",
                                })
                                first_init_sent = True

                        handle_stream_event(message, config, run_id, log_file=log_file, state=stream_state)

                        # Check result for completion
                        if isinstance(message, ResultMessage):
                            result_text = getattr(message, "result", "")
                            if _is_work_complete(result_text):
                                work_complete = True

                    if work_complete:
                        logger.info("Agent Teams orchestration completed for %s", repo)
                        break

                    # Brief pause before re-entry
                    await asyncio.sleep(15)

            if not work_complete:
                logger.warning(
                    "Orchestrator exhausted %d re-entries for %s without completion",
                    max_reentries, repo,
                )

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
