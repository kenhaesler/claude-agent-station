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
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import httpx

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from claude_agent_sdk.types import (
    AgentDefinition,
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from sqlalchemy import select

from agent.audit_hook import (
    make_audited_policy,
    write_audit_finished_from_block,
    write_audit_started_from_block,
)
from agent.auto_mode import AutonomyLevel, _coerce_level
from agent.run_control import (
    OrchestratorStopRequested,
    drain_pending_controls,
    set_run_paused,
)
from agent.tools.run_complete import (
    RunCompleteInput,
    build_run_complete_server,
)
from agent.vision_analyst import _ensure_workspace
from agent.webhook_emitter import emit
from pydantic import ValidationError as _PydanticValidationError

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
    # The lead orchestrator's session_id, captured from the first
    # SystemMessage(subtype="init"). Used to filter ResultMessages so
    # teammate sub-session results don't trigger a premature
    # orchestrator_complete webhook. See #371.
    main_session_id: str | None = None
    # #385: Latched when the lead calls the RunComplete SDK tool. None until
    # the tool fires; once set, handle_stream_event suppresses the legacy
    # ResultMessage-driven orchestrator_complete emission, and the inner
    # orchestrate loop breaks at the next iteration boundary.
    run_complete_payload: dict | None = None
    # #385 fallback path: True after the first time the prose-matching
    # _is_work_complete heuristic fires on this run. Keeps the
    # "lead did not call RunComplete" WARNING log fire-once-per-run so
    # operators get the signal without log spam on long runs.
    fallback_warning_logged: bool = False



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


# ── Queue Draining (issue #266 follow-up) ──────────────────────
#
# When the plan-review gate (or the operator override) approves a
# ``plan_only`` run, it enqueues a follow-up ``full`` ``QueueItem`` per
# approved plan. Without a consumer, those items sit forever. The
# orchestrator now drains them at the start of each per-project loop,
# bypassing the GitHub-issue-driven flow when there's pre-approved work
# pending.


@dataclass
class _ClaimedQueueItem:
    """A pending queue item we've claimed for this run.

    Bundles the DB row id with the issue dict (in the shape
    :func:`fetch_eligible_issues` returns) so the rest of the loop can
    treat it identically. Carries the per-item ``mode`` and any
    ``approved_plan_path`` from the gate's context for the spawn-prompt
    builder to surface.
    """
    queue_item_id: int
    queue_mode: str
    approved_plan_path: str | None
    issue: dict


async def claim_pending_queue_items(
    project_repo: str,
    full_run_id: str,
    *,
    max_items: int = 10,
) -> list[_ClaimedQueueItem]:
    """Atomically claim pending queue items for the project.

    Each pending ``QueueItem`` is transitioned to ``state='claimed'``
    with ``run_id`` bound to the current orchestrator run, then we
    ``gh issue view`` to fetch title/body/labels matching what
    :func:`fetch_eligible_issues` returns. Items whose underlying GitHub
    issue can't be read (closed, deleted, network blip) are released
    back to ``state='failed'`` with the error captured.

    Bypasses :data:`SKIP_LABELS` — items in the queue are operator- or
    manager-approved already, so re-filtering by label here would
    silently drop work the operator told us to do (vision-suggested
    issues being the obvious case after an APPROVE_PLAN).
    """
    from app.database import async_session
    from app.models import QueueItem

    claimed: list[_ClaimedQueueItem] = []
    async with async_session() as db:
        result = await db.execute(
            select(QueueItem)
            .where(
                QueueItem.project_repo == project_repo,
                QueueItem.state == "pending",
            )
            .order_by(QueueItem.id.asc())
            .limit(max_items)
        )
        items = list(result.scalars().all())
        if not items:
            return []

        for item in items:
            issue_data: dict | None = None
            error: str | None = None
            try:
                proc = subprocess.run(
                    ["gh", "issue", "view", str(item.issue_number),
                     "--repo", project_repo,
                     "--json", "number,title,body,labels"],
                    capture_output=True, text=True, timeout=15,
                )
                if proc.returncode == 0:
                    issue_data = json.loads(proc.stdout)
                else:
                    error = proc.stderr.strip() or f"gh exit {proc.returncode}"
            except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as exc:
                error = str(exc)

            if issue_data is None:
                logger.warning(
                    "Could not fetch queued issue #%s for %s: %s",
                    item.issue_number, project_repo, error,
                )
                item.state = "failed"
                item.error_message = error or "issue lookup failed"
                continue

            ctx: dict = {}
            if item.context:
                try:
                    ctx = json.loads(item.context)
                    if not isinstance(ctx, dict):
                        ctx = {}
                except json.JSONDecodeError:
                    ctx = {}

            item.state = "claimed"
            item.run_id = f"run-{full_run_id}"
            item.assigned_at = datetime.now(timezone.utc)

            issue_dict = {
                "number": issue_data.get("number"),
                "title": issue_data.get("title", ""),
                "body": issue_data.get("body", ""),
                "labels": [
                    {"name": (l.get("name") if isinstance(l, dict) else str(l))}
                    for l in (issue_data.get("labels") or [])
                ],
            }
            claimed.append(_ClaimedQueueItem(
                queue_item_id=item.id,
                queue_mode=(item.mode or "full"),
                approved_plan_path=ctx.get("approved_plan_path"),
                issue=issue_dict,
            ))

        await db.commit()

    return claimed


async def finalise_claimed_queue_items(
    items: list[_ClaimedQueueItem],
    *,
    outcome: str,
) -> None:
    """Mark previously-claimed items completed or failed at run end.

    ``outcome`` is the QueueItem state to land on:
    ``"completed"`` on a clean SDK run (regardless of per-issue verdict —
    that's tracked on the Run row), or ``"failed"`` when the run as a
    whole errored before producing useful work.

    Best-effort: lookup-failures are logged so a subsequent run can
    retry rather than wedge.
    """
    if not items:
        return
    from app.database import async_session
    from app.models import QueueItem

    async with async_session() as db:
        for c in items:
            row = await db.get(QueueItem, c.queue_item_id)
            if row is None:
                logger.warning("Queue item %s vanished during run", c.queue_item_id)
                continue
            row.state = outcome
        await db.commit()


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


def _resolve_project_id_by_repo(repo: str) -> int | None:
    """Look up the dashboard's Project.id by repo name. Returns None on
    any error (DB missing, no row, import failure).

    Defensive fallback: ``handle_empty_backlog`` needs a project_id to
    dispatch the vision_analyst. The orchestrator's config-loaded dict
    omits ``id`` (sync_db_to_config doesn't write it), so without this
    lookup an operator-edited config silently disables the analyst
    dispatch path. See diagnosis of run-20260512T112429Z."""
    try:
        import sqlite3
        db_path = os.environ.get(
            "STATION_DB_PATH",
            "/var/lib/claude-agent-station/station.db",
        )
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT id FROM projects WHERE repo = ?",
                (repo,),
            ).fetchone()
        return int(row[0]) if row else None
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("_resolve_project_id_by_repo(%s) failed: %s", repo, exc)
        return None


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

    # If the project_id wasn't carried through the config (sync_db_to_config
    # historically omitted it from the JSON), look it up from the DB by
    # repo. Without this fallback the analyst can never be dispatched on
    # operator-edited configs.
    if project_id is None:
        project_id = _resolve_project_id_by_repo(repo)

    if not has_vision:
        skip_reason = "no-eligible-issues-no-vision"
    elif proposals_pending:
        skip_reason = "no-eligible-issues-proposals-pending"
    elif project_id is None:
        # Distinct from the no-vision case: vision EXISTS but we can't
        # locate the project in the dashboard's DB to dispatch the analyst.
        # Surfacing a different skip_reason avoids the misleading
        # "no-vision" label that hid this bug for issue-bootstrapping.
        skip_reason = "no-eligible-issues-vision-but-no-project-id"
    else:
        outcome = dispatch_vision_bootstrap(project_id)
        skip_reason = (
            "no-eligible-issues-bootstrap-dispatched"
            if outcome == "dispatched"
            else "no-eligible-issues-bootstrap-already-running"
        )

    # Empty-backlog skip: mode is unknown here (no project context loaded
    # at this layer); leave as None so handle_finished doesn't overwrite an
    # earlier project-mode value with a stale "agent-teams" sentinel.
    post_webhook(config, "finished", {
        "run_id": f"run-{run_id}",
        "project": repo,
        "status": "completed",
        "skip_reason": skip_reason,
    })
    logger.info("Empty backlog for %s: %s", repo, skip_reason)
    return skip_reason


