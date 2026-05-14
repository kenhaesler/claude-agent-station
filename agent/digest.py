"""Run-digest markdown writer.

Python port of agent/scripts/run-manager.sh::write_digest (issue #383).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def write_digest(*, run_id: str, results: list[dict], log_dir: str) -> str:
    """Write a markdown digest for the run. Returns the absolute output path."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    out = Path(log_dir) / f"{run_id}-digest.md"

    lines: list[str] = [
        f"# Run Digest — {run_id}",
        "",
        f"_Generated: {datetime.now(timezone.utc).isoformat()}_",
        "",
    ]
    if not results:
        lines += ["## Verdicts", "", "_No verdicts produced this run._", ""]
    else:
        lines += ["## Verdicts", ""]
        by_project: dict[str, list[dict]] = {}
        for r in results:
            by_project.setdefault(r.get("project", "?"), []).append(r)
        for project, items in by_project.items():
            lines.append(f"### {project}")
            lines.append("")
            for item in items:
                num = item.get("issue_number")
                dec = item.get("decision", "?")
                branch = item.get("branch", "")
                reason = item.get("reasoning") or item.get("error") or ""
                lines.append(f"- **#{num}** — `{dec}`" + (f" — `{branch}`" if branch else "")
                             + (f" — {reason}" if reason else ""))
            lines.append("")

    out.write_text("\n".join(lines))
    logger.info("digest: wrote %s (%d verdicts)", out, len(results))
    return str(out)
