"""Independent verification: automated code review for high-risk changes.

Spawns a reviewer agent (reviewer.md prompt) on the branch diff to provide
structured feedback. Only triggered when risk criteria are met:
  - Changes touch > 5 files (cross-cutting)
  - Escalated items (already failed once)
  - Complexity score >= 4
  - Auto-PR candidates (no human in the loop otherwise)
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def run_verification(
    workspace: str,
    branch: str,
    base_branch: str = "main",
    repo: str = "",
    report_file: str = "",
    prompt_dir: str = "",
    integration_branch: str | None = None,
) -> dict:
    """Run independent verification on a branch diff.

    Returns a structured verification result:
        {
            "verified": bool,
            "issues": [{"severity": "critical|warning|info", "description": "...", "file": "..."}],
            "summary": "...",
            "recommendation": "approve|revoke_auto_pr|needs_review",
        }
    """
    target = integration_branch if integration_branch else base_branch

    # Get the diff to review
    try:
        diff_result = subprocess.run(
            ["git", "-C", workspace, "diff", f"{target}...{branch}"],
            capture_output=True, text=True, timeout=30,
        )
        if diff_result.returncode != 0:
            return {"verified": False, "issues": [], "summary": "Failed to get diff",
                    "recommendation": "needs_review"}
        diff_text = diff_result.stdout
    except Exception as e:
        logger.warning("Failed to get diff: %s", e)
        return {"verified": False, "issues": [], "summary": str(e),
                "recommendation": "needs_review"}

    if not diff_text.strip():
        return {"verified": True, "issues": [], "summary": "No changes to review",
                "recommendation": "approve"}

    # Truncate diff for reviewer context (max ~10K chars)
    if len(diff_text) > 10000:
        diff_text = diff_text[:10000] + "\n\n... (diff truncated, review remaining files manually)"

    # Build reviewer prompt
    reviewer_prompt = f"""Review this code diff for a pull request.

Repository: {repo}
Branch: {branch}
Base: {target}

```diff
{diff_text}
```

Analyze the diff for:
1. Critical bugs or logic errors
2. Security vulnerabilities (injection, auth bypass, secrets exposure)
3. Missing error handling for failure scenarios
4. Breaking changes to public APIs
5. Test coverage gaps for new code paths

Output JSON only:
{{"verified": true/false, "issues": [{{"severity": "critical|warning|info", "description": "...", "file": "...", "line": null}}], "summary": "one-line summary", "recommendation": "approve|revoke_auto_pr|needs_review"}}

If there are no critical issues, set verified=true and recommendation="approve".
Only flag genuine problems, not style preferences."""

    # Run reviewer via Anthropic SDK (direct API, no subprocess)
    try:
        # Load system prompt if available
        system = ""
        prompt_file = Path(prompt_dir) / "reviewer.md" if prompt_dir else None
        if prompt_file and prompt_file.exists():
            system = prompt_file.read_text()

        from agent.coordinator.llm import call_llm
        resp = call_llm(
            reviewer_prompt,
            model="claude-sonnet-4-6",
            system=system,
            max_tokens=4096,
        )

        if resp.text.strip():
            text = resp.text.strip()
            start = text.find("{")
            end = text.rfind("}") + 1
            if start != -1 and end > 0:
                review = json.loads(text[start:end])
                return {
                    "verified": review.get("verified", False),
                    "issues": review.get("issues", []),
                    "summary": review.get("summary", ""),
                    "recommendation": review.get("recommendation", "needs_review"),
                }
    except json.JSONDecodeError:
        logger.warning("Failed to parse reviewer response")
    except Exception as e:
        logger.warning("Verification failed: %s", e)

    return {"verified": False, "issues": [], "summary": "Verification process failed",
            "recommendation": "needs_review"}
