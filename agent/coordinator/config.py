"""Coordinator configuration from manager-config.json and environment."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CoordinatorConfig:
    """Configuration for the coordinator process."""

    run_id: str = ""
    config_file: str = ""
    log_dir: str = ""
    workspaces_dir: str = ""
    assignments_file: str = ""
    concurrent_group_id: str = ""

    # From manager-config.json
    max_concurrent: int = 3
    max_per_project: int = 2
    max_employee_turns: int = 200
    budget_strategy: str = "equal_split"
    employee_model: str = "claude-opus-4-6"
    webhook_url: str = "http://127.0.0.1:8420/api/webhook/run-event"
    webhook_secret: str = ""
    db_path: str = "/opt/git/claude-agent-station/dashboard/backend/station.db"

    # Coordinator-specific
    stream_poll_interval: float = 0.5
    guidance_check_interval: float = 5.0
    conflict_detection: bool = True
    max_consecutive_failures: int = 3
    decomposition_model: str = "claude-haiku-4-5-20251001"

    # Plan usage enforcement
    plan_tier: str = "max_5x"
    max_usage_percent: float = 85.0
    plan_usage_detection_enabled: bool = True

    # Planning phase configuration (plan-before-implement)
    planning_enabled: bool = True
    planning_max_revisions: int = 2

    # Mode-specific model/turn overrides
    analyst_model: str = "claude-sonnet-4-6"
    planner_model: str = "claude-sonnet-4-6"
    router_model: str = "claude-haiku-4-5-20251001"
    max_analyst_turns: int = 50
    max_planner_turns: int = 50
    max_fix_turns: int = 75
    max_triage_turns: int = 30
    max_review_turns: int = 30

    # Intelligence features (all default off for backward compatibility)
    auto_mode_selection: bool = False
    progressive_deepening: bool = False
    confidence_gating: bool = False
    independent_verification: bool = False
    adaptive_scheduling: bool = False
    work_stealing: bool = False

    # Project mode (set per-assignment in __main__.py, not from config file)
    project_mode: str = "full"  # "full", "plan", "analyze", "fix", "triage", "review"

    @classmethod
    def from_args(
        cls,
        run_id: str,
        config_file: str,
        log_dir: str,
        workspaces_dir: str,
        assignments_file: str,
        concurrent_group_id: str,
    ) -> CoordinatorConfig:
        """Build config from CLI args + config file."""
        cfg = cls(
            run_id=run_id,
            config_file=config_file,
            log_dir=log_dir,
            workspaces_dir=workspaces_dir,
            assignments_file=assignments_file,
            concurrent_group_id=concurrent_group_id,
        )

        # Load manager-config.json
        if Path(config_file).exists():
            with open(config_file) as f:
                data = json.load(f)

            limits = data.get("limits", {})
            cfg.max_concurrent = limits.get("max_concurrent_employees", cfg.max_concurrent)
            cfg.max_per_project = limits.get("max_employees_per_project", cfg.max_per_project)
            cfg.max_employee_turns = limits.get("max_employee_turns", cfg.max_employee_turns)
            cfg.budget_strategy = limits.get("token_budget_strategy", cfg.budget_strategy)

            models = data.get("models", {})
            cfg.employee_model = models.get("employee", cfg.employee_model)
            cfg.analyst_model = models.get("analyst", cfg.analyst_model)
            cfg.planner_model = models.get("planner", cfg.planner_model)
            cfg.router_model = models.get("router", cfg.router_model)

            # Mode-specific turn limits
            cfg.max_analyst_turns = limits.get("max_analyst_turns", cfg.max_analyst_turns)
            cfg.max_planner_turns = limits.get("max_planner_turns", cfg.max_planner_turns)
            cfg.max_fix_turns = limits.get("max_fix_turns", cfg.max_fix_turns)
            cfg.max_triage_turns = limits.get("max_triage_turns", cfg.max_triage_turns)
            cfg.max_review_turns = limits.get("max_review_turns", cfg.max_review_turns)

            # Intelligence features
            intelligence = data.get("intelligence", {})
            cfg.auto_mode_selection = intelligence.get("auto_mode_selection", cfg.auto_mode_selection)
            cfg.progressive_deepening = intelligence.get("progressive_deepening", cfg.progressive_deepening)
            cfg.confidence_gating = intelligence.get("confidence_gating", cfg.confidence_gating)
            cfg.independent_verification = intelligence.get("independent_verification", cfg.independent_verification)
            cfg.adaptive_scheduling = intelligence.get("adaptive_scheduling", cfg.adaptive_scheduling)
            cfg.work_stealing = intelligence.get("work_stealing", cfg.work_stealing)

            dashboard = data.get("dashboard", {})
            cfg.webhook_url = dashboard.get(
                "webhook_url", cfg.webhook_url
            )
            cfg.webhook_secret = dashboard.get(
                "webhook_secret", cfg.webhook_secret
            )

            coordinator = data.get("coordinator", {})
            cfg.stream_poll_interval = coordinator.get("stream_poll_interval", cfg.stream_poll_interval)
            cfg.conflict_detection = coordinator.get("conflict_detection", cfg.conflict_detection)
            cfg.max_consecutive_failures = coordinator.get("max_consecutive_failures", cfg.max_consecutive_failures)
            cfg.decomposition_model = coordinator.get("decomposition_model", cfg.decomposition_model)
            cfg.db_path = coordinator.get("db_path", cfg.db_path)
            cfg.plan_tier = coordinator.get("plan_tier", cfg.plan_tier)
            cfg.max_usage_percent = coordinator.get("max_usage_percent", cfg.max_usage_percent)
            cfg.plan_usage_detection_enabled = coordinator.get(
                "plan_usage_detection_enabled", cfg.plan_usage_detection_enabled
            )

            planning = data.get("planning", {})
            cfg.planning_enabled = planning.get("enabled", cfg.planning_enabled)
            cfg.planning_max_revisions = planning.get("max_revisions", cfg.planning_max_revisions)

        # Environment overrides
        cfg.db_path = os.environ.get("STATION_DB", cfg.db_path)
        cfg.webhook_url = os.environ.get("STATION_WEBHOOK_URL", cfg.webhook_url)
        cfg.webhook_secret = os.environ.get("STATION_WEBHOOK_SECRET", cfg.webhook_secret)

        return cfg