# ── Team Prompt Construction ──────────────────────────────────

TEAMMATE_ROLES = ["backend", "frontend", "qa"]


VALID_PROJECT_MODES = ("full", "analyze", "plan", "plan_only")


def _normalize_project_mode(raw: str | None) -> str:
    """Coerce a project.mode value to one of VALID_PROJECT_MODES.

    Issue #266: legacy/unknown values default to ``"full"`` so the
    orchestrator never silently misinterprets a typo. ``triage`` and
    ``review`` are out-of-scope for this gate (see github_webhook router)
    and are also coerced to ``"full"`` here — they take a different
    code path.
    """
    if not raw:
        return "full"
    raw = str(raw).strip().lower()
    return raw if raw in VALID_PROJECT_MODES else "full"


def build_mode_block(
    project_mode: str,
    workspace: str,
    plan_revision_feedback: str | None = None,
    prior_plan_path: str | None = None,
) -> str:
    """Build the mode-specific block to inject into a teammate spawn prompt.

    Returns an empty string for ``full`` and ``plan`` (no extra block — the
    worker proceeds normally; for ``plan`` the manager reviews under Plan
    Mode Review and rejects any source modification).

    For ``analyze`` returns an ``ANALYZE_MODE`` block instructing read-only
    investigation and pointing at the analyze-report file.

    For ``plan_only`` returns a ``PLAN_ONLY_MODE`` block matching the
    contract documented in ``agent/prompts/employee.md:72-109`` —
    teammate writes a plan to ``.claude-employee-plan-{index}.json`` and
    stops before any branch / commit / push.

    When ``plan_revision_feedback`` is provided (REVISE_PLAN loop), append
    a ``PLAN_REVISION`` section with the manager's feedback and the prior
    plan path so the teammate revises rather than starts from scratch.
    """
    if project_mode == "analyze":
        return f"""

## ANALYZE_MODE — READ-ONLY INVESTIGATION

You are in **analyze mode**. Your role is investigation, not implementation.

**STRICT RULES:**
- Do NOT modify, create, or delete any source file under any circumstance.
- Do NOT create a feature branch, commit, or push.
- Do NOT run `gh issue edit ... --add-label autonomous-agent/in-progress` or any GitHub
  state-changing command on production code.
- The ONLY file you may write is your analyze report at
  ``{workspace}/.claude-analyze-report-{{index}}.json``.

**WHAT TO PRODUCE** (JSON shape):
```json
{{
  "mode": "analyze",
  "issue_number": 42,
  "findings": [
    {{"file": "src/auth.py", "line": 117, "severity": "warning", "summary": "..."}}
  ],
  "files_inspected": ["src/auth.py", "src/login.tsx"],
  "recommendations": [
    "Add unit test for the null-cookie path",
    "Refactor `validate_token()` into smaller pieces"
  ],
  "notes": ""
}}
```

The manager reviews under **Analyze Mode Review** — never rejects for
"no code changes". Stay strictly read-only.
"""

    if project_mode == "plan_only":
        revision_block = ""
        if plan_revision_feedback and prior_plan_path:
            revision_block = f"""

### PLAN_REVISION — manager requested changes

The manager reviewed your previous plan and requested revisions.

Prior plan file: ``{prior_plan_path}``

Manager feedback:
{plan_revision_feedback}

Read the prior plan, apply the feedback, and write the **updated** plan
to the same plan output path below. Do not start from scratch — improve
the existing plan.
"""
        return f"""

## PLAN_ONLY_MODE — PRE-IMPLEMENTATION GATE

You are in **plan-only mode**. Produce an implementation plan and stop —
do NOT write any code, do NOT create a branch, do NOT commit, do NOT push.

**STRICT RULES:**
- After Step 3 (Plan), STOP. Do not proceed to Step 4 (Implement).
- Write the plan to ``{workspace}/.claude-employee-plan-{{index}}.json``
  using the Plan JSON schema in ``agent/prompts/REPORT-SCHEMAS.md``.
- Write your final report with ``"mode": "plan_only"`` (not ``"full"``).
- Leave the working tree clean: no source changes, no new files except
  the plan file.

After the manager reviews the plan, an **APPROVE_PLAN** verdict triggers a
follow-up run that implements your plan. **REVISE_PLAN** sends you back
with feedback. **REJECT_PLAN** stops the issue entirely. Treat the plan
as a real deliverable — depth matters.{revision_block}
"""

    # full / plan / unknown → no extra block. The 'plan' path runs the
    # standard worker flow but is reviewed under Plan Mode Review by the
    # manager (which rejects if any source was modified).
    return ""


