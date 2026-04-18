"""Tests for shell helpers in agent/scripts/run-manager.sh — ADR-0001.

We don't want to mock the whole shell pipeline, but the auto-draft rate
limit is pure state + timestamp logic that's worth pinning. We source the
script in a subshell with a throwaway rate-limit directory.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RUN_MANAGER = REPO_ROOT / "agent" / "scripts" / "run-manager.sh"


def _run_helper(snippet: str, *, env_overrides: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Source run-manager.sh and run a bash snippet against the helpers.

    We short-circuit the script's main flow by setting a sentinel that causes
    the top-level argument parser to no-op; in practice we just source the
    file and then exec our snippet. The script defines functions at top level
    but also runs main logic — we wrap the call in `( ... )` so side effects
    like `set -e` don't kill the test host.
    """
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)

    # Use `bash -c`: source the script with sourcing short-circuited to just
    # pick up function definitions by setting CLAUDE_AGENT_STATION_SOURCE_ONLY.
    # The script doesn't honour such a flag today, so we instead inline the
    # helper functions by copying them with `declare -f` after sourcing.
    # Simpler: source, then run the snippet; if the source side-effects try
    # to run main, they'll hit missing args and exit. We guard with ||true.
    cmd = f"source '{RUN_MANAGER}' 2>/dev/null || true; {snippet}"
    result = subprocess.run(
        ["bash", "-c", cmd], env=env, capture_output=True, text=True, timeout=10,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def test_rate_limit_allows_first_call(tmp_path):
    """With no lockfile, auto_draft_rate_limit_allowed returns 0 (allow)."""
    rc, _, _ = _run_helper(
        'auto_draft_rate_limit_allowed "owner/repo"; echo $?',
        env_overrides={
            "AUTO_DRAFT_RATE_LIMIT_DIR": str(tmp_path),
            "AUTO_DRAFT_RATE_LIMIT_SECONDS": "3600",
        },
    )
    # We ran the helper then echoed $?; capture its stdout.
    result = subprocess.run(
        [
            "bash", "-c",
            f"source '{RUN_MANAGER}' 2>/dev/null || true; "
            f'if auto_draft_rate_limit_allowed "owner/repo"; then echo YES; else echo NO; fi',
        ],
        env={
            **os.environ,
            "AUTO_DRAFT_RATE_LIMIT_DIR": str(tmp_path),
            "AUTO_DRAFT_RATE_LIMIT_SECONDS": "3600",
        },
        capture_output=True, text=True, timeout=10,
    )
    assert "YES" in result.stdout


def test_rate_limit_blocks_within_window(tmp_path):
    """After recording a hit, a second call within the window is denied."""
    env = {
        **os.environ,
        "AUTO_DRAFT_RATE_LIMIT_DIR": str(tmp_path),
        "AUTO_DRAFT_RATE_LIMIT_SECONDS": "3600",
    }
    # Record a hit.
    rec = subprocess.run(
        [
            "bash", "-c",
            f"source '{RUN_MANAGER}' 2>/dev/null || true; "
            f'auto_draft_rate_limit_record "owner/repo"',
        ],
        env=env, capture_output=True, text=True, timeout=10,
    )
    assert rec.returncode == 0

    # Lockfile should exist and be ~now.
    locks = list(tmp_path.glob("*.lock"))
    assert len(locks) == 1
    saved_epoch = int(locks[0].read_text().strip())
    assert abs(saved_epoch - int(time.time())) < 5

    # Second call is denied.
    check = subprocess.run(
        [
            "bash", "-c",
            f"source '{RUN_MANAGER}' 2>/dev/null || true; "
            f'if auto_draft_rate_limit_allowed "owner/repo"; then echo YES; else echo NO; fi',
        ],
        env=env, capture_output=True, text=True, timeout=10,
    )
    assert "NO" in check.stdout


def test_rate_limit_releases_after_window(tmp_path):
    """A stale lockfile older than the window allows a new hit."""
    slug = "owner_repo"
    lock = tmp_path / f"{slug}.lock"
    lock.write_text(str(int(time.time()) - 3700))  # 1h 1m 40s ago

    env = {
        **os.environ,
        "AUTO_DRAFT_RATE_LIMIT_DIR": str(tmp_path),
        "AUTO_DRAFT_RATE_LIMIT_SECONDS": "3600",
    }
    result = subprocess.run(
        [
            "bash", "-c",
            f"source '{RUN_MANAGER}' 2>/dev/null || true; "
            f'if auto_draft_rate_limit_allowed "owner/repo"; then echo YES; else echo NO; fi',
        ],
        env=env, capture_output=True, text=True, timeout=10,
    )
    assert "YES" in result.stdout


def test_rate_limit_slug_sanitises_repo_name(tmp_path):
    """Slug strips path separators so 'owner/repo' doesn't create subdirs."""
    env = {
        **os.environ,
        "AUTO_DRAFT_RATE_LIMIT_DIR": str(tmp_path),
        "AUTO_DRAFT_RATE_LIMIT_SECONDS": "3600",
    }
    subprocess.run(
        [
            "bash", "-c",
            f"source '{RUN_MANAGER}' 2>/dev/null || true; "
            f'auto_draft_rate_limit_record "owner/repo"',
        ],
        env=env, check=True, timeout=10, capture_output=True,
    )
    # Should produce exactly one lockfile at top level, no subdirs.
    subdirs = [p for p in tmp_path.iterdir() if p.is_dir()]
    locks = list(tmp_path.glob("*.lock"))
    assert subdirs == []
    assert len(locks) == 1
    assert "owner_repo" in locks[0].name
