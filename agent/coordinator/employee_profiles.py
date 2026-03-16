"""Employee performance profiles — emergent specialization from outcomes.

Instead of hard-coding roles, specialization emerges from historical
performance data in the task_outcomes table. Each employee builds a
profile showing success rates by issue_type, subsystem, complexity,
and file area familiarity.

Inspired by CrewAI research on emergent role specialization.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Default success rate for unknown dimensions (prior)
DEFAULT_RATE = 0.5


@dataclass
class EmployeeProfile:
    """Learned capability profile — emergent from outcomes, NOT configured."""

    employee_index: int

    # Success rates by dimension (computed from task_outcomes)
    by_issue_type: dict[str, float] = field(default_factory=dict)
    by_subsystem: dict[str, float] = field(default_factory=dict)
    by_complexity: dict[int, float] = field(default_factory=dict)

    # Efficiency metrics
    avg_tokens_per_success: float = 0.0
    avg_duration_per_success: float = 0.0

    # Calibration
    confidence_calibration: float = 1.0  # >1 = underconfident, <1 = overconfident

    # Sample sizes
    total_outcomes: int = 0
    outcomes_by_type: dict[str, int] = field(default_factory=dict)
    outcomes_by_subsystem: dict[str, int] = field(default_factory=dict)

    # File area familiarity (directory -> count of tasks touching it)
    file_areas: dict[str, int] = field(default_factory=dict)

    # Current state (set by scheduler, not from DB)
    current_tasks: int = 0
    currently_touching: set[str] = field(default_factory=set)

    @property
    def is_mature(self) -> bool:
        """Profile has enough data to be trusted for routing."""
        return self.total_outcomes >= 5


def build_employee_profiles(
    db_path: str,
    project_repo: str | None = None,
) -> dict[int, EmployeeProfile]:
    """Build profiles from historical task_outcomes.

    Args:
        db_path: Path to the SQLite database
        project_repo: If given, filter outcomes to this project

    Returns:
        dict mapping employee_index -> EmployeeProfile
    """
    profiles: dict[int, EmployeeProfile] = {}

    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row
    except Exception as e:
        logger.warning("Failed to connect to DB for profiles: %s", e)
        return profiles

    try:
        where = "WHERE employee_index IS NOT NULL"
        params: list = []
        if project_repo:
            where += " AND project_repo = ?"
            params.append(project_repo)

        # Fetch all outcomes with employee_index
        rows = conn.execute(
            f"SELECT * FROM task_outcomes {where} ORDER BY created_at",
            params,
        ).fetchall()

        for row in rows:
            idx = row["employee_index"]
            if idx not in profiles:
                profiles[idx] = EmployeeProfile(employee_index=idx)
            p = profiles[idx]
            p.total_outcomes += 1
            success = bool(row["success"])

            # By issue type
            itype = row["issue_type"]
            if itype:
                p.outcomes_by_type[itype] = p.outcomes_by_type.get(itype, 0) + 1

            # By subsystem
            sub = row["subsystem"]
            if sub:
                p.outcomes_by_subsystem[sub] = p.outcomes_by_subsystem.get(sub, 0) + 1

        # Compute success rates by dimension
        _compute_rates(conn, profiles, where, params)

        # Compute efficiency metrics
        _compute_efficiency(conn, profiles, where, params)

        # Compute confidence calibration
        _compute_calibration(conn, profiles, where, params)

    except Exception as e:
        logger.warning("Failed to build employee profiles: %s", e)
    finally:
        conn.close()

    return profiles


def _compute_rates(
    conn: sqlite3.Connection,
    profiles: dict[int, EmployeeProfile],
    where: str,
    params: list,
) -> None:
    """Compute success rates by issue_type, subsystem, and complexity."""
    # By issue type
    rows = conn.execute(
        f"""SELECT employee_index, issue_type,
            COUNT(*) as total,
            SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as wins
        FROM task_outcomes
        {where} AND issue_type IS NOT NULL
        GROUP BY employee_index, issue_type""",
        params,
    ).fetchall()
    for row in rows:
        idx = row["employee_index"]
        if idx in profiles:
            rate = row["wins"] / max(1, row["total"])
            profiles[idx].by_issue_type[row["issue_type"]] = rate

    # By subsystem
    rows = conn.execute(
        f"""SELECT employee_index, subsystem,
            COUNT(*) as total,
            SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as wins
        FROM task_outcomes
        {where} AND subsystem IS NOT NULL
        GROUP BY employee_index, subsystem""",
        params,
    ).fetchall()
    for row in rows:
        idx = row["employee_index"]
        if idx in profiles:
            rate = row["wins"] / max(1, row["total"])
            profiles[idx].by_subsystem[row["subsystem"]] = rate

    # By complexity
    rows = conn.execute(
        f"""SELECT employee_index, complexity_score,
            COUNT(*) as total,
            SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as wins
        FROM task_outcomes
        {where} AND complexity_score IS NOT NULL
        GROUP BY employee_index, complexity_score""",
        params,
    ).fetchall()
    for row in rows:
        idx = row["employee_index"]
        if idx in profiles:
            rate = row["wins"] / max(1, row["total"])
            profiles[idx].by_complexity[row["complexity_score"]] = rate


def _compute_efficiency(
    conn: sqlite3.Connection,
    profiles: dict[int, EmployeeProfile],
    where: str,
    params: list,
) -> None:
    """Compute avg tokens and duration per successful outcome."""
    rows = conn.execute(
        f"""SELECT employee_index,
            AVG(tokens_consumed) as avg_tokens,
            AVG(duration_seconds) as avg_duration
        FROM task_outcomes
        {where} AND success = 1
        AND tokens_consumed IS NOT NULL
        GROUP BY employee_index""",
        params,
    ).fetchall()
    for row in rows:
        idx = row["employee_index"]
        if idx in profiles:
            profiles[idx].avg_tokens_per_success = row["avg_tokens"] or 0.0
            profiles[idx].avg_duration_per_success = row["avg_duration"] or 0.0


def _compute_calibration(
    conn: sqlite3.Connection,
    profiles: dict[int, EmployeeProfile],
    where: str,
    params: list,
) -> None:
    """Compute confidence calibration: how well self-reported confidence matches reality.

    calibration > 1.0 = underconfident (safe to trust more)
    calibration < 1.0 = overconfident (discount their confidence)
    calibration = 1.0 = perfectly calibrated
    """
    rows = conn.execute(
        f"""SELECT employee_index,
            AVG(confidence_reported) as avg_confidence,
            AVG(CASE WHEN success = 1 THEN 1.0 ELSE 0.0 END) as actual_rate
        FROM task_outcomes
        {where} AND confidence_reported IS NOT NULL
        GROUP BY employee_index
        HAVING COUNT(*) >= 3""",
        params,
    ).fetchall()
    for row in rows:
        idx = row["employee_index"]
        if idx in profiles:
            avg_conf = row["avg_confidence"] or 0.5
            actual = row["actual_rate"] or 0.5
            if avg_conf > 0:
                profiles[idx].confidence_calibration = actual / avg_conf
            else:
                profiles[idx].confidence_calibration = 1.0


def detect_failure_patterns(
    project_repo: str,
    db_path: str,
) -> list[dict]:
    """Detect recurring failure patterns from task_outcomes.

    Returns list of pattern dicts with description and recommendation.
    """
    patterns: list[dict] = []

    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row
    except Exception:
        return patterns

    try:
        # Pattern: high complexity always fails with certain mode
        rows = conn.execute(
            """SELECT mode_used, complexity_score,
                COUNT(*) as total,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as wins
            FROM task_outcomes
            WHERE project_repo = ? AND complexity_score >= 4
            GROUP BY mode_used, complexity_score
            HAVING total >= 3""",
            (project_repo,),
        ).fetchall()
        for row in rows:
            rate = row["wins"] / max(1, row["total"])
            if rate < 0.4:
                patterns.append({
                    "type": "complexity_mode_mismatch",
                    "description": (
                        f"Complexity {row['complexity_score']} with "
                        f"{row['mode_used']} mode has {rate:.0%} success rate"
                    ),
                    "recommendation": "Escalate earlier or use full mode with Opus",
                })

        # Pattern: specific subsystem always fails
        rows = conn.execute(
            """SELECT subsystem, employee_index,
                COUNT(*) as total,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as wins
            FROM task_outcomes
            WHERE project_repo = ? AND subsystem IS NOT NULL AND employee_index IS NOT NULL
            GROUP BY subsystem, employee_index
            HAVING total >= 3""",
            (project_repo,),
        ).fetchall()
        for row in rows:
            rate = row["wins"] / max(1, row["total"])
            if rate < 0.4:
                patterns.append({
                    "type": "subsystem_employee_mismatch",
                    "description": (
                        f"Employee {row['employee_index']} has {rate:.0%} success "
                        f"on {row['subsystem']} tasks"
                    ),
                    "recommendation": f"Avoid assigning {row['subsystem']} tasks to employee {row['employee_index']}",
                })

    except Exception as e:
        logger.warning("Failed to detect failure patterns: %s", e)
    finally:
        conn.close()

    return patterns


def get_project_averages(db_path: str, project_repo: str) -> EmployeeProfile:
    """Get project-wide average profile for bootstrapping new employees."""
    default = EmployeeProfile(employee_index=-1)
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row

        row = conn.execute(
            """SELECT
                AVG(CASE WHEN success = 1 THEN 1.0 ELSE 0.0 END) as avg_rate,
                AVG(CASE WHEN success = 1 THEN tokens_consumed ELSE NULL END) as avg_tokens,
                AVG(CASE WHEN success = 1 THEN duration_seconds ELSE NULL END) as avg_duration,
                COUNT(*) as total
            FROM task_outcomes
            WHERE project_repo = ?""",
            (project_repo,),
        ).fetchone()

        if row and row["total"] > 0:
            default.by_issue_type = {"feature": row["avg_rate"] or 0.5}
            default.by_subsystem = {"mixed": row["avg_rate"] or 0.5}
            default.avg_tokens_per_success = row["avg_tokens"] or 0.0
            default.avg_duration_per_success = row["avg_duration"] or 0.0
            default.total_outcomes = row["total"]

        conn.close()
    except Exception:
        pass

    return default
