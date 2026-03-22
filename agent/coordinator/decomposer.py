"""Task decomposition using Claude to split issues into sub-tasks."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

import anthropic

if TYPE_CHECKING:
    from agent.coordinator.config import CoordinatorConfig

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent.coordinator.dag import TaskDAG

logger = logging.getLogger(__name__)

_SKIP_LABELS_FALLBACK = frozenset({
    "autonomous-agent/in-progress",
    "autonomous-agent/needs-help",
    "NO AI",
    "backlog",
})


def _fetch_open_issues(repo: str, workspace: str, count: int) -> list[dict]:
    """Fetch top N open issues eligible for autonomous work."""
    try:
        result = subprocess.run(
            ["gh", "issue", "list", "--repo", repo, "--state", "open",
             "--limit", str(count + 10),
             "--json", "number,title,body,labels"],
            capture_output=True, text=True, timeout=15, cwd=workspace,
        )
        if result.returncode != 0:
            return []
        eligible: list[dict] = []
        for issue in json.loads(result.stdout):
            labels = {l.get("name", "") for l in issue.get("labels", [])}
            if labels & _SKIP_LABELS_FALLBACK:
                continue
            eligible.append({
                "number": issue["number"],
                "body": f"# {issue.get('title', '')}\n\n{issue.get('body', '')}",
            })
            if len(eligible) >= count:
                break
        return eligible
    except Exception as e:
        logger.warning("Failed to fetch issues for fallback: %s", e)
        return []


DECOMPOSITION_PROMPT = """<identity>
You are a task decomposition agent. Given a GitHub issue, split it into sub-tasks for separate employee agents (Claude Code).
</identity>

<rules>
1. Each sub-task must be independently implementable by one employee.
2. Each sub-task should touch at most 5 files.
3. Define clear dependencies (which tasks must complete before others can start).
4. Aim for 2-5 sub-tasks. Don't over-decompose simple issues.
5. Each task should touch different files to minimize merge conflicts.
6. Include a final integration/test task that depends on all implementation tasks.
7. If the issue is simple enough for one employee, return a single task.
</rules>

<output-format>
Return ONLY valid JSON in this exact format:

{
  "tasks": [
    {
      "title": "Short task title",
      "description": "What this employee should do",
      "depends_on": [],
      "expected_files": ["path/to/file1.py", "path/to/file2.py"]
    }
  ]
}

The depends_on field uses 0-based task indices from this array.
</output-format>

<examples>
Input: "Add user profile page with avatar upload and activity feed"

Good decomposition:
{
  "tasks": [
    {
      "title": "Add profile page layout and routing",
      "description": "Create the profile page component with routing. Add basic layout with placeholder sections for avatar and activity feed.",
      "depends_on": [],
      "expected_files": ["src/routes/profile.svelte", "src/routes/+layout.svelte"]
    },
    {
      "title": "Implement avatar upload backend and UI",
      "description": "Add avatar upload endpoint and file storage. Add the upload widget to the profile page.",
      "depends_on": [0],
      "expected_files": ["src/api/avatar.py", "src/components/AvatarUpload.svelte", "tests/test_avatar.py"]
    },
    {
      "title": "Implement activity feed",
      "description": "Add activity feed query and component. Wire into the profile page layout.",
      "depends_on": [0],
      "expected_files": ["src/api/activity.py", "src/components/ActivityFeed.svelte", "tests/test_activity.py"]
    },
    {
      "title": "Integration testing",
      "description": "Run full test suite. Verify profile page renders with avatar and activity feed. Test edge cases (no avatar, empty feed).",
      "depends_on": [1, 2],
      "expected_files": ["tests/test_profile_integration.py"]
    }
  ]
}
</examples>"""


async def decompose_issue(
    issue_body: str,
    repo: str,
    workspace: str,
    config: CoordinatorConfig,
    session_factory: async_sessionmaker[AsyncSession],
    run_id: str | None = None,
    issue_number: int | None = None,
    employee_count: int = 2,
) -> TaskDAG:
    """Decompose a GitHub issue into a task DAG.

    Uses Claude Haiku for fast, cheap decomposition.
    Falls back to a single-task DAG on any failure.
    """
    effective_run_id = run_id or config.run_id

    # For analyze mode, create parallel analysis tasks (one per employee)
    if config.project_mode == "analyze":
        if employee_count <= 1:
            logger.info("Analyze mode (single employee) — creating read-only analysis task")
            title = (
                f"Analyze codebase for issue #{issue_number}"
                if issue_number
                else "Analyze codebase"
            )
            return await TaskDAG.single_task(
                effective_run_id, repo, session_factory,
                title=title,
                description=issue_body[:2000],
                issue_number=issue_number,
            )
        # Multi-employee analyze: partition codebase across employees
        logger.info("Analyze mode (%d employees) — partitioning codebase", employee_count)
        partitions = _get_workspace_partitions(workspace, employee_count)
        dag = TaskDAG(effective_run_id, repo, session_factory)
        for i in range(employee_count):
            scope = partitions[i] if i < len(partitions) else []
            if scope:
                scope_label = ", ".join(scope)
                title = f"Analyze: {scope_label}"
                desc = f"Focus your analysis on these directories: {scope_label}\nOther employees are covering the rest of the codebase."
            else:
                title = f"Analyze codebase (cross-cutting, employee {i})"
                desc = "Perform cross-cutting analysis: CI/CD, dependencies, config, docs, root-level files."
            if issue_number:
                title += f" (issue #{issue_number})"
                desc = f"{issue_body[:1500]}\n\n{desc}"
            await dag.add_task(
                title=title,
                description=desc,
                issue_number=issue_number,
                expected_files=scope,
            )
        return dag

    # For single employee, skip decomposition
    if employee_count <= 1:
        logger.info("Single employee — skipping decomposition")
        return await TaskDAG.single_task(
            effective_run_id, repo, session_factory,
            title=f"Implement issue #{issue_number}" if issue_number else "Implement feature",
            description=issue_body[:2000],
            issue_number=issue_number,
        )

    # Get repo file listing for context
    file_list = _get_file_listing(workspace)

    prompt = f"""{DECOMPOSITION_PROMPT}

