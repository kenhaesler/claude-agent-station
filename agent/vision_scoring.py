"""Hook 1: vision-aware issue prioritisation.

One LLM call per orchestrator run. Falls back to neutral 0.5 on any
failure so the orchestrator still runs with label-only priority.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


_PROMPT_TEMPLATE = """You are scoring open issues against a project vision.

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

# Issues to score

{issues_block}

# Task

For each issue, output a score in [0, 1] (higher = more aligned with the
vision) plus a one-sentence reason. Output ONLY a JSON array, no prose:

[{{"number": <int>, "score": <float>, "why": "<one sentence>"}}]
"""


def _format_issues(issues: list[dict]) -> str:
    parts = []
    for issue in issues:
        body = (issue.get("body") or "")[:500]
        parts.append(f"## #{issue['number']}: {issue.get('title', '')}\n{body}")
    return "\n\n".join(parts)


def _call_model(prompt: str, model: str) -> str:
    """Invoke the bundled `claude` CLI for one-shot inference.

    Uses --print mode (no streaming) since we just need the final JSON.
    """
    proc = subprocess.run(
        ["claude", "--print", "--model", model, "--no-session-persistence",
         "--dangerously-skip-permissions", prompt],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def score_issues_against_vision(
    issues: list[dict], vision: dict, model: str,
) -> list[dict]:
    """Add vision_score (0-1) and vision_reason fields to each issue.

    On any failure, all issues get vision_score=0.5 (neutral) so the
    orchestrator's combined ranking falls back to pure priority labels.
    """
    if not issues:
        return issues

    prompt = _PROMPT_TEMPLATE.format(
        issues_block=_format_issues(issues),
        **vision,
    )

    try:
        raw = _call_model(prompt, model)
    except Exception as e:
        logger.warning("vision scoring model call failed: %s", e)
        return [{**i, "vision_score": 0.5, "vision_reason": ""} for i in issues]

    # Strip code fences if the model added them
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.rstrip("` \n")

    try:
        scored = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("vision scoring response not JSON: %s", e)
        return [{**i, "vision_score": 0.5, "vision_reason": ""} for i in issues]

    score_by_num = {item["number"]: item for item in scored if isinstance(item, dict)}
    out = []
    for issue in issues:
        match = score_by_num.get(issue["number"], {})
        out.append({
            **issue,
            "vision_score": float(match.get("score", 0.5)),
            "vision_reason": match.get("why", ""),
        })
    return out
