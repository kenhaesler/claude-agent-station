"""Pydantic request/response schemas."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

# --- Projects ---

class ProjectCreate(BaseModel):
    repo: str
    priority: str = "medium"
    mode: str = "full"
    enabled: bool = True
    branch: str = "main"
    custom_instructions: str | None = None
    setup_script: str | None = None
    security_review_enabled: bool = False
    # ADR-0001
    autonomy_level: str = "assisted"
    max_budget_usd: float | None = None


class ProjectUpdate(BaseModel):
    priority: str | None = None
    mode: str | None = None
    enabled: bool | None = None
    branch: str | None = None
    custom_instructions: str | None = None
    setup_script: str | None = None
    security_review_enabled: bool | None = None
    # ADR-0001
    autonomy_level: str | None = None
    max_budget_usd: float | None = None


class ProjectOut(BaseModel):
    id: int
    repo: str
    priority: str
    mode: str
    enabled: bool
    branch: str
    custom_instructions: str | None = None
    setup_script: str | None = None
    security_review_enabled: bool = False
    # ADR-0001
    autonomy_level: str = "assisted"
    max_budget_usd: float | None = None
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
    # Agent Teams fields
    team_name: str | None = None
    team_members: str | None = None  # JSON
    # ADR-0001
    autonomy_level: str | None = None
    max_budget_usd: float | None = None
    # Vision-bootstrap fields — spec 2026-05-08-vision-issue-bootstrap-design.md
    vision_bootstrap_count: int | None = None
    vision_bootstrap_proposals: list[dict] | None = None
    skip_reason: str | None = None

    @field_validator("vision_bootstrap_proposals", mode="before")
    @classmethod
    def _deserialize_proposals(cls, v: object) -> object:
        """Accept raw JSON string from the DB column or a parsed list."""
        if isinstance(v, str):
            return json.loads(v)
        return v

    model_config = ConfigDict(from_attributes=True)


class RunList(BaseModel):
    runs: list[RunOut]
    total: int


class ActiveEmployeeOut(BaseModel):
    """A currently-running agent/employee for the workspace visualization.
    Kept for backward compatibility — use ActiveTeammateOut for new code.
    """
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
    tokens_total: int | None = None
    started_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# Alias for Agent Teams terminology
ActiveTeammateOut = ActiveEmployeeOut


class TeammateStatus(BaseModel):
    """Status of a single teammate in an Agent Teams run."""
    agent_id: str
    name: str
    task_id: str | None = None
    issue_number: int | None = None
    status: str = "spawned"  # spawned/planning/implementing/completed/stuck
    turns_used: int = 0
    tokens_used: int = 0
    files_touched: list[str] = []


class TeamSummary(BaseModel):
    """Summary of an Agent Teams run."""
    team_name: str
    lead_agent_id: str | None = None
    teammates: list[TeammateStatus] = []
    tasks_total: int = 0
    tasks_completed: int = 0
    tasks_in_progress: int = 0
    conflicts: list[str] = []


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


# --- Provider API keys (OpenAI / Gemini / ...) ---


class ProviderKeyStatus(BaseModel):
    """Public status of one provider's stored API key.

    Note ``masked_key`` carries only the safe-to-display redacted form
    (e.g. ``sk-pro…aBc1``) — the raw key is never serialised back.
    """

    configured: bool
    masked_key: str | None = None
    last_updated: datetime | None = None


class ProviderKeysOut(BaseModel):
    """Snapshot of every supported provider's status."""

    openai: ProviderKeyStatus
    gemini: ProviderKeyStatus


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

    @field_validator("event")
    @classmethod
    def _validate_event_name(cls, v: str) -> str:
        """Reject event names that could break the SSE protocol (issue #187).

        The event name is interpolated into the SSE ``event: <name>\\n`` frame
        line, so any control character (CR/LF, null, etc.) would let an
        attacker inject extra protocol lines. We require a non-empty
        single-line identifier under 100 chars; legitimate event names from
        ``run-manager.sh`` are short snake_case strings well within this.
        """
        if not isinstance(v, str) or not v:
            raise ValueError("event must be a non-empty string")
        if len(v) > 100:
            raise ValueError("event exceeds 100 characters")
        # Reject any C0 control char (0x00-0x1F) and DEL (0x7F).
        # Tab (0x09) is also rejected — event names should be plain identifiers.
        for ch in v:
            if ord(ch) < 0x20 or ord(ch) == 0x7F:
                raise ValueError(
                    "event must not contain control characters"
                )
        return v

    @field_validator("issue_number", mode="before")
    @classmethod
    def coerce_issue_number(cls, v: object) -> int | None:
        if v is None or v == "None" or v == "null" or v == "":
            return None
        return int(v)
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
    log_file: str | None = None
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
    # Agent Teams fields
    team_name: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    member_count: int | None = None
    # Vision misalignment fields
    violated_section: str | None = None
    quote: str | None = None
    # Hook-callback failure surface — count of pre/post hook callback failures
    # observed during one project's session (see agent/audit_hook.py). Posted
    # by the orchestrator when nonzero so operators can spot SDK stream-close
    # incidents from Mission Control instead of grepping launcher.out.
    count: int | None = None
    plan_excerpt: str | None = None
    # Narration ("The Bridge" Phase 1): one-sentence present-tense intent
    # statements the agent emits before tool calls so the operator never
    # has to guess what's happening.
    narration: str | None = None
    narration_kind: str | None = None  # "directive" | "step" | "system"
    # Vision-bootstrap fields — spec 2026-05-08-vision-issue-bootstrap-design.md
    vision_bootstrap_count: int | None = None
    vision_bootstrap_proposals: list[dict] | None = None
    skip_reason: str | None = None


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
    # Agent Teams fields
    teammate_agent_id: str | None = None
    claimed_by: str | None = None
    claimed_at: datetime | None = None
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


