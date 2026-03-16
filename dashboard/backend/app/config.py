"""Application configuration via pydantic-settings."""


from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    db_path: str = "/var/lib/claude-agent-station/station.db"

    # Agent log directory
    log_dir: str = "/var/log/claude-agent"

    # Station config JSON (source of truth for agent)
    config_path: str = "/home/claude-agent/.claude/autonomous/manager-config.json"

    # Employee report base directory
    workspaces_dir: str = "/home/claude-agent/workspaces"

    # Path to Claude CLI credentials file
    credentials_path: str = "/home/claude-agent/.claude/.credentials.json"

    # Server
    host: str = "127.0.0.1"
    port: int = 8420

    # WebSocket polling interval (seconds)
    ws_poll_interval: float = 0.5

    # API key for authenticating dashboard API requests.
    # When set via STATION_API_KEY, all API endpoints (except /api/health and
    # /api/webhook/*) require a matching Bearer token or ?token= query param.
    # When None (default), no auth is required (backward-compatible open access).
    api_key: str | None = None

    # Shared secret for authenticating webhook requests from the agent.
    # When set, all POST /api/webhook/* requests must include a matching
    # X-Webhook-Token header.  When empty (default), no auth is required
    # (backward-compatible with existing deployments).
    webhook_secret: str | None = None

    # GitHub OAuth App credentials (for dashboard-managed GitHub login)
    # Set via STATION_GITHUB_CLIENT_ID and STATION_GITHUB_CLIENT_SECRET env vars
    github_client_id: str = ""
    github_client_secret: str = ""
    # Optional redirect URI for GitHub OAuth callback
    github_oauth_redirect_uri: str = ""

    # Secret for verifying GitHub webhook HMAC-SHA256 signatures.
    # Set via STATION_GITHUB_WEBHOOK_SECRET env var.
    github_webhook_secret: str | None = None

    # CORS allowed origins for cross-origin requests (e.g. frontend dev server)
    # Override with STATION_ALLOWED_ORIGINS as a JSON list or comma-separated string
    allowed_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:4173",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:4173",
    ]

    model_config = SettingsConfigDict(env_prefix="STATION_", env_file=".env", env_file_encoding="utf-8")


settings = Settings()
