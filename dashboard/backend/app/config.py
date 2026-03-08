"""Application configuration via pydantic-settings."""

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


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

    class Config:
        env_prefix = "STATION_"


settings = Settings()
