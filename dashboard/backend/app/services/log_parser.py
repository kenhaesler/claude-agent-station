"""Parse agent log files: stream JSONL, verdict JSON, employee reports."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Matches: run-20260308T130028Z-employee-ai-portainer-dashboard.stream.jsonl
# Also:    run-20260307T203444Z-github-issues.stream.jsonl (old format)
# Also:    run-20260308T130028Z-employee-ai-portainer-dashboard.stderr.log
RUN_ID_RE = re.compile(r"run-(\d{8}T\d{6}Z)")
REPO_FROM_EMPLOYEE_RE = re.compile(r"employee-(.+?)\.(?:stream\.jsonl|stderr\.log)")
REPO_FROM_OLD_FORMAT_RE = re.compile(r"run-\d{8}T\d{6}Z-(.+?)\.(?:stream\.jsonl|json|stderr\.log)")


def parse_run_id_from_filename(filename: str) -> str | None:
    """Extract run ID (e.g. '20260308T130028Z') from a log filename."""
    m = RUN_ID_RE.search(filename)
    return m.group(1) if m else None


def parse_repo_from_filename(filename: str) -> str | None:
    """Extract repo name from employee log filename.

    'run-20260308T130028Z-employee-ai-portainer-dashboard.stream.jsonl'
    -> 'ai-portainer-dashboard'
    """
    m = REPO_FROM_EMPLOYEE_RE.search(filename)
    if m:
        return m.group(1)
    # Old format: task name (not a repo, but useful for matching)
    m = REPO_FROM_OLD_FORMAT_RE.search(filename)
    if m:
        name = m.group(1)
        if name not in ("employee",):
            return name
    return None


def parse_run_timestamp(run_id: str) -> datetime | None:
    """Parse '20260308T130028Z' into a datetime."""
    try:
        return datetime.strptime(run_id, "%Y%m%dT%H%M%SZ")
    except ValueError:
        return None


def parse_result_json(filepath: str) -> dict[str, Any] | None:
    """Parse a run result .json file (the old format summary).

    Returns dict with: cost_usd, tokens_input, tokens_output, tokens_total,
    turns, duration_ms, status, model.
    """
    try:
        with open(filepath) as f:
            data = json.load(f)
        if data.get("type") != "result":
            return None
        tokens = _extract_tokens(data)
        return {
            "cost_usd": data.get("total_cost_usd"),
            "tokens_input": tokens[0],
            "tokens_output": tokens[1],
            "tokens_total": tokens[2],
            "turns": data.get("num_turns"),
            "duration_ms": data.get("duration_ms"),
            "status": "success" if data.get("subtype") == "success" else "failed",
            "model": _extract_model(data),
        }
    except Exception as e:
        logger.warning("Failed to parse result JSON %s: %s", filepath, e)
        return None


def parse_stream_result(filepath: str) -> dict[str, Any] | None:
    """Parse the 'result' event from a stream JSONL file (last line usually).

    Returns dict with: cost_usd, turns, duration_ms, status, model.
    """
    try:
        result_line = None
        with open(filepath) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("type") == "result":
                        result_line = data
                except json.JSONDecodeError:
                    continue

        if not result_line:
            return None

        tokens = _extract_tokens(result_line)
        return {
            "cost_usd": result_line.get("total_cost_usd"),
            "tokens_input": tokens[0],
            "tokens_output": tokens[1],
            "tokens_total": tokens[2],
            "turns": result_line.get("num_turns"),
            "duration_ms": result_line.get("duration_ms"),
            "status": "success" if result_line.get("subtype") == "success" else "failed",
            "model": _extract_model(result_line),
        }
    except Exception as e:
        logger.warning("Failed to parse stream JSONL %s: %s", filepath, e)
        return None


def parse_verdicts_file(filepath: str) -> dict[str, Any] | None:
    """Parse a verdicts JSON file.

    Returns dict with: verdicts list, summary.
    """
    try:
        with open(filepath) as f:
            data = json.load(f)
        return data
    except Exception as e:
        logger.warning("Failed to parse verdicts %s: %s", filepath, e)
        return None


def parse_employee_report(repo_name: str) -> dict[str, Any] | None:
    """Read employee report from workspace directory."""
    report_path = Path(settings.workspaces_dir) / repo_name / ".claude-employee-report.json"
    if not report_path.exists():
        return None
    try:
        with open(report_path) as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Failed to parse employee report %s: %s", report_path, e)
        return None


def discover_run_files(log_dir: str) -> dict[str, dict[str, list[str]]]:
    """Scan log directory and group files by run_id.

    Returns: {run_id: {"streams": [...], "results": [...], "verdicts": [...], "stderr": [...]}}
    """
    runs: dict[str, dict[str, list[str]]] = {}

    try:
        filenames = os.listdir(log_dir)
    except OSError as e:
        logger.error("Cannot list log directory %s: %s", log_dir, e)
        return runs

    for fname in filenames:
        run_id = parse_run_id_from_filename(fname)
        if not run_id:
            continue

        if run_id not in runs:
            runs[run_id] = {"streams": [], "results": [], "verdicts": [], "stderr": []}

        full_path = os.path.join(log_dir, fname)

        if fname.endswith(".stream.jsonl"):
            runs[run_id]["streams"].append(full_path)
        elif fname.endswith("-verdicts.json"):
            runs[run_id]["verdicts"].append(full_path)
        elif fname.endswith(".json") and not fname.endswith("-verdicts.json"):
            runs[run_id]["results"].append(full_path)
        elif fname.endswith(".stderr.log"):
            runs[run_id]["stderr"].append(full_path)

    return runs


def _extract_model(data: dict[str, Any]) -> str | None:
    """Extract model name from result data."""
    model_usage = data.get("modelUsage", {})
    if model_usage:
        return next(iter(model_usage.keys()))
    return None


def _extract_tokens(data: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    """Extract token usage from result data.

    Claude CLI result events contain a modelUsage dict with per-model token counts.
    We sum across all models to get totals.

    Returns: (input_tokens, output_tokens, total_tokens)
    """
    model_usage = data.get("modelUsage", {})
    if not model_usage:
        return (None, None, None)

    total_input = 0
    total_output = 0
    for _model, usage in model_usage.items():
        total_input += usage.get("inputTokens", 0) or 0
        total_output += usage.get("outputTokens", 0) or 0

    total = total_input + total_output
    return (total_input or None, total_output or None, total or None)
