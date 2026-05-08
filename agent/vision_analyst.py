"""Hook 3: gap detection.

Analyses a project's vision against the current repo state and proposes
new GitHub issues to fill gaps. Issues land with the `vision-suggested`
label so the orchestrator's SKIP_LABELS prevents autonomous implementation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import uuid
from typing import Any

import httpx

from agent.vision import load_vision

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

MAX_PROPOSALS = 5
DISCLAIMER = (
    "*Proposed by Claude Station based on the project vision. "
    "Review and accept by removing the `vision-suggested` label, "
    "or close to reject.*\n\n---\n\n"
)


def _webhook_url() -> str | None:
    return os.environ.get("STATION_WEBHOOK_URL") or None


def _webhook_secret() -> str | None:
    return os.environ.get("STATION_WEBHOOK_SECRET") or None


def _post_webhook(payload: dict) -> None:
    """Best-effort POST to STATION_WEBHOOK_URL. Failures are logged, not raised."""
    url = _webhook_url()
    if not url:
        logger.info("vision_analyst: STATION_WEBHOOK_URL unset, skipping webhook")
        return
    headers = {}
    secret = _webhook_secret()
    if secret:
        headers["X-Webhook-Token"] = secret
    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=5.0)
        if resp.status_code >= 400:
            logger.warning("vision_analyst webhook %s -> %s: %s",
                           payload.get("event"), resp.status_code, resp.text[:200])
    except httpx.HTTPError as exc:
        logger.warning("vision_analyst webhook POST failed: %s", exc)


def _gather_repo_state(workspace: str, repo: str) -> dict:
    """Snapshot the repo: file tree top-N, README, last 50 commits, issues."""
    state: dict[str, Any] = {"tree": [], "readme": "", "commits": [], "open_issues": [], "closed_issues": []}

    # File tree
    for root, _dirs, files in os.walk(workspace):
        if any(part.startswith(".") for part in os.path.relpath(root, workspace).split(os.sep)):
            continue
        for f in files:
            p = os.path.relpath(os.path.join(root, f), workspace)
            state["tree"].append(p)
            if len(state["tree"]) >= 200:
                break
        if len(state["tree"]) >= 200:
            break

    # README
    for fn in ("README.md", "README.rst", "README.txt"):
        p = os.path.join(workspace, fn)
        if os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                state["readme"] = f.read()[:5000]
            break

    # Recent commits
    try:
        result = subprocess.run(
            ["git", "-C", workspace, "log", "--oneline", "-50"],
            capture_output=True, text=True, timeout=15,
        )
        state["commits"] = [line for line in result.stdout.splitlines() if line]
    except Exception as e:
        logger.warning("git log failed: %s", e)

    # Issues via gh
    try:
        result = subprocess.run(
            ["gh", "issue", "list", "--repo", repo, "--state", "open", "--limit", "100",
             "--json", "number,title,labels"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            state["open_issues"] = json.loads(result.stdout)
        result = subprocess.run(
            ["gh", "issue", "list", "--repo", repo, "--state", "closed", "--limit", "100",
             "--json", "number,title"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            state["closed_issues"] = json.loads(result.stdout)
    except Exception as e:
        logger.warning("gh issue list failed: %s", e)

    return state


_PROMPT = """You are a project analyst. Given a project vision and the current
state of the repository, propose {max} new GitHub issues that would help close
the gap between today's state and the vision.

# Vision
## Problem
{problem}

## Users
{users}

## End-state
{end_state}

## Non-goals
{non_goals}

## Principles
{principles}

## Horizons
{horizons}

## Anti-patterns
{anti_patterns}

# Repo state

## File tree (sample)
{tree}

## README (truncated)
{readme}

## Recent commits
{commits}

## Open issues
{open_issues}

## Recently closed issues
{closed_issues}

# Task

Propose at most {max} new issues that would advance toward the vision. Skip
ideas that are already covered by existing open or closed issues. Skip
anything that violates a non-goal or anti-pattern.

Output ONLY a JSON array, no prose:

