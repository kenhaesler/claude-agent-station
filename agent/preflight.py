"""Preflight checks executed before any project work begins.

Python port of agent/scripts/run-manager.sh::preflight (issue #383).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class PreflightError(RuntimeError):
    """Raised when a preflight check fails."""


_REQUIRED_BINARIES = ("git", "gh", "claude")


def _has_binary(name: str) -> bool:
    return shutil.which(name) is not None


def _refresh_oauth_token() -> bool:
    """Invoke agent/scripts/refresh-token.py. Returns True on success."""
    script = Path(__file__).resolve().parent / "scripts" / "refresh-token.py"
    if not script.exists():
        return False
    result = subprocess.run(
        ["python3", str(script)],
        capture_output=True, text=True, timeout=30,
    )
    return result.returncode == 0


def _rate_limit_tripped() -> bool:
    from agent.rate_limit import is_tripped
    return is_tripped()


def run_preflight(config_path: str) -> None:
    """Run all preflight checks. Raises PreflightError on the first failure."""
    cfg_path = Path(config_path)
    if not cfg_path.is_file():
        raise PreflightError(f"config not readable: {cfg_path}")
    try:
        json.loads(cfg_path.read_text())
    except json.JSONDecodeError as exc:
        raise PreflightError(f"config invalid JSON: {exc}") from exc

    for binary in _REQUIRED_BINARIES:
        if not _has_binary(binary):
            raise PreflightError(f"required binary not on PATH: {binary}")

    if not os.environ.get("CLAUDE_OAUTH_TOKEN"):
        if not _refresh_oauth_token():
            raise PreflightError("OAuth token absent and refresh failed")

    if _rate_limit_tripped():
        raise PreflightError("rate limit tripped — refusing to start a new run")

    logger.info("preflight: all checks passed")
