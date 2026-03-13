"""Pydantic request/response schemas."""

from datetime import datetime
from typing import Optional, List, Any

from pydantic import BaseModel, ConfigDict, Field


# --- Projects ---

class ProjectCreate(BaseModel):
    repo: str
    priority: str = "medium"
    mode: str = "full"
    enabled: bool = True
    branch: str = "main"
    custom_instructions: Optional[str] = None
    setup_script: Optional[str] = None


class ProjectUpdate(BaseModel):
    priority: Optional[str] = None
    mode: Optional[str] = None
    enabled: Optional[bool] = None
    branch: Optional[str] = None
    custom_instructions: Optional[str] = None
    setup_script: Optional[str] = None


class ProjectOut(BaseModel):
    id: int
    repo: str
    priority: str
    mode: str
    enabled: bool
    branch: str
    custom_instructions: Optional[str] = None
    setup_script: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# --- Runs ---

class RunOut(BaseModel):
    id: int
    run_id: str
    project_id: Optional[int] = None
    mode: Optional[str] = None
    model: Optional[str] = None
    status: Optional[str] = None
    verdict: Optional[str] = None
    issue_number: Optional[int] = None
    branch: Optional[str] = None
    cost_usd: Optional[float] = None  # Deprecated: kept for historical data
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None
    tokens_total: Optional[int] = None
    turns: Optional[int] = None
    duration_ms: Optional[int] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    employee_report: Optional[str] = None
    verdict_detail: Optional[str] = None
    log_file: Optional[str] = None
    employee_index: Optional[int] = None
    concurrent_group_id: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class RunList(BaseModel):
    runs: List[RunOut]
    total: int


class ActiveEmployeeOut(BaseModel):
    """A currently-running agent/employee for the workspace visualization."""
    run_id: str
    project_id: int
    mode: str
    status: str
    issue_number: Optional[int] = None
    turns: Optional[int] = None
    employee_index: Optional[int] = None
    concurrent_group_id: Optional[str] = None
    model: Optional[str] = None
    branch: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# --- Config ---

class ConfigOut(BaseModel):
    key: str
    value: Optional[str] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ConfigUpdate(BaseModel):
    value: Any


# --- Notifications ---

class NotificationOut(BaseModel):
    id: int
    run_id: Optional[str] = None
    type: Optional[str] = None
    message: Optional[str] = None
    read: bool
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# --- Plans ---

class PlanCreate(BaseModel):
    project_id: int
    issue_number: Optional[int] = None
    issue_title: Optional[str] = None
    title: str
    description: Optional[str] = None
    steps: Optional[str] = None  # JSON array
    estimated_scope: Optional[str] = None
    files_affected: Optional[str] = None  # JSON array
    status: str = "draft"
    run_id: Optional[str] = None


class PlanUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    steps: Optional[str] = None
    estimated_scope: Optional[str] = None
    files_affected: Optional[str] = None
    status: Optional[str] = None
    implementation_run_id: Optional[str] = None


class PlanOut(BaseModel):
    id: int
    project_id: int
    issue_number: Optional[int] = None
    issue_title: Optional[str] = None
    title: str
    description: Optional[str] = None
    steps: Optional[str] = None
    estimated_scope: Optional[str] = None
    files_affected: Optional[str] = None
    status: str
    run_id: Optional[str] = None
    implementation_run_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PlanList(BaseModel):
    plans: List[PlanOut]
    total: int


# --- System ---

class HealthResponse(BaseModel):
    status: str = "ok"


class SystemStatus(BaseModel):
    service_active: bool
    timer_active: bool
    timer_next: Optional[str] = None
    memory_mb: Optional[float] = None
    load_avg: Optional[List[float]] = None
    disk_free_gb: Optional[float] = None
    uptime_seconds: Optional[float] = None


class AuthStatus(BaseModel):
    logged_in: bool
    expires_at: Optional[str] = None
    expired: bool = False


# --- Webhook ---

