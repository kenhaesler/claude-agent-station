"""Borg-style two-phase smart router for issue-to-employee assignment.

Phase 1: Feasibility filter (instant, zero LLM cost)
Phase 2: Suitability scoring + optimal matching via Hungarian algorithm

Inspired by Google Borg (two-phase scheduling), Kubernetes (affinity/anti-affinity).
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from agent.coordinator.issue_profiler import IssueProfile, profile_issue
from agent.coordinator.employee_profiles import (
    EmployeeProfile,
    build_employee_profiles,
    get_project_averages,
    DEFAULT_RATE,
)

logger = logging.getLogger(__name__)

# Labels that should be skipped entirely
SKIP_LABELS = {
    "autonomous-agent/in-progress",
    "autonomous-agent/needs-help",
    "autonomous-agent/refined",
    "NO AI",
    "backlog",
    "wontfix",
}


@dataclass
class Assignment:
    """Result of smart routing: issue → employee with reasoning."""

    employee_index: int
    issue_number: int
    issue_title: str
    issue_profile: IssueProfile
    mode: str
    model: str
    max_turns: int
    suitability_score: float
    reasoning: str
    instructions: str


def route_issues(
    issues: list[IssueProfile],
    employees: list[EmployeeProfile],
    max_concurrent: int = 5,
    max_per_project: int = 3,
) -> list[Assignment]:
    """Two-phase assignment: Feasibility → Scoring → Optimal matching."""
    if not issues or not employees:
        return []

    # Phase 1: Build feasibility matrix
    n_issues = len(issues)
    n_employees = len(employees)

    # Phase 2: Build suitability score matrix
    try:
        import numpy as np
        from scipy.optimize import linear_sum_assignment
    except ImportError:
        logger.warning("scipy/numpy not available, using greedy assignment")
        return _greedy_assign(issues, employees)

    cost_matrix = np.full((n_issues, n_employees), 1e6)

    for i, issue in enumerate(issues):
        for e, emp in enumerate(employees):
            if _is_feasible(issue, emp):
                score = _score(issue, emp)
                cost_matrix[i][e] = -score  # Minimize cost = maximize score

    # Optimal assignment via Hungarian algorithm (O(n³))
    try:
        issue_idx, emp_idx = linear_sum_assignment(cost_matrix)
    except ValueError:
        logger.warning("Hungarian algorithm failed, using greedy")
        return _greedy_assign(issues, employees)

    assignments: list[Assignment] = []
    for i, e in zip(issue_idx, emp_idx):
        if cost_matrix[i][e] >= 1e6:
            continue  # Infeasible pair

        issue = issues[i]
        emp = employees[e]
        score = -cost_matrix[i][e]
        mode, model, turns = _select_mode_model(issue)

        assignments.append(Assignment(
            employee_index=emp.employee_index,
            issue_number=issue.number,
            issue_title=issue.title,
            issue_profile=issue,
            mode=mode,
            model=model,
            max_turns=turns,
            suitability_score=score,
            reasoning=_build_reasoning(issue, emp, score),
            instructions=_build_instructions(issue),
        ))

    # Sort by suitability score (highest first)
    assignments.sort(key=lambda a: a.suitability_score, reverse=True)
    return assignments


def select_best_employee(
    issue: IssueProfile,
    employees: list[EmployeeProfile],
) -> int | None:
    """Select the single best employee for a given issue.

    Used by the coordinator scheduler for per-task assignment.
    Returns employee_index or None if no feasible assignment.
    """
    best_idx = None
    best_score = -1.0

    for emp in employees:
        if not _is_feasible(issue, emp):
            continue
        score = _score(issue, emp)
        if score > best_score:
            best_score = score
            best_idx = emp.employee_index

    return best_idx


def _is_feasible(issue: IssueProfile, employee: EmployeeProfile) -> bool:
    """Phase 1: Hard constraint checks (zero cost, instant)."""
    # Skip issues with blocking labels
    if set(issue.labels) & SKIP_LABELS:
        return False

    # Skip issues that already have a PR
    if issue.has_pr:
        return False

    # Don't assign if employee is overloaded
    if employee.current_tasks >= 2:
        return False

    return True


def _score(issue: IssueProfile, employee: EmployeeProfile) -> float:
    """Phase 2: Compute suitability score (0-100 scale)."""
    score = 0.0

    # 1. Type affinity (0-25 points)
    type_rate = employee.by_issue_type.get(issue.issue_type, DEFAULT_RATE)
    score += type_rate * 25

    # 2. Subsystem affinity (0-25 points)
    sub_rate = employee.by_subsystem.get(issue.subsystem, DEFAULT_RATE)
    score += sub_rate * 25

    # 3. Complexity match (0-20 points)
    comp_rate = employee.by_complexity.get(issue.complexity, DEFAULT_RATE)
    score += comp_rate * 20

    # 4. File area familiarity (0-15 points)
    if issue.mentioned_dirs and employee.file_areas:
        familiarity = sum(
            employee.file_areas.get(d, 0)
            for d in issue.mentioned_dirs
        ) / max(1, len(issue.mentioned_dirs))
        score += min(15, familiarity * 3)

    # 5. Load balance bonus (0-10 points)
    if employee.current_tasks == 0:
        score += 10
    else:
        score += 5 / employee.current_tasks

    # 6. Conflict penalty (-20 points)
    if employee.currently_touching and issue.mentioned_files:
        overlap = len(set(issue.mentioned_files) & employee.currently_touching)
        score -= overlap * 10

    # 7. Previous failure penalty (-10 points per attempt)
    if issue.previous_attempts > 0:
        score -= issue.previous_attempts * 5

    # 8. Priority bonus (0-8 points)
    score += issue.priority * 2

    return max(0, score)


def _select_mode_model(issue: IssueProfile) -> tuple[str, str, int]:
    """Select mode, model, and max_turns based on issue profile."""
    from agent.coordinator.modes import MODE_REGISTRY

    # Bug with low complexity -> fix mode
    if issue.issue_type == "bug" and issue.complexity <= 2:
        mode = "fix"
    elif issue.complexity >= 4:
        mode = "full"
    else:
        mode = "full"

    spec = MODE_REGISTRY.get(mode)
    if spec:
        model = spec.default_model
        turns = spec.default_max_turns
    else:
        model = "claude-sonnet-4-6"
        turns = 200

    # Upgrade model for high complexity
    if issue.complexity >= 4:
        model = "claude-opus-4-6"

    return mode, model, turns


def _build_reasoning(issue: IssueProfile, emp: EmployeeProfile, score: float) -> str:
    """Build human-readable reasoning for an assignment."""
    parts = [f"Score: {score:.1f}/100"]

    type_rate = emp.by_issue_type.get(issue.issue_type, DEFAULT_RATE)
    if emp.is_mature:
        parts.append(f"{issue.issue_type} success: {type_rate:.0%}")

    sub_rate = emp.by_subsystem.get(issue.subsystem, DEFAULT_RATE)
    if emp.is_mature:
        parts.append(f"{issue.subsystem} success: {sub_rate:.0%}")

    if not emp.is_mature:
        parts.append("(new employee, using defaults)")

    return " | ".join(parts)


def _build_instructions(issue: IssueProfile) -> str:
    """Build instructions for the employee based on issue analysis."""
    parts = [f"Type: {issue.issue_type}, Subsystem: {issue.subsystem}"]
    if issue.mentioned_files:
        parts.append(f"Key files: {', '.join(issue.mentioned_files[:5])}")
    if issue.previous_attempts > 0:
        parts.append(f"Previous attempts: {issue.previous_attempts}")
        if issue.last_failure_category:
            parts.append(f"Last failure: {issue.last_failure_category}")
    return ". ".join(parts)


def _greedy_assign(
    issues: list[IssueProfile],
    employees: list[EmployeeProfile],
) -> list[Assignment]:
    """Fallback greedy assignment when scipy is not available."""
    assignments: list[Assignment] = []
    used_employees: set[int] = set()

    # Sort issues by priority (descending)
    sorted_issues = sorted(issues, key=lambda i: i.priority, reverse=True)

    for issue in sorted_issues:
        best_emp = None
        best_score = -1.0

        for emp in employees:
            if emp.employee_index in used_employees:
                continue
            if not _is_feasible(issue, emp):
                continue
            score = _score(issue, emp)
            if score > best_score:
                best_score = score
                best_emp = emp

        if best_emp is not None:
            mode, model, turns = _select_mode_model(issue)
            assignments.append(Assignment(
                employee_index=best_emp.employee_index,
                issue_number=issue.number,
                issue_title=issue.title,
                issue_profile=issue,
                mode=mode,
                model=model,
                max_turns=turns,
                suitability_score=best_score,
                reasoning=_build_reasoning(issue, best_emp, best_score),
                instructions=_build_instructions(issue),
            ))
            used_employees.add(best_emp.employee_index)

    return assignments


def _fetch_task_outcomes(db_path: str) -> dict[int, list[dict]]:
    """Fetch task outcomes grouped by issue number."""
    import sqlite3
    outcomes: dict[int, list[dict]] = {}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT issue_number, success, failure_category, mode_used, "
            "complexity_score, employee_index FROM task_outcomes "
            "WHERE issue_number IS NOT NULL"
        ).fetchall()
        conn.close()
        for row in rows:
            num = row["issue_number"]
            outcomes.setdefault(num, []).append(dict(row))
    except Exception as e:
        logger.warning("Failed to fetch task_outcomes: %s", e)
    return outcomes


# ---------------------------------------------------------------------------
# CLI entry point for run-manager.sh
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI interface for smart routing from run-manager.sh."""
    parser = argparse.ArgumentParser(description="Smart issue-to-employee router")
    parser.add_argument("--repo", required=True, help="GitHub repo (owner/name)")
    parser.add_argument("--workspace", required=True, help="Workspace path")
    parser.add_argument("--employee-count", type=int, required=True)
    parser.add_argument("--config", required=True, help="Config file path")
    parser.add_argument("--run-id", default="", help="Current run ID")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Load config for DB path
    config_data = {}
    try:
        config_data = json.loads(Path(args.config).read_text())
    except Exception:
        pass

    db_path = (
        config_data.get("coordinator", {}).get("db_path")
        or "/var/lib/claude-agent-station/station.db"
    )

    # Fetch open issues
    try:
        result = subprocess.run(
            ["gh", "issue", "list", "--repo", args.repo, "--state", "open",
             "--limit", "30", "--json", "number,title,body,labels,assignees"],
            capture_output=True, text=True, timeout=30, cwd=args.workspace,
        )
        if result.returncode != 0:
            logger.error("Failed to fetch issues: %s", result.stderr)
            sys.exit(1)
        issues_data = json.loads(result.stdout)
    except Exception as e:
        logger.error("Failed to fetch issues: %s", e)
        sys.exit(1)

    # Fetch open PRs
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--repo", args.repo, "--state", "all",
             "--json", "number,title,headRefName,state"],
            capture_output=True, text=True, timeout=15, cwd=args.workspace,
        )
        prs = json.loads(result.stdout) if result.returncode == 0 else []
    except Exception:
        prs = []

    # Fetch task outcomes for learning loop
    all_outcomes = _fetch_task_outcomes(db_path)

    # Profile each issue
    profiles = [
        profile_issue(
            issue,
            open_prs=prs,
            task_outcomes=all_outcomes.get(issue.get("number"), None),
        )
        for issue in issues_data
    ]

    # Filter out issues with skip labels
    profiles = [p for p in profiles if not (set(p.labels) & SKIP_LABELS)]

    if not profiles:
        logger.info("No suitable issues found for assignment")
        # Write empty assignments
        _write_assignments(args.workspace, [], args.employee_count)
        sys.exit(0)

    # Build employee profiles from historical data
    emp_profiles = build_employee_profiles(db_path, project_repo=args.repo)

    # Ensure we have profiles for all employee indices
    employees: list[EmployeeProfile] = []
    default_profile = get_project_averages(db_path, args.repo)
    for i in range(args.employee_count):
        if i in emp_profiles:
            employees.append(emp_profiles[i])
        else:
            # New employee gets project-wide averages
            ep = EmployeeProfile(employee_index=i)
            ep.by_issue_type = dict(default_profile.by_issue_type)
            ep.by_subsystem = dict(default_profile.by_subsystem)
            employees.append(ep)

    # Run smart routing
    assignments = route_issues(profiles, employees)

    # Write assignment files
    _write_assignments(args.workspace, assignments, args.employee_count)

    # Report results
    for a in assignments:
        logger.info(
            "Assigned issue #%d (%s) to employee %d [score=%.1f, mode=%s]",
            a.issue_number, a.issue_title[:40], a.employee_index,
            a.suitability_score, a.mode,
        )

    if not assignments:
        logger.info("No assignments produced")
        sys.exit(1)  # Signal to run-manager.sh to fall back


