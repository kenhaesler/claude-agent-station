"""Per-project iteration entry-point.

This module currently delegates to the existing bash logic via
subprocess. As the migration progresses (follow-up sub-PRs to #349),
the per-project body will be ported here directly. The interface is
stable across the migration so the RunDriver doesn't change.

See spec/plan: 2026-05-11-run-lifecycle-overhaul.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def iterate_projects(run_id: str, config_path: str,
                     workspaces_dir: str) -> int:
    """Iterate over enabled projects and dispatch agent work.

    Currently shells to ``run-manager.sh --internal-iterate`` which
    contains the legacy bash body. Migration sub-PRs will replace this
    with native Python iteration.

    Returns exit code: 0 on success, non-zero on failure. Does not
    raise — the caller (RunDriver) handles exceptions via try/finally
    around its own webhook emission.
    """
    script_dir = Path(__file__).resolve().parent / "scripts"
    runmgr = script_dir / "run-manager.sh"
    if not runmgr.exists():
        logger.error("project_loop: run-manager.sh not found at %s", runmgr)
        return 127

    env = os.environ.copy()
    env["STATION_RUN_ID_OVERRIDE"] = run_id

    logger.info("project_loop: shelling to %s --internal-iterate", runmgr)
    result = subprocess.run(
        [str(runmgr), "--internal-iterate"],
        env=env,
        cwd=workspaces_dir if Path(workspaces_dir).exists() else None,
    )
    return result.returncode