[{{"title": "...", "body": "...", "labels": ["..."], "priority": "low|medium|high|critical"}}]
"""


def _call_model(prompt: str, model: str) -> str:
    proc = subprocess.run(
        ["claude", "--print", "--model", model, "--no-session-persistence",
         "--dangerously-skip-permissions", prompt],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def format_proposal_body(body: str) -> str:
    """Wrap a proposal body with the user-facing disclaimer."""
    return DISCLAIMER + body.strip() + "\n"


def propose_gaps(workspace: str, vision: dict, repo: str, model: str) -> list[dict]:
    """Return a list of proposal dicts, capped at MAX_PROPOSALS. Empty on failure."""
    state = _gather_repo_state(workspace, repo)

    open_titles = [f"#{i['number']}: {i['title']}" for i in state["open_issues"]][:50]
    closed_titles = [f"#{i['number']}: {i['title']}" for i in state["closed_issues"]][:50]

    prompt = _PROMPT.format(
        max=MAX_PROPOSALS,
        problem=vision["problem"], users=vision["users"], end_state=vision["end_state"],
        non_goals=vision["non_goals"], principles=vision["principles"],
        horizons=vision["horizons"], anti_patterns=vision["anti_patterns"],
        tree="\n".join(state["tree"][:80]),
        readme=state["readme"][:3000],
        commits="\n".join(state["commits"][:30]),
        open_issues="\n".join(open_titles),
        closed_issues="\n".join(closed_titles),
    )

    try:
        raw = _call_model(prompt, model)
    except Exception as e:
        logger.error("vision_analyst model call failed: %s", e)
        return []

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.rstrip("` \n")
    try:
        proposals = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("vision_analyst response not JSON: %s", e)
        return []
    if not isinstance(proposals, list):
        return []
    return proposals[:MAX_PROPOSALS]


def create_proposed_issues(repo: str, proposals: list[dict]) -> list[tuple[int, dict]]:
    """Create issues via `gh`. Returns list of (issue_number, proposal) tuples
    for the proposals that actually succeeded."""
    created: list[tuple[int, dict]] = []
    for p in proposals:
        labels = ["vision-suggested"]
        priority = (p.get("priority") or "low").lower()
        if priority in ("low", "medium", "high", "critical"):
            labels.append(priority)
        labels.extend([l for l in (p.get("labels") or []) if l != "vision-suggested"])

        body = format_proposal_body(p.get("body") or "")
        cmd = ["gh", "issue", "create", "--repo", repo,
               "--title", p["title"], "--body", body,
               "--label", ",".join(labels)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                logger.warning("gh issue create failed: %s", result.stderr)
                continue
            url = result.stdout.strip()
            num = int(url.rstrip("/").rsplit("/", 1)[1])
            created.append((num, p))
            logger.info("Proposed issue #%d: %s", num, p["title"])
        except Exception as e:
            logger.warning("gh issue create failed: %s", e)
    return created


def _ensure_workspace(workspace: str, repo: str, branch: str = "main") -> bool:
    """Clone the repo into workspace if not already present, then refresh
    the checkout to the tip of ``branch``.

    The refresh is best-effort: if ``git fetch`` or ``git reset --hard`` fails
    we still return ``True`` so the caller can attempt to read whatever is on
    disk. ``load_vision`` will surface a clear error if the file is missing.

    Logs each git step so operators can ``grep`` worker output for stale-
    workspace symptoms without inspecting the volume.
    """
    parent = os.path.dirname(workspace)
    name = os.path.basename(workspace)
    if not os.path.isdir(os.path.join(workspace, ".git")):
        os.makedirs(parent, exist_ok=True)
        clone = subprocess.run(
            ["gh", "repo", "clone", repo, name],
            cwd=parent, capture_output=True, text=True, timeout=120,
        )
        if clone.returncode != 0:
            logger.warning(
                "gh repo clone %s failed: %s",
                repo, clone.stderr.strip(),
            )
            return False

    # Refresh the existing checkout to the tip of the configured branch.
    fetch = subprocess.run(
        ["git", "-C", workspace, "fetch", "--depth", "1", "origin", branch],
        capture_output=True, text=True, timeout=60,
    )
    if fetch.returncode != 0:
        logger.warning(
            "git fetch in %s failed: %s",
            workspace, fetch.stderr.strip(),
        )
        # Fall through — try to use whatever is on disk.
        return True
    logger.info("git fetch in %s: ok (origin/%s)", workspace, branch)

    reset = subprocess.run(
        ["git", "-C", workspace, "reset", "--hard", f"origin/{branch}"],
        capture_output=True, text=True, timeout=30,
    )
    if reset.returncode != 0:
        logger.warning(
            "git reset in %s failed: %s",
            workspace, reset.stderr.strip(),
        )
    else:
        logger.info("git reset in %s: ok (origin/%s)", workspace, branch)
    return True


async def run_for_project(project_id: int) -> dict:
    """Entry point: load project from DB, run analyst, return summary.

    Self-registers as a `vision-bootstrap` Run via webhooks so the dashboard
    sees the activity in Mission Control + Runs list.
    """
    from app.database import async_session, init_db
    from app.models import Project

    await init_db()
    async with async_session() as db:
        project = await db.get(Project, project_id)
        if not project:
            return {"ok": False, "error": "project not found"}
        repo = project.repo
        branch = project.branch or "main"

    workspaces_dir = os.environ.get("STATION_WORKSPACES", "/var/lib/claude-agent-station/workspaces")
    name = repo.split("/")[-1]
    workspace = os.path.join(workspaces_dir, name)

    run_id = f"run-vb-{uuid.uuid4().hex[:12]}"
    _post_webhook({
        "event": "started",
        "run_id": run_id,
        "project": repo,
        "mode": "vision-bootstrap",
    })

    def _finish(status: str, **extra) -> None:
        _post_webhook({
            "event": "finished",
            "run_id": run_id,
            "project": repo,
            "mode": "vision-bootstrap",
            "status": status,
            **extra,
        })

    if not _ensure_workspace(workspace, repo, branch):
        _finish("error")
        return {"ok": False, "error": f"could not clone {repo}"}

    vision = load_vision(workspace)
    if vision is None:
        _finish("error")
        return {"ok": False, "error": "no vision file at docs/vision.md"}

    model = os.environ.get("STATION_VISION_ANALYST_MODEL", "claude-sonnet-4-6")
    proposals = propose_gaps(workspace, vision, repo, model)
    if not proposals:
        _finish("success", vision_bootstrap_count=0, vision_bootstrap_proposals=[])
        return {"ok": True, "proposals": [], "created": []}

    created_pairs = create_proposed_issues(repo, proposals)
    proposal_records = [
        {
            "number": num,
            "title": p.get("title", ""),
            "url": f"https://github.com/{repo}/issues/{num}",
        }
        for num, p in created_pairs
    ]
    created_numbers = [num for num, _ in created_pairs]
    _finish(
        "success",
        vision_bootstrap_count=len(created_pairs),
        vision_bootstrap_proposals=proposal_records,
    )
    return {"ok": True, "proposals": proposals, "created": created_numbers}


def _main():
    parser = argparse.ArgumentParser(description="Run vision-analyst gap detection for a project")
    parser.add_argument("--project-id", type=int, required=True)
    args = parser.parse_args()
    result = asyncio.run(run_for_project(args.project_id))
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    _main()