def _write_assignments(
    workspace: str,
    assignments: list[Assignment],
    employee_count: int,
) -> None:
    """Write assignment files compatible with existing employee protocol."""
    assigned_employees: set[int] = set()

    for a in assignments:
        assignment_data = {
            "employee_index": a.employee_index,
            "issue_number": a.issue_number,
            "issue_title": a.issue_title,
            "instructions": a.instructions,
            "mode": a.mode,
            "model": a.model,
            "max_turns": a.max_turns,
            "suitability_score": a.suitability_score,
            "reasoning": a.reasoning,
            "labels": a.issue_profile.labels,
            "body": a.issue_profile.body,
        }
        path = Path(workspace) / f".claude-assignment-{a.employee_index}.json"
        path.write_text(json.dumps(assignment_data, indent=2))
        assigned_employees.add(a.employee_index)

    # Write the combined assignments JSON for compatibility
    combined = {
        "assignments": [
            {
                "employee_index": a.employee_index,
                "issue_number": a.issue_number,
                "issue_title": a.issue_title,
                "instructions": a.instructions,
            }
            for a in assignments
        ],
        "unassigned_employees": [
            i for i in range(employee_count) if i not in assigned_employees
        ],
    }
    combined_path = Path(workspace) / ".claude-assignments.json"
    combined_path.write_text(json.dumps(combined, indent=2))


if __name__ == "__main__":
    main()
