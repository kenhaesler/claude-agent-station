"""Graduated backpressure service for managing system load."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Backpressure levels
GREEN = "GREEN"   # < 70% — full speed
YELLOW = "YELLOW"  # 70-85% — halve concurrency, prefer Sonnet
RED = "RED"       # 85-95% — single employee, Sonnet only
BLACK = "BLACK"   # > 95% — no new work, stop signals


@dataclass
class BackpressureState:
    """Current backpressure state and its effects."""
    level: str
    usage_percent: float
    max_concurrent: int
    effective_concurrent: int
    model_restriction: str | None  # None = no restriction
    turn_cap: int | None  # None = no cap


def calculate_backpressure(
    usage_percent: float,
    base_max_concurrent: int,
    base_max_turns: int = 200,
) -> BackpressureState:
    """Calculate backpressure level and its effects on scheduling.

    Args:
        usage_percent: Current plan/token usage as a percentage (0-100).
        base_max_concurrent: Configured max concurrent employees.
        base_max_turns: Configured max turns per employee.
    """
    if usage_percent >= 95:
        return BackpressureState(
            level=BLACK,
            usage_percent=usage_percent,
            max_concurrent=base_max_concurrent,
            effective_concurrent=0,
            model_restriction="none",
            turn_cap=0,
        )

    if usage_percent >= 85:
        return BackpressureState(
            level=RED,
            usage_percent=usage_percent,
            max_concurrent=base_max_concurrent,
            effective_concurrent=1,
            model_restriction="claude-sonnet-4-6",
            turn_cap=min(base_max_turns, 75),
        )

    if usage_percent >= 70:
        return BackpressureState(
            level=YELLOW,
            usage_percent=usage_percent,
            max_concurrent=base_max_concurrent,
            effective_concurrent=max(1, base_max_concurrent // 2),
            model_restriction="claude-sonnet-4-6",
            turn_cap=min(base_max_turns, 100),
        )

    return BackpressureState(
        level=GREEN,
        usage_percent=usage_percent,
        max_concurrent=base_max_concurrent,
        effective_concurrent=base_max_concurrent,
        model_restriction=None,
        turn_cap=None,
    )


def get_usage_percent_from_config(config_path: str | Path) -> float:
    """Read current usage percent from plan usage history or config.

    Returns 0.0 if no usage data is available.
    """
    try:
        config = json.loads(Path(config_path).read_text())
        return float(config.get("limits", {}).get("max_usage_percent", 80))
    except (OSError, json.JSONDecodeError, ValueError):
        return 0.0
