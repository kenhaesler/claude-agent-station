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
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import httpx

from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.types import (
    AgentDefinition,
    AssistantMessage,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TextBlock,
    ToolUseBlock,
)

from agent.audit_hook import (
    make_audited_policy,
    make_post_tool_hook,
    make_pre_tool_hook,
)
from agent.auto_mode import AutonomyLevel, _coerce_level
from agent.run_control import (
    OrchestratorStopRequested,
    drain_pending_controls,
    set_run_paused,
)
from agent.vision_analyst import _ensure_workspace

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


async def _user_prompt_stream(text: str):
    """Wrap a string prompt as the AsyncIterable form the SDK requires when
    can_use_tool is supplied. Yields one user message then ends, matching
    the dict shape the SDK uses internally for string prompts.
    """
    yield {
        "type": "user",
        "session_id": "",
        "message": {"role": "user", "content": text},
        "parent_tool_use_id": None,
    }


SKIP_LABELS = frozenset({
    "autonomous-agent/in-progress",
    "autonomous-agent/needs-help",
    "NO AI",
    "backlog",
    "wontfix",
    "vision-suggested",  # Hook 3: proposed by vision_analyst, awaits human acceptance
})

# Priority label ordering for deterministic assignment
PRIORITY_ORDER = {
    "priority/critical": 0,
    "priority/high": 1,
    "priority/medium": 2,
    "priority/low": 3,
}


def priority_key(issue: dict) -> int:
    """Return the priority rank for an issue (lower = higher priority)."""
    for label in issue.get("labels", []) or []:
        name = label.get("name", "")
        if name in PRIORITY_ORDER:
            return PRIORITY_ORDER[name]
    return len(PRIORITY_ORDER)  # unlabeled = lowest


from agent.vision import load_vision  # noqa: E402
from agent.vision_scoring import score_issues_against_vision  # noqa: E402


def _combined_rank_issues(
    issues: list[dict],
    vision: dict | None,
    weight: float,
    model: str,
) -> list[dict]:
    """Combine label-priority and vision-alignment into a single sort.

    No vision (or weight=0) → pure priority. Returns issues with
    vision_score / vision_reason fields (0.5 / "" when no vision).
    """
    N = len(PRIORITY_ORDER)  # number of priority labels
    if not issues:
        return issues

    if vision is None or weight <= 0:
        scored = [{**i, "vision_score": 0.5, "vision_reason": ""} for i in issues]
        weight = 0.0
    else:
        scored = score_issues_against_vision(issues, vision, model)

    def combined(issue: dict) -> float:
        # priority_label_rank: 0=critical … N-1=unlabeled. Convert to score:
        # 1.0 for critical, 0.0 for unlabeled.
        rank = priority_key(issue)  # 0..N (or N if no label)
        prio_score = 1.0 - (min(rank, N - 1) / max(N - 1, 1))
        v = float(issue.get("vision_score", 0.5))
        return prio_score * (1.0 - weight) + v * weight

    return sorted(scored, key=combined, reverse=True)


# ── Configuration ──────────────────────────────────────────────

def load_config(config_file: str) -> dict:
    """Load manager-config.json."""
    with open(config_file) as f:
        return json.load(f)


def get_limit(config: dict, key: str, default: int) -> int:
    return config.get("limits", {}).get(key, default)


def get_model(config: dict, key: str, default: str) -> str:
    return config.get("models", {}).get(key, default)


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
    model = meta.get("model", "") or None

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
    eligible.sort(key=priority_key)
    return eligible[:limit]


