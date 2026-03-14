"""LLM-based complexity routing for intelligent mode selection.

Two-tier routing:
  Tier 1 — Rule-based fast path (zero LLM cost): explicit labels override everything.
  Tier 2 — Haiku complexity assessment: cheap LLM scores issue complexity.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Complexity → model mapping (progressive deepening ladder)
COMPLEXITY_TO_MODEL: dict[int, str] = {
    1: "claude-sonnet-4-6",
    2: "claude-sonnet-4-6",
    3: "claude-sonnet-4-6",
    4: "claude-opus-4-6",
    5: "claude-opus-4-6",
}

COMPLEXITY_TO_MODE: dict[int, str] = {
    1: "fix",
    2: "fix",
    3: "full",
    4: "full",
    5: "full",
}

COMPLEXITY_TO_TURNS: dict[int, int] = {
    1: 30,
    2: 50,
    3: 100,
    4: 150,
    5: 200,
}


@dataclass
class ModeDecision:
    """Result of mode selection (either rule-based or LLM-based)."""
    mode: str
    model: str
    max_turns: int
    complexity_score: int | None  # None for rule-based decisions
    reasoning: str
    source: str  # "label", "haiku", "adaptive", "default"


def select_mode_from_labels(labels: list[str | dict]) -> ModeDecision | None:
    """Tier 1: Rule-based fast path from issue labels.

    Returns ModeDecision if a definitive label is found, None otherwise.
    """
    label_names = []
    for label in labels:
        if isinstance(label, dict):
            label_names.append(label.get("name", ""))
        else:
            label_names.append(str(label))

    # Explicit mode labels always win
    for name in label_names:
        if name.startswith("mode/"):
            mode = name.split("/", 1)[1]
            if mode in ("full", "fix", "analyze", "plan", "triage", "review"):
                from agent.coordinator.modes import MODE_REGISTRY
                spec = MODE_REGISTRY.get(mode)
                if spec:
                    return ModeDecision(
                        mode=mode,
                        model=spec.default_model,
                        max_turns=spec.default_max_turns,
                        complexity_score=None,
                        reasoning=f"Explicit mode/{mode} label",
                        source="label",
                    )

    # Review-requested label
    if "review-requested" in label_names:
        return ModeDecision(
            mode="review",
            model="claude-sonnet-4-6",
            max_turns=30,
            complexity_score=None,
            reasoning="review-requested label",
            source="label",
        )

    return None


def build_complexity_prompt(issue: dict) -> str:
    """Build the prompt for Haiku complexity assessment."""
    title = issue.get("title", "")
    body = (issue.get("body", "") or "")[:1000]
    labels = [
        l.get("name", "") if isinstance(l, dict) else str(l)
        for l in issue.get("labels", [])
    ]

    return f"""Score this GitHub issue 1-5 on implementation complexity.

Title: {title}
Body: {body}
Labels: {labels}

Output JSON only, no other text:
{{"complexity": <1-5>, "reasoning": "<brief explanation>", "recommended_mode": "<fix|full>", "estimated_turns": <20-200>}}

Scoring guide:
1 = Typo/one-line fix
2 = Small bug or simple feature addition
3 = Medium feature, multiple files
4 = Large feature or significant refactor
5 = Architecture change, cross-cutting concern"""


def parse_complexity_response(response: str) -> ModeDecision | None:
    """Parse Haiku's complexity assessment response."""
    try:
        # Try to extract JSON from the response
        text = response.strip()
        # Find JSON object in the response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            return None

        data = json.loads(text[start:end])
        complexity = int(data.get("complexity", 3))
        complexity = max(1, min(5, complexity))  # Clamp to 1-5

        return ModeDecision(
            mode=data.get("recommended_mode", COMPLEXITY_TO_MODE.get(complexity, "full")),
            model=COMPLEXITY_TO_MODEL.get(complexity, "claude-sonnet-4-6"),
            max_turns=int(data.get("estimated_turns", COMPLEXITY_TO_TURNS.get(complexity, 100))),
            complexity_score=complexity,
            reasoning=data.get("reasoning", ""),
            source="haiku",
        )
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning("Failed to parse complexity response: %s", e)
        return None


def default_mode_decision(project_mode: str = "full") -> ModeDecision:
    """Fallback mode decision when auto-selection is disabled or fails."""
    from agent.coordinator.modes import MODE_REGISTRY
    spec = MODE_REGISTRY.get(project_mode)
    if spec:
        return ModeDecision(
            mode=project_mode,
            model=spec.default_model,
            max_turns=spec.default_max_turns,
            complexity_score=None,
            reasoning="Default project mode",
            source="default",
        )
    return ModeDecision(
        mode="full",
        model="claude-opus-4-6",
        max_turns=200,
        complexity_score=None,
        reasoning="Fallback default",
        source="default",
    )
