"""SQLAlchemy ORM models."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column, Integer, Text, Boolean, Float, DateTime, ForeignKey
)

from app.database import Base


def _utcnow() -> datetime:
    """Return current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    repo = Column(Text, nullable=False, unique=True)
    priority = Column(Text, default="medium")
    mode = Column(Text, default="full")
    enabled = Column(Boolean, default=True)
    branch = Column(Text, default="main")
    custom_instructions = Column(Text, nullable=True, default=None)
    setup_script = Column(Text, nullable=True, default=None)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class Run(Base):
    __tablename__ = "runs"

    id = Column(Integer, primary_key=True)
    run_id = Column(Text, nullable=False, unique=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    mode = Column(Text, nullable=True)
    model = Column(Text, nullable=True)
    status = Column(Text, nullable=True)  # running/success/failed
    verdict = Column(Text, nullable=True)  # APPROVE/PR/REJECT/null
    issue_number = Column(Integer, nullable=True)
    branch = Column(Text, nullable=True)
    cost_usd = Column(Float, nullable=True)  # Deprecated: kept for historical data
    tokens_input = Column(Integer, nullable=True)
    tokens_output = Column(Integer, nullable=True)
    tokens_total = Column(Integer, nullable=True)
    turns = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    employee_report = Column(Text, nullable=True)  # JSON as text
    verdict_detail = Column(Text, nullable=True)  # JSON as text
    log_file = Column(Text, nullable=True)
    employee_index = Column(Integer, nullable=True, default=0)
    concurrent_group_id = Column(Text, nullable=True)


class ConfigEntry(Base):
    __tablename__ = "config"

    key = Column(Text, primary_key=True)
    value = Column(Text, nullable=True)  # JSON-encoded
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class Plan(Base):
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    issue_number = Column(Integer, nullable=True)
    issue_title = Column(Text, nullable=True)
    title = Column(Text, nullable=False)
    description = Column(Text, nullable=True)  # Markdown plan content
    steps = Column(Text, nullable=True)  # JSON array of implementation steps
    estimated_scope = Column(Text, nullable=True)  # small/medium/large
    files_affected = Column(Text, nullable=True)  # JSON array of file paths
    status = Column(Text, default="draft")  # draft/approved/implementing/completed/rejected
    run_id = Column(Text, nullable=True)  # run that created this plan
    implementation_run_id = Column(Text, nullable=True)  # run that implemented this plan
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CoordinatorTask(Base):
    __tablename__ = "coordinator_tasks"

    id = Column(Text, primary_key=True)  # "task-{run_id}-{seq}"
    run_id = Column(Text, nullable=False, index=True)
    project_repo = Column(Text, nullable=False)
    issue_number = Column(Integer, nullable=True)
    title = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(Text, default="pending")  # pending/ready/running/completed/failed/blocked
    employee_index = Column(Integer, nullable=True)
    depends_on = Column(Text, nullable=True)  # JSON array of task IDs
    workspace = Column(Text, nullable=True)
    expected_files = Column(Text, nullable=True)  # JSON array
    touched_files = Column(Text, nullable=True)  # JSON array
    exit_code = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    dag_json = Column(Text, nullable=True)  # Full DAG snapshot (on first task only)
    created_at = Column(DateTime, default=_utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class CoordinatorMessage(Base):
    __tablename__ = "coordinator_messages"

    id = Column(Integer, primary_key=True)
    run_id = Column(Text, nullable=False, index=True)
    task_id = Column(Text, nullable=True)
    direction = Column(Text, nullable=False)  # to_employee / from_monitor / system
    message_type = Column(Text, nullable=False)  # guidance / conflict / progress / error
    content = Column(Text, nullable=False)  # JSON
    employee_index = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    run_id = Column(Text, nullable=True)
    type = Column(Text, nullable=True)  # approve/reject/pr/error/info
    message = Column(Text, nullable=True)
    read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_utcnow)


class PlanUsageHistory(Base):
    __tablename__ = "plan_usage_history"

    id = Column(Integer, primary_key=True)
    timestamp = Column(Text, nullable=False)
    detection_method = Column(Text, nullable=True)  # cli_scrape / heuristic
    plan_tier = Column(Text, nullable=True)
    session_tokens_used = Column(Integer, default=0)
    session_tokens_limit = Column(Integer, default=0)
    session_usage_percent = Column(Float, default=0.0)
    weekly_tokens_used = Column(Integer, default=0)
    weekly_tokens_limit = Column(Integer, default=0)
    weekly_usage_percent = Column(Float, default=0.0)
    weekly_reset_at = Column(Text, nullable=True)
    per_model_json = Column(Text, nullable=True)  # JSON array of per-model usage
    is_throttled = Column(Boolean, default=False)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