def has_open_vision_proposals(repo: str) -> bool:
    """True if any open issue carries the `vision-suggested` label.

    Used by Trigger A to skip dispatch when prior proposals are pending.
    A `gh` failure returns False — fail-safe; we'd rather miss a dispatch
    than spam the operator.
    """
    cmd = [
        "gh", "issue", "list",
        "--repo", repo,
        "--state", "open",
        "--label", "vision-suggested",
        "--limit", "1",
        "--json", "number,labels",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            logger.warning("has_open_vision_proposals: gh failed: %s", result.stderr.strip())
            return False
        return len(json.loads(result.stdout or "[]")) > 0
    except Exception as exc:
        logger.warning("has_open_vision_proposals: %s", exc)
        return False


def dispatch_vision_bootstrap(project_id: int) -> str:
    """Trigger a vision-analyst run for ``project_id``.

    Tries the in-container launcher first (compose-mode path) and falls
    back to spawning the worker via subprocess (systemd-mode path or any
    failure where the launcher is unreachable).

    Returns one of:
      - "dispatched"      — analyst was started
      - "already-running" — launcher reported 409
    """
    launcher_url = os.environ.get(
        "STATION_AGENT_LAUNCHER_URL", "http://localhost:8421",
    ).rstrip("/")
    token = os.environ.get("STATION_LAUNCHER_TOKEN", "")
    headers = {"X-Launcher-Token": token} if token else {}

    try:
        resp = httpx.post(
            f"{launcher_url}/vision-analyst",
            params={"project_id": project_id},
            headers=headers,
            timeout=5.0,
        )
        if resp.status_code == 409:
            logger.info("vision-analyst already running (409)")
            return "already-running"
        if 200 <= resp.status_code < 300:
            return "dispatched"
        logger.warning(
            "launcher /vision-analyst returned %s: %s",
            resp.status_code, resp.text[:200],
        )
    except httpx.RequestError as exc:
        logger.info("launcher unreachable (%s); falling back to subprocess", exc)

    # Fallback: spawn the worker directly. No cross-process lock; best
    # effort. Reached on systemd path (no launcher), connection failure,
    # OR an unexpected launcher status code (e.g. 401/500) — we'd
    # rather over-deliver than silently fail.
    subprocess.Popen(
        ["python", "-m", "agent.vision_analyst", "--project-id", str(project_id)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    return "dispatched"


def handle_empty_backlog(
    config: dict,
    repo: str,
    project_id: int | None,
    workspace: str,
    run_id: str,
) -> str:
    """Decide what to do when a project's backlog is empty.

    Returns the skip_reason string. Side-effects:
      - posts a `finished` webhook for the regular run with skip_reason
      - dispatches the vision_analyst when conditions match (Trigger A)
    """
    has_vision = os.path.isfile(os.path.join(workspace, "docs", "vision.md"))
    proposals_pending = has_vision and has_open_vision_proposals(repo)

    if not has_vision:
        skip_reason = "no-eligible-issues-no-vision"
    elif proposals_pending:
        skip_reason = "no-eligible-issues-proposals-pending"
    elif project_id is None:
        # Can't dispatch without a project_id (manager-config drift).
        skip_reason = "no-eligible-issues-no-vision"
    else:
        outcome = dispatch_vision_bootstrap(project_id)
        skip_reason = (
            "no-eligible-issues-bootstrap-dispatched"
            if outcome == "dispatched"
            else "no-eligible-issues-bootstrap-already-running"
        )

    post_webhook(config, "finished", {
        "run_id": f"run-{run_id}",
        "project": repo,
        "mode": "agent-teams",
        "status": "completed",
        "skip_reason": skip_reason,
    })
    logger.info("Empty backlog for %s: %s", repo, skip_reason)
    return skip_reason


# ── Team Prompt Construction ──────────────────────────────────

TEAMMATE_ROLES = ["backend", "frontend", "qa"]


def build_team_prompt(
    repo: str,
    issues: list[dict],
    config: dict,
    run_id: str,
    workspace: str = "",
    worktree_paths: dict[str, str] | None = None,
    vision: dict | None = None,
) -> str:
    """Build the lead agent prompt that creates and manages the team."""
    issue_entries = []
    for issue in issues:
        labels_str = ", ".join(l.get("name", "") for l in issue.get("labels", []))
        why = issue.get("vision_reason", "")
        line = f"- **#{issue['number']}**: {issue.get('title', 'Untitled')}"
        if labels_str:
            line += f" [{labels_str}]"
        if why:
            line += f"\n    *Why this advances the vision:* {why}"
        issue_entries.append(line)
    issue_list = "\n".join(issue_entries)

    max_turns = get_limit(config, "max_employee_turns", 200)
    teammate_model = get_model(config, "employee", "claude-opus-4-7")

    # Determine base branch (always use integration dev branch)
    integration = config.get("integration", {})
    base_branch = integration.get("dev_branch", "autonomous/dev")

    # Build worktree assignment section
    wt_section = ""
    if worktree_paths:
        wt_lines = [f"- **{role}** specialist → `{path}`" for role, path in worktree_paths.items()]
        wt_section = "\n".join(wt_lines)

    vision_section = ""
    if vision is not None:
        non_goals = (vision.get("non_goals") or "").strip() or "_(not specified)_"
        anti_patterns = (vision.get("anti_patterns") or "").strip() or "_(not specified)_"
        # Resolve webhook URL with the same precedence as post_webhook():
        # STATION_WEBHOOK_URL env (set by compose) → config dashboard.webhook_url
        # → localhost default for systemd. Hardcoding "http://dashboard:8420"
        # only resolves on the compose network and silently breaks Hook 2 on
        # systemd-mode deployments.
        webhook_url = os.environ.get("STATION_WEBHOOK_URL") or config.get(
            "dashboard", {}
        ).get("webhook_url", "http://127.0.0.1:8420/api/webhook/run-event")
        vision_section = f"""
## Vision check (when reviewing teammate plans)

This project has a vision. Before approving ANY teammate plan, verify the
plan does not violate the non-goals or anti-patterns below. If it does:

1. Reject the plan with a specific quote from the violated section.
2. Apply label `autonomous-agent/needs-help` to the issue:
   `gh issue edit <number> --add-label autonomous-agent/needs-help`
3. POST a misalignment event to the dashboard:
   `curl -s -X POST {webhook_url} \\
       -H "Content-Type: application/json" \\
       -d '{{"event":"vision_misalignment","run_id":"run-{run_id}",
            "issue_number":<number>,"violated_section":"<non_goals|anti_patterns>",
            "quote":"<exact quote>","plan_excerpt":"<short excerpt>"}}'`
4. Reassign the teammate to a different task or stop them.

### Vision — Non-goals
{non_goals}

### Vision — Anti-patterns
{anti_patterns}

(Full vision available at `{workspace}/docs/vision.md` if you need other context.)
"""

    return f"""You are the lead of an agent team implementing GitHub issues for **{repo}**.

## Your Workflow

1. **Create a team** called "{repo.split('/')[-1]}-{run_id[:8]}"
2. **Analyze all issues** and decompose them into granular tasks (research, implement, test, review)
3. **Create tasks** on the shared task list with dependencies and specialization tags
4. **Spawn 3 specialized teammates** using the `issue-worker` agent type:
   - **Backend specialist** — Python/FastAPI, database, API changes
   - **Frontend specialist** — Svelte/TypeScript, UI components, CSS
   - **QA specialist** — writes tests, validates implementations, runs linters
5. **Require plan approval** before any teammate starts implementation
6. Review plans — reject if they conflict with another teammate's work
7. **Actively monitor** teammates until ALL tasks are completed (see monitoring rules)
8. After all work is done, **synthesize a final JSON summary**

## Narration (MANDATORY — ends operator silence)

**Before every single tool call**, emit one short present-tense sentence of plain text
describing what you are about to do and why. Eight to twenty words. No headings, no
markdown, no lists. One sentence, then the tool call.

Good: "Checking whether the backend teammate has written a report yet so I can move on."
Good: "Sleeping 60 seconds to let teammates make progress before the next status sweep."
Bad: (silent tool call), "Now I will...", multi-paragraph explanations, JSON dumps.

This narration is surfaced on the operator's Bridge so they can follow your reasoning
in real time. Silent tool calls break their trust in the system. Never skip this.

## Issues to Work On ({len(issues)} total)

{issue_list}

Decompose these into specific tasks. A single issue may require tasks from multiple specialists.
For example, a bug fix might need: "research the bug" (any), "implement backend fix" (backend),
"update UI error handling" (frontend), "write regression test" (qa).

## Teammate Worktrees (ISOLATED — one per specialist)

Each teammate MUST work in their assigned worktree. Tell each teammate their path at spawn time.

{wt_section}

When spawning a teammate, include in their prompt:
"Your worktree is at <path>. Run `cd <path>` as your FIRST action before doing anything else."

## Teammate Configuration

- Agent type: `issue-worker`
- Model: `{teammate_model}`
- Max turns: {max_turns}
- Teammates must commit locally and push their branch — NEVER push to main
- Each teammate works in their own isolated git worktree (paths above)

## Communication Rules

- Teammates can and SHOULD message each other directly for coordination
- If a teammate reports a blocker, help them or reassign to another specialist
- When one teammate completes work another depends on, ensure they notify each other
- If a task turns out to need a different specialty, create a sub-task and message the right teammate

## CRITICAL: Active Monitoring Rules

After spawning teammates, you MUST actively monitor their progress using tool calls.
**NEVER end your turn while any teammate is still working.**

Follow this monitoring loop:
1. After spawning all teammates, run: `sleep 60` (Bash tool)
2. Check for completed reports: `find {workspace} -name ".claude-employee-report*.json" -type f 2>/dev/null`
3. For each report found, read it and record the status
4. If any teammate has not yet reported, **go back to step 1**
5. Only end your turn and provide the final JSON summary AFTER:
   - All tasks on the shared task list are completed, OR
   - 20 minutes have elapsed since spawning (timeout for remaining)

**Why this matters**: If you say "I'm waiting" and end your turn, the session terminates
and your teammates lose their work. You must keep making tool calls to stay alive.

## Rules

- Spawn exactly 3 teammates (backend, frontend, qa)
- Multiple teammates may contribute to the same issue — that's expected
- If two teammates need to modify the same file, coordinate via task dependencies
- After all work is done, provide a JSON summary with:
  - issues_completed: list of issue numbers
  - issues_failed: list of issue numbers with reasons
  - tasks_completed: count of tasks completed
  - total_turns: sum across all teammates
  - conflicts_detected: any file conflicts found

## Environment

- Repository: {repo}
- Run ID: {run_id}
- Workspace: {workspace}
- Base branch: `{base_branch}` (teammates must branch FROM this)
- GH_TOKEN is available for GitHub CLI operations
{vision_section}"""


def build_followup_prompt(
    workspace: str,
    operator_messages: list[str] | None = None,
) -> str:
    """Build a follow-up prompt for re-entering the lead agent session.

    When ``operator_messages`` is non-empty, they are prepended as high-priority
    guidance from the human operator. Use this to inject Mission Control
    messages captured while the previous iteration was running.
    """
    header = ""
    if operator_messages:
        joined = "\n\n".join(f"> {m}" for m in operator_messages if m.strip())
        if joined:
            header = (
                "━━━ OPERATOR MESSAGES (received during your last turn) ━━━\n"
                f"{joined}\n"
                "━━━ Acknowledge these and adjust your plan if needed. ━━━\n\n"
            )
    return header + (
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
                "total_tokens": _usage_val(message.usage, "total_tokens", 0),
                "tool_uses": _usage_val(message.usage, "tool_uses", 0),
                "duration_ms": _usage_val(message.usage, "duration_ms", 0),
            }

    elif isinstance(message, TaskNotificationMessage):
        result["type"] = "system"
        result["subtype"] = "task_notification"
        result["task_id"] = message.task_id
        result["status"] = message.status
        result["summary"] = message.summary
        if message.usage:
            result["usage"] = {
                "total_tokens": _usage_val(message.usage, "total_tokens", 0),
                "tool_uses": _usage_val(message.usage, "tool_uses", 0),
                "duration_ms": _usage_val(message.usage, "duration_ms", 0),
            }

    elif isinstance(message, SystemMessage):
        result["type"] = "system"
        result["subtype"] = getattr(message, "subtype", "")
        sid = getattr(message, "session_id", None) if hasattr(message, "session_id") else None
        if sid:
            result["session_id"] = sid

    return result


def _apply_controls(
    full_run_id: str,
    config: dict,
    pending_messages: list[str],
    flags: dict[str, bool],
) -> None:
    """Drain the run_controls queue for this run and apply each action.

    - pause/resume flip the per-run pause flag (the policy engine reads it).
    - stop sets flags['stop']; the caller breaks out of the SDK stream.
    - message accumulates operator text for the next followup prompt.

    All actions emit a webhook so the dashboard timeline shows the
    intervention alongside agent activity. Never raises.

    NOTE: This synchronous version is retained for the startup drain (called
    before any iteration begins) and for tests. The main runtime path now
    uses :func:`_control_poll_loop` running as a dedicated asyncio task so
    controls are picked up within ~1s even during long tool calls when no
    SDK messages are flowing.
    """
    rows = drain_pending_controls(full_run_id)
    if not rows:
        return
    for row in rows:
        action = row.action
        if action == "pause":
            set_run_paused(full_run_id, True)
            logger.info("Mission Control: run paused by %s", row.requested_by or "operator")
            post_webhook(config, "run_paused", {
                "run_id": full_run_id,
                "requested_by": row.requested_by,
                "control_id": row.id,
            })
        elif action == "resume":
            set_run_paused(full_run_id, False)
            logger.info("Mission Control: run resumed by %s", row.requested_by or "operator")
            post_webhook(config, "run_resumed", {
                "run_id": full_run_id,
                "requested_by": row.requested_by,
                "control_id": row.id,
            })
        elif action == "stop":
            flags["stop"] = True
            logger.info("Mission Control: stop requested by %s", row.requested_by or "operator")
            post_webhook(config, "run_stop_requested", {
                "run_id": full_run_id,
                "requested_by": row.requested_by,
                "control_id": row.id,
            })
        elif action == "message":
            text = ""
            if isinstance(row.payload, dict):
                text = str(row.payload.get("text") or "").strip()
            if text:
                pending_messages.append(text)
                logger.info(
                    "Mission Control: queued operator message (%d chars) from %s",
                    len(text), row.requested_by or "operator",
                )
                post_webhook(config, "run_message_queued", {
                    "run_id": full_run_id,
                    "requested_by": row.requested_by,
                    "control_id": row.id,
                    "text": text[:500],
                })
        else:
            logger.warning("Mission Control: unknown action %r (id=%d)", action, row.id)


async def _control_poll_loop(
    full_run_id: str,
    config: dict,
    pending_messages: list[str],
    flags: dict[str, bool],
    *,
    interval: float = 1.0,
) -> None:
    """Dedicated asyncio task that drains run_controls every ``interval``
    seconds for the lifetime of the run. Runs concurrently with the SDK
    stream loop so operator interventions are picked up even when no SDK
    messages are flowing (long tool calls, idle waits, API stalls).

    Cancellation is the only way this coroutine exits — the caller cancels
    it in a ``finally:`` block when the run ends. We swallow CancelledError
    so the cleanup path doesn't log a traceback.

    SQLite access in :func:`drain_pending_controls` is synchronous; we call
    it directly on the event loop because the drain is cheap (<5ms for the
    empty case, which is 99% of ticks) and wrapping in run_in_executor adds
    more latency than it saves. If drain latency ever becomes a problem,
    switch to ``asyncio.to_thread``.
    """
    logger.info("Mission Control: control poll task started for %s (interval=%.1fs)",
                full_run_id, interval)
    try:
        while True:
            try:
                _apply_controls(full_run_id, config, pending_messages, flags)
            except Exception as exc:  # pragma: no cover — never crash the poll loop
                logger.warning("Mission Control: control poll tick failed: %s", exc)
            # Exit fast once stop is latched so the stream loop doesn't have
            # to wait a full tick for the task to notice.
            if flags.get("stop"):
                logger.info("Mission Control: control poll task exiting (stop latched)")
                return
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.debug("Mission Control: control poll task cancelled for %s", full_run_id)
        raise


def post_webhook(config: dict, event: str, data: dict | None = None) -> None:
    """Send an event to the dashboard webhook (best-effort).

    URL precedence: ``STATION_WEBHOOK_URL`` env (set by compose so the agent
    container reaches the dashboard service by name), then config-file
    ``dashboard.webhook_url``, then a localhost default for systemd hosts.
    """
    webhook_url = os.environ.get("STATION_WEBHOOK_URL") or config.get("dashboard", {}).get(
        "webhook_url", "http://127.0.0.1:8420/api/webhook/run-event"
    )
    # urllib.request honors file://, ftp://, etc. — restrict to http/https so a
    # misconfigured env or config can't be coerced into reading local files.
    if not webhook_url.startswith(("http://", "https://")):
        logger.warning("Refusing webhook URL with unsupported scheme: %s", webhook_url)
        return
    webhook_secret = os.environ.get("STATION_WEBHOOK_SECRET", "") or config.get(
        "dashboard", {}
    ).get("webhook_secret", "")

    payload = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if data:
        payload.update(data)

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if webhook_secret:
        headers["X-Webhook-Token"] = webhook_secret

    # httpx (vs. urllib.request) refuses file:// and other local schemes, so
    # even without the explicit guard above we cannot be tricked into reading
    # local files via a misconfigured webhook_url.
    try:
        with httpx.Client(timeout=3.0) as client:
            client.post(webhook_url, content=json.dumps(payload), headers=headers)
    except Exception:
        pass  # Best-effort


def _usage_val(usage, key: str, default=0):
    """Safely get a usage field whether usage is a dict or object."""
    if usage is None:
        return default
    if isinstance(usage, dict):
        return usage.get(key, default)
    return getattr(usage, key, default)


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
        # Walk content blocks: narrate text, count tool calls.
        #
        # Phase 1 of "The Bridge": text blocks immediately before a tool_use
        # are emitted as `narration` webhooks so the operator sees the lead's
        # stated intent in real time. The lead's prompt asks for one
        # present-tense sentence; we cap at 500 chars for safety.
        #
        # Block-type discrimination: the SDK delivers ``TextBlock`` /
        # ``ToolUseBlock`` dataclass instances (see
        # ``claude_agent_sdk._internal.message_parser``) — these have no
        # ``.type`` attribute, so ``isinstance`` is the only correct check.
        # The dict fallback is kept for raw-passthrough cases.
        if message.content:
            pending_narration: str | None = None
            for block in (message.content if isinstance(message.content, list) else [message.content]):
                if isinstance(block, TextBlock):
                    text = block.text
                    if text and text.strip():
                        pending_narration = text.strip()
                elif isinstance(block, ToolUseBlock):
                    if state:
                        state.tool_calls += 1
                    logger.info("Lead agent tool call: %s", block.name)
                    if pending_narration:
                        post_webhook(config, "narration", {
                            "run_id": f"run-{run_id}",
                            "agent_name": "Lead",
                            "narration": pending_narration[:500],
                            "narration_kind": "directive",
                        })
                        pending_narration = None
                elif isinstance(block, dict):
                    bt = block.get("type")
                    if bt == "text":
                        text = block.get("text")
                        if text and text.strip():
                            pending_narration = text.strip()
                    elif bt == "tool_use":
                        if state:
                            state.tool_calls += 1
                        logger.info("Lead agent tool call: %s", block.get("name"))
                        if pending_narration:
                            post_webhook(config, "narration", {
                                "run_id": f"run-{run_id}",
                                "agent_name": "Lead",
                                "narration": pending_narration[:500],
                                "narration_kind": "directive",
                            })
                            pending_narration = None
            # Flush trailing narration (lead spoke but no tool followed)
            if pending_narration:
                post_webhook(config, "narration", {
                    "run_id": f"run-{run_id}",
                    "agent_name": "Lead",
                    "narration": pending_narration[:500],
                    "narration_kind": "directive",
                })
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
        post_webhook(config, "narration", {
            "run_id": f"run-{run_id}",
            "agent_name": "Lead",
            "narration": f"Spawning teammate: {(message.description or message.task_id)[:300]}",
            "narration_kind": "system",
        })

    elif isinstance(message, TaskProgressMessage):
        if state and message.usage:
            state.turns = _usage_val(message.usage, "tool_uses", 0)
        logger.info(
            "Teammate progress: task=%s tools=%s last=%s",
            message.task_id,
            _usage_val(message.usage, "tool_uses", "?"),
            message.last_tool_name,
        )
        post_webhook(config, "teammate_progress", {
            "run_id": f"run-{run_id}",
            "task_id": message.task_id,
            "agent_name": message.last_tool_name or "",
            "tokens_total": _usage_val(message.usage, "total_tokens", 0) if message.usage else 0,
            "turns": _usage_val(message.usage, "tool_uses", 0) if message.usage else 0,
        })
        if message.last_tool_name:
            post_webhook(config, "narration", {
                "run_id": f"run-{run_id}",
                "agent_name": f"Teammate {message.task_id}",
                "narration": f"Running {message.last_tool_name}",
                "narration_kind": "step",
            })

    elif isinstance(message, TaskNotificationMessage):
        logger.info("Teammate finished: task=%s status=%s", message.task_id, message.status)
        post_webhook(config, "teammate_completed", {
            "run_id": f"run-{run_id}",
            "task_id": message.task_id,
            "status": message.status,
            "agent_name": message.summary[:100] if message.summary else "",
            "tokens_total": _usage_val(message.usage, "total_tokens", 0) if message.usage else 0,
            "turns": _usage_val(message.usage, "tool_uses", 0) if message.usage else 0,
        })
        summary_text = (message.summary or "").strip()
        post_webhook(config, "narration", {
            "run_id": f"run-{run_id}",
            "agent_name": f"Teammate {message.task_id}",
            "narration": f"Finished ({message.status})" + (f": {summary_text[:300]}" if summary_text else ""),
            "narration_kind": "step",
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
            employee_override = get_model(config, "employee", "")
            if employee_override and employee_override != worker_def.model:
                logger.info(
                    "Overriding teammate model from config: %s (was %s)",
                    employee_override, worker_def.model,
                )
                worker_def = replace(worker_def, model=employee_override)
            agents_dict = {worker_name: worker_def}
            logger.info("Loaded agent definition: %s from %s (model=%s)", worker_name, worker_file, worker_def.model)
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
        project_branch = project.get("branch") or "main"

        # Refresh the workspace to the tip of the project's default branch
        # before deciding eligibility. Without this, persistent compose
        # volumes keep stale checkouts that hide newly-committed
        # docs/vision.md files (issue #271). Best-effort: clone-if-missing
        # plus fetch+reset; failures are logged but non-fatal.
        try:
            _ensure_workspace(workspace, repo, project_branch)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "workspace refresh for %s failed: %s",
                workspace, exc,
            )

        # Resolve autonomy per ADR-0001. Level comes from project config
        # (falls back to config-level default, then to 'assisted'). The
        # policy engine is enforced via can_use_tool on ClaudeAgentOptions;
        # every decision is appended to agent_events by the audit hook.
        #
        # We intentionally leave permission_mode='default' at every level
        # so can_use_tool is always consulted — that's what keeps the
        # ALWAYS_DENY list and the audit trail in force even under 'auto'.
        default_level = config.get("autonomy", {}).get("default_level", "assisted")
        autonomy_level = _coerce_level(project.get("autonomy_level") or default_level)
        max_budget_usd = project.get("max_budget_usd")

        logger.info(
            "Processing project: %s (autonomy=%s, budget=%s)",
            repo, autonomy_level.value, max_budget_usd,
        )

        # Fetch and filter issues
        issues = fetch_eligible_issues(repo, max_per_project, workspace)
        if not issues:
            handle_empty_backlog(
                config=config,
                repo=repo,
                project_id=project.get("id"),
                workspace=workspace,
                run_id=run_id,
            )
            continue

        # Hook 1: vision-aware prioritisation
        vision = load_vision(workspace)
        weight = float((config.get("vision") or {}).get("scoring_weight", 0.4))
        analyst_model = get_model(config, "analyst", "claude-sonnet-4-6")
        issues = _combined_rank_issues(issues, vision=vision, weight=weight, model=analyst_model)

        if vision is not None:
            for issue in issues:
                logger.info(
                    "Picked #%s (vision_score=%.2f): %s",
                    issue["number"], issue.get("vision_score", 0.5), issue.get("vision_reason", ""),
                )

        logger.info(
            "Found %d eligible issues for %s: %s",
            len(issues), repo, [f"#{i['number']}" for i in issues],
        )

        # Determine base branch for worktrees
        integration = config.get("integration", {})
        base_branch = integration.get("dev_branch", "autonomous/dev")

        # Ensure base branch exists locally
        subprocess.run(
            ["git", "fetch", "origin"],
            cwd=workspace, capture_output=True, timeout=30,
        )
        # Try checking out base branch; create if missing
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"origin/{base_branch}"],
            cwd=workspace, capture_output=True,
        )
        if result.returncode != 0:
            subprocess.run(
                ["git", "checkout", "-b", base_branch],
                cwd=workspace, capture_output=True,
            )
        else:
            subprocess.run(
                ["git", "checkout", base_branch],
                cwd=workspace, capture_output=True,
            )
            subprocess.run(
                ["git", "pull", "origin", base_branch],
                cwd=workspace, capture_output=True,
            )

        # Create one worktree per teammate role
        worktree_paths: dict[str, str] = {}
        for role in TEAMMATE_ROLES:
            wt_path = os.path.join(workspaces_dir, f"{repo_name}-{role}")
            if os.path.isdir(wt_path):
                # Clean up stale worktree
                subprocess.run(
                    ["git", "worktree", "remove", "--force", wt_path],
                    cwd=workspace, capture_output=True,
                )
            wt_branch = f"worktree/{role}-{run_id[:8]}"
            # Delete stale branch if it exists
            subprocess.run(
                ["git", "branch", "-D", wt_branch],
                cwd=workspace, capture_output=True,
            )
            result = subprocess.run(
                ["git", "worktree", "add", "-b", wt_branch, wt_path, base_branch],
                cwd=workspace, capture_output=True, text=True,
            )
            if result.returncode == 0:
                worktree_paths[role] = wt_path
                logger.info("Created worktree for %s: %s", role, wt_path)
            else:
                logger.warning("Failed to create worktree for %s: %s", role, result.stderr.strip())

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
        # Mission Control: operator messages captured mid-stream, flushed
        # into the followup prompt on the next iteration.
        pending_operator_messages: list[str] = []
        full_run_id = f"run-{run_id}"
        # Mutable box so _apply_control can signal stop back to the loop.
        control_flags = {"stop": False}
        control_task: asyncio.Task | None = None

        try:
            logger.info("Starting Agent Teams lead for %s (%d issues, model=%s)", repo, len(issues), manager_model)
            # Mission Control: kick off the dedicated control-polling task
            # now so operator actions are applied within ~1s for the entire
            # lifetime of the run — not just at iteration boundaries and SDK
            # message boundaries, which can be 30+ seconds apart during long
            # tool calls. The task is cancelled in the outer finally block.
            control_task = asyncio.create_task(
                _control_poll_loop(
                    full_run_id, config,
                    pending_operator_messages, control_flags,
                    interval=1.0,
                ),
                name=f"mission-control-{full_run_id}",
            )

            with open(stream_log_path, "a") as log_file:
                for iteration in range(max_reentries):
                    is_followup = iteration > 0

                    # Early exit if the background poller already latched stop
                    # (e.g. operator clicked Stop before the first iteration).
                    if control_flags["stop"]:
                        logger.info("Stop requested before iteration %d", iteration + 1)
                        break

                    if is_followup:
                        prompt = build_followup_prompt(
                            workspace,
                            operator_messages=pending_operator_messages,
                        )
                        pending_operator_messages.clear()
                        logger.info(
                            "Re-entering lead session (iteration %d/%d, session=%s)",
                            iteration + 1, max_reentries, session_id,
                        )
                    else:
                        prompt = build_team_prompt(repo, issues, config, run_id, workspace, worktree_paths, vision=vision)

                    # Build options — use resume for follow-up iterations.
                    # Auto Mode (ADR-0001) is wired here: can_use_tool runs
                    # the policy engine and records every decision to
                    # agent_events (event_type='auto_mode_decision').
                    options = ClaudeAgentOptions(
                        cwd=workspace,
                        env={
                            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
                            "GITHUB_REPO": repo,
                        },
                        mcp_servers={
                            "playwright": {
                                "command": "npx",
                                "args": ["-y", "@playwright/mcp@latest"],
                            },
                            "ref": {
                                "url": "https://api.ref.tools/mcp",
                            },
                        },
                        allowed_tools=["Read", "Bash", "Glob", "Grep", "Edit", "Write", "Agent", "mcp__playwright__*", "mcp__ref__*"],
                        max_turns=manager_turns,
                        model=manager_model,
                        agents=agents_dict,
                        can_use_tool=make_audited_policy(
                            run_id=f"run-{run_id}",
                            level=autonomy_level,
                            agent_id="lead",
                        ),
                        # Issue #73: per-tool-call audit_log telemetry.
                        # Pre-hook writes a 'started' row keyed by SDK tool_use_id;
                        # Post-hook updates the same row with status + tails.
                        hooks={
                            "PreToolUse": [HookMatcher(hooks=[
                                make_pre_tool_hook(
                                    run_id=f"run-{run_id}",
                                    actor="lead",
                                    trace_id=f"run-{run_id}",
                                ),
                            ])],
                            "PostToolUse": [HookMatcher(hooks=[
                                make_post_tool_hook(
                                    run_id=f"run-{run_id}",
                                    actor="lead",
                                ),
                            ])],
                        },
                        max_budget_usd=max_budget_usd,
                    )
                    if is_followup and session_id:
                        options.resume = session_id
                        options.continue_conversation = True

                    async for message in query(prompt=_user_prompt_stream(prompt), options=options):
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

                        # The background control poll task is already running;
                        # we only need to check the stop flag here to break
                        # out of the stream loop as soon as it latches.
                        if control_flags["stop"]:
                            logger.info("Stop requested; breaking SDK stream")
                            break

                        # Check result for completion
                        if isinstance(message, ResultMessage):
                            result_text = getattr(message, "result", "")
                            if _is_work_complete(result_text):
                                work_complete = True

                    if control_flags["stop"]:
                        raise OrchestratorStopRequested()

                    if work_complete:
                        logger.info("Agent Teams orchestration completed for %s", repo)
                        break

                    # Brief pause before re-entry. The control task keeps
                    # running during this sleep so a mid-idle stop/message
                    # is picked up immediately.
                    await asyncio.sleep(15)

            if not work_complete and not control_flags["stop"]:
                logger.warning(
                    "Orchestrator exhausted %d re-entries for %s without completion",
                    max_reentries, repo,
                )

        except OrchestratorStopRequested:
            logger.info("Agent Teams orchestration interrupted by operator for %s", repo)
            post_webhook(config, "orchestrator_complete", {
                "run_id": f"run-{run_id}",
                "is_error": False,
                "duration_ms": 0,
                "num_turns": stream_state.turns,
                "status": "interrupted",
            })
            # Ensure the run record flips to 'interrupted' via the webhook
            # lifecycle handler; also clear any stale pause flag.
            set_run_paused(f"run-{run_id}", False)

        except Exception as e:
            logger.exception("Agent Teams orchestration failed for %s: %s", repo, e)
            post_webhook(config, "orchestrator_error", {
                "run_id": f"run-{run_id}",
                "project": repo,
                "error": str(e)[:500],
            })
            exit_code = 1
        finally:
            # Always stop the background control task before anything else so
            # it can't race with cleanup (worktree removal, next project).
            if control_task is not None and not control_task.done():
                control_task.cancel()
                try:
                    await control_task
                except (asyncio.CancelledError, Exception):
                    pass

            # Clean up worktrees
            for role, wt_path in worktree_paths.items():
                if os.path.isdir(wt_path):
                    result = subprocess.run(
                        ["git", "worktree", "remove", "--force", wt_path],
                        cwd=workspace, capture_output=True, text=True,
                    )
                    if result.returncode == 0:
                        logger.info("Cleaned up worktree for %s: %s", role, wt_path)
                    else:
                        logger.warning("Failed to clean up worktree %s: %s", wt_path, result.stderr.strip())

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