# --- Dispatch telemetry ---

class TelemetryActive(BaseModel):
    count: int
    teammates: int
    roles: list[str] = []


class TelemetryQueue(BaseModel):
    total: int
    claimed: int
    done: int
    pending: int
    # Catch-all for queue items not bucketed into the three above (e.g.
    # ``failed``, ``paused``, ``cancelled``). Keeps ``claimed + done +
    # pending + other == total`` so the UI can reconcile the cells with
    # the headline count without silently dropping rows.
    other: int = 0


class TelemetryTokens7d(BaseModel):
    total: int
    runs: int
    input: int
    output: int
    spark: list[int] = []


class TelemetrySystem(BaseModel):
    status: str  # NOMINAL | DEGR | CRIT
    disk_free_gb: float | None = None
    memory_used_pct: int | None = None
    uptime_secs: float | None = None


class TelemetryVerdicts7d(BaseModel):
    """Verdict counts over the same 7-day window as ``tokens_7d``.

    ``ok`` covers APPROVE verdicts, ``pr`` covers PR verdicts, ``x`` covers
    REJECT plus any other non-null terminal verdict (so the three buckets
    exhaust the verdict-bearing run set without dropping rows).
    """
    ok: int = 0
    pr: int = 0
    x: int = 0


class TelemetrySummaryOut(BaseModel):
    active: TelemetryActive
    queue: TelemetryQueue
    tokens_7d: TelemetryTokens7d
    system: TelemetrySystem
    verdicts_7d: TelemetryVerdicts7d = TelemetryVerdicts7d()


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
    # ``queue_item`` is the first matching row for backwards-compat with
    # callers that expected exactly-one. ``queue_items`` is the full
    # list — required since #290 wired the orchestrator to drain
    # multiple QueueItems per run from the plan-review gate.
    queue_item: QueueItemOut | None = None
    queue_items: list[QueueItemOut] = []
    plan: PlanOut | None = None
    project_repo: str | None = None
    intelligence_decisions: list[AgentEventOut] = []
    team_summary: TeamSummary | None = None


# --- Agent Events ---

class AgentEventCreate(BaseModel):
    workflow_id: str
    run_id: str | None = None
    agent_id: str
    event_type: str
    event_data: str  # JSON
    parent_event_id: int | None = None
    team_name: str | None = None


class AgentEventOut(BaseModel):
    event_id: int
    workflow_id: str
    run_id: str | None = None
    agent_id: str
    event_type: str
    event_data: str
    parent_event_id: int | None = None
    team_name: str | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# --- Audit Log (issue #73) ---

