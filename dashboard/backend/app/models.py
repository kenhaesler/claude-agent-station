from __future__ import annotations

"""SQLAlchemy ORM models."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base

# Dialect-aware JSON type: JSONB on Postgres, plain JSON on SQLite (#393).
# Intentionally a module-level singleton TypeEngine instance — every Column
# declaration references the same object. Future column-specific options
# (e.g. JSONB(astext_type=...)) should construct a fresh JSON().with_variant
# rather than mutate this shared instance.
JsonType = JSON().with_variant(JSONB(), "postgresql")


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
    # Promotion target for the long-lived integration branch's meta-PR.
    # NULL means fall back to ``branch``.
    promotion_target = Column(Text, nullable=True, default=None)
    custom_instructions = Column(Text, nullable=True, default=None)
    setup_script = Column(Text, nullable=True, default=None)
    security_review_enabled = Column(Boolean, default=False)
    # ADR-0001: autonomy level (manual/assisted/auto); default budget ceiling
    autonomy_level = Column(Text, default="assisted")
    max_budget_usd = Column(Float, nullable=True, default=None)
    # Per-project Docker resource quotas (#386). NULL = use compose-level defaults.
    # runner_memory_limit: bytes (e.g. 536870912 = 512 MiB). Passed as --memory.
    # runner_cpu_limit: fractional CPUs (e.g. 0.5). Passed as --cpus.
    runner_memory_limit = Column(Integer, nullable=True, default=None)
    runner_cpu_limit = Column(Float, nullable=True, default=None)
    # Vision cache (Phase 1 — see docs/superpowers/specs/2026-05-07-project-vision-design.md)
    vision_cached_sha = Column(Text, nullable=True, default=None)
    vision_cached_body = Column(Text, nullable=True, default=None)
    vision_cached_at = Column(DateTime(timezone=True), nullable=True, default=None)
    last_vision_analyzed_sha = Column(Text, nullable=True, default=None)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class Run(Base):
    """Execution tracking: what happened during a Claude agent session.

    Lifecycle: running -> reviewing -> completed/failed/interrupted
    One Run per employee invocation. Stores tokens consumed, verdicts, timing.
    Linked to QueueItem via run_id. Linked to CoordinatorTask via run_id.
    """
    __tablename__ = "runs"

    id = Column(Integer, primary_key=True)
    run_id = Column(Text, nullable=False, unique=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    mode = Column(Text, nullable=True)
    model = Column(Text, nullable=True)
    status = Column(Text, nullable=True, index=True)  # running/success/failed
    verdict = Column(Text, nullable=True, index=True)  # APPROVE/PR/REJECT/null
    issue_number = Column(Integer, nullable=True)
    branch = Column(Text, nullable=True)
    cost_usd = Column(Float, nullable=True)  # Deprecated: kept for historical data
    tokens_input = Column(Integer, nullable=True)
    tokens_output = Column(Integer, nullable=True)
    tokens_total = Column(Integer, nullable=True)
    turns = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True, index=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    employee_report = Column(JsonType, nullable=True)  # JSON/JSONB
    verdict_detail = Column(JsonType, nullable=True)  # JSON/JSONB
    log_file = Column(Text, nullable=True)
    trace_id = Column(Text, nullable=True)
    employee_index = Column(Integer, nullable=True, default=0)
    concurrent_group_id = Column(Text, nullable=True, index=True)
    # Agent Teams fields
    team_name = Column(Text, nullable=True)
    team_members = Column(Text, nullable=True)  # JSON: [{agent_id, name, status}]
    # ADR-0001: autonomy snapshot at trigger time; per-run budget override
    autonomy_level = Column(Text, nullable=True, default="assisted")
    max_budget_usd = Column(Float, nullable=True, default=None)
    # Vision-bootstrap (spec 2026-05-08-vision-issue-bootstrap-design.md)
    skip_reason = Column(Text, nullable=True)
    vision_bootstrap_count = Column(Integer, nullable=True)
    vision_bootstrap_proposals = Column(Text, nullable=True)  # JSON list
    # Updated on every webhook event for this run_id. NULL for legacy
    # rows. See issue #348.
    last_event_at = Column(DateTime(timezone=True), nullable=True, default=None, index=True)


class ConfigEntry(Base):
    __tablename__ = "config"

    key = Column(Text, primary_key=True)
    value = Column(Text, nullable=True)  # JSON-encoded
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


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
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


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
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    # Per-teammate progress (issue #336). Run.tokens_total / Run.turns hold the
    # lead's aggregate; these are the per-task slice the Fleet page reads.
    tokens_total = Column(Integer, nullable=True)
    turns = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)


class CoordinatorMessage(Base):
    __tablename__ = "coordinator_messages"

    id = Column(Integer, primary_key=True)
    run_id = Column(Text, nullable=False, index=True)
    task_id = Column(Text, nullable=True)
    direction = Column(Text, nullable=False)  # to_employee / from_monitor / system
    message_type = Column(Text, nullable=False)  # guidance / conflict / progress / error
    content = Column(Text, nullable=False)  # JSON
    employee_index = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    run_id = Column(Text, nullable=True)
    type = Column(Text, nullable=True)  # approve/reject/pr/error/info
    message = Column(Text, nullable=True)
    read = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


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
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    assigned_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)


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
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class AuditEntry(Base):
    """Per-tool-call telemetry: append-only audit trail of actions executed.

    Boundary vs. ``agent_events``: ``agent_events`` records orchestration-level
    decisions and workflow state transitions (e.g., ``auto_mode_decision``,
    ``employee_started``). ``audit_log`` records the *actions* an employee
    actually executed — tool calls, file edits, git operations, test runs —
    along with their outcome (status, exit code, stdout/stderr tails, timing).

    Rows are written in two phases: the SDK ``PreToolUse`` hook inserts a
    ``status='started'`` row keyed by ``idempotency_key`` (= SDK ``tool_use_id``);
    ``PostToolUse`` updates the same row with the result. The unique constraint
    on ``idempotency_key`` makes both phases idempotent under retries.
    """
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    trace_id = Column(Text, nullable=True, index=True)
    idempotency_key = Column(Text, nullable=False, unique=True)
    run_id = Column(Text, nullable=False, index=True)
    actor = Column(Text, nullable=False)  # "lead", "teammate-{name}", "manager"
    action_kind = Column(Text, nullable=False, index=True)  # "tool.bash", "tool.edit", ...
    action_detail = Column(JsonType, nullable=True)  # JSON/JSONB: command, file path, etc.
    status = Column(Text, nullable=False, index=True)  # "started" | "ok" | "error" | "timeout"
    exit_code = Column(Integer, nullable=True)
    stdout_tail = Column(Text, nullable=True)
    stderr_tail = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, index=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    # Issue #387: the timeline endpoint scans by run_id and orders by
    # started_at. A composite index turns a child-scan + sort into a
    # single range scan.
    __table_args__ = (
        Index("ix_audit_log_run_id_started", "run_id", "started_at"),
    )


class AgentEvent(Base):
    """Append-only event log for structured audit trail (ESAA pattern)."""
    __tablename__ = "agent_events"

    event_id = Column(Integer, primary_key=True)
    workflow_id = Column(Text, nullable=False, index=True)  # Groups events for one task
    run_id = Column(Text, nullable=True, index=True)
    agent_id = Column(Text, nullable=False)  # "employee-0", "manager", "teammate-{name}"
    event_type = Column(Text, nullable=False, index=True)  # "task.claimed", "analysis.complete", etc.
    team_name = Column(Text, nullable=True)  # Agent Teams team name
    event_data = Column(JsonType, nullable=False)  # JSON/JSONB payload
    parent_event_id = Column(Integer, nullable=True)  # Causal chain
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class ConflictResolution(Base):
    """One conflict-resolution attempt per row.

    Keyed on (branch, started_at) for the rolling 24h budget query in
    agent.conflict_resolver.budget. See spec
    docs/superpowers/specs/2026-05-10-conflict-resolution-design.md.
    """
    __tablename__ = "conflict_resolutions"

    id = Column(Integer, primary_key=True)
    branch = Column(Text, nullable=False, index=True)
    repo = Column(Text, nullable=False)
    pr_number = Column(Integer, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    # mechanical / lockfile / llm / budget_exhausted
    phase_reached = Column(Text, nullable=False)
    # resolved / tests_failed / manager_rejected / budget_exhausted / error
    outcome = Column(Text, nullable=False)
    tokens_input = Column(Integer, nullable=True)
    tokens_output = Column(Integer, nullable=True)
    # Denormalized for cheap budget queries (sum across input + output).
    tokens_total = Column(Integer, nullable=True)
    model_used = Column(Text, nullable=True)
    # How many feedback rounds were consumed across tests + manager review.
    feedback_rounds = Column(Integer, nullable=True, default=0)
    # pre_pr / at_merge
    triggered_by = Column(Text, nullable=False)
    run_id = Column(Text, nullable=True)
    error_detail = Column(Text, nullable=True)


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
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class BrainstormSession(Base):
    """A brainstorm conversation session."""
    __tablename__ = "brainstorm_sessions"

    id = Column(Text, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    title = Column(Text, nullable=True)
    persona = Column(Text, default="architect")  # architect/security/performance/devops
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class BrainstormMessage(Base):
    """A single message in a brainstorm session."""
    __tablename__ = "brainstorm_messages"

    id = Column(Text, primary_key=True)
    session_id = Column(Text, ForeignKey("brainstorm_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(Text, nullable=False)  # 'user' or 'assistant'
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


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
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


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
    created_at = Column(DateTime(timezone=True), default=_utcnow)


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
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    resolved_at = Column(DateTime(timezone=True), nullable=True)


class RunControl(Base):
    """Queued operator intervention for a running agent (Mission Control, Phase A).

    The dashboard writes rows here; the orchestrator polls for unconsumed rows
    between SDK messages and applies them. One row per discrete action so
    messages queue naturally. Consumed rows are kept for the audit trail.
    """
    __tablename__ = "run_controls"

    id = Column(Integer, primary_key=True)
    run_id = Column(Text, nullable=False, index=True)
    action = Column(Text, nullable=False)  # 'pause' | 'resume' | 'stop' | 'message'
    payload = Column(Text, nullable=True)  # JSON — e.g. {"text": "..."}
    requested_by = Column(Text, nullable=True)  # operator id or 'api'
    requested_at = Column(DateTime(timezone=True), default=_utcnow, index=True)
    consumed_at = Column(DateTime(timezone=True), nullable=True, index=True)


class StationControl(Base):
    """Singleton table holding global intervention flags.

    Only the row with id=1 is used. The orchestrator + policy engine read
    ``global_pause`` to force every subsequent tool call (on any run) to the
    permission tray regardless of autonomy level.
    """
    __tablename__ = "station_control"

    id = Column(Integer, primary_key=True)  # always 1
    global_pause = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    updated_by = Column(Text, nullable=True)


class VisionChatSession(Base):
    """In-flight chat session for collaborative vision authoring.

    "One active per project" is enforced in the application layer (SQLite
    can't do partial unique indexes); historical 'approved' and 'cancelled'
    rows coexist freely. See spec 2026-05-07-project-vision-design.md.
    """
    __tablename__ = "vision_chat_sessions"

    id = Column(Text, primary_key=True)  # UUID
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    state = Column(Text, nullable=False, default="active")  # active|approved|cancelled
    phase = Column(Text, nullable=False, default="freeform")  # freeform|structured
    coverage = Column(Text, nullable=False, default="{}")  # JSON
    sdk_session_id = Column(Text, nullable=True, default=None)
    messages = Column(Text, nullable=False, default="[]")  # JSON list
    assembled = Column(Text, nullable=True, default=None)  # JSON
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
