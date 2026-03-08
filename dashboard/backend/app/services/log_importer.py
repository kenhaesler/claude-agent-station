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
    """Scan log directory and import runs not yet in DB. Returns count imported."""
    run_files = discover_run_files(settings.log_dir)
    imported = 0

    # Pre-fetch existing run_ids to avoid per-run queries
    result = await db.execute(select(Run.run_id))
    existing_ids = {row[0] for row in result.fetchall()}

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
        if full_run_id in existing_ids:
            continue

        run_data = _build_run_data(run_id, files, projects_by_short)
        if run_data is None:
            continue

        run = Run(**run_data)
        db.add(run)
        imported += 1

    if imported > 0:
        await db.commit()

    logger.info("Imported %d historical runs (%d already existed)", imported, len(existing_ids))
    return imported


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
        "turns": result_data.get("turns") if result_data else None,
        "duration_ms": result_data.get("duration_ms") if result_data else None,
        "started_at": started_at,
        "finished_at": finished_at,
        "employee_report": employee_report,
        "verdict_detail": verdict_detail,
        "log_file": log_file,
    }