class AuditEntryOut(BaseModel):
    id: int
    trace_id: str | None = None
    idempotency_key: str
    run_id: str
    actor: str
    action_kind: str
    action_detail: str | None = None
    status: str
    exit_code: int | None = None
    stdout_tail: str | None = None
    stderr_tail: str | None = None
    started_at: datetime
    finished_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class AuditStats(BaseModel):
    days: int
    total: int
    by_kind: dict[str, int]
    error_rate: float  # 0.0–1.0
    avg_duration_ms: float | None = None


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
    subsystem: str | None = None
    employee_index: int | None = None
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
    subsystem: str | None = None
    employee_index: int | None = None
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


# --- Permission Tray (ADR-0001, P2.T10) ---

class PermissionRequestOut(BaseModel):
    """Payload for a pending operator permission prompt."""
    id: int
    request_id: str
    run_id: str
    agent_id: str
    tool_name: str
    tool_input: dict[str, Any]
    autonomy_level: str
    reason: str | None = None
    status: str
    resolution_note: str | None = None
    created_at: datetime | None = None
    resolved_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("tool_input", mode="before")
    @classmethod
    def _parse_json_input(cls, v: Any) -> Any:
        """tool_input is stored as a JSON string; unwrap for the API."""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return {"raw": v}
        return v or {}


class PermissionDecisionIn(BaseModel):
    """Operator response to a pending permission request."""
    decision: str  # 'approve' or 'deny'
    note: str | None = None

    @field_validator("decision")
    @classmethod
    def _validate_decision(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ("approve", "deny"):
            raise ValueError("decision must be 'approve' or 'deny'")
        return v


class PermissionCreateIn(BaseModel):
    """Agent-side payload to raise a new permission request.

    Used by the policy engine when it would otherwise deny-return at
    manual/assisted — writes a row + emits the SSE event so the tray
    can pop in real time.
    """
    request_id: str
    run_id: str
    agent_id: str
    tool_name: str
    tool_input: dict[str, Any]
    autonomy_level: str
    reason: str | None = None

    @field_validator("autonomy_level")
    @classmethod
    def _validate_level(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ("manual", "assisted", "auto"):
            raise ValueError("autonomy_level must be manual/assisted/auto")
        return v


# --- Mission Control: run intervention (Phase A) ---

class RunMessage(BaseModel):
    """Message the operator wants injected into the running agent's next turn."""
    text: str

    @field_validator("text")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("text must not be empty")
        if len(v) > 4000:
            raise ValueError("text exceeds 4000 characters")
        return v


class RunControlAck(BaseModel):
    run_id: str
    action: str
    control_id: int
    queued_at: datetime


class GlobalPauseState(BaseModel):
    global_pause: bool
    updated_at: datetime | None = None
    updated_by: str | None = None


# ── Vision (Phase 1) ───────────────────────────────────────────

class VisionDoc(BaseModel):
    """Structured vision payload — one field per section."""
    problem: str
    users: str
    end_state: str
    non_goals: str
    principles: str
    horizons: str
    anti_patterns: str


class VisionRead(BaseModel):
    """Response for GET /api/projects/{id}/vision."""
    sha: str
    body: str
    last_refined_at: str | None = None  # ISO timestamp from latest commit
    last_refined_by: str | None = None  # GitHub login from latest commit
    cache_age_seconds: int


class VisionCommitIn(BaseModel):
    """Body for POST /api/projects/{id}/vision."""
    vision_doc: VisionDoc


class VisionCommitOut(BaseModel):
    """Response for POST /api/projects/{id}/vision."""
    sha: str
    html_url: str
    analyst_dispatched: bool = False  # True when the SHA-gated dispatch fired (or 409'd)


class VisionStaleSha(BaseModel):
    """409 envelope for POST /api/projects/{id}/vision."""
    code: str = "stale_sha"
    current_sha: str
    current_body: str


class VisionChatSessionOut(BaseModel):
    """Response for GET /api/projects/{id}/vision/chat."""
    id: str
    project_id: int
    state: str
    phase: str
    coverage: dict
    messages: list[dict]
    assembled: dict | None
    created_at: str
    updated_at: str


class VisionChatTurnIn(BaseModel):
    """Body for POST /api/projects/{id}/vision/chat (turn)."""
    session_id: str | None = None  # None on first turn
    message: str


class VisionProposalsRead(BaseModel):
    """Response for GET /api/projects/{id}/vision/proposals."""
    open: int
    accepted_recent: int