## Issue #{issue_number or 'N/A'}

{issue_body[:3000]}

## Repository Files (top-level structure)

{file_list}

## Target Employee Count: {employee_count}

Decompose this issue into sub-tasks for {employee_count} employees."""

    max_retries = 3
    retry_backoff = [2, 4]  # seconds between retries

    from agent.coordinator.llm import call_llm

    for attempt in range(max_retries):
        try:
            resp = call_llm(
                prompt,
                model=config.decomposition_model,
                system=DECOMPOSITION_PROMPT,
                max_tokens=4096,
            )
            logger.info(
                "Decomposition LLM call: %d input + %d output tokens",
                resp.input_tokens, resp.output_tokens,
            )

            if not resp.text.strip():
                logger.warning("Decomposition returned empty response, using fallback")
                return await _fallback_dag(
                    config, session_factory, repo, issue_body, issue_number,
                    effective_run_id, employee_count=employee_count, workspace=workspace,
                )

            return await _parse_decomposition(resp.text, config, session_factory, repo, issue_number, effective_run_id)

        except (anthropic.APITimeoutError, anthropic.RateLimitError) as e:
            if attempt < max_retries - 1:
                wait = retry_backoff[attempt] if attempt < len(retry_backoff) else retry_backoff[-1]
                logger.warning("Decomposition attempt %d/%d failed (%s), retrying in %ds", attempt + 1, max_retries, e, wait)
                time.sleep(wait)
            else:
                logger.warning("Decomposition failed after %d attempts: %s", max_retries, e)

        except anthropic.APIStatusError as e:
            if e.status_code in (500, 529) and attempt < max_retries - 1:
                wait = retry_backoff[attempt] if attempt < len(retry_backoff) else retry_backoff[-1]
                logger.warning("Decomposition attempt %d/%d failed (HTTP %d), retrying in %ds", attempt + 1, max_retries, e.status_code, wait)
                time.sleep(wait)
            elif e.status_code in (500, 529):
                logger.warning("Decomposition failed after %d attempts: HTTP %d", max_retries, e.status_code)
            else:
                logger.warning("Decomposition failed (non-retryable HTTP %d): %s", e.status_code, e)
                break  # Don't retry 4xx errors

        except Exception as e:
            logger.warning("Decomposition failed (unexpected): %s", e)
            break  # Don't retry unexpected errors

    return await _fallback_dag(
        config, session_factory, repo, issue_body, issue_number,
        effective_run_id, employee_count=employee_count, workspace=workspace,
    )


async def _parse_decomposition(
    output: str,
    config: CoordinatorConfig,
    session_factory: async_sessionmaker[AsyncSession],
    repo: str,
    issue_number: int | None,
    run_id: str | None = None,
) -> TaskDAG:
    """Parse the decomposition agent's JSON output into a TaskDAG."""
    effective_run_id = run_id or config.run_id

    # Extract JSON from potential markdown wrapping
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", output, re.DOTALL)
    text = match.group(1) if match else output.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse decomposition JSON, using single task")
        return await _fallback_dag(config, session_factory, repo, "", issue_number, effective_run_id)

    tasks_data = data.get("tasks", [])
    if not tasks_data or len(tasks_data) < 1:
        logger.warning("Decomposition returned empty tasks, using single task")
        return await _fallback_dag(config, session_factory, repo, "", issue_number, effective_run_id)

    dag = TaskDAG(effective_run_id, repo, session_factory)

    # Map index-based depends_on to task IDs
    task_ids: list[str] = []
    for i, td in enumerate(tasks_data):
        deps = []
        for dep_idx in td.get("depends_on", []):
            if isinstance(dep_idx, int) and 0 <= dep_idx < len(task_ids):
                deps.append(task_ids[dep_idx])

        task = await dag.add_task(
            title=td.get("title", f"Task {i}"),
            description=td.get("description", ""),
            depends_on=deps,
            issue_number=issue_number,
            expected_files=td.get("expected_files", []),
        )
        task_ids.append(task.id)

    # Safety check: detect cycles in the dependency graph
    if await dag.has_cycle():
        logger.warning("Decomposition produced cyclic dependencies, falling back to single task")
        return await _fallback_dag(config, session_factory, repo, "", issue_number, effective_run_id)

    logger.info("Decomposed issue into %d tasks", len(task_ids))
    return dag


