"""Application configuration via pydantic-settings."""

from pathlib import Path
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    db_path: str = "/opt/git/claude-agent-station/dashboard/backend/station.db"

    # Agent log directory
    log_dir: str = "/var/log/claude-agent"

    # Station config JSON (source of truth for agent)
    config_path: str = "/home/claude-agent/.claude/autonomous/manager-config.json"

    # Employee report base directory
    workspaces_dir: str = "/home/claude-agent/workspaces"

    # Server
    host: str = "127.0.0.1"
    port: int = 8420

    # WebSocket polling interval (seconds)
    ws_poll_interval: float = 0.5

    # Shared secret for authenticating webhook requests from the agent.
    # When set, all POST /api/webhook/* requests must include a matching
    # X-Webhook-Token header.  When empty (default), no auth is required
    # (backward-compatible with existing deployments).
    webhook_secret: Optional[str] = None

    # CORS allowed origins for cross-origin requests (e.g. frontend dev server)
    # Override with STATION_ALLOWED_ORIGINS as a JSON list or comma-separated string
    allowed_origins: List[str] = [
        "http://localhost:5173",
        "http://localhost:4173",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:4173",
    ]

    model_config = SettingsConfigDict(env_prefix="STATION_")


settings = Settings()
