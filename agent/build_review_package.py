"""CLI shim that calls :func:`station_orchestrator._ensure_review_package`.

Why this exists: the lead spawns the manager sibling mid-session, but
the orchestrator's post-session ``_ensure_review_package`` only runs
*after* the lead session ends. On 2026-05-21 two LCM runs hit the
resulting failure mode — the manager read ``run-<id>-review.md``,
found no file, fell back to globbing the base workspace, and picked up
last run's stale ``.claude-employee-report-*.json`` files.

The lead now invokes this CLI via Bash immediately before spawning the
manager. Iterating worktree paths at *that* moment captures the work
the current run's teammates actually produced, and the resulting
review package lands at the path the manager-paths sidecar already
names. Both the in-session call and the existing post-session
finally-block call use the same idempotent helper, so a second
invocation is a cheap no-op when the file is already populated.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agent.build_review_package",
        description=(
            "Compose the manager review package for the current run from "
            "worktree employee reports + diff summaries. Idempotent: a "
            "non-empty file at the target path is left untouched."
        ),
    )
    parser.add_argument(
        "--run-id", required=True,
        help="Run id without the 'run-' prefix (e.g. 20260521T151955Z).",
    )
    parser.add_argument(
        "--log-dir", required=True,
        help="Directory where run-<id>-review.md will be written.",
    )
    parser.add_argument(
        "--workspaces", required=True, nargs="+",
        help="One or more worktree paths to scan for "
             ".claude-employee-report-*.json files.",
    )
    parser.add_argument(
        "--mode", default="full",
        help="Project mode tag for the package header. Default: full.",
    )
    args = parser.parse_args(argv)

    # Imported lazily so ``python -m agent.build_review_package --help`` is
    # cheap and does not pull in the full SDK/asyncio stack.
    from agent.station_orchestrator import _ensure_review_package

    workspaces = [Path(p) for p in args.workspaces]
    out = _ensure_review_package(
        run_id=args.run_id,
        log_dir=Path(args.log_dir),
        workspaces=workspaces,
        mode=args.mode,
    )
    # Print the absolute path so the lead can Bash-capture it and pass it
    # forward without parsing prose.
    print(str(out))
    return 0


if __name__ == "__main__":  # pragma: no cover — module CLI entry
    sys.exit(main())
