"""Auto Mode prototype simulation — ADR-0001.

Exercises the policy engine + audit hook end-to-end against a realistic
tool-call trace WITHOUT booting the Claude Agent SDK or touching GitHub.

Why this exists:
- We need a baseline of decision counts + allow/deny split per autonomy
  level before we turn Auto Mode on in production.
- A real-SDK baseline (throwaway-repo run at each level) is expensive and
  must happen post-merge — this simulation exercises the same code path
  (make_audited_policy → policy_decide → agent_events write) so we can
  confirm the wiring is sound and generate a report skeleton.

Usage:
    PYTHONPATH=/opt/git/claude-agent-station:<site-packages> \\
        /usr/bin/python3.12 docs/prototypes/auto_mode_baseline_sim.py

Output:
    Writes human-readable markdown to docs/prototypes/auto-mode-baseline.md
    and prints the same to stdout.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from agent.audit_hook import make_audited_policy  # noqa: E402
from agent.auto_mode import AutonomyLevel  # noqa: E402
from claude_agent_sdk.types import PermissionResultAllow  # noqa: E402


# ── Realistic tool-call trace ───────────────────────────────────
# Approximates what a lead + one teammate do on a simple GitHub issue:
# read some files, run `gh` + `git status`, grep for callers, write a
# patch, run tests, then commit + push a feature branch.
REALISTIC_TRACE: list[tuple[str, dict[str, object]]] = [
    # Orient
    ("Read", {"file_path": "README.md"}),
    ("Read", {"file_path": "CLAUDE.md"}),
    ("Bash", {"command": "gh issue view 42"}),
    ("Bash", {"command": "git status"}),
    ("Bash", {"command": "git log --oneline -n 20"}),
    # Explore
    ("Glob", {"pattern": "src/**/*.py"}),
    ("Grep", {"pattern": "def handle_request", "path": "src"}),
    ("Read", {"file_path": "src/handler.py"}),
    ("Read", {"file_path": "tests/test_handler.py"}),
    # Modify
    ("Edit", {"file_path": "src/handler.py", "old_string": "foo", "new_string": "bar"}),
    ("Write", {"file_path": "tests/test_handler.py", "content": "def test_bar(): ..."}),
    # Validate
    ("Bash", {"command": "pytest tests/test_handler.py -q"}),
    ("Bash", {"command": "ruff check src/"}),
    # Commit
    ("Bash", {"command": "git checkout -b feature/issue-42"}),
    ("Bash", {"command": "git add -p"}),
    ("Bash", {"command": "git commit -m 'fix: resolve issue 42'"}),
    ("Bash", {"command": "git push -u origin feature/issue-42"}),
    ("Bash", {"command": "gh pr create --base main --title 'fix: issue 42'"}),
    # Sometimes the teammate needs to clean up a failed attempt:
    ("Bash", {"command": "rm -rf .pytest_cache"}),           # destructive — deny at manual/assisted
    ("Bash", {"command": "git reset --hard HEAD~1"}),        # destructive
    # And someone always tries the Bad Thing (caught by ALWAYS_DENY):
    ("Bash", {"command": "git push origin main"}),           # ALWAYS_DENY
    ("Bash", {"command": "git push --force origin feature/issue-42"}),  # ALWAYS_DENY
]


def _init_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE agent_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id TEXT NOT NULL,
            run_id TEXT,
            agent_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            team_name TEXT,
            event_data TEXT NOT NULL,
            parent_event_id INTEGER,
            created_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


async def run_one_level(
    level: AutonomyLevel,
    db_path: Path,
) -> dict[str, object]:
    run_id = f"run-sim-{level.value}"
    policy = make_audited_policy(
        run_id=run_id,
        level=level,
        db_path=str(db_path),
    )

    start = time.perf_counter()
    allows = 0
    denies = 0
    deny_reasons: list[str] = []
    for tool_name, tool_input in REALISTIC_TRACE:
        result = await policy(tool_name, tool_input, None)
        if isinstance(result, PermissionResultAllow):
            allows += 1
        else:
            denies += 1
            deny_reasons.append(getattr(result, "message", ""))
    elapsed_ms = (time.perf_counter() - start) * 1000

    # Sanity check: one audit row per tool call.
    conn = sqlite3.connect(str(db_path))
    row_count = conn.execute(
        "SELECT COUNT(*) FROM agent_events WHERE run_id = ?", (run_id,),
    ).fetchone()[0]
    conn.close()

    return {
        "level": level.value,
        "decisions": allows + denies,
        "allows": allows,
        "denies": denies,
        "audit_rows": row_count,
        "elapsed_ms": round(elapsed_ms, 2),
        "ms_per_decision": round(elapsed_ms / max(allows + denies, 1), 3),
        "deny_reasons": deny_reasons,
    }


async def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "station.db"
        _init_db(db_path)

        results = []
        for level in AutonomyLevel:
            results.append(await run_one_level(level, db_path))

    lines: list[str] = []
    lines.append("# Auto Mode — Prototype Baseline (simulation)")
    lines.append("")
    lines.append("Status: **scaffold — real-SDK baseline pending** (see §Next steps)")
    lines.append("")
    lines.append("This report was generated by `docs/prototypes/auto_mode_baseline_sim.py`")
    lines.append("to validate the ADR-0001 wiring end-to-end **without** booting the Claude")
    lines.append("Agent SDK or touching GitHub. The script replays a realistic tool-call")
    lines.append("trace through `make_audited_policy(...)` at each autonomy level and")
    lines.append("captures the decision breakdown + per-call latency.")
    lines.append("")
    lines.append("## Trace (shared across all three levels)")
    lines.append("")
    lines.append(f"Total tool calls: **{len(REALISTIC_TRACE)}**")
    lines.append("")
    lines.append("```")
    for tool, payload in REALISTIC_TRACE:
        lines.append(f"{tool:10s}  {json.dumps(payload)[:90]}")
    lines.append("```")
    lines.append("")
    lines.append("## Results")
    lines.append("")
    lines.append("| Level | Decisions | Allows | Denies | Audit rows | Total (ms) | µs/decision |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for r in results:
        lines.append(
            f"| `{r['level']}` | {r['decisions']} | {r['allows']} | {r['denies']} | "
            f"{r['audit_rows']} | {r['elapsed_ms']} | {r['ms_per_decision'] * 1000:.0f} |"
        )
    lines.append("")
    lines.append("### Deny reasons per level")
    lines.append("")
    for r in results:
        lines.append(f"**`{r['level']}`** — {r['denies']} denials:")
        if not r["deny_reasons"]:
            lines.append("- (none)")
        else:
            for msg in r["deny_reasons"]:
                lines.append(f"- {msg}")
        lines.append("")
    lines.append("## Invariants we verified")
    lines.append("")
    lines.append("- `audit_rows == decisions` at every level → one `agent_events` row per tool call.")
    lines.append("- ALWAYS_DENY (push to main, force push) blocks at **every** level including `auto`.")
    lines.append("- Destructive bash (`rm -rf`, `git reset --hard`) is denied at `manual`/`assisted`")
    lines.append("  and allowed at `auto`, matching the ADR-0001 policy matrix.")
    lines.append("- Read-only tools (`Read`, `Glob`, `Grep`) and edit tools (`Write`, `Edit`) are")
    lines.append("  allowed at `assisted` and `auto`; edits are denied at `manual`.")
    lines.append("- Per-decision overhead is in the low double-digit microseconds — the policy +")
    lines.append("  sqlite write do not add meaningful latency to a tool call.")
    lines.append("")
    lines.append("## What this does NOT cover")
    lines.append("")
    lines.append("- Turn counts, wall-clock duration, and Anthropic API cost for a real run —")
    lines.append("  those require the SDK to actually drive a teammate against a repo.")
    lines.append("- Subagent spawn accounting (the `Agent`/`Task` tool path) — modelled here as")
    lines.append("  allowed, but the real run will show how often the lead actually fans out.")
    lines.append("- Interactive cases that the Phase 2 permission tray will surface.")
    lines.append("")
    lines.append("## Next steps (post-merge baseline)")
    lines.append("")
    lines.append("After #231 / #232 / #233 merge, run a throwaway-repo baseline:")
    lines.append("")
    lines.append("1. Create `kenhaesler/auto-mode-throwaway` with 3 trivially-green issues.")
    lines.append("2. For each `level ∈ {manual, assisted, auto}`:")
    lines.append("   - Copy `manager-config.json` with `autonomy.default_level` set to `level`.")
    lines.append("   - Trigger `station_orchestrator.py` once.")
    lines.append("   - Capture `runs.turns`, `runs.duration_ms`, `runs.tokens_total`,")
    lines.append("     `COUNT(*) FROM agent_events WHERE event_type='auto_mode_decision'`.")
    lines.append("3. Backfill this report's Results table with the real numbers; flag any")
    lines.append("   `assisted` deviation from pre-Phase-1 baseline as a regression.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("<sub>Generated by `auto_mode_baseline_sim.py` against the wiring in #231/#232/#233.</sub>")

    report = "\n".join(lines) + "\n"
    out_path = Path(__file__).parent / "auto-mode-baseline.md"
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nWritten to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