# #385: Contract paragraph injected into every team/followup prompt so the
# lead always knows how to terminate the run authoritatively.
_RUN_COMPLETE_CONTRACT = """
## Ending the run

When all teammates are done — or you cannot proceed further — call the
`RunComplete` tool with a structured summary. This is the ONLY way to end
the run cleanly. Do not announce "the work is done" in prose; the
orchestrator does not read your prose for completion.

Status values:
- "success": all in-flight issues have a verdict.
- "partial": some issues progressed, some did not (record the rest in
  `verdicts` with `decision: "SKIP"` and a reason).
- "blocked": you cannot proceed without operator input.

Each `verdicts` entry must include `project`, `decision`
(APPROVE | APPROVE_INTEGRATION | PR | REJECT | SKIP), and may include
`issue_number`, `reasoning`, `branch`, and `base_branch`.
"""


def build_team_prompt(
    repo: str,
    issues: list[dict],
    config: dict,
    run_id: str,
    workspace: str = "",
    worktree_paths: dict[str, str] | None = None,
    vision: dict | None = None,
    project_mode: str = "full",
    approved_plan_paths: list[str] | None = None,
) -> str:
    """Build the lead agent prompt that creates and manages the team.

    ``approved_plan_paths`` are the absolute paths to plan files an
    earlier ``plan_only`` run produced and that the manager / operator
    has since approved. The prompt instructs each teammate to read its
    matching plan file (named ``.claude-employee-plan-{index}.json``)
    as ``APPROVED_PLAN`` guidance before writing code, so the
    follow-up ``full`` run honours the work already done.
    """
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

    project_mode = _normalize_project_mode(project_mode)
    mode_block = build_mode_block(project_mode, workspace)
    mode_instruction = ""
    if project_mode == "analyze":
        mode_instruction = (
            "\n## Project Mode: ANALYZE (read-only)\n\n"
            "This project is in **analyze mode**. Each teammate must operate read-only — "
            "no source edits, no branches, no commits, no pushes. They write findings to "
            "`.claude-analyze-report-<index>.json` and stop. Include the ANALYZE_MODE block "
            "below verbatim in every teammate spawn prompt.\n"
        )
    elif project_mode == "plan":
        mode_instruction = (
            "\n## Project Mode: PLAN (read-only plan output)\n\n"
            "This project is in **plan mode**. Teammates produce plan-quality output but "
            "must NOT modify any source file. The manager reviews under Plan Mode Review "
            "and will reject any read-only violation.\n"
        )
    elif project_mode == "plan_only":
        mode_instruction = (
            "\n## Project Mode: PLAN_ONLY (pre-implementation gate)\n\n"
            "This project is in **plan-only mode**. Each teammate writes an implementation "
            "plan to `.claude-employee-plan-<index>.json` and STOPS — no branch, no commit, "
            "no push. The manager will review the plan and decide APPROVE_PLAN / REVISE_PLAN "
            "/ REJECT_PLAN. Include the PLAN_ONLY_MODE block below verbatim in every "
            "teammate spawn prompt.\n"
        )

    approved_plan_section = ""
    if approved_plan_paths:
        plan_lines = "\n".join(f"  - `{p}`" for p in approved_plan_paths)
        approved_plan_section = f"""
## Approved plans from a prior plan_only run (READ FIRST)

The {len(approved_plan_paths)} plan files listed below were produced by an
earlier ``plan_only`` run and have **already** been approved (manager
auto-verdict or operator override on the dashboard). Plan approval is
DONE — this run is implementation only. Do **not** wait for plan
submissions, do not gate teammates on a plan-review signal, do not
poll for "plan approval requests". The plans exist on disk:

{plan_lines}

Each teammate must read its matching plan file (`.claude-employee-plan-<index>.json`)
as `APPROVED_PLAN` guidance before writing code, treat it as the agreed
approach, and only deviate when an explicit issue requirement contradicts
it (raise on the team chat first). When you decompose tasks, route each
issue to the teammate that wrote its plan when possible — they have the
most context.
"""

    repo_short = repo.split("/")[-1]
    run_id_short = run_id[:8]
    if approved_plan_paths:
        workflow_section = f"""## Your Workflow (IMPLEMENTATION — plans pre-approved)

1. **Create a team** called "{repo_short}-{run_id_short}".
2. **Read every approved plan file** listed above so you know what each teammate signed up to build.
3. **Spawn exactly 3 specialized teammates** (backend, frontend, qa) using the
   `issue-worker` agent type. Tell each teammate which plan file to load as
   `APPROVED_PLAN` guidance and which worktree to `cd` into. Spawn each role
   ONCE — do not respawn extra teammates whose only purpose is to wait or poll.
4. **Skip plan approval** — teammates implement straight from their approved
   plan; do not block on a "plan submitted" or "plan approved" signal.
5. **Actively monitor** teammates until each has written
   `.claude-employee-report-<index>.json` or 20 minutes elapse (see monitoring rules below).
6. After teammates finish, **synthesize a final JSON summary**.
"""
    else:
        workflow_section = f"""## Your Workflow

1. **Create a team** called "{repo_short}-{run_id_short}"
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
"""

    vision_section = ""
    if vision is not None:
        non_goals = (vision.get("non_goals") or "").strip() or "_(not specified)_"
        anti_patterns = (vision.get("anti_patterns") or "").strip() or "_(not specified)_"
        # Issue #335: surface tech_stack and runtime_target as informational
        # context so the lead — and the teammates it spawns — pick the right
        # frameworks, base images, and runtime patterns.
        tech_stack_text = (vision.get("tech_stack") or "").strip() or "_(not specified)_"
        runtime_target_text = (vision.get("runtime_target") or "").strip() or "_(not specified)_"
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

## Project shape (informational context, NOT misalignment criteria)

The two sections below describe what the project is built with and where it
runs. **Do NOT reject teammate plans on these grounds** — they are not
misalignment criteria like non-goals and anti-patterns. They exist so
teammates pick the right frameworks, base images, and runtime patterns.
If a plan diverges from the stated tech stack for a good reason, that is a
judgement call, not a violation.

### Vision — Tech Stack
{tech_stack_text}

### Vision — Runtime Target
{runtime_target_text}

(Full vision available at `{workspace}/docs/vision.md` if you need other context.)
"""

    return f"""You are the lead of an agent team implementing GitHub issues for **{repo}**.
{mode_instruction}
{approved_plan_section}
{workflow_section}

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
1. After spawning all teammates, run **`sleep 60`** via the **Bash tool**.
   Do **NOT** spawn a teammate just to wait — the Task tool is for real
   implementation work, never for sleep proxies. Spawning a teammate with a
   description like "Wait 3 minutes then check progress" is a bug; use Bash sleep instead.
