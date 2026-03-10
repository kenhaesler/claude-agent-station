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
    db_path: str = "/var/lib/claude-agent-station/station.db"

    # Coordinator-specific
    stream_poll_interval: float = 0.5
    guidance_check_interval: float = 5.0
    conflict_detection: bool = True
    max_consecutive_failures: int = 3
    decomposition_model: str = "claude-haiku-4-5-20251001"

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

            dashboard = data.get("dashboard", {})
            cfg.webhook_url = dashboard.get(
                "webhook_url", cfg.webhook_url
            )

            coordinator = data.get("coordinator", {})
            cfg.stream_poll_interval = coordinator.get("stream_poll_interval", cfg.stream_poll_interval)
            cfg.conflict_detection = coordinator.get("conflict_detection", cfg.conflict_detection)
            cfg.max_consecutive_failures = coordinator.get("max_consecutive_failures", cfg.max_consecutive_failures)
            cfg.decomposition_model = coordinator.get("decomposition_model", cfg.decomposition_model)
            cfg.db_path = coordinator.get("db_path", cfg.db_path)

        # Environment overrides
        cfg.db_path = os.environ.get("STATION_DB", cfg.db_path)
        cfg.webhook_url = os.environ.get("STATION_WEBHOOK_URL", cfg.webhook_url)

        return cfg
