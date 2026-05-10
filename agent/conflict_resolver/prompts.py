"""Prompt assembly for the conflict resolver.

Loads the system prompt body from agent/prompts/conflict_resolver.md and
injects per-attempt context: branch, base, file list, advisory tiers, prior
failure reason. See spec
docs/superpowers/specs/2026-05-10-conflict-resolution-design.md.
"""

from __future__ import annotations

from enum import Enum, auto
from pathlib import Path

_PROMPT_BODY_PATH = Path(__file__).parent.parent / "prompts" / "conflict_resolver.md"


class AdvisoryTier(Enum):
    """Conditions that adjust the resolver's strategy. See spec 'Pre-attempt advisory tiers'."""
    LARGE_DIFF = auto()      # >500 lines conflicting
    MANY_FILES = auto()      # >10 files conflicting
    STALE_PR = auto()        # PR older than 7 days
    PRIOR_ATTEMPT = auto()   # already-attempted-and-still-dirty


def build_resolver_prompt(
    *,
    branch: str,
    base_branch: str,
    conflicted_files: list[str],
    advisory_tiers: set[AdvisoryTier],
    prior_failure_reason: str | None,
) -> str:
    """Assemble the full prompt: body + injected context."""
    body = _PROMPT_BODY_PATH.read_text()
    parts: list[str] = [body, "", "## Run-specific context"]
    parts.append(f"- Branch: `{branch}`")
    parts.append(f"- Base: `{base_branch}`")
    parts.append("- Conflicted files:")
    for f in conflicted_files:
        parts.append(f"  - `{f}`")

    if AdvisoryTier.LARGE_DIFF in advisory_tiers:
        parts.append("")
        parts.append("WARNING: This is a **large** conflict (>500 lines). Be deliberate; "
                     "read both sides fully before editing.")
    if AdvisoryTier.MANY_FILES in advisory_tiers:
        parts.append("")
        parts.append("WARNING: This conflict touches many files. Resolve them in dependency "
                     "order where possible.")
    if AdvisoryTier.STALE_PR in advisory_tiers:
        parts.append("")
        parts.append("WARNING: This PR is **stale** (>7 days old). The base may have diverged "
                     "significantly; expect the merged tree to differ from what the "
                     "feature branch was tested against.")
    if AdvisoryTier.PRIOR_ATTEMPT in advisory_tiers:
        parts.append("")
        parts.append("WARNING: A previous attempt at resolving this conflict failed. Use the "
                     "failure reason below to avoid the same trap.")

    if prior_failure_reason:
        parts.append("")
        parts.append("## Previous attempt failed")
        parts.append("")
        parts.append(prior_failure_reason)

    return "\n".join(parts)
