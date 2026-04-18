from __future__ import annotations

"""SQLAlchemy ORM models."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text

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
    security_review_enabled = Column(Boolean, default=False)
    # ADR-0001: autonomy level (manual/assisted/auto); default budget ceiling
    autonomy_level = Column(Text, default="assisted")
    max_budget_usd = Column(Float, nullable=True, default=None)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class Run(Base):
    """Execution tracking: what happened during a Claude agent session.

    Lifecycle: running -> reviewing -> completed/failed/interrupted
    One Run per employee invocation. Stores tokens consumed, verdicts, timing.
    Linked to QueueItem via run_id. Linked to CoordinatorTask via run_id.
    """
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
    trace_id = Column(Text, nullable=True)
    employee_index = Column(Integer, nullable=True, default=0)
    concurrent_group_id = Column(Text, nullable=True)
    # Agent Teams fields
    team_name = Column(Text, nullable=True)
    team_members = Column(Text, nullable=True)  # JSON: [{agent_id, name, status}]
    # ADR-0001: autonomy snapshot at trigger time; per-run budget override
    autonomy_level = Column(Text, nullable=True, default="assisted")
    max_budget_usd = Column(Float, nullable=True, default=None)


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
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class CoordinatorTask(Base):
    """DAG orchestration: task decomposition for multi-agent runs.

    Lifecycle: pending -> ready -> running -> completed/failed/blocked
    Used when a Run decomposes work into a dependency graph. Each task may be
    assigned to a different employee. Linked to Run via run_id.
    """
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
    result_summary = Column(Text, nullable=True)  # Brief summary of what the task produced
    log_path = Column(Text, nullable=True)  # Path to employee log file
    branch = Column(Text, nullable=True)  # Git branch used by the employee
    dag_json = Column(Text, nullable=True)  # Full DAG snapshot (on first task only)
    # Agent Teams fields
    teammate_agent_id = Column(Text, nullable=True)  # Agent Teams agent ID
    claimed_by = Column(Text, nullable=True)  # Teammate name that claimed the task
    claimed_at = Column(DateTime, nullable=True)
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


class QueueItem(Base):
    """Work management: what needs to be done.

    Lifecycle: pending -> claimed -> assigned -> in_progress -> review -> completed/failed
    The input side of the pipeline. Tracks retries, escalation, priority.
    Linked to Run via run_id once claimed by an employee.
    """
    __tablename__ = "task_queue"

    id = Column(Integer, primary_key=True)
    project_repo = Column(Text, nullable=False, index=True)
    issue_number = Column(Integer, nullable=True)
    issue_title = Column(Text, nullable=True)
    state = Column(Text, nullable=False, default="pending", index=True)
    priority = Column(Integer, default=0)
    assigned_to = Column(Integer, nullable=True)
    run_id = Column(Text, nullable=True, index=True)
    employee_report = Column(Text, nullable=True)  # JSON
    manager_feedback = Column(Text, nullable=True)  # JSON
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=1)
    context = Column(Text, nullable=True)  # JSON
    error_message = Column(Text, nullable=True)
    # Orchestration fields
    mode = Column(Text, nullable=True)  # Mode for this item
    complexity_score = Column(Integer, nullable=True)  # 1-5 from router
    escalation_rung = Column(Integer, default=0)  # Current position on escalation ladder
    escalated_from = Column(Integer, nullable=True)  # Parent item ID
    parent_task_id = Column(Text, nullable=True)  # Link to coordinator_tasks
    confidence = Column(Float, nullable=True)  # Employee's reported confidence score
    handoff_context = Column(Text, nullable=True)  # JSON handoff document
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    assigned_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)


class PlanUsageHistory(Base):
    __tablename__ = "plan_usage_history"

    id = Column(Integer, primary_key=True)
    timestamp = Column(Text, nullable=False)
    detection_method = Column(Text, nullable=True)  # cli_scrape / heuristic
    plan_tier = Column(Text, nullable=True)
    session_tokens_used = Column(Integer, default=0)
    session_tokens_limit = Column(Integer, default=0)
    session_usage_percent = Column(Float, default=0.0)
    session_reset_at = Column(Text, nullable=True)
    seconds_until_session_reset = Column(Integer, default=0)
    session_is_exhausted = Column(Integer, default=0)
    weekly_tokens_used = Column(Integer, default=0)
    weekly_tokens_limit = Column(Integer, default=0)
    weekly_usage_percent = Column(Float, default=0.0)
    weekly_reset_at = Column(Text, nullable=True)
    seconds_until_weekly_reset = Column(Integer, default=0)
    per_model_json = Column(Text, nullable=True)  # JSON array of per-model usage
    is_throttled = Column(Boolean, default=False)
    overuse_active = Column(Integer, default=0)
    overuse_signals_json = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class AgentEvent(Base):
    """Append-only event log for structured audit trail (ESAA pattern)."""
    __tablename__ = "agent_events"

    event_id = Column(Integer, primary_key=True)
    workflow_id = Column(Text, nullable=False, index=True)  # Groups events for one task
    run_id = Column(Text, nullable=True, index=True)
    agent_id = Column(Text, nullable=False)  # "employee-0", "manager", "teammate-{name}"
    event_type = Column(Text, nullable=False, index=True)  # "task.claimed", "analysis.complete", etc.
    team_name = Column(Text, nullable=True)  # Agent Teams team name
    event_data = Column(Text, nullable=False)  # JSON payload
    parent_event_id = Column(Integer, nullable=True)  # Causal chain
    created_at = Column(DateTime, default=_utcnow)


class TaskOutcome(Base):
    """Tracks outcomes for adaptive scheduling and learning."""
    __tablename__ = "task_outcomes"

    id = Column(Integer, primary_key=True)
    queue_item_id = Column(Integer, nullable=True)
    project_repo = Column(Text, nullable=False, index=True)
    issue_number = Column(Integer, nullable=True)
    issue_type = Column(Text, nullable=True)  # bug, feature, chore (from triage/labels)
    complexity_score = Column(Integer, nullable=True)  # 1-5 from router
    mode_used = Column(Text, nullable=False)
    model_used = Column(Text, nullable=False)
    escalation_rung = Column(Integer, default=0)
    prompt_version = Column(Integer, default=1)
    confidence_reported = Column(Float, nullable=True)
    success = Column(Boolean, nullable=False)
    tests_passed = Column(Boolean, nullable=True)
    verdict = Column(Text, nullable=True)  # approve, pr, reject
    failure_category = Column(Text, nullable=True)  # test_failure, wrong_approach, incomplete, quality
    subsystem = Column(Text, nullable=True)  # frontend, backend, agent, infra, mixed
    employee_index = Column(Integer, nullable=True)
    tokens_consumed = Column(Integer, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    analyst_role = Column(Text, nullable=True)  # visionary, architect, etc. (sprint learning loop)
    validation_passed = Column(Boolean, nullable=True)  # did the feature pass validation on dev?
    created_at = Column(DateTime, default=_utcnow)


class BrainstormSession(Base):
    """A brainstorm conversation session."""
    __tablename__ = "brainstorm_sessions"

    id = Column(Text, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    title = Column(Text, nullable=True)
    persona = Column(Text, default="architect")  # architect/security/performance/devops
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class BrainstormMessage(Base):
    """A single message in a brainstorm session."""
    __tablename__ = "brainstorm_messages"

    id = Column(Text, primary_key=True)
    session_id = Column(Text, ForeignKey("brainstorm_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(Text, nullable=False)  # 'user' or 'assistant'
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_utcnow)


class IntegrationFeature(Base):
    """A feature branch merged into the integration (dev) branch."""
    __tablename__ = "integration_features"

    id = Column(Integer, primary_key=True)
    project_repo = Column(Text, nullable=False, index=True)
    issue_number = Column(Integer, nullable=True)
    issue_title = Column(Text, nullable=True)
    branch = Column(Text, nullable=False)
    state = Column(Text, nullable=False, default="merged_to_dev")
    merge_commit = Column(Text, nullable=True)
    validation_status = Column(Text, nullable=True)
    validation_output = Column(Text, nullable=True)
    pr_number = Column(Integer, nullable=True)
    run_id = Column(Text, nullable=True)
    promotion_run_id = Column(Text, nullable=True)
    excluded_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class PromptVersion(Base):
    """Tracks prompt versions for A/B testing and evolution."""
    __tablename__ = "prompt_versions"

    id = Column(Integer, primary_key=True)
    prompt_name = Column(Text, nullable=False, index=True)  # "employee", "analyst", etc.
    version = Column(Integer, nullable=False)
    content_hash = Column(Text, nullable=False)  # SHA256 of prompt content
    change_description = Column(Text, nullable=True)
    active = Column(Boolean, default=True)
    success_rate = Column(Float, nullable=True)  # Calculated from task_outcomes
    sample_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=_utcnow)


class PermissionRequest(Base):
    """Pending operator permission requests raised by the policy engine.

    ADR-0001: under manual/assisted, destructive bash or edit calls can be
    referred to the operator instead of being denied outright. The policy
    engine writes a row here, an SSE event notifies the dashboard, the
    operator clicks approve/deny, and the agent polls the row to unblock.

    Auto-deny after 5 minutes if the operator doesn't respond — configurable
    via STATION_PERMISSION_TRAY_TIMEOUT_SECONDS.
    """
    __tablename__ = "permission_requests"

    id = Column(Integer, primary_key=True)
    request_id = Column(Text, nullable=False, unique=True, index=True)
    run_id = Column(Text, nullable=False, index=True)
    agent_id = Column(Text, nullable=False)
    tool_name = Column(Text, nullable=False)
    tool_input = Column(Text, nullable=False)  # JSON
    autonomy_level = Column(Text, nullable=False)
    reason = Column(Text, nullable=True)  # Why the policy referred this to the operator
    status = Column(Text, nullable=False, default="pending", index=True)
    # pending | approved | denied | timed_out
    resolution_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    resolved_at = Column(DateTime, nullable=True)
