"""Pydantic request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

# --- Projects ---

class ProjectCreate(BaseModel):
    repo: str
    priority: str = "medium"
    mode: str = "full"
    enabled: bool = True
    branch: str = "main"
    custom_instructions: str | None = None
    setup_script: str | None = None


class ProjectUpdate(BaseModel):
    priority: str | None = None
    mode: str | None = None
    enabled: bool | None = None
    branch: str | None = None
    custom_instructions: str | None = None
    setup_script: str | None = None


class ProjectOut(BaseModel):
    id: int
    repo: str
    priority: str
    mode: str
    enabled: bool
    branch: str
    custom_instructions: str | None = None
    setup_script: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# --- Runs ---

class RunOut(BaseModel):
    id: int
    run_id: str
    project_id: int | None = None
    mode: str | None = None
    model: str | None = None
    status: str | None = None
    verdict: str | None = None
    issue_number: int | None = None
    branch: str | None = None
    cost_usd: float | None = None  # Deprecated: kept for historical data
    tokens_input: int | None = None
    tokens_output: int | None = None
    tokens_total: int | None = None
    turns: int | None = None
    duration_ms: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    employee_report: str | None = None
    verdict_detail: str | None = None
    log_file: str | None = None
    employee_index: int | None = None
    trace_id: str | None = None
    concurrent_group_id: str | None = None

    model_config = ConfigDict(from_attributes=True)


class RunList(BaseModel):
    runs: list[RunOut]
    total: int


class ActiveEmployeeOut(BaseModel):
    """A currently-running agent/employee for the workspace visualization."""
    run_id: str
    project_id: int | None = None
    mode: str
    status: str
    issue_number: int | None = None
    turns: int | None = None
    employee_index: int | None = None
    concurrent_group_id: str | None = None
    model: str | None = None
    branch: str | None = None

    model_config = ConfigDict(from_attributes=True)


# --- Config ---

class ConfigOut(BaseModel):
    key: str
    value: str | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ConfigUpdate(BaseModel):
    value: Any


# --- Notifications ---

class NotificationOut(BaseModel):
    id: int
    run_id: str | None = None
    type: str | None = None
    message: str | None = None
    read: bool
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# --- Plans ---

class PlanCreate(BaseModel):
    project_id: int
    issue_number: int | None = None
    issue_title: str | None = None
    title: str
    description: str | None = None
    steps: str | None = None  # JSON array
    estimated_scope: str | None = None
    files_affected: str | None = None  # JSON array
    status: str = "draft"
    run_id: str | None = None


class PlanUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    steps: str | None = None
    estimated_scope: str | None = None
    files_affected: str | None = None
    status: str | None = None
    implementation_run_id: str | None = None


class PlanOut(BaseModel):
    id: int
    project_id: int
    issue_number: int | None = None
    issue_title: str | None = None
    title: str
    description: str | None = None
    steps: str | None = None
    estimated_scope: str | None = None
    files_affected: str | None = None
    status: str
    run_id: str | None = None
    implementation_run_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class PlanList(BaseModel):
    plans: list[PlanOut]
    total: int


# --- System ---

class HealthResponse(BaseModel):
    status: str = "ok"


class SystemStatus(BaseModel):
    service_active: bool
    timer_active: bool
    timer_next: str | None = None
    memory_mb: float | None = None
    load_avg: list[float] | None = None
    disk_free_gb: float | None = None
    uptime_seconds: float | None = None


class AuthStatus(BaseModel):
    logged_in: bool
    expires_at: str | None = None
    expired: bool = False


# --- Webhook ---

class WebhookRunEvent(BaseModel):
    run_id: str
    event: str  # started/finished/verdict + coordinator events
    # Trace and idempotency fields
    event_id: str | None = None  # Unique ID per event (for idempotency)
    trace_id: str | None = None  # Correlates all events in a pipeline run
    parent_event_id: str | None = None  # Links to parent event (e.g., task to run)
    sequence: int | None = None  # Ordering within a trace
    project: str | None = None
    mode: str | None = None
    model: str | None = None
    status: str | None = None
    verdict: str | None = None
    issue_number: int | None = None
    branch: str | None = None
    cost_usd: float | None = None  # Deprecated: kept for backward compat
    tokens_input: int | None = None
    tokens_output: int | None = None
    tokens_total: int | None = None
    turns: int | None = None
    duration_ms: int | None = None
    reasoning: str | None = None
    timestamp: str | None = None
    employee_index: int | None = None
    concurrent_group_id: str | None = None
    # Coordinator task fields
    task_id: str | None = None
    task_title: str | None = None
    task_count: int | None = None
    depends_on: str | None = None  # JSON array
    dag_file: str | None = None
    summary: dict | None = None
    # Conflict detection fields
    file_path: str | None = None
    employee_a: int | None = None
    employee_b: int | None = None
    # Guidance fields
    guidance_type: str | None = None
    guidance_content: str | None = None


# --- Coordinator ---

class CoordinatorTaskOut(BaseModel):
    id: str
    run_id: str
    project_repo: str
    issue_number: int | None = None
    title: str
    description: str | None = None
    status: str
    employee_index: int | None = None
    depends_on: str | None = None  # JSON array of task IDs
    workspace: str | None = None
    expected_files: str | None = None
    touched_files: str | None = None
    exit_code: int | None = None
    error_message: str | None = None
    result_summary: str | None = None
    log_path: str | None = None
    branch: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class CoordinatorTaskDetailOut(CoordinatorTaskOut):
    """Extended task details with employee report and log excerpt."""
    employee_report: dict | None = None
    log_excerpt: str | None = None


class CoordinatorDAGOut(BaseModel):
    run_id: str
    project_repo: str
    tasks: list[CoordinatorTaskOut]
    summary: dict


class CoordinatorMessageOut(BaseModel):
    id: int
    run_id: str
    task_id: str | None = None
    direction: str
    message_type: str
    content: str
    employee_index: int | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class GuidanceSend(BaseModel):
    run_id: str
    employee_index: int
    guidance_type: str = "info"  # warning/redirect/stop/info
    content: str
    workspace: str | None = None


# --- Queue ---

class QueueItemCreate(BaseModel):
    project_repo: str
    issue_number: int | None = None
    issue_title: str | None = None
    state: str = "pending"
    priority: int = 0
    assigned_to: int | None = None
    run_id: str | None = None
    max_retries: int = 1
    context: str | None = None  # JSON
    mode: str | None = None
    complexity_score: int | None = None
    escalation_rung: int = 0
    escalated_from: int | None = None
    parent_task_id: str | None = None
    handoff_context: str | None = None  # JSON


class QueueItemUpdate(BaseModel):
    state: str | None = None
    priority: int | None = None
    assigned_to: int | None = None
    run_id: str | None = None
    employee_report: str | None = None
    manager_feedback: str | None = None
    retry_count: int | None = None
    error_message: str | None = None
    context: str | None = None
    mode: str | None = None
    complexity_score: int | None = None
    escalation_rung: int | None = None
    confidence: float | None = None
    handoff_context: str | None = None


class QueueItemOut(BaseModel):
    id: int
    project_repo: str
    issue_number: int | None = None
    issue_title: str | None = None
    state: str
    priority: int
    assigned_to: int | None = None
    run_id: str | None = None
    employee_report: str | None = None
    manager_feedback: str | None = None
    retry_count: int
    max_retries: int
    context: str | None = None
    error_message: str | None = None
    mode: str | None = None
    complexity_score: int | None = None
    escalation_rung: int = 0
    escalated_from: int | None = None
    parent_task_id: str | None = None
    confidence: float | None = None
    handoff_context: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    assigned_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class QueueItemList(BaseModel):
    items: list[QueueItemOut]
    total: int


class QueueStats(BaseModel):
    by_state: dict
    total: int
    avg_time_to_complete_ms: float | None = None


# --- Analytics ---

class DailyTokenUsage(BaseModel):
    """Token usage aggregated by day."""
    date: str
    tokens_total: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    run_count: int = 0


class VerdictDistribution(BaseModel):
    """Count of runs per verdict type."""
    verdict: str
    count: int


class ProjectTokenUsage(BaseModel):
    """Token usage aggregated by project."""
    project_id: int
    project_repo: str
    tokens_total: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    run_count: int = 0


class DailyRunCount(BaseModel):
    """Run frequency aggregated by day."""
    date: str
    total: int = 0
    success: int = 0
    failed: int = 0


class AnalyticsResponse(BaseModel):
    """Aggregated analytics data for charts."""
    days: int
    total_tokens: int = 0
    total_tokens_input: int = 0
    total_tokens_output: int = 0
    total_runs: int = 0
    failed_runs: int = 0
    daily_token_usage: list[DailyTokenUsage] = []
    verdict_distribution: list[VerdictDistribution] = []
    project_token_usage: list[ProjectTokenUsage] = []
    daily_run_counts: list[DailyRunCount] = []


# --- Unified Run Context ---

class RunFullContext(BaseModel):
    """Unified run context: run + coordinator tasks + queue item + plan + intelligence.

    Powers the unified Run Detail view (AC2) by returning all related
    data in a single response instead of requiring 4+ separate API calls.
    """
    run: RunOut
    coordinator_tasks: list[CoordinatorTaskOut] = []
    coordinator_messages: list[CoordinatorMessageOut] = []
    queue_item: QueueItemOut | None = None
    plan: PlanOut | None = None
    project_repo: str | None = None
    intelligence_decisions: list[AgentEventOut] = []


# --- Agent Events ---

class AgentEventCreate(BaseModel):
    workflow_id: str
    run_id: str | None = None
    agent_id: str
    event_type: str
    event_data: str  # JSON
    parent_event_id: int | None = None


class AgentEventOut(BaseModel):
    event_id: int
    workflow_id: str
    run_id: str | None = None
    agent_id: str
    event_type: str
    event_data: str
    parent_event_id: int | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# --- Task Outcomes ---

class TaskOutcomeCreate(BaseModel):
    queue_item_id: int | None = None
    project_repo: str
    issue_number: int | None = None
    issue_type: str | None = None
    complexity_score: int | None = None
    mode_used: str
    model_used: str
    escalation_rung: int = 0
    prompt_version: int = 1
    confidence_reported: float | None = None
    success: bool
    tests_passed: bool | None = None
    verdict: str | None = None
    failure_category: str | None = None
    tokens_consumed: int | None = None
    duration_seconds: int | None = None


class TaskOutcomeOut(BaseModel):
    id: int
    queue_item_id: int | None = None
    project_repo: str
    issue_number: int | None = None
    issue_type: str | None = None
    complexity_score: int | None = None
    mode_used: str
    model_used: str
    escalation_rung: int
    prompt_version: int
    confidence_reported: float | None = None
    success: bool
    tests_passed: bool | None = None
    verdict: str | None = None
    failure_category: str | None = None
    tokens_consumed: int | None = None
    duration_seconds: int | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# --- Prompt Versions ---

class PromptVersionOut(BaseModel):
    id: int
    prompt_name: str
    version: int
    content_hash: str
    change_description: str | None = None
    active: bool
    success_rate: float | None = None
    sample_count: int
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# --- Backpressure ---

class BackpressureStatus(BaseModel):
    level: str  # GREEN, YELLOW, RED, BLACK
    usage_percent: float
    max_concurrent: int
    effective_concurrent: int
    model_restriction: str | None = None
    turn_cap: int | None = None


# --- Adaptive Scheduling ---

class EffortPrediction(BaseModel):
    mode: str
    model: str
    predicted_tokens: float | None = None
    confidence: float | None = None
    sample_count: int = 0