class WebhookRunEvent(BaseModel):
    run_id: str
    event: str  # started/finished/verdict + coordinator events
    project: Optional[str] = None
    mode: Optional[str] = None
    model: Optional[str] = None
    status: Optional[str] = None
    verdict: Optional[str] = None
    issue_number: Optional[int] = None
    branch: Optional[str] = None
    cost_usd: Optional[float] = None  # Deprecated: kept for backward compat
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None
    tokens_total: Optional[int] = None
    turns: Optional[int] = None
    duration_ms: Optional[int] = None
    reasoning: Optional[str] = None
    timestamp: Optional[str] = None
    employee_index: Optional[int] = None
    concurrent_group_id: Optional[str] = None
    # Coordinator task fields
    task_id: Optional[str] = None
    task_title: Optional[str] = None
    task_count: Optional[int] = None
    depends_on: Optional[str] = None  # JSON array
    dag_file: Optional[str] = None
    summary: Optional[dict] = None
    # Conflict detection fields
    file_path: Optional[str] = None
    employee_a: Optional[int] = None
    employee_b: Optional[int] = None
    # Guidance fields
    guidance_type: Optional[str] = None
    guidance_content: Optional[str] = None


# --- Coordinator ---

class CoordinatorTaskOut(BaseModel):
    id: str
    run_id: str
    project_repo: str
    issue_number: Optional[int] = None
    title: str
    description: Optional[str] = None
    status: str
    employee_index: Optional[int] = None
    depends_on: Optional[str] = None  # JSON array of task IDs
    workspace: Optional[str] = None
    expected_files: Optional[str] = None
    touched_files: Optional[str] = None
    exit_code: Optional[int] = None
    error_message: Optional[str] = None
    result_summary: Optional[str] = None
    log_path: Optional[str] = None
    branch: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class CoordinatorTaskDetailOut(CoordinatorTaskOut):
    """Extended task details with employee report and log excerpt."""
    employee_report: Optional[dict] = None
    log_excerpt: Optional[str] = None


class CoordinatorDAGOut(BaseModel):
    run_id: str
    project_repo: str
    tasks: List[CoordinatorTaskOut]
    summary: dict


class CoordinatorMessageOut(BaseModel):
    id: int
    run_id: str
    task_id: Optional[str] = None
    direction: str
    message_type: str
    content: str
    employee_index: Optional[int] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class GuidanceSend(BaseModel):
    run_id: str
    employee_index: int
    guidance_type: str = "info"  # warning/redirect/stop/info
    content: str
    workspace: Optional[str] = None


# --- Queue ---

class QueueItemCreate(BaseModel):
    project_repo: str
    issue_number: Optional[int] = None
    issue_title: Optional[str] = None
    state: str = "pending"
    priority: int = 0
    assigned_to: Optional[int] = None
    run_id: Optional[str] = None
    max_retries: int = 1
    context: Optional[str] = None  # JSON


class QueueItemUpdate(BaseModel):
    state: Optional[str] = None
    priority: Optional[int] = None
    assigned_to: Optional[int] = None
    run_id: Optional[str] = None
    employee_report: Optional[str] = None
    manager_feedback: Optional[str] = None
    retry_count: Optional[int] = None
    error_message: Optional[str] = None
    context: Optional[str] = None


class QueueItemOut(BaseModel):
    id: int
    project_repo: str
    issue_number: Optional[int] = None
    issue_title: Optional[str] = None
    state: str
    priority: int
    assigned_to: Optional[int] = None
    run_id: Optional[str] = None
    employee_report: Optional[str] = None
    manager_feedback: Optional[str] = None
    retry_count: int
    max_retries: int
    context: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    assigned_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class QueueItemList(BaseModel):
    items: List[QueueItemOut]
    total: int


class QueueStats(BaseModel):
    by_state: dict
    total: int
    avg_time_to_complete_ms: Optional[float] = None


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
    daily_token_usage: List[DailyTokenUsage] = []
    verdict_distribution: List[VerdictDistribution] = []
    project_token_usage: List[ProjectTokenUsage] = []
    daily_run_counts: List[DailyRunCount] = []


# --- Unified Run Context ---

class RunFullContext(BaseModel):
    """Unified run context: run + coordinator tasks + queue item + plan.

    Powers the unified Run Detail view (AC2) by returning all related
    data in a single response instead of requiring 4+ separate API calls.
    """
    run: RunOut
    coordinator_tasks: List[CoordinatorTaskOut] = []
    coordinator_messages: List[CoordinatorMessageOut] = []
    queue_item: Optional[QueueItemOut] = None
    plan: Optional[PlanOut] = None
    project_repo: Optional[str] = None
