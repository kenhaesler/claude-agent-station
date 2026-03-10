"""Import existing log files into the database on startup."""

import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Project, Run
from app.services.log_parser import (
    discover_run_files,
    parse_run_id_from_filename,
    parse_repo_from_filename,
    parse_run_timestamp,
    parse_result_json,
    parse_stream_result,
    parse_verdicts_file,
    parse_employee_report,
)

logger = logging.getLogger(__name__)


async def import_historical_runs(db: AsyncSession) -> int:
    """Scan log directory and import/update runs in DB. Returns count of new imports."""
    run_files = discover_run_files(settings.log_dir)
    imported = 0

    # Pre-fetch existing runs to check for new ones and incomplete ones
    result = await db.execute(select(Run))
    existing_runs = {r.run_id: r for r in result.scalars().all()}

    # Pre-fetch projects for repo matching
    result = await db.execute(select(Project))
    projects = {p.repo: p for p in result.scalars().all()}
    # Also index by short name (last part of owner/repo)
    projects_by_short = {}
    for repo, proj in projects.items():
        short = repo.split("/")[-1] if "/" in repo else repo
        projects_by_short[short] = proj

    for run_id, files in run_files.items():
        full_run_id = f"run-{run_id}"
        run_data = _build_run_data(run_id, files, projects_by_short)
        if run_data is None:
            continue

        existing = existing_runs.get(full_run_id)
        if existing is None:
            # New run — insert it
            run = Run(**run_data)
            db.add(run)
            imported += 1
        else:
            # Existing run — fill in any missing fields from log files
            _update_run_from_logs(existing, run_data)

    if imported > 0 or run_files:
        await db.commit()

    logger.info("Imported %d historical runs (%d already existed)", imported, len(existing_runs))
    return imported


def _update_run_from_logs(run: "Run", data: dict) -> None:
    """Update an existing Run record with data from log files if fields are missing."""
    # Only fill in fields that are currently empty/None
    if not run.status or run.status == "running" or run.status == "unknown":
        if data.get("status") and data["status"] not in ("unknown",):
            run.status = data["status"]
    if not run.model and data.get("model"):
        run.model = data["model"]
    if not run.cost_usd and data.get("cost_usd"):
        run.cost_usd = data["cost_usd"]
    if not run.tokens_input and data.get("tokens_input"):
        run.tokens_input = data["tokens_input"]
    if not run.tokens_output and data.get("tokens_output"):
        run.tokens_output = data["tokens_output"]
    if not run.tokens_total and data.get("tokens_total"):
        run.tokens_total = data["tokens_total"]
    if not run.turns and data.get("turns"):
        run.turns = data["turns"]
    if not run.duration_ms and data.get("duration_ms"):
        run.duration_ms = data["duration_ms"]
    if not run.started_at and data.get("started_at"):
        run.started_at = data["started_at"]
    if not run.finished_at and data.get("finished_at"):
        run.finished_at = data["finished_at"]
    if not run.verdict and data.get("verdict"):
        run.verdict = data["verdict"]
    if not run.verdict_detail and data.get("verdict_detail"):
        run.verdict_detail = data["verdict_detail"]
    if not run.issue_number and data.get("issue_number"):
        run.issue_number = data["issue_number"]
    if not run.branch and data.get("branch"):
        run.branch = data["branch"]
    if not run.employee_report and data.get("employee_report"):
        run.employee_report = data["employee_report"]
    if not run.log_file and data.get("log_file"):
        run.log_file = data["log_file"]
    if not run.project_id and data.get("project_id"):
        run.project_id = data["project_id"]


def _build_run_data(
    run_id: str,
    files: dict,
    projects_by_short: dict,
) -> Optional[dict]:
    """Build a Run record dict from log files."""
    started_at = parse_run_timestamp(run_id)

    # Determine project from filenames
    project_id = None
    repo_name = None
    for stream_file in files.get("streams", []):
        repo_name = parse_repo_from_filename(stream_file.split("/")[-1])
        if repo_name and repo_name in projects_by_short:
            project_id = projects_by_short[repo_name].id
            break

    # Parse result data from stream JSONL (preferred) or result JSON
    result_data = None
    for stream_file in files.get("streams", []):
        result_data = parse_stream_result(stream_file)
        if result_data:
            break

    if not result_data:
        for result_file in files.get("results", []):
            result_data = parse_result_json(result_file)
            if result_data:
                break

    # Parse verdict data
    verdict = None
    verdict_detail = None
    issue_number = None
    branch = None
    for verdict_file in files.get("verdicts", []):
        vdata = parse_verdicts_file(verdict_file)
        if vdata and vdata.get("verdicts"):
            # Match verdict to repo if possible
            for v in vdata["verdicts"]:
                v_project = v.get("project", "")
                v_short = v_project.split("/")[-1] if "/" in v_project else v_project
                if repo_name and v_short == repo_name:
                    verdict = v.get("verdict")
                    issue_number = v.get("issue_number")
                    branch = v.get("branch")
                    verdict_detail = json.dumps(v)
                    break
            # If no repo match, take first verdict
            if not verdict and vdata["verdicts"]:
                v = vdata["verdicts"][0]
                verdict = v.get("verdict")
                issue_number = v.get("issue_number")
                branch = v.get("branch")
                verdict_detail = json.dumps(v)
            break

    # Parse employee report if we know the repo
    employee_report = None
    if repo_name:
        report = parse_employee_report(repo_name)
        if report:
            employee_report = json.dumps(report)

    # Build finished_at from started_at + duration
    finished_at = None
    if started_at and result_data and result_data.get("duration_ms"):
        from datetime import timedelta
        finished_at = started_at + timedelta(milliseconds=result_data["duration_ms"])

    # Determine log file (first stream file)
    log_file = None
    if files.get("streams"):
        log_file = files["streams"][0]

    return {
        "run_id": f"run-{run_id}",
        "project_id": project_id,
        "mode": None,  # Not in log files
        "model": result_data.get("model") if result_data else None,
        "status": result_data.get("status") if result_data else "unknown",
        "verdict": verdict,
        "issue_number": issue_number,
        "branch": branch,
        "cost_usd": result_data.get("cost_usd") if result_data else None,
        "tokens_input": result_data.get("tokens_input") if result_data else None,
        "tokens_output": result_data.get("tokens_output") if result_data else None,
        "tokens_total": result_data.get("tokens_total") if result_data else None,
        "turns": result_data.get("turns") if result_data else None,
        "duration_ms": result_data.get("duration_ms") if result_data else None,
        "started_at": started_at,
        "finished_at": finished_at,
        "employee_report": employee_report,
        "verdict_detail": verdict_detail,
        "log_file": log_file,
    }
