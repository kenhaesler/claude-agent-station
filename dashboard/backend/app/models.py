"""SQLAlchemy ORM models."""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column, Integer, Text, Boolean, Float, DateTime, ForeignKey
)

from app.database import Base


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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
    cost_usd = Column(Float, nullable=True)
    turns = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    employee_report = Column(Text, nullable=True)  # JSON as text
    verdict_detail = Column(Text, nullable=True)  # JSON as text
    log_file = Column(Text, nullable=True)


class ConfigEntry(Base):
    __tablename__ = "config"

    key = Column(Text, primary_key=True)
    value = Column(Text, nullable=True)  # JSON-encoded
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    run_id = Column(Text, nullable=True)
    type = Column(Text, nullable=True)  # approve/reject/pr/error/info
    message = Column(Text, nullable=True)
    read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
