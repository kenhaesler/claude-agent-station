"""Application configuration via pydantic-settings."""


from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    db_path: str = "/var/lib/claude-agent-station/station.db"
    db_url: str | None = None  # preferred; full SQLAlchemy URL incl. driver
    # Path to a file containing the DB password (compose ``secrets:`` mount).
    # When set, the literal token ``${DB_PASSWORD}`` in ``db_url`` is replaced
    # with the file's contents at startup. Keeps the password out of process
    # env (visible via ``/proc/<pid>/environ`` or ``docker inspect``).
    db_password_file: str | None = None

    @property
    def resolved_db_url(self) -> str:
        """Return the URL the engine should use.

        Order:
        1. ``db_url`` env (production). If ``${DB_PASSWORD}`` appears in the
           URL and ``db_password_file`` points at a readable file, the token
           is substituted with the file's contents (trimmed).
        2. ``db_path`` (SQLite fallback).

        Empty ``db_url`` treated as unset.
        """
        if self.db_url:
            url = self.db_url
            if "${DB_PASSWORD}" in url and self.db_password_file:
                try:
                    with open(self.db_password_file, "r", encoding="utf-8") as f:
                        password = f.read().strip()
                    url = url.replace("${DB_PASSWORD}", password)
                except OSError:
                    # Fall through with the placeholder intact; the engine
                    # will fail loudly with a clear "auth failed" message
                    # rather than the silent-empty-password failure mode
                    # the original ``${DB_PASSWORD}`` shell-interp had.
                    pass
            return url
        return f"sqlite+aiosqlite:///{self.db_path}"

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

    # Secret for verifying GitHub webhook HMAC-SHA256 signatures.
    # Set via STATION_GITHUB_WEBHOOK_SECRET env var.
    github_webhook_secret: str | None = None

    # Runner mode: "container" (Docker SDK) | "inline" (legacy subprocess)
    runner_mode: str = "container"
    # Image used to spawn ephemeral runner containers. The ":dev" tag is a
    # development placeholder — production deployments should pin a specific
    # tag (e.g. a release SHA) via STATION_RUNNER_IMAGE. PR-3 of the #386
    # rollout wires this into the compose file as the cas-runner service's
    # image; the dashboard never needs to pull it directly.
    runner_image: str = "claude-agent-station/agent:dev"
    # Memory/CPU quotas applied to runner containers when a Project row
    # doesn't pin its own. Memory accepts Docker's unit-suffixed strings
    # ("2g") or raw byte counts as a string; cpus is fractional ("1.0").
    default_runner_memory_limit: str = "2g"
    default_runner_cpu_limit: str = "1.0"

    # CORS allowed origins for cross-origin requests (e.g. frontend dev server)
    # Override with STATION_ALLOWED_ORIGINS as a JSON list or comma-separated string
    allowed_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:4173",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:4173",
    ]

    model_config = SettingsConfigDict(
        env_prefix="STATION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
