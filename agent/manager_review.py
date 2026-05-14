"""Manager review phase — invoke `claude -p` against a review package and parse verdicts.

Python port of agent/scripts/run-manager.sh::run_manager_review (issue #383).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

from agent.verdict_execution import Verdict

logger = logging.getLogger(__name__)


class ManagerReviewError(RuntimeError):
    """Raised when manager review fails (bad exit, bad JSON, empty input)."""


def _manager_prompt_path() -> Path:
    return Path(__file__).resolve().parent / "prompts" / "manager.md"


def run_manager_review(
    review_package_path: str, run_id: str, config: dict,
) -> list[Verdict]:
    """Invoke claude -p with the manager prompt and the review package.

    Returns the list of Verdict objects parsed from stdout. Raises
    ManagerReviewError on bad inputs / bad exits / unparseable output.
    """
    pkg = Path(review_package_path)
    if not pkg.is_file():
        raise ManagerReviewError(f"review package not found: {pkg}")
    contents = pkg.read_text()
    if not contents.strip():
        raise ManagerReviewError("review package is empty")

    model = (config.get("models") or {}).get("manager", "claude-sonnet-4-6")
    prompt_path = _manager_prompt_path()
    if not prompt_path.is_file():
        raise ManagerReviewError(f"manager prompt missing: {prompt_path}")

    cmd = [
        "claude", "-p", contents,
        "--model", model,
        "--system-prompt-file", str(prompt_path),
        "--output-format", "json",
    ]
    env = os.environ.copy()
    env["STATION_RUN_ID"] = run_id

    logger.info("manager_review: invoking claude -p (run=%s, model=%s)", run_id, model)
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=1800)
    if result.returncode != 0:
        raise ManagerReviewError(
            f"claude -p exited {result.returncode}: {result.stderr.strip()[:500]}"
        )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ManagerReviewError(f"manager output is not JSON: {exc}") from exc

    verdicts_raw = payload.get("verdicts") or []
    return [Verdict.from_dict(v) for v in verdicts_raw]
