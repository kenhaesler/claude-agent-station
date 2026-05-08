from __future__ import annotations

"""Async SQLAlchemy engine, session factory, and DB initialization."""

import logging

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

DATABASE_URL = f"sqlite+aiosqlite:///{settings.db_path}"

engine = create_async_engine(DATABASE_URL, echo=False)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    """Enable WAL mode for concurrent reads."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


logger = logging.getLogger(__name__)


async def _migrate_add_columns(conn) -> None:
    """Add columns that may be missing from older databases."""
    migrations = [
        ("projects", "custom_instructions", "ALTER TABLE projects ADD COLUMN custom_instructions TEXT"),
        ("projects", "setup_script", "ALTER TABLE projects ADD COLUMN setup_script TEXT"),
        ("runs", "tokens_input", "ALTER TABLE runs ADD COLUMN tokens_input INTEGER"),
        ("runs", "tokens_output", "ALTER TABLE runs ADD COLUMN tokens_output INTEGER"),
        ("runs", "tokens_total", "ALTER TABLE runs ADD COLUMN tokens_total INTEGER"),
        ("runs", "employee_index", "ALTER TABLE runs ADD COLUMN employee_index INTEGER DEFAULT 0"),
        ("runs", "concurrent_group_id", "ALTER TABLE runs ADD COLUMN concurrent_group_id TEXT"),
        ("runs", "trace_id", "ALTER TABLE runs ADD COLUMN trace_id TEXT"),
        ("coordinator_tasks", "result_summary", "ALTER TABLE coordinator_tasks ADD COLUMN result_summary TEXT"),
        ("coordinator_tasks", "log_path", "ALTER TABLE coordinator_tasks ADD COLUMN log_path TEXT"),
        ("coordinator_tasks", "branch", "ALTER TABLE coordinator_tasks ADD COLUMN branch TEXT"),
        ("coordinator_tasks", "dag_json", "ALTER TABLE coordinator_tasks ADD COLUMN dag_json TEXT"),
        # plan_usage_history columns added in Phase 0 stabilization
        ("plan_usage_history", "session_reset_at", "ALTER TABLE plan_usage_history ADD COLUMN session_reset_at TEXT"),
        ("plan_usage_history", "seconds_until_session_reset", "ALTER TABLE plan_usage_history ADD COLUMN seconds_until_session_reset INTEGER DEFAULT 0"),
        ("plan_usage_history", "session_is_exhausted", "ALTER TABLE plan_usage_history ADD COLUMN session_is_exhausted INTEGER DEFAULT 0"),
        ("plan_usage_history", "seconds_until_weekly_reset", "ALTER TABLE plan_usage_history ADD COLUMN seconds_until_weekly_reset INTEGER DEFAULT 0"),
        ("plan_usage_history", "overuse_active", "ALTER TABLE plan_usage_history ADD COLUMN overuse_active INTEGER DEFAULT 0"),
        ("plan_usage_history", "overuse_signals_json", "ALTER TABLE plan_usage_history ADD COLUMN overuse_signals_json TEXT"),
        # Queue orchestration columns (agent orchestration overhaul)
        ("task_queue", "mode", "ALTER TABLE task_queue ADD COLUMN mode TEXT"),
        ("task_queue", "complexity_score", "ALTER TABLE task_queue ADD COLUMN complexity_score INTEGER"),
        ("task_queue", "escalation_rung", "ALTER TABLE task_queue ADD COLUMN escalation_rung INTEGER DEFAULT 0"),
        ("task_queue", "escalated_from", "ALTER TABLE task_queue ADD COLUMN escalated_from INTEGER"),
        ("task_queue", "parent_task_id", "ALTER TABLE task_queue ADD COLUMN parent_task_id TEXT"),
        ("task_queue", "confidence", "ALTER TABLE task_queue ADD COLUMN confidence REAL"),
        ("task_queue", "handoff_context", "ALTER TABLE task_queue ADD COLUMN handoff_context TEXT"),
        # Security reviewer feature (issue #128)
        ("projects", "security_review_enabled", "ALTER TABLE projects ADD COLUMN security_review_enabled BOOLEAN DEFAULT 0"),
        # Intelligent Agent Swarm: subsystem + employee_index on task_outcomes
        ("task_outcomes", "subsystem", "ALTER TABLE task_outcomes ADD COLUMN subsystem TEXT"),
        ("task_outcomes", "employee_index", "ALTER TABLE task_outcomes ADD COLUMN employee_index INTEGER"),
        # Self-healing learning loop columns (Phase 4)
        ("task_outcomes", "analyst_role", "ALTER TABLE task_outcomes ADD COLUMN analyst_role TEXT"),
        ("task_outcomes", "validation_passed", "ALTER TABLE task_outcomes ADD COLUMN validation_passed BOOLEAN"),
        # Agent Teams migration (multi-employee coordination overhaul)
        ("runs", "team_name", "ALTER TABLE runs ADD COLUMN team_name TEXT"),
        ("runs", "team_members", "ALTER TABLE runs ADD COLUMN team_members TEXT"),
        ("coordinator_tasks", "teammate_agent_id", "ALTER TABLE coordinator_tasks ADD COLUMN teammate_agent_id TEXT"),
        ("coordinator_tasks", "claimed_by", "ALTER TABLE coordinator_tasks ADD COLUMN claimed_by TEXT"),
        ("coordinator_tasks", "claimed_at", "ALTER TABLE coordinator_tasks ADD COLUMN claimed_at DATETIME"),
        ("agent_events", "team_name", "ALTER TABLE agent_events ADD COLUMN team_name TEXT"),
        # ADR-0001: autonomy level + per-run/project budget ceiling
        ("projects", "autonomy_level", "ALTER TABLE projects ADD COLUMN autonomy_level TEXT DEFAULT 'assisted'"),
        ("projects", "max_budget_usd", "ALTER TABLE projects ADD COLUMN max_budget_usd REAL"),
        ("runs", "autonomy_level", "ALTER TABLE runs ADD COLUMN autonomy_level TEXT DEFAULT 'assisted'"),
        ("runs", "max_budget_usd", "ALTER TABLE runs ADD COLUMN max_budget_usd REAL"),
        # Project vision cache (Phase 1 — vision authoring)
        ("projects", "vision_cached_sha",  "ALTER TABLE projects ADD COLUMN vision_cached_sha TEXT"),
        ("projects", "vision_cached_body", "ALTER TABLE projects ADD COLUMN vision_cached_body TEXT"),
        ("projects", "vision_cached_at",   "ALTER TABLE projects ADD COLUMN vision_cached_at DATETIME"),
    ]
    # `table` and `sql` below are hardcoded literals from the migrations tuple
    # list above; PRAGMA + ALTER TABLE do not support bound parameters for
    # identifiers, so f-strings are required.
    for table, column, sql in migrations:
        try:
            result = await conn.execute(text(f"PRAGMA table_info({table})"))  # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text
            columns = [row[1] for row in result.fetchall()]
            if column not in columns:
                await conn.execute(text(sql))  # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text
                logger.info("Migration: added %s.%s", table, column)
        except Exception as e:
            logger.debug("Migration skip %s.%s: %s", table, column, e)

    # Indexes on runs columns used by list/filter/analytics queries (issue #191).
    # SQLAlchemy's create_all creates indexes from model declarations on fresh
    # databases, but columns added later via ALTER TABLE never get indexed
    # unless we do it explicitly here. CREATE INDEX IF NOT EXISTS is idempotent.
    index_migrations = [
        "CREATE INDEX IF NOT EXISTS ix_runs_status ON runs(status)",
        "CREATE INDEX IF NOT EXISTS ix_runs_project_id ON runs(project_id)",
        "CREATE INDEX IF NOT EXISTS ix_runs_verdict ON runs(verdict)",
        "CREATE INDEX IF NOT EXISTS ix_runs_started_at ON runs(started_at)",
        "CREATE INDEX IF NOT EXISTS ix_runs_concurrent_group_id ON runs(concurrent_group_id)",
    ]
    for sql in index_migrations:
        try:
            await conn.execute(text(sql))  # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text
        except Exception as e:
            logger.debug("Index migration skip: %s: %s", sql, e)


async def init_db():
    """Create all tables and run migrations."""
    async with engine.begin() as conn:
        from app.models import (  # noqa: F401
            AgentEvent,
            BrainstormMessage,
            BrainstormSession,
            ConfigEntry,
            CoordinatorMessage,
            CoordinatorTask,
            IntegrationFeature,
            Notification,
            PermissionRequest,
            Plan,
            PlanUsageHistory,
            PromptVersion,
            Project,
            QueueItem,
            Run,
            RunControl,
            StationControl,
            TaskOutcome,
            VisionChatSession,  # ← add
        )
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_add_columns(conn)
        # Seed StationControl singleton (id=1). Idempotent via INSERT OR IGNORE.
        await conn.execute(
            text("INSERT OR IGNORE INTO station_control (id, global_pause) VALUES (1, 0)")
        )

    # Run JSON config migrations (idempotent, safe to call every startup)
    try:
        import importlib
        mod = importlib.import_module("migrations.0003_simplify_config_schema")
        mod.run()
    except Exception as e:
        logger.debug("Config schema migration skipped: %s", e)
