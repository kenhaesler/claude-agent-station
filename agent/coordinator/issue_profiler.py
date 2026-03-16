"""Issue intelligence: classify and profile issues before assignment.

Builds IssueProfile objects used by the smart router to make
informed assignment decisions based on issue characteristics.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Subsystem detection patterns
SUBSYSTEM_PATTERNS: dict[str, list[str]] = {
    "frontend": [
        "dashboard/frontend/", "src/lib/", "src/routes/",
        ".svelte", ".tsx", ".css", ".scss", "tailwind",
    ],
    "backend": [
        "dashboard/backend/", "app/routers/", "app/models",
        "app/services/", "app/schemas", "fastapi", "uvicorn",
    ],
    "agent": [
        "agent/scripts/", "agent/prompts/", "agent/coordinator/",
        "run-manager", "run-employee", "claude",
    ],
    "infra": [
        "systemd", ".service", "Dockerfile", "docker-compose",
        "nginx", ".conf", "deploy", "ci/cd",
    ],
}

# File path pattern for extraction from issue bodies
FILE_PATH_RE = re.compile(
    r'(?:^|\s|`)'
    r'((?:src|dashboard|agent|app|tests?|lib|config|scripts?|public)'
    r'(?:/[\w._-]+)+(?:\.\w+)?)'
    r'(?:\s|`|$|:|\))',
)

# Label → issue type mapping
ISSUE_TYPE_LABELS: dict[str, str] = {
    "bug": "bug",
    "fix": "bug",
    "hotfix": "bug",
    "enhancement": "feature",
    "feature": "feature",
    "feat": "feature",
    "chore": "chore",
    "maintenance": "chore",
    "docs": "chore",
    "documentation": "chore",
    "refactor": "refactor",
    "tech-debt": "refactor",
    "cleanup": "refactor",
}

# Label → priority mapping
PRIORITY_LABELS: dict[str, int] = {
    "priority/critical": 4,
    "priority/high": 3,
    "priority/medium": 2,
    "priority/low": 1,
}


@dataclass
class IssueProfile:
    """Rich profile of a GitHub issue for intelligent routing."""

    number: int
    title: str
    issue_type: str = "feature"       # bug, feature, chore, refactor
    subsystem: str = "mixed"          # frontend, backend, agent, infra, mixed
    complexity: int = 3               # 1-5
    mentioned_files: list[str] = field(default_factory=list)
    mentioned_dirs: list[str] = field(default_factory=list)
    estimated_scope: str = "multi-file"  # single-file, multi-file, cross-cutting
    labels: list[str] = field(default_factory=list)
    priority: int = 0                 # 0-4 (critical=4, high=3, medium=2, low=1)
    has_pr: bool = False
    previous_attempts: int = 0
    last_failure_category: str | None = None
    body: str = ""


def profile_issue(
    issue: dict,
    open_prs: list[dict] | None = None,
    task_outcomes: list[dict] | None = None,
) -> IssueProfile:
    """Build a rich profile from a GitHub issue dict.

    Args:
        issue: GitHub issue JSON (number, title, body, labels, assignees)
        open_prs: List of open PR dicts to check for existing PRs
        task_outcomes: Historical outcomes for this issue number
    """
    number = issue.get("number", 0)
    title = issue.get("title", "")
    body = issue.get("body", "") or ""
    raw_labels = issue.get("labels", [])

    # Normalize labels
    labels = _extract_label_names(raw_labels)

    # Detect issue type from labels
    issue_type = _detect_issue_type(labels)

    # Detect priority from labels
    priority = _detect_priority(labels)

    # Extract mentioned files from body
    mentioned_files = _extract_file_paths(body)
    mentioned_dirs = _extract_dirs(mentioned_files)

    # Detect subsystem from files + body text
    subsystem = detect_subsystem(mentioned_files, title + " " + body)

    # Estimate scope from file count
    scope = _estimate_scope(mentioned_files)

    # Check for existing PR
    has_pr = _has_linked_pr(number, open_prs or [])

    # Check previous attempts
    attempts = 0
    last_failure = None
    if task_outcomes:
        attempts = len(task_outcomes)
        failed = [o for o in task_outcomes if not o.get("success")]
        if failed:
            last_failure = failed[-1].get("failure_category")

    return IssueProfile(
        number=number,
        title=title,
        issue_type=issue_type,
        subsystem=subsystem,
        complexity=3,  # Will be filled by Haiku scorer when available
        mentioned_files=mentioned_files,
        mentioned_dirs=mentioned_dirs,
        estimated_scope=scope,
        labels=labels,
        priority=priority,
        has_pr=has_pr,
        previous_attempts=attempts,
        last_failure_category=last_failure,
        body=body[:2000],
    )


def detect_subsystem(mentioned_files: list[str], text: str) -> str:
    """Detect the primary subsystem from file references and body text.

    Returns the highest-scoring subsystem, or 'mixed' if tied or none match.
    """
    scores: dict[str, int] = {k: 0 for k in SUBSYSTEM_PATTERNS}

    search_text = " ".join(mentioned_files) + " " + text.lower()
    for subsystem, patterns in SUBSYSTEM_PATTERNS.items():
        for pattern in patterns:
            if pattern.lower() in search_text:
                scores[subsystem] += 1

    if not any(scores.values()):
        return "mixed"

    max_score = max(scores.values())
    winners = [k for k, v in scores.items() if v == max_score]

    if len(winners) == 1:
        return winners[0]

    # Multiple subsystems tied — it's cross-cutting
    return "mixed"


def _extract_label_names(raw_labels: list) -> list[str]:
    """Extract label name strings from mixed label formats."""
    names = []
    for label in raw_labels:
        if isinstance(label, dict):
            names.append(label.get("name", ""))
        else:
            names.append(str(label))
    return [n for n in names if n]


def _detect_issue_type(labels: list[str]) -> str:
    """Detect issue type from labels."""
    for label in labels:
        normalized = label.lower().replace(" ", "-")
        if normalized in ISSUE_TYPE_LABELS:
            return ISSUE_TYPE_LABELS[normalized]
    return "feature"


def _detect_priority(labels: list[str]) -> int:
    """Detect priority from labels. Returns 0-4."""
    for label in labels:
        if label in PRIORITY_LABELS:
            return PRIORITY_LABELS[label]
    return 0


def _extract_file_paths(body: str) -> list[str]:
    """Extract file paths mentioned in the issue body."""
    matches = FILE_PATH_RE.findall(body)
    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            result.append(m)
    return result[:20]  # Cap at 20 files


def _extract_dirs(files: list[str]) -> list[str]:
    """Extract unique directory prefixes from file paths."""
    dirs: set[str] = set()
    for f in files:
        parts = f.split("/")
        if len(parts) >= 2:
            # Use first two path components as directory
            dirs.add("/".join(parts[:2]) + "/")
    return sorted(dirs)


def _estimate_scope(files: list[str]) -> str:
    """Estimate change scope from mentioned file count."""
    n = len(files)
    if n <= 1:
        return "single-file"
    elif n <= 5:
        return "multi-file"
    return "cross-cutting"


def _has_linked_pr(issue_number: int, prs: list[dict]) -> bool:
    """Check if an issue already has an associated PR (open or merged)."""
    for pr in prs:
        branch = pr.get("headRefName", "")
        if str(issue_number) in branch:
            return True
        title = pr.get("title", "")
        if f"#{issue_number}" in title:
            return True
    return False
