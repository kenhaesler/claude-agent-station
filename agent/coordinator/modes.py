"""Mode registry: centralized definitions for all agent operation modes."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModeSpec:
    """Specification for an agent operation mode.

    Each mode defines model selection, turn budgets, prompt routing,
    tool restrictions, escalation behavior, and concurrency weight.
    """

    name: str
    model_config_key: str  # JSON path in config, e.g. "models.employee"
    default_model: str
    turns_config_key: str  # JSON path, e.g. "limits.max_employee_turns"
    default_max_turns: int
    prompt_file: str  # Filename in agent/prompts/
    readonly: bool
    planning_gate: bool  # Whether plan-before-implement applies
    multi_employee: bool  # Whether coordinator can decompose into sub-tasks
    manager_review: bool  # Whether manager reviews the output
    verification_required: bool  # Whether independent reviewer checks the work
    disallowed_tools: list[str] = field(default_factory=list)
    escalates_to: str | None = None  # Mode to escalate on failure/low-confidence
    concurrency_weight: float = 1.0  # 0.0 = free (read-only), 1.0 = full slot
    max_retries: int = 1
    confidence_threshold: float = 0.9  # Auto-merge threshold


# Immutable tool restriction list for read-only modes
_READONLY_TOOLS = ["Edit", "Write", "NotebookEdit"]

MODE_REGISTRY: dict[str, ModeSpec] = {
    "full": ModeSpec(
        name="full",
        model_config_key="models.employee",
        default_model="claude-opus-4-6",
        turns_config_key="limits.max_employee_turns",
        default_max_turns=200,
        prompt_file="employee.md",
        readonly=False,
        planning_gate=True,
        multi_employee=True,
        manager_review=True,
        verification_required=True,
        disallowed_tools=[],
        escalates_to=None,
        concurrency_weight=1.0,
        max_retries=1,
        confidence_threshold=0.9,
    ),
    "fix": ModeSpec(
        name="fix",
        model_config_key="models.employee",
        default_model="claude-sonnet-4-6",
        turns_config_key="limits.max_fix_turns",
        default_max_turns=75,
        prompt_file="employee.md",
        readonly=False,
        planning_gate=False,
        multi_employee=False,
        manager_review=True,
        verification_required=False,
        disallowed_tools=[],
        escalates_to="full",
        concurrency_weight=0.5,
        max_retries=0,
        confidence_threshold=0.85,
    ),
    "analyze": ModeSpec(
        name="analyze",
        model_config_key="models.analyst",
        default_model="claude-sonnet-4-6",
        turns_config_key="limits.max_analyst_turns",
        default_max_turns=50,
        prompt_file="analyst.md",
        readonly=True,
        planning_gate=False,
        multi_employee=False,
        manager_review=True,
        verification_required=False,
        disallowed_tools=_READONLY_TOOLS,
        escalates_to=None,
        concurrency_weight=0.0,
        max_retries=0,
        confidence_threshold=0.7,
    ),
    "plan": ModeSpec(
        name="plan",
        model_config_key="models.planner",
        default_model="claude-sonnet-4-6",
        turns_config_key="limits.max_planner_turns",
        default_max_turns=50,
        prompt_file="planner.md",
        readonly=True,
        planning_gate=False,
        multi_employee=False,
        manager_review=True,
        verification_required=False,
        disallowed_tools=_READONLY_TOOLS,
        escalates_to=None,
        concurrency_weight=0.0,
        max_retries=0,
        confidence_threshold=0.7,
    ),
    "triage": ModeSpec(
        name="triage",
        model_config_key="models.analyst",
        default_model="claude-sonnet-4-6",
        turns_config_key="limits.max_triage_turns",
        default_max_turns=30,
        prompt_file="triager.md",
        readonly=True,
        planning_gate=False,
        multi_employee=False,
        manager_review=False,
        verification_required=False,
        disallowed_tools=_READONLY_TOOLS,
        escalates_to=None,
        concurrency_weight=0.0,
        max_retries=0,
        confidence_threshold=0.5,
    ),
    "review": ModeSpec(
        name="review",
        model_config_key="models.analyst",
        default_model="claude-sonnet-4-6",
        turns_config_key="limits.max_review_turns",
        default_max_turns=30,
        prompt_file="reviewer.md",
        readonly=True,
        planning_gate=False,
        multi_employee=False,
        manager_review=False,
        verification_required=False,
        disallowed_tools=_READONLY_TOOLS,
        escalates_to=None,
        concurrency_weight=0.0,
        max_retries=0,
        confidence_threshold=0.5,
    ),
}


def get_mode(name: str) -> ModeSpec:
    """Get a mode spec by name, raising ValueError for unknown modes."""
    if name not in MODE_REGISTRY:
        raise ValueError(
            f"Unknown mode '{name}'. Valid modes: {sorted(MODE_REGISTRY.keys())}"
        )
    return MODE_REGISTRY[name]


def is_readonly_mode(name: str) -> bool:
    """Check if a mode is read-only."""
    return MODE_REGISTRY.get(name, ModeSpec(
        name=name, model_config_key="", default_model="", turns_config_key="",
        default_max_turns=50, prompt_file="", readonly=True, planning_gate=False,
        multi_employee=False, manager_review=False, verification_required=False,
    )).readonly


# Progressive deepening escalation ladder
ESCALATION_LADDER: list[dict[str, str | int]] = [
    {"mode": "fix", "model": "claude-sonnet-4-6", "thinking": "standard", "max_turns": 50},
    {"mode": "full", "model": "claude-sonnet-4-6", "thinking": "extended", "max_turns": 100},
    {"mode": "full", "model": "claude-opus-4-6", "thinking": "standard", "max_turns": 150},
    {"mode": "full", "model": "claude-opus-4-6", "thinking": "extended", "max_turns": 200},
]


def starting_rung(complexity_score: int) -> int:
    """Determine the starting escalation rung from a complexity score (1-5)."""
    if complexity_score <= 2:
        return 0
    elif complexity_score <= 3:
        return 1
    else:
        return 2
