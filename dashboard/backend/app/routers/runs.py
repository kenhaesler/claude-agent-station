"""Run history endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.models import (
    AgentEvent,
    CoordinatorMessage,
    CoordinatorTask,
    Plan,
    Project,
    QueueItem,
    Run,
    RunControl,
)
from app.schemas import (
    ActiveEmployeeOut,
    ActiveTeammateOut,
    AgentEventOut,
    CoordinatorMessageOut,
    CoordinatorTaskOut,
    PlanOut,
    QueueItemOut,
    RunControlAck,
    RunFullContext,
    RunList,
    RunMessage,
    RunOut,
    TeamSummary,
    TeammateStatus,
    TelemetryActive,
    TelemetryQueue,
    TelemetrySummaryOut,
    TelemetrySystem,
    TelemetryTokens7d,
    TelemetryVerdicts7d,
)
from app.services import service_control
from app.services.diff_parser import DiffResult, parse_unified_diff
from app.services.event_bus import publish
from app.services.log_importer import import_historical_runs
from app.services.systemd import get_system_resources, systemctl

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("", response_model=RunList)
async def list_runs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    project_id: int | None = None,
    status: str | None = None,
    verdict: str | None = None,
    concurrent_group_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Run)
    count_query = select(func.count(Run.id))

    if project_id is not None:
        query = query.where(Run.project_id == project_id)
        count_query = count_query.where(Run.project_id == project_id)
    if status:
        query = query.where(Run.status == status)
        count_query = count_query.where(Run.status == status)
    if verdict:
        query = query.where(Run.verdict == verdict)
        count_query = count_query.where(Run.verdict == verdict)
    if concurrent_group_id:
        query = query.where(Run.concurrent_group_id == concurrent_group_id)
        count_query = count_query.where(Run.concurrent_group_id == concurrent_group_id)

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    query = query.order_by(desc(Run.started_at)).offset(offset).limit(limit)
    result = await db.execute(query)
    runs = result.scalars().all()

    return RunList(runs=runs, total=total)


@router.get("/active-employees", response_model=list[ActiveEmployeeOut])
async def get_active_employees(db: AsyncSession = Depends(get_db)):
    """Return all currently running employee/agent runs for workspace visualization.

    Queries Run records first. If only 1 Run is found but there are multiple
    running CoordinatorTasks, synthesize additional ActiveEmployeeOut entries
    from the coordinator tasks (handles the coordinated multi-employee path).
    """
    result = await db.execute(
        select(Run).where(Run.status.in_(["running", "plan_reviewing", "reviewing"]))
    )
    runs = result.scalars().all()
    employees = [
        ActiveEmployeeOut(
            run_id=r.run_id,
            project_id=r.project_id,
            mode=r.mode or "employee",
            status=r.status or "running",
            issue_number=r.issue_number,
            turns=r.turns,
            employee_index=r.employee_index,
            concurrent_group_id=r.concurrent_group_id,
            model=r.model,
            branch=r.branch,
            tokens_total=r.tokens_total,
            started_at=r.started_at,
        )
        for r in runs
    ]

    # Fallback: check for running coordinator tasks that don't have Run records.
    # This covers the coordinated path where the Python coordinator spawns
    # employees via task_started events instead of employee_start events.
    if len(employees) <= 1:
        # Only synthesize from coordinator_tasks whose parent run is still
        # in a non-terminal state. Without this guard, stale rows linger
        # after the parent run completes and the API resurrects them as
        # phantom running employees. See issue #345.
        coord_result = await db.execute(
            select(CoordinatorTask)
            .join(Run, Run.run_id == CoordinatorTask.run_id, isouter=True)
            .where(
                CoordinatorTask.status == "running",
                Run.status.in_(("running", "reviewing", "plan_reviewing"))
                | (Run.status.is_(None)),
            )
        )
        coord_tasks = coord_result.scalars().all()

        # Only synthesize if we have coordinator tasks beyond what's already shown
        seen_indices = {e.employee_index for e in employees}
        for ct in coord_tasks:
            if ct.employee_index in seen_indices:
                continue
            seen_indices.add(ct.employee_index)

            # Find the project_id from the first run (they share the same project)
            project_id = employees[0].project_id if employees else None
            if not project_id:
                proj_result = await db.execute(
                    select(Project).where(Project.repo == ct.project_repo)
                )
                proj = proj_result.scalar_one_or_none()
                project_id = proj.id if proj else 0

            parent_run = employees[0] if employees else None
            # Issue #336: surface per-teammate tokens/turns from CoordinatorTask.
            # Earlier code copied the lead's aggregate (parent_run.tokens_total)
            # onto every teammate, which made every cell display the same number
            # — or 0 before the first batch flush. Prefer the per-task counters
            # populated by handle_teammate_progress; fall back to the aggregate
            # only if the per-task counter is missing.
            tokens = ct.tokens_total
            if tokens is None and parent_run is not None:
                tokens = parent_run.tokens_total
            employees.append(ActiveEmployeeOut(
                run_id=ct.run_id,
                project_id=project_id,
                mode="employee",
                status="running",
                issue_number=ct.issue_number,
                turns=ct.turns,
                employee_index=ct.employee_index,
                concurrent_group_id=ct.run_id,
                model=None,
                branch=ct.branch,
                tokens_total=tokens,
                started_at=ct.started_at or (parent_run.started_at if parent_run else None),
            ))

    return employees


@router.get("/active-teammates", response_model=list[ActiveTeammateOut])
async def get_active_teammates(db: AsyncSession = Depends(get_db)):
    """Alias for active-employees using Agent Teams terminology."""
    return await get_active_employees(db)


@router.get("/telemetry-summary", response_model=TelemetrySummaryOut)
async def get_telemetry_summary(db: AsyncSession = Depends(get_db)):
    """Aggregate the four telemetry cells on the Dispatch board:
    Active runs, Queue stats, Tokens (7d) summary + sparkline,
    and a coarse System health label derived from disk/memory.
    """
    # --- 1. Active runs / teammates / roles ---
    active_runs_res = await db.execute(
        select(Run).where(Run.status.in_(["running", "plan_reviewing", "reviewing"]))
    )
    active_runs = active_runs_res.scalars().all()

    roles: list[str] = []
    teammates_count = 0
    # NOTE: ``CoordinatorTask`` does not currently carry an explicit ``role``
    # column — see ``dashboard/backend/app/models.py`` (the only ``role`` is
    # on ``CoordinatorMessage`` for chat direction). Until a schema migration
    # adds one (and the orchestrator populates it), we fall back to substring
    # matching teammate names against the canonical role tags. This is
    # brittle: a teammate named ``frontend-spright`` matches but a renamed
    # variant like ``ui-spright`` would silently drop out.
    # TODO(#311): once teammate roles are first-class (either as a column on
    # CoordinatorTask or as an explicit ``role`` field in the JSON written
    # to ``Run.team_members``), prefer that over name-substring matching.
    _ROLE_TAGS = ("backend", "frontend", "qa", "lead")
    if active_runs:
        for r in active_runs:
            members_raw = r.team_members
            if not members_raw:
                continue
            try:
                members = json.loads(members_raw) if isinstance(members_raw, str) else members_raw
                if isinstance(members, list):
                    teammates_count += len(members)
                    for m in members:
                        # Prefer an explicit ``role`` field if the
                        # orchestrator started writing one; otherwise fall
                        # back to substring-matching the name.
                        explicit = (m.get("role") if isinstance(m, dict) else None) or ""
                        name = (m.get("name") if isinstance(m, dict) else str(m)) or ""
                        if explicit and explicit.lower() in _ROLE_TAGS and explicit.lower() not in roles:
                            roles.append(explicit.lower())
                            continue
                        for tag in _ROLE_TAGS:
                            if tag in name.lower() and tag not in roles:
                                roles.append(tag)
            except Exception:  # noqa: BLE001 — best-effort introspection
                continue

        # Fallback: derive from running coordinator tasks if team_members was empty.
        if teammates_count == 0:
            ct_res = await db.execute(
                select(CoordinatorTask).where(
                    CoordinatorTask.status == "running",
                    CoordinatorTask.run_id.in_([r.run_id for r in active_runs]),
                )
            )
            tasks = ct_res.scalars().all()
            teammates_count = len(tasks)
            for t in tasks:
                # CoordinatorTask has no role column today; match teammate
                # name as a brittle proxy. See TODO(#311) above.
                name = (t.claimed_by or t.teammate_agent_id or "") or ""
                for tag in _ROLE_TAGS:
                    if tag in name.lower() and tag not in roles:
                        roles.append(tag)

    active = TelemetryActive(
        count=len(active_runs),
        teammates=teammates_count,
        roles=roles,
    )

    # --- 2. Queue stats ---
    qstate_res = await db.execute(
        select(QueueItem.state, func.count(QueueItem.id)).group_by(QueueItem.state)
    )
    by_state = {row[0]: row[1] for row in qstate_res.all()}
    _CLAIMED_STATES = {"claimed", "assigned", "planning", "in_progress"}
    _DONE_STATES = {"completed", "approved"}
    _PENDING_STATES = {"pending"}
    claimed = sum(v for k, v in by_state.items() if k in _CLAIMED_STATES)
    done = sum(v for k, v in by_state.items() if k in _DONE_STATES)
    pending = sum(v for k, v in by_state.items() if k in _PENDING_STATES)
    queue_total = sum(by_state.values())
    # Anything not bucketed above (e.g. ``failed``, ``paused``, ``cancelled``,
    # plus any future states) gets routed into ``other`` so the cells add up
    # to ``total`` and the operator can see queue items aren't simply lost.
    other = queue_total - (claimed + done + pending)
    if other < 0:
        other = 0  # defensive: shouldn't happen, but keep ``other`` non-negative.

    queue = TelemetryQueue(
        total=queue_total,
        claimed=claimed,
        done=done,
        pending=pending,
        other=other,
    )

    # --- 3. Tokens (7d) — sum, run count, in/out, sparkline ---
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=7)
    daily_q = (
        select(
            func.date(Run.started_at).label("date"),
            func.coalesce(func.sum(Run.tokens_total), 0).label("tot"),
        )
        .where(Run.started_at >= cutoff)
        .group_by(func.date(Run.started_at))
        .order_by(func.date(Run.started_at))
    )
    daily_rows = (await db.execute(daily_q)).all()
    # Backfill missing days with 0 so the sparkline always has 7 points
    # ordered oldest → today. ``func.date(...)`` returns a string in SQLite,
    # so we key by ISO date strings.
    by_day = {str(row.date): int(row.tot or 0) for row in daily_rows}
    today = now_utc.date()
    spark: list[int] = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        spark.append(by_day.get(day.isoformat(), 0))

    sum_q = (
        select(
            func.coalesce(func.sum(Run.tokens_total), 0).label("total"),
            func.coalesce(func.sum(Run.tokens_input), 0).label("input"),
            func.coalesce(func.sum(Run.tokens_output), 0).label("output"),
            func.count(Run.id).label("runs"),
        ).where(Run.started_at >= cutoff)
    )
    sum_row = (await db.execute(sum_q)).mappings().one()
    tot7 = sum_row["total"] or 0
    in7 = sum_row["input"] or 0
    out7 = sum_row["output"] or 0
    runs7 = sum_row["runs"] or 0

    tokens_7d = TelemetryTokens7d(
        total=int(tot7),
        runs=int(runs7),
        input=int(in7),
        output=int(out7),
        spark=spark,
    )

    # --- 4. System status ---
    try:
        resources = await get_system_resources()
    except Exception:  # noqa: BLE001
        resources = {}

    disk_free = resources.get("disk_free_gb")
    mem_used = resources.get("memory_used_mb")
    mem_total = resources.get("memory_total_mb")
    uptime = resources.get("uptime_seconds")
    mem_pct: int | None = None
    if mem_used is not None and mem_total:
        mem_pct = int(round(100 * mem_used / mem_total))

    status_label = "NOMINAL"
    # CRIT thresholds
    if (disk_free is not None and disk_free < 1) or (mem_pct is not None and mem_pct > 90):
        status_label = "CRIT"
    elif (disk_free is not None and disk_free < 5) or (mem_pct is not None and mem_pct > 70):
        status_label = "DEGR"

    system = TelemetrySystem(
        status=status_label,
        disk_free_gb=disk_free,
        memory_used_pct=mem_pct,
        uptime_secs=uptime,
    )

    # --- 5. Verdicts (7d) — same cutoff as tokens above ---
    # Group APPROVE → ok, PR → pr, anything else non-null (REJECT and any
    # future terminal verdict tag) → x. NULL verdicts are skipped (run still
    # in flight, or never reached the manager). Verdicts are stored
    # case-sensitively as APPROVE/PR/REJECT but we lower-case the SQL output
    # to be defensive against historical writes that mixed cases.
    verdict_q = (
        select(
            func.lower(Run.verdict).label("verdict"),
            func.count(Run.id).label("n"),
        )
        .where(Run.started_at >= cutoff, Run.verdict.isnot(None))
        .group_by(func.lower(Run.verdict))
    )
    verdict_rows = (await db.execute(verdict_q)).all()
    v_ok = 0
    v_pr = 0
    v_x = 0
    for v, n in verdict_rows:
        if v == "approve":
            v_ok += int(n or 0)
        elif v == "pr":
            v_pr += int(n or 0)
        else:
            v_x += int(n or 0)

    verdicts_7d = TelemetryVerdicts7d(ok=v_ok, pr=v_pr, x=v_x)

    return TelemetrySummaryOut(
        active=active,
        queue=queue,
        tokens_7d=tokens_7d,
        system=system,
        verdicts_7d=verdicts_7d,
    )


@router.get("/latest", response_model=Optional[RunOut])
async def get_latest_run(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Run).order_by(desc(Run.started_at)).limit(1)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="No runs found")
    return run


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Run).where(Run.run_id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/{run_id}/full", response_model=RunFullContext)
async def get_run_full_context(run_id: str, db: AsyncSession = Depends(get_db)):
    """Return unified run context: run + coordinator tasks + queue item + plan.

    This powers the unified Run Detail view (AC2) by fetching all related
    data in a single request instead of requiring 4+ separate API calls.
    """
    # 1. Fetch the run
    result = await db.execute(select(Run).where(Run.run_id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    # 2. Fetch coordinator tasks for this run
    tasks_result = await db.execute(
        select(CoordinatorTask).where(CoordinatorTask.run_id == run_id)
    )
    tasks = tasks_result.scalars().all()

    # 3. Fetch coordinator messages for this run
    msgs_result = await db.execute(
        select(CoordinatorMessage)
        .where(CoordinatorMessage.run_id == run_id)
        .order_by(CoordinatorMessage.created_at)
    )
    messages = msgs_result.scalars().all()

    # 4. Fetch related queue items (by run_id). Issue #290 wired the
    # orchestrator to drain multiple QueueItems per run when a plan_only
    # run was approved, so ``scalar_one_or_none()`` here would 500 with
    # ``MultipleResultsFound`` for any post-#290 run that drained more
    # than one item. ``queue_item`` is kept as the first row for old
    # consumers; ``queue_items`` exposes the full list.
    queue_result = await db.execute(
        select(QueueItem)
        .where(QueueItem.run_id == run_id)
        .order_by(QueueItem.id.asc())
    )
    queue_items = list(queue_result.scalars().all())
    queue_item = queue_items[0] if queue_items else None

    # 5. Fetch related plan (by implementation_run_id or run_id)
    plan_result = await db.execute(
        select(Plan).where(
            (Plan.implementation_run_id == run_id) | (Plan.run_id == run_id)
        )
    )
    plan = plan_result.scalar_one_or_none()

    # 6. Get project repo name
    project_repo: str | None = None
    if run.project_id:
        proj_result = await db.execute(
            select(Project).where(Project.id == run.project_id)
        )
        project = proj_result.scalar_one_or_none()
        if project:
            project_repo = project.repo

    # 7. Fetch intelligence decisions (agent events with intelligence.* type)
    intel_result = await db.execute(
        select(AgentEvent)
        .where(AgentEvent.run_id == run_id)
        .where(AgentEvent.event_type.like("intelligence.%"))
        .order_by(AgentEvent.created_at.asc())
    )
    intel_events = intel_result.scalars().all()

    # Build team summary if this is an Agent Teams run
    team_summary = None
    if run.team_name:
        import json as _json
        members_raw = _json.loads(run.team_members) if run.team_members else []
        teammates = [
            TeammateStatus(
                agent_id=m.get("agent_id", ""),
                name=m.get("name", ""),
                task_id=m.get("task_id"),
                issue_number=m.get("issue_number"),
                status=m.get("status", "spawned"),
                turns_used=m.get("turns_used", 0),
                tokens_used=m.get("tokens_used", 0),
            )
            for m in members_raw
        ]
        completed = sum(1 for t in tasks if t.status == "completed")
        in_progress = sum(1 for t in tasks if t.status in ("running", "in_progress"))
        team_summary = TeamSummary(
            team_name=run.team_name,
            teammates=teammates,
            tasks_total=len(tasks),
            tasks_completed=completed,
            tasks_in_progress=in_progress,
        )

    return RunFullContext(
        run=RunOut.model_validate(run),
        coordinator_tasks=[CoordinatorTaskOut.model_validate(t) for t in tasks],
        coordinator_messages=[CoordinatorMessageOut.model_validate(m) for m in messages],
        queue_item=QueueItemOut.model_validate(queue_item) if queue_item else None,
        queue_items=[QueueItemOut.model_validate(q) for q in queue_items],
        plan=PlanOut.model_validate(plan) if plan else None,
        project_repo=project_repo,
        intelligence_decisions=[AgentEventOut.model_validate(e) for e in intel_events],
        team_summary=team_summary,
    )


@router.get("/{run_id}/diff", response_model=DiffResult)
async def get_run_diff(run_id: str, db: AsyncSession = Depends(get_db)):
    """Return the git diff for a run's branch vs the base branch.

    Looks up the run to find its branch and project, then runs
    `git diff <base_branch>...<employee_branch>` in the project workspace.
    Returns a structured diff with per-file hunks and line data.
    """
    # 1. Look up the run
    result = await db.execute(select(Run).where(Run.run_id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    # No branch means no diff (e.g., analyze mode)
    if not run.branch:
        return DiffResult()

    # 2. Look up the project to get workspace path and base branch
    base_branch = "main"
    repo_name: str | None = None

    if run.project_id:
        proj_result = await db.execute(
            select(Project).where(Project.id == run.project_id)
        )
        project = proj_result.scalar_one_or_none()
        if project:
            base_branch = project.branch or "main"
            repo_name = project.repo.split("/")[-1] if "/" in project.repo else project.repo

    if not repo_name:
        # Try to infer from the employee report or return empty
        return DiffResult()

    # 3. Compute the workspace path
    workspace = Path(settings.workspaces_dir) / repo_name
    if not workspace.is_dir():
        return DiffResult()

    # 4. Check if the branch exists
    try:
        check_branch = await asyncio.create_subprocess_exec(
            "git", "-C", str(workspace), "rev-parse", "--verify", run.branch,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(check_branch.communicate(), timeout=30.0)
        except TimeoutError:
            check_branch.kill()
            await check_branch.wait()
            logger.warning("git rev-parse timed out for run %s", run_id)
            return DiffResult()
        if check_branch.returncode != 0:
            return DiffResult()
    except Exception:
        return DiffResult()

    # 5. Run git diff
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(workspace), "diff",
            f"{base_branch}...{run.branch}", "--",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("git diff timed out for run %s", run_id)
            return DiffResult()

        if proc.returncode != 0:
            logger.warning("git diff failed for run %s: %s", run_id, stderr.decode())
            return DiffResult()

        diff_text = stdout.decode(errors="replace")
    except Exception as exc:
        logger.error("Error running git diff for run %s: %s", run_id, exc)
        return DiffResult()

    # 6. Parse and return
    return parse_unified_diff(diff_text)


@router.post("/rescan")
async def rescan_logs(db: AsyncSession = Depends(get_db)):
    """Manually trigger a re-scan of log files to import new runs."""
    imported = await import_historical_runs(db)
    return {"status": "ok", "imported": imported}


@router.post("/trigger")
async def trigger_run():
    """Trigger the agent service immediately.

    Delegates to :mod:`app.services.service_control` which branches on
    ``STATION_DEPLOY_MODE`` between ``sudo systemctl start`` (systemd
    deployments) and ``POST /run`` on the agent launcher (compose).
    """
    result = await service_control.start_agent_service()
    if not result.get("success"):
        # Compose path may set status_code to a launcher 4xx (e.g. 409
        # "already running") or 502 (unreachable); systemd path returns
        # generic 500. Preserve the upstream status so the UI can show a
        # precise message.
        status = result.get("status_code") or 500
        if status < 400:
            status = 500
        # Detail precedence: structured error fields first, then any
        # JSON ``detail`` from the launcher response, then ``raw`` for
        # plain-text 4xx bodies (the launcher's HTTPException emits JSON
        # but tests and some clients exercise the text path), then a
        # generic fallback.
        raise HTTPException(
            status_code=status,
            detail=(
                result.get("error")
                or result.get("stderr")
                or result.get("detail")
                or result.get("raw")
                or "Failed to trigger run"
            ),
        )
    # Choose the success message based on the actual deploy mode rather
    # than sniffing for ``pid`` in the result. The previous heuristic would
    # silently flip to the launcher message if a future systemd
    # implementation surfaced MainPID.
    is_compose = service_control.deploy_mode() == "compose"
    detail = result.get("detail") or (
        "agent launcher accepted run" if is_compose else "claude-agent.service started"
    )
    return {
        "status": "triggered",
        "detail": detail,
        **{k: v for k, v in result.items() if k not in {"success", "status_code"}},
    }


# --- Mission Control: per-run intervention (Phase A) -----------------------
# The orchestrator polls run_controls between SDK messages. These endpoints
# just enqueue a row and broadcast an SSE event; the actual pause/stop/
# message-injection happens agent-side in station_orchestrator.py.

# Terminal run statuses — controls targeting these runs are rejected because
# the orchestrator has already exited and no polling loop will ever drain
# them. This closes the Mission Control "orphan row" hole where the UI was
# happily queueing messages to dead runs.
_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "interrupted"})


async def _require_live_run(db: AsyncSession, run_id: str) -> Run:
    """Fetch the run, 404 if missing, 409 if it has already terminated.

    Callers use this for pause/resume/stop/message — all three of which are
    pointless (and misleading) once the run's orchestrator has exited.
    """
    result = await db.execute(select(Run).where(Run.run_id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if (run.status or "") in _TERMINAL_RUN_STATUSES or run.finished_at is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Run {run_id} is no longer active "
                f"(status={run.status or 'unknown'}) — intervention has no effect."
            ),
        )
    return run


async def _run_exists(db: AsyncSession, run_id: str) -> None:
    """Back-compat: only verifies existence, does not check status.

    Retained for internal callers that deliberately want to allow controls
    on terminated runs (there are none today, but tests rely on it). Public
    endpoints use :func:`_require_live_run` instead.
    """
    result = await db.execute(select(Run.id).where(Run.run_id == run_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Run not found")


async def _queue_control(
    db: AsyncSession,
    run_id: str,
    action: str,
    payload: dict | None = None,
    requested_by: str = "api",
) -> RunControlAck:
    import json as _json

    row = RunControl(
        run_id=run_id,
        action=action,
        payload=_json.dumps(payload) if payload else None,
        requested_by=requested_by,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    # Broadcast so the UI updates without polling.
    await publish({
        "type": f"run_control_{action}",
        "data": {
            "run_id": run_id,
            "control_id": row.id,
            "requested_by": requested_by,
            "payload": payload or {},
        },
    })
    return RunControlAck(
        run_id=run_id,
        action=action,
        control_id=row.id,
        queued_at=row.requested_at,
    )


@router.post("/{run_id}/pause", response_model=RunControlAck)
async def pause_run(run_id: str, db: AsyncSession = Depends(get_db)):
    """Ask the orchestrator to route every subsequent tool call for this run
    to the permission tray. Unblock with ``POST /api/runs/{run_id}/resume``
    or individual tray approvals. Returns 409 if the run has ended."""
    await _require_live_run(db, run_id)
    return await _queue_control(db, run_id, "pause")


@router.post("/{run_id}/resume", response_model=RunControlAck)
async def resume_run(run_id: str, db: AsyncSession = Depends(get_db)):
    """Clear the per-run pause flag so the policy engine goes back to the
    configured autonomy level. Returns 409 if the run has ended."""
    await _require_live_run(db, run_id)
    return await _queue_control(db, run_id, "resume")


@router.post("/{run_id}/stop", response_model=RunControlAck)
async def stop_run(run_id: str, db: AsyncSession = Depends(get_db)):
    """Hard stop: kill claude-agent.service AND queue a cooperative unwind.

    Why "hard": most runs today execute inside the bash ``run-manager.sh``
    launched by ``claude-agent.service`` — that path does NOT poll the
    ``run_controls`` queue, so cooperative-only stop silently failed and
    the button looked broken. We now issue ``systemctl stop
    claude-agent.service`` unconditionally so the agent actually dies,
    then flip every currently-active run to ``interrupted`` so the UI
    reflects the change within a second instead of minutes-later via the
    stale-run reaper.

    The cooperative-stop queue row is still written for Python SDK runs
    that DO read it; when both mechanisms fire, whichever lands first
    wins and the other is a no-op.
    """
    await _require_live_run(db, run_id)

    ack = await _queue_control(db, run_id, "stop")

    # Fire-and-forget — systemctl stop is bounded by the unit's own
    # TimeoutStopSec (and our 10s subprocess timeout). We don't block the
    # HTTP response on it; the status flip below is what the UI reads.
    asyncio.create_task(systemctl("stop", "claude-agent.service"))

    # Mark every active run terminated. claude-agent.service is the shared
    # process so killing it ends all of them; reflecting that in the DB
    # now avoids the "still running" ghost on the dashboard.
    now = datetime.now(timezone.utc)
    running = await db.execute(
        select(Run).where(
            Run.status.in_(("started", "running", "reviewing", "plan_reviewing"))
        )
    )
    for active in running.scalars().all():
        active.status = "interrupted"
        if active.finished_at is None:
            active.finished_at = now
    await db.commit()

    await publish({
        "type": "run_interrupted",
        "data": {
            "run_id": run_id,
            "reason": "operator_stop_hard",
            "hard_kill": True,
        },
    })

    logger.warning(
        "Mission Control: hard stop issued for %s — systemctl stop claude-agent.service + mark interrupted",
        run_id,
    )

    return ack


@router.post("/{run_id}/message", response_model=RunControlAck)
async def message_run(
    run_id: str,
    payload: RunMessage,
    db: AsyncSession = Depends(get_db),
):
    """Inject a user message into the agent's next turn. The orchestrator
    resumes the SDK session with this text prepended, so it behaves as
    though the operator typed it in an interactive chat. Returns 409 if the
    run has already ended — previously messages were silently orphaned."""
    await _require_live_run(db, run_id)
    return await _queue_control(db, run_id, "message", {"text": payload.text})


# ── Plan-review gate operator override (issue #266 follow-up) ─────────────


_PLAN_REVIEW_AWAITING_STATES = ("awaiting_plan_review",)


def _verdicts_path_for_run(run_id: str) -> Path:
    """Return the manager-verdicts JSON path for a given ``run_id``.

    The shell driver writes ``{LOG_DIR}/{run_id}-verdicts.json``
    (run-manager.sh:1718). The same volume is mounted into the dashboard
    container in compose mode, so we can read it directly.
    """
    log_dir = Path(os.environ.get("STATION_LOG_DIR", "/var/log/claude-agent"))
    return log_dir / f"{run_id}-verdicts.json"


def _build_followup_queue_item(
    *,
    project_repo: str,
    issue_number: int | None,
    plan_path: str | None,
    parent_run_id: str,
    employee_index: int | None = None,
    operator_approved: bool = False,
) -> dict:
    """Build a QueueItem dict for a follow-up ``full`` run after a
    plan_only run was approved.

    Mirrors the shape ``agent.plan_review_gate.build_followup_queue_item``
    produces, but doesn't import from agent/ — the plan-review gate
    runs in the agent container; this runs in the dashboard backend
    after operator action.
    """
    return {
        "project_repo": project_repo,
        "issue_number": issue_number,
        "mode": "full",
        "state": "pending",
        "context": json.dumps({
            "approved_plan_path": plan_path,
            "from_plan_only_run": True,
            "parent_run_id": parent_run_id,
            "parent_employee_index": employee_index,
            "approved_by_operator": operator_approved,
        }),
    }


async def _require_plan_review_run(db: AsyncSession, run_id: str) -> Run:
    """Resolve a plan_only run that's currently waiting on the gate.

    Returns 404 when missing, 409 when the run isn't in the right
    mode/state for an operator override.
    """
    result = await db.execute(select(Run).where(Run.run_id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    if (run.mode or "").lower() != "plan_only":
        raise HTTPException(
            status_code=409,
            detail=f"run {run_id} is mode={run.mode!r}, expected 'plan_only'",
        )
    if run.status not in _PLAN_REVIEW_AWAITING_STATES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"run {run_id} is status={run.status!r}; operator override is only "
                f"available while a plan_only run is awaiting_plan_review"
            ),
        )
    return run


@router.post("/{run_id}/plan/approve")
async def operator_approve_plan(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Operator override: approve the plan(s) from a plan_only run and
    enqueue follow-up ``full`` run(s) to implement them.

    Reads the manager-verdicts JSON to recover the per-employee
    issue/plan mapping. For each entry the operator override treats
    EVERY verdict as approved (regardless of what the manager said) —
    the operator's word overrides the manager's. When the verdicts file
    is missing or empty, the run is still marked ``plan_approved`` but
    no follow-up runs are enqueued — the operator has signalled "stop
    waiting" without binding the orchestrator to specific issues.

    This is the override flow. The auto-approve path (manager's
    ``APPROVE_PLAN`` verdict) lives in :mod:`agent.plan_review_gate`
    and runs from inside the agent container after manager review.
    """
    run = await _require_plan_review_run(db, run_id)
    project = await db.get(Project, run.project_id) if run.project_id else None
    project_repo = project.repo if project else None

    enqueued: list[dict] = []
    verdicts_file = _verdicts_path_for_run(run_id)
    if project_repo and verdicts_file.is_file():
        try:
            data = json.loads(verdicts_file.read_text())
            entries = data.get("plan_verdicts") or []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                payload = _build_followup_queue_item(
                    project_repo=project_repo,
                    issue_number=entry.get("issue_number"),
                    plan_path=entry.get("plan_path"),
                    parent_run_id=run_id,
                    employee_index=entry.get("employee_index"),
                    operator_approved=True,
                )
                qi = QueueItem(
                    project_repo=payload["project_repo"],
                    issue_number=payload["issue_number"],
                    mode=payload["mode"],
                    state=payload["state"],
                    context=payload["context"],
                )
                db.add(qi)
                await db.flush()
                enqueued.append({
                    "id": qi.id,
                    "issue_number": qi.issue_number,
                })
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "Operator approve %s: could not parse verdicts file %s: %s",
                run_id, verdicts_file, exc,
            )

    run.status = "plan_approved"
    if not run.finished_at:
        run.finished_at = datetime.now(timezone.utc)
    await db.commit()

    await publish({
        "type": "run_status",
        "run_id": run_id,
        "status": "plan_approved",
        "operator_approved": True,
    })

    return {
        "run_id": run_id,
        "status": "plan_approved",
        "enqueued": enqueued,
        "verdicts_file_found": verdicts_file.is_file(),
    }


@router.post("/{run_id}/plan/reject")
async def operator_reject_plan(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Operator override: reject the plan(s) from a plan_only run.

    Marks the run ``plan_rejected`` and emits a status webhook so the
    UI banner updates. No follow-up runs are enqueued — operator owns
    the next step (e.g. close the issue, edit the issue body, retrigger
    in a different mode).
    """
    run = await _require_plan_review_run(db, run_id)
    run.status = "plan_rejected"
    if not run.finished_at:
        run.finished_at = datetime.now(timezone.utc)
    await db.commit()

    await publish({
        "type": "run_status",
        "run_id": run_id,
        "status": "plan_rejected",
        "operator_rejected": True,
    })

    return {"run_id": run_id, "status": "plan_rejected"}