async def _fallback_dag(
    config: CoordinatorConfig,
    session_factory: async_sessionmaker[AsyncSession],
    repo: str,
    description: str,
    issue_number: int | None,
    run_id: str | None = None,
    employee_count: int = 1,
    workspace: str | None = None,
) -> TaskDAG:
    """Create a fallback DAG.

    User-assigned issue or single employee: 1 task (avoid conflicts).
    Multi-employee: pre-fetch distinct issues, one per employee.
    """
    effective_run_id = run_id or config.run_id

    if issue_number or employee_count <= 1:
        return await TaskDAG.single_task(
            effective_run_id, repo, session_factory,
            title=f"Implement issue #{issue_number}" if issue_number else "Implement feature",
            description=description[:2000],
            issue_number=issue_number,
        )

    # Multi-employee: pre-fetch distinct issues to avoid race condition
    issues = _fetch_open_issues(repo, workspace, employee_count) if workspace else []
    dag = TaskDAG(effective_run_id, repo, session_factory)

    if issues:
        logger.info("Fallback: pre-assigning %d issues to %d employees", len(issues), employee_count)
    else:
        logger.info("Fallback: creating %d self-select tasks (no issues fetched)", employee_count)

    for i in range(employee_count):
        if i < len(issues):
            await dag.add_task(
                title=f"Implement issue #{issues[i]['number']}",
                description=issues[i]['body'][:2000],
                issue_number=issues[i]['number'],
            )
        else:
            await dag.add_task(
                title=f"Self-select and implement (employee {i + 1})",
                description="Pick an open issue from the repository and implement it.",
            )
    return dag


def _get_workspace_partitions(workspace: str, employee_count: int) -> list[list[str]]:
    """Partition workspace top-level directories across employees."""
    EXCLUDED = {
        '.git', 'node_modules', '__pycache__', '.venv', 'venv',
        'dist', 'build', '.svelte-kit', '.next', '.cache',
        '.mypy_cache', '.pytest_cache', '.ruff_cache', 'egg-info',
    }
    try:
        dirs = sorted(
            d.name for d in Path(workspace).iterdir()
            if d.is_dir() and d.name not in EXCLUDED and not d.name.endswith('.egg-info')
        )
    except OSError:
        return [[] for _ in range(employee_count)]

    # Round-robin distribute directories
    partitions: list[list[str]] = [[] for _ in range(employee_count)]
    for i, d in enumerate(dirs):
        partitions[i % employee_count].append(f"{d}/")

    return partitions


def _get_file_listing(workspace: str, max_depth: int = 3) -> str:
    """Get a condensed file listing of the workspace."""
    try:
        result = subprocess.run(
            ["find", ".", "-maxdepth", str(max_depth), "-type", "f",
             "-not", "-path", "./.git/*", "-not", "-path", "./node_modules/*",
             "-not", "-path", "./.venv/*"],
            capture_output=True, text=True, cwd=workspace, timeout=5,
        )
        lines = result.stdout.strip().split("\n")
        # Limit to 100 most relevant files
        return "\n".join(lines[:100])
    except Exception:
        return "(file listing unavailable)"