2. Check for completed reports: `find {workspace} -name ".claude-employee-report*.json" -type f 2>/dev/null`
3. For each report found, read it and record the status
4. If any teammate has not yet reported, **go back to step 1**
5. Only end your turn and provide the final JSON summary AFTER:
   - All tasks on the shared task list are completed, OR
   - 20 minutes have elapsed since spawning (timeout for remaining)

**Why this matters**: If you say "I'm waiting" and end your turn, the session terminates
and your teammates lose their work. You must keep making tool calls to stay alive —
but those tool calls should be Bash sleeps and report-file polls, not new Task spawns.

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
{vision_section}
{mode_block}""" + _RUN_COMPLETE_CONTRACT


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
    ) + _RUN_COMPLETE_CONTRACT


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

    # Also ping the launcher's /webhook-tick so its zombie reaper sees
    # the orchestrator's activity. The PR #364 wrapper only pinged from
    # the bash-side webhook_event helper, but most Agent Teams runtime
    # traffic flows through this Python path; without this ping the
    # launcher reaped active runs after 120s of "bash silence" (see
    # run-20260512T122255Z post-mortem).
    _ping_launcher_best_effort()


def _ping_launcher_best_effort() -> None:
    """Bump the launcher's heartbeat clock so its _zombie_reaper sees
    that the Python orchestrator is making forward progress. Mirrors
    agent.webhook_emitter._ping_launcher. Silent on all errors.

    Defaults to ``http://localhost:8421`` because the orchestrator
    runs inside the agent container and the launcher listens there.
    The env var is the override (e.g. for tests that run the
    orchestrator outside the container).
    """
    base = os.environ.get("STATION_AGENT_LAUNCHER_URL") or "http://localhost:8421"
    token = os.environ.get("STATION_LAUNCHER_TOKEN", "")
    headers = {"X-Launcher-Token": token} if token else {}
    try:
        with httpx.Client(timeout=1.0) as client:
            client.post(f"{base.rstrip('/')}/webhook-tick", headers=headers)
    except Exception:
        pass


def _usage_val(usage, key: str, default=0):
    """Safely get a usage field whether usage is a dict or object."""
    if usage is None:
        return default
    if isinstance(usage, dict):
        return usage.get(key, default)
    return getattr(usage, key, default)


def _actor_for_message(message, *, default: str = "lead") -> str:
    """Compute the audit_log ``actor`` for an AssistantMessage.

    SDK populates ``parent_tool_use_id`` on messages that originate
    inside a sub-agent / Agent Teams teammate. When present, label the
    audit row as ``teammate-<parent_tool_use_id>`` so the timeline can
    distinguish lead vs teammate work; falls back to ``default`` for
    main-thread messages.

    The pre-#389 hook factory consulted ``agent_id`` from the SDK's
    hook-input dict, but that field is part of the hook-callback API
    surface, not the streamed message API. After stream-derived audit
    (#389) we use ``parent_tool_use_id`` directly — it is the stable
    correlation key the SDK exposes on every sub-agent message.
    Readable names (``backend``/``frontend``/``qa``/``manager``) can be
    resolved post-hoc by joining audit_log rows against
    coordinator_tasks on ``tool_use_id``; we don't try to denormalise
    here.
    """
    parent = getattr(message, "parent_tool_use_id", None)
    if parent:
        return f"teammate-{parent}"
    return default


async def handle_stream_event(
    message, config: dict, run_id: str, log_file=None, state: _StreamState | None = None,
) -> None:
    """Forward SDK stream messages to the dashboard and write to log file.

    Async after #389: the stream-derived audit writer offloads sqlite3
    via ``asyncio.to_thread``.
    """
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
                    # #389: write audit_log row inline from the block,
                    # not from a separate SDK hook callback. Off-load
                    # sqlite3 so the stream loop is not blocked.
                    actor = _actor_for_message(message, default="lead")
                    await asyncio.to_thread(
                        write_audit_started_from_block,
                        run_id=f"run-{run_id}",
                        actor=actor,
                        block=block,
                        trace_id=f"run-{run_id}",
                    )
                    if block.name == "RunComplete":
                        # #385: We re-validate on the stream side even though
                        # the SDK-side tool handler already validated. The
                        # tool handler runs in the SDK subprocess (whose
                        # pydantic version we don't pin), and its rejection
                        # only surfaces to the lead as a tool_result error.
                        # The orchestrator must independently confirm shape
                        # before latching anything that downstream consumers
                        # (the webhook payload) depend on. Cheap by design;
                        # bounded by pydantic's parsing speed for a small dict.
                        try:
                            parsed = RunCompleteInput.model_validate(block.input or {})
                        except _PydanticValidationError as exc:
                            # Schema-invalid input — the tool handler's tool_result
                            # already tells the lead to retry. Do NOT latch.
                            logger.warning("RunComplete malformed: %s", exc)
                        else:
                            if state is not None and state.run_complete_payload is None:
                                state.run_complete_payload = parsed.model_dump()
                                # #385: this is the authoritative
                                # orchestrator_complete emission. The fallback
                                # branch in the ResultMessage path is gated
                                # below on state.run_complete_payload being None.
                                post_webhook(config, "orchestrator_complete", {
                                    "run_id": f"run-{run_id}",
                                    "is_error": False,
                                    "status": parsed.status,
                                    "verdicts": [v.model_dump() for v in parsed.verdicts],
                                    "summary": parsed.summary,
                                    "duration_ms": 0,
                                    "num_turns": state.turns,
                                })
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

    elif isinstance(message, UserMessage):
        # #389: tool results arrive as ToolResultBlock items inside
        # UserMessage.content. Walk them and write the matching audit_log
        # finish row.
        content = message.content if isinstance(message.content, list) else [message.content]
        for block in content:
            if isinstance(block, ToolResultBlock):
                await asyncio.to_thread(
                    write_audit_finished_from_block,
                    block=block,
                )

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
        # ResultMessages from teammate sub-sessions (spawned via the
        # Agent tool) propagate up through the lead's stream. Each one
        # would otherwise trigger orchestrator_complete and mark the
        # parent run terminal — exactly the bug that left
        # run-20260512T124731Z stuck while the lead kept working. Gate
        # the emission on session_id matching the captured main session.
        # See #371.
        msg_session_id = getattr(message, "session_id", None)
        is_main_session = (
            state is not None
            and state.main_session_id is not None
            and msg_session_id is not None
            and msg_session_id == state.main_session_id
        )
        if not is_main_session:
            logger.info(
                "Skipping orchestrator_complete emit — ResultMessage is from "
                "sub-session %s (main=%s, subtype=%s, turns=%s)",
                msg_session_id,
                state.main_session_id if state else None,
                getattr(message, "subtype", "?"),
                getattr(message, "num_turns", "?"),
            )
            return

        # Even when the session matches, this may be an intermediate
        # ResultMessage from the lead — e.g. when the lead delegates work
        # via the Agent tool, the SDK emits a turn-complete ResultMessage
        # for the lead well before any actual work is done. The outer loop
        # already uses _is_work_complete() to distinguish; mirror that
        # check here so orchestrator_complete only fires once, when the
        # lead signals actual completion. Without this gate the dashboard
        # marks the run terminal on the first ResultMessage and the
        # ongoing lead/teammate work runs orphaned. Surfaced after the
        # _user_prompt_stream stdin fix (PR #381) removed the
        # incidental "one-result-per-query" rate-limit that had been
        # masking this. See run-20260512T205423Z.
        result_text = getattr(message, "result", "")
        if not _is_work_complete(result_text):
            # Still flush accumulated tokens so progress_update is current,
            # but do NOT emit orchestrator_complete — the lead is mid-flight.
            if state:
                post_webhook(config, "progress_update", {
                    "run_id": f"run-{run_id}",
                    "tokens_input": state.tokens_in,
                    "tokens_output": state.tokens_out,
                    "tokens_total": state.tokens_in + state.tokens_out,
                    "turns": state.turns,
                })
            logger.info(
                "Skipping orchestrator_complete emit — main-session ResultMessage "
                "but result_text does not signal work completion (turns=%s, "
                "result_preview=%r)",
                getattr(message, "num_turns", "?"),
                (result_text or "")[:120],
            )
            return

        # Final flush of accumulated tokens
        if state:
            post_webhook(config, "progress_update", {
                "run_id": f"run-{run_id}",
                "tokens_input": state.tokens_in,
                "tokens_output": state.tokens_out,
                "tokens_total": state.tokens_in + state.tokens_out,
                "turns": state.turns,
            })
        # #385: if the lead already called the RunComplete tool, the
        # authoritative orchestrator_complete fired from the ToolUseBlock
        # branch. Skip the legacy emission to keep the contract single-firing.
        if state is not None and state.run_complete_payload is not None:
            logger.info(
                "Skipping legacy ResultMessage orchestrator_complete — "
                "RunComplete tool already latched the payload."
            )
            return
        post_webhook(config, "orchestrator_complete", {
            "run_id": f"run-{run_id}",
            "is_error": getattr(message, "is_error", False),
            "duration_ms": getattr(message, "duration_ms", 0),
            "num_turns": getattr(message, "num_turns", 0),
        })
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


# ── Employee Report Fallback ──────────────────────────────────

def _synthesize_employee_report(
    *,
    role: str,
    wt_path: str,
    workspace: str,
    base_branch: str,
    run_id: str,
    project_mode: str,
    issue_numbers: list[int],
) -> bool:
    """Best-effort fallback when a teammate exits without writing its own
    ``.claude-employee-report.json``.

    The Agent-Teams-mode prompt asks teammates to write per-role reports
    under ``<workspace>/.claude-employee-report-<index>.json``. When they
    skip that step, ``run-manager.sh`` finds nothing to review and emits
    ``"No employee reports found. Skipping manager review"`` — the run
    completes with no verdict, no push, no PR, even though commits exist
    in the worktree branches. To prevent that, this function inspects the
    role's worktree before the orchestrator tears it down and writes a
    minimal report carrying the fields the manager review pipeline needs:
    branch, base_branch, files changed, commits, mode, issue number(s).

    Skipped silently if:
    - The teammate already wrote a report (don't clobber).
    - The worktree path is missing (already removed).
    - There are no commits and no diff vs. ``base_branch`` (no work done).

    Returns True if a report file was written.
    """
    main_report = Path(workspace) / ".claude-employee-report.json"
    indexed_report = Path(workspace) / f".claude-employee-report-{role}.json"
    if main_report.exists() or indexed_report.exists():
        return False
    if not os.path.isdir(wt_path):
        return False

    try:
        branch_proc = subprocess.run(
            ["git", "-C", wt_path, "branch", "--show-current"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        branch = branch_proc.stdout.strip()
        if not branch:
            return False

        commits_proc = subprocess.run(
            ["git", "-C", wt_path, "log", "--format=%H", f"{base_branch}..HEAD"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        commits = [c for c in commits_proc.stdout.splitlines() if c.strip()]

        files_proc = subprocess.run(
            ["git", "-C", wt_path, "diff", "--name-only", f"{base_branch}..HEAD"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        files = [f for f in files_proc.stdout.splitlines() if f.strip()]

        if not commits and not files:
            return False

        # run-manager.sh reads `issue_number` (singular) for the manager
        # review package. Use the run's first issue when there's exactly
        # one; leave null otherwise so the manager doesn't see a misleading
        # arbitrary pick. The full list lives under `issue_numbers` for
        # any downstream consumer that needs the multi-issue context.
        single_issue = issue_numbers[0] if len(issue_numbers) == 1 else None

        report = {
            "status": "success",
            "mode": project_mode,
            "issue_number": single_issue,
            "issue_numbers": issue_numbers,
            "branch": branch,
            "base_branch": base_branch,
            "files_changed": files,
            "commits": commits,
            "tests_run": False,
            "tests_passed": False,
            "confidence": 0.5,
            "confidence_reasoning": (
                "Synthesized by orchestrator from worktree git state; the "
                "teammate did not write its own report. Branch and commits "
                "are real but tests/quality were not verified by the teammate."
            ),
            "notes": (
                "This report was generated automatically by the orchestrator "
                "as a fallback so the manager review can proceed. The "
                "teammate exited without writing "
                ".claude-employee-report.json."
            ),
            "synthesized_by": "orchestrator",
            "role": role,
            "run_id": run_id,
        }
        indexed_report.write_text(json.dumps(report, indent=2))
        logger.info(
            "Synthesized employee report for %s with %d commit(s) on %s",
            role, len(commits), branch,
        )
        return True
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Failed to synthesize employee report for %s: %s", role, exc)
        return False


# ── Main Orchestration ─────────────────────────────────────────


async def orchestrate_project(
    project: dict, config: dict, run_id: str, workspaces_dir: str,
) -> tuple[int, "_StreamState | None"]:
    """Run the Agent Teams session for a single project.

    Returns ``(exit_code, stream_state)``. ``stream_state`` is ``None`` if
    the project short-circuited before the orchestrator session began
    (no eligible issues, workspace error, etc.). Callers use the returned
    state for telemetry aggregation — see :class:`RunDriver._finalize_telemetry`.

    Extracted from orchestrate() in #383 so iterate_projects (Python-only)
    can drive per-project work directly without delegating the outer loop
    to bash. The tuple return supersedes the prior ``_LAST_STREAM_STATE``
    module-global hand-off so concurrent or repeated calls cannot trample
    each other's telemetry.
    """
    max_per_project = get_limit(config, "max_employees_per_project", 3)
    manager_model = get_model(config, "manager", "claude-sonnet-4-6")
    manager_turns = get_limit(config, "max_manager_turns", 30)
    max_reentries = 6  # Up to 6 re-entries if lead exits prematurely

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

    if True:  # single-project body (formerly the `for project in enabled_projects:` loop body) noqa
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
        # Issue #266: read project mode and propagate to spawn prompt + webhooks.
        # Falls back to "full" for legacy projects without an explicit mode.
        project_mode = _normalize_project_mode(project.get("mode"))
        logger.info("Project mode for %s: %s", repo, project_mode)

        logger.info(
            "Processing project: %s (autonomy=%s, budget=%s)",
            repo, autonomy_level.value, max_budget_usd,
        )

        # Issue #266 follow-up: drain the queue first. Pre-approved
        # follow-up runs (manager APPROVE_PLAN auto-gate, or operator
        # override via /api/runs/.../plan/approve) land in QueueItems
        # with state='pending' and their own ``mode`` (typically
        # ``full``). Without this consumer they sat forever and the
        # follow-up implementation never happened.
        claimed_items = await claim_pending_queue_items(repo, run_id)
        approved_plan_paths: list[str] = []
        if claimed_items:
            issues = [c.issue for c in claimed_items]
            queued_modes = {c.queue_mode for c in claimed_items}
            if len(queued_modes) == 1:
                project_mode = _normalize_project_mode(next(iter(queued_modes)))
                logger.info(
                    "Draining %d queue items for %s; using queued mode %s",
                    len(claimed_items), repo, project_mode,
                )
            else:
                logger.warning(
                    "Mixed queue-item modes for %s: %s; falling back to "
                    "project mode %s for the batch",
                    repo, queued_modes, project_mode,
                )
            approved_plan_paths = [
                c.approved_plan_path for c in claimed_items if c.approved_plan_path
            ]
            if approved_plan_paths:
                logger.info(
                    "Approved plan files in this batch: %s",
                    approved_plan_paths,
                )
        else:
            # Fall back to GitHub-issue-driven flow (the original
            # behaviour for projects with no queued work).
            issues = fetch_eligible_issues(repo, max_per_project, workspace)

        if not issues:
            handle_empty_backlog(
                config=config,
                repo=repo,
                project_id=project.get("id"),
                workspace=workspace,
                run_id=run_id,
            )
            # No issues: clean exit. ``stream_state`` is uninitialised here
            # because the orchestrator session never started — return None so
            # telemetry aggregation in the caller can skip this project.
            return exit_code, None

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

        # Persist the run's effective mode so run-manager.sh's review-package
        # builder uses it instead of the static project config. Without this
        # marker, an approved-plan follow-up run (drained queue items with
        # mode=full) gets reviewed under the project's configured mode
        # (often plan_only) and the manager auto-rejects every teammate as
        # "MODE MISMATCH — you wrote code in plan mode". Run-manager reads
        # this file in collect_employee_reports and falls back to the static
        # config only if the marker is absent or unreadable.
        try:
            with open(os.path.join(workspace, ".claude-run-mode"), "w") as f:
                f.write(project_mode)
        except OSError as exc:
            logger.warning(
                "could not write .claude-run-mode marker for %s: %s",
                repo, exc,
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

        # Notify dashboard. Issue #266: webhook 'mode' carries the project's
        # configured mode (full/analyze/plan/plan_only) so the dashboard,
        # manager, and downstream consumers can branch on it. The Agent
        # Teams flow is implied by the orchestrator emitting these events
        # at all — no separate "agent-teams" sentinel is needed.
        post_webhook(config, "run_start", {
            "run_id": f"run-{run_id}",
            "project": repo,
            "mode": project_mode,
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
            "mode": project_mode,
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
                # Build options once — ClaudeSDKClient owns the session for
                # the lifetime of `async with`, so resume tokens are unnecessary.
                _run_complete_server = build_run_complete_server()

                options = ClaudeAgentOptions(
                    cwd=workspace,
                    env={
                        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
                        "GITHUB_REPO": repo,
                    },
                    mcp_servers={
                        "run_complete": _run_complete_server,
                        "playwright": {
                            "type": "stdio",
                            "command": "npx",
                            "args": ["-y", "@playwright/mcp@latest"],
                        },
                        "ref": {
                            "type": "http",
                            "url": "https://api.ref.tools/mcp",
                        },
                    },
                    allowed_tools=["Read", "Bash", "Glob", "Grep", "Edit", "Write", "Agent", "mcp__playwright__*", "mcp__ref__*", "mcp__run_complete__RunComplete"],
                    max_turns=manager_turns,
                    model=manager_model,
                    agents=agents_dict,
                    can_use_tool=make_audited_policy(
                        run_id=f"run-{run_id}",
                        level=autonomy_level,
                        agent_id="lead",
                    ),
                    max_budget_usd=max_budget_usd,
                )

                stop_signalled = False
                async with ClaudeSDKClient(options=options) as client:
                    for iteration in range(max_reentries):
                        is_followup = iteration > 0

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
                                "Re-entering lead session (iteration %d/%d)",
                                iteration + 1, max_reentries,
                            )
                        else:
                            prompt = build_team_prompt(
                                repo, issues, config, run_id, workspace, worktree_paths,
                                vision=vision, project_mode=project_mode,
                                approved_plan_paths=approved_plan_paths,
                            )

                        await client.query(prompt)

                        async for message in client.receive_response():
                            sid = getattr(message, "session_id", None)
                            if sid and stream_state.main_session_id is None:
                                stream_state.main_session_id = sid
                                logger.info(
                                    "Captured lead session_id=%s for run-%s",
                                    sid, run_id,
                                )

                            if isinstance(message, SystemMessage) and getattr(message, "subtype", "") == "init":
                                if not first_init_sent:
                                    post_webhook(config, "orchestrator_start", {
                                        "run_id": f"run-{run_id}",
                                        "mode": project_mode,
                                    })
                                    first_init_sent = True

                            await handle_stream_event(message, config, run_id, log_file=log_file, state=stream_state)

                            if control_flags["stop"] and not stop_signalled:
                                stop_signalled = True
                                logger.info("Stop requested; interrupting client")
                                await client.interrupt()
                                break

                            # #385 primary completion gate: the lead called
                            # the RunComplete tool. handle_stream_event
                            # latched state.run_complete_payload; we exit
                            # the iterator naturally.
                            if stream_state.run_complete_payload is not None:
                                work_complete = True
                                logger.info(
                                    "RunComplete tool received; breaking SDK stream"
                                )
                                break

                            # Fallback (one release window) — _is_work_complete
                            # prose match. Warning is fire-once-per-run on
                            # ``stream_state.fallback_warning_logged`` so
                            # long runs with multiple matches don't spam
                            # the log; operators still see the signal once
                            # per run, which is what triage needs.
                            if isinstance(message, ResultMessage):
                                result_text = getattr(message, "result", "")
                                if _is_work_complete(result_text):
                                    if not stream_state.fallback_warning_logged:
                                        logger.warning(
                                            "RunComplete fallback engaged: lead did "
                                            "not call the tool; relying on prose "
                                            "match. Run: run-%s",
                                            run_id,
                                        )
                                        stream_state.fallback_warning_logged = True
                                    work_complete = True
                                    break

                        if control_flags["stop"]:
                            raise OrchestratorStopRequested()

                        if work_complete:
                            logger.info("Agent Teams orchestration completed for %s", repo)
                            break

                        # Brief pause before the next follow-up turn. The control
                        # task keeps running during this sleep.
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

            # Synthesize fallback employee reports BEFORE removing worktrees:
            # see _synthesize_employee_report. Without this, run-manager.sh
            # skips manager review when teammates forget to write their own
            # report, even though commits exist on the worktree branches.
            #
            # ``issues`` may not be bound if the project loop short-circuited
            # before issue selection (early exception, empty backlog ``continue``);
            # default to an empty list so issue_number is left null rather
            # than crashing the cleanup path.
            try:
                _issue_numbers = [int(i["number"]) for i in issues if "number" in i]
            except NameError:
                _issue_numbers = []
            for role, wt_path in worktree_paths.items():
                _synthesize_employee_report(
                    role=role,
                    wt_path=wt_path,
                    workspace=workspace,
                    base_branch=base_branch,
                    run_id=run_id,
                    project_mode=project_mode,
                    issue_numbers=_issue_numbers,
                )

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

            # Finalise any queue items we claimed for this project so the
            # next orchestrator pass doesn't see them as still-pending.
            # ``completed`` regardless of per-issue verdict — the run's
            # success/failure is tracked on the Run row; the queue item's
            # job is just to mark "this work was attempted".
            if claimed_items:
                outcome = "completed" if exit_code == 0 else "failed"
                try:
                    await finalise_claimed_queue_items(claimed_items, outcome=outcome)
                except Exception as exc:  # pragma: no cover — defensive
                    logger.warning(
                        "Failed to finalise queue items for %s: %s",
                        repo, exc,
                    )

    return exit_code, stream_state


async def orchestrate(config: dict, run_id: str, workspaces_dir: str) -> int:
    """Outer driver: iterate over enabled projects, run each one in sequence.

    Delegates per-project work to orchestrate_project() (#383 extraction).
    """
    projects = config.get("projects", [])
    enabled_projects = [p for p in projects if p.get("enabled", True)]
    skipped = [p.get("repo", "<unnamed>") for p in projects if not p.get("enabled", True)]
    for repo in skipped:
        logger.info("Skipping disabled project: %s", repo)
    if not enabled_projects:
        logger.info(
            "No enabled projects configured; emitting run_complete for run-%s",
            run_id,
        )
        post_webhook(config, "finished", {
            "run_id": f"run-{run_id}",
            "status": "completed",
            "skip_reason": "no-projects-configured",
        })
        return 0

    exit_code = 0
    for project in enabled_projects:
        # orchestrate_project now returns (exit_code, stream_state); the
        # legacy `orchestrate()` driver only cares about the exit code,
        # discards the state.
        proj_rc, _state = await orchestrate_project(
            project, config, run_id, workspaces_dir,
        )
        if proj_rc != 0:
            exit_code = proj_rc
    return exit_code


# ============================================================================
# RunDriver — issue #349 sub-PR 5c, enriched in #361
# ============================================================================


@dataclass
class RunTelemetry:
    """Counters and identifiers attached to a run.

    ``project_count`` / ``max_concurrent`` / ``concurrent_group_id`` /
    ``log_file`` are derived from config at startup and shipped on
    ``run_start``. ``tokens_*`` / ``turns`` are read back from the bash
    telemetry dump after ``iterate_projects`` returns (the bash shim
    in `--internal-iterate` mode writes them to a known path so the
    Python driver can include them in ``run_complete`` without
    re-extracting from stream files).
    """

    started_at: datetime
    project_count: int = 0
    max_concurrent: int = 1
    concurrent_group_id: str = ""
    log_file: str = ""
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_total: int = 0
    turns: int = 0


class RunDriver:
    """Owns the full run lifecycle: emits run_start at entry, emits
    run_complete at exit (always — via try/finally), delegating the
    project iteration to agent.project_loop.

    Replaces run-manager.sh's EXIT-trap webhook construct, which had
    reliability holes (silent drops on interrupted bash). The Python
    try/finally cannot be skipped short of SIGKILL.

    Payload parity with the bash path (issue #361):

    - ``run_start``: ``project_count``, ``max_concurrent``,
      ``concurrent_group_id``, ``log_file``.
    - ``run_complete``: ``status``, ``exit_code``, ``tokens_input``,
      ``tokens_output``, ``tokens_total``, ``turns``, ``duration_ms``.

    Signal handling:

    - SIGINT (Ctrl-C, ``docker compose kill --signal SIGINT``) raises
      ``KeyboardInterrupt`` by Python default. We catch it and map to
      ``status="interrupted"`` with exit code 130 (POSIX convention
      for SIGINT-triggered exit).
    - SIGTERM (``_zombie_reaper``, launcher ``/stop``) is mapped to
      ``KeyboardInterrupt`` via a process-level signal handler so it
      flows through the same ``status="interrupted"`` path.
    """

    # Where the bash shim drops its telemetry on exit (#361). The Python
    # driver reads this file after iterate_projects returns.
    _LOG_DIR_ENV = "STATION_LOG_DIR"
    _DEFAULT_LOG_DIR = "/var/log/claude-agent"

    def __init__(self, *, run_id: str, config_path: str,
                 workspaces_dir: str) -> None:
        self.run_id = run_id
        self.config_path = config_path
        self.workspaces_dir = workspaces_dir
        self._clean_id = run_id.removeprefix("run-")
        self._log_dir = os.environ.get(self._LOG_DIR_ENV, self._DEFAULT_LOG_DIR)
        self.telemetry = self._init_telemetry()

    def _init_telemetry(self) -> RunTelemetry:
        try:
            config = load_config(self.config_path)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("RunDriver: failed to load config for telemetry (%s) — "
                           "run_start payload will use defaults", exc)
            config = {}
        projects = config.get("projects") or []
        enabled = sum(1 for p in projects if p.get("enabled", True))
        max_concurrent = (config.get("limits") or {}).get("max_concurrent_employees", 1)
        return RunTelemetry(
            started_at=datetime.now(timezone.utc),
            project_count=enabled,
            max_concurrent=int(max_concurrent or 1),
            concurrent_group_id=f"group-{self._clean_id}",
            log_file=str(Path(self._log_dir) / f"run-{self._clean_id}.log"),
        )

    def _wire_run_id(self) -> str:
        # Webhook wire convention is ``run-<id>``. Be defensive in case the
        # caller passed the raw timestamp without the prefix.
        return self.run_id if self.run_id.startswith("run-") else f"run-{self.run_id}"

    def _finalize_telemetry(self, stream_state) -> None:
        """Copy in-process counters from the orchestrator's stream state.

        Replaces the bash telemetry JSON hand-off after #383. iterate_projects
        is responsible for passing its accumulated _StreamState here in its
        return path; if it doesn't, counters remain at zero.
        """
        self.telemetry.tokens_input = int(getattr(stream_state, "tokens_in", 0) or 0)
        self.telemetry.tokens_output = int(getattr(stream_state, "tokens_out", 0) or 0)
        self.telemetry.tokens_total = self.telemetry.tokens_input + self.telemetry.tokens_output
        self.telemetry.turns = int(getattr(stream_state, "turns", 0) or 0)

    def _install_signal_handlers(self) -> tuple:
        """Map SIGTERM → KeyboardInterrupt so reaper/launcher /stop calls
        flow through the same ``interrupted`` path as Ctrl-C.

        Returns the previous handler so the caller can restore it.
        """

        def _term_to_interrupt(signum, frame):  # noqa: ARG001
            raise KeyboardInterrupt

        try:
            prev = signal.signal(signal.SIGTERM, _term_to_interrupt)
        except ValueError:
            # Not running in the main thread (e.g. embedded tests). Skip;
            # the test driver can install its own handler.
            return (signal.SIG_DFL,)
        return (prev,)

    def _emit_run_start(self) -> None:
        emit(
            "run_start",
            run_id=self._wire_run_id(),
            payload={
                "project_count": self.telemetry.project_count,
                "max_concurrent": self.telemetry.max_concurrent,
                "concurrent_group_id": self.telemetry.concurrent_group_id,
                "log_file": self.telemetry.log_file,
            },
        )

    def _emit_run_complete(self, *, status: str, exit_code: int,
                           error: str | None) -> None:
        duration_ms = int(
            (datetime.now(timezone.utc) - self.telemetry.started_at).total_seconds() * 1000
        )
        tokens_total = (
            self.telemetry.tokens_total
            or (self.telemetry.tokens_input + self.telemetry.tokens_output)
        )
        payload: dict = {
            "status": status,
            "exit_code": exit_code,
            "tokens_input": self.telemetry.tokens_input,
            "tokens_output": self.telemetry.tokens_output,
            "tokens_total": tokens_total,
            "turns": self.telemetry.turns,
            "duration_ms": duration_ms,
        }
        if error:
            payload["error"] = error
        emit("run_complete", run_id=self._wire_run_id(), payload=payload)

    def run(self) -> int:
        """Execute the run. Returns process exit code.

        Always emits run_start and run_complete. Exit-code conventions:

        - ``0`` → ``status="completed"``
        - ``130`` (child or self via SIGINT) → ``status="interrupted"``
        - SIGTERM-mapped KeyboardInterrupt → ``status="interrupted"``,
          exit code 130
        - Any other non-zero from ``iterate_projects`` → ``status="failed"``
        - Uncaught Python exception → ``status="error"``, exit code 1
        """
        self._install_signal_handlers()
        self._emit_run_start()

        status = "completed"
        exit_code = 0
        error: str | None = None
        last_state = None

        try:
            from agent.project_loop import iterate_projects
            exit_code, last_state = iterate_projects(
                self.run_id, self.config_path, self.workspaces_dir,
            )
            if exit_code == 130:
                status = "interrupted"
            elif exit_code != 0:
                status = "failed"
        except KeyboardInterrupt:
            logger.warning("RunDriver: KeyboardInterrupt — marking run interrupted")
            status = "interrupted"
            exit_code = 130
        except Exception as e:  # noqa: BLE001 — driver MUST NOT propagate
            logger.exception("RunDriver: iterate_projects raised")
            status = "error"
            exit_code = 1
            error = f"{type(e).__name__}: {e}"
        finally:
            # Telemetry is threaded through iterate_projects's tuple return
            # (last_state). _finalize_telemetry tolerates None — the
            # iterate_projects exception paths leave last_state at its
            # initialiser.
            if last_state is not None:
                self._finalize_telemetry(last_state)
            self._emit_run_complete(status=status, exit_code=exit_code, error=error)

        return exit_code


# ── CLI Entry Point ────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Agent Teams station orchestrator")
    parser.add_argument("--config", required=True, help="Path to manager-config.json")
    parser.add_argument("--run-id", required=True, help="Unique run identifier")
    parser.add_argument("--workspaces-dir", required=True, help="Directory for project workspaces")
    parser.add_argument(
        "--driver",
        action="store_true",
        default=False,
        help=(
            "Run via RunDriver (issue #349 migration path): emits run_start/"
            "run_complete via Python try/finally, delegates project iteration "
            "to agent.project_loop (which currently shells to run-manager.sh "
            "--internal-iterate). Use this flag during the bash→Python "
            "migration; omit it for the existing Agent Teams orchestration path."
        ),
    )
    return parser.parse_args()



def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    args = parse_args()

    if args.driver:
        # Migration path (issue #349): RunDriver owns run_start/run_complete;
        # bash project iteration is called via agent.project_loop.
        driver = RunDriver(
            run_id=args.run_id,
            config_path=args.config,
            workspaces_dir=args.workspaces_dir,
        )
        sys.exit(driver.run())

    # Existing Agent Teams orchestration path. ClaudeSDKClient (#384) owns
    # subprocess teardown via its __aexit__, so asyncio.run() can finalise
    # cleanly without the /proc-walk shutdown hack.
    config = load_config(args.config)
    sys.exit(asyncio.run(orchestrate(config, args.run_id, args.workspaces_dir)))


if __name__ == "__main__":
    main()
