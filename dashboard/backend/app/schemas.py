"""Pydantic request/response schemas."""

from datetime import datetime
from typing import Optional, List, Any

from pydantic import BaseModel, Field


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

    class Config:
        from_attributes = True


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
    cost_usd: Optional[float] = None
    turns: Optional[int] = None
    duration_ms: Optional[int] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    employee_report: Optional[str] = None
    verdict_detail: Optional[str] = None
    log_file: Optional[str] = None

    class Config:
        from_attributes = True


class RunList(BaseModel):
    runs: List[RunOut]
    total: int


# --- Config ---

class ConfigOut(BaseModel):
    key: str
    value: Optional[str] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


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

    class Config:
        from_attributes = True


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
    event: str  # started/finished/verdict
    project: Optional[str] = None
    mode: Optional[str] = None
    model: Optional[str] = None
    status: Optional[str] = None
    verdict: Optional[str] = None
    issue_number: Optional[int] = None
    branch: Optional[str] = None
    cost_usd: Optional[float] = None
    turns: Optional[int] = None
    duration_ms: Optional[int] = None
    reasoning: Optional[str] = None
    timestamp: Optional[str] = None
