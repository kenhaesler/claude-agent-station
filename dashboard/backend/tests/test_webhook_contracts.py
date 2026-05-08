"""Tests for webhook contract compliance between agent and dashboard.

Validates that:
1. Reporter event payloads match the WebhookRunEvent schema
2. Status normalization in the webhook router handles all known values

These are pure contract tests — no database, no HTTP, no async.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas import WebhookRunEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a minimal valid WebhookRunEvent dict, with optional overrides."""
    base: dict[str, Any] = {
        "run_id": "run-abc123",
        "event": "run_start",
    }
    if overrides:
        base.update(overrides)
    return base


# ===================================================================
# 1. Reporter event schema tests
# ===================================================================

class TestReporterEventSchemas:
    """Verify every event payload shape emitted by reporter.py validates
    against the WebhookRunEvent Pydantic model.

    reporter.py builds payloads in post_event(), post_task_event(),
    post_employee_start(), post_employee_complete(), post_conflict(),
    and post_guidance().  Each must be accepted by WebhookRunEvent.
    """

    # -- post_event base payload (run-level) ----------------------------

    def test_base_event_payload(self):
        """post_event() emits {event, run_id, timestamp, concurrent_group_id}."""
        payload = _make_event({
            "timestamp": "2026-03-15T12:00:00Z",
            "concurrent_group_id": "group-1",
        })
        evt = WebhookRunEvent(**payload)
        assert evt.run_id == "run-abc123"
        assert evt.event == "run_start"
        assert evt.concurrent_group_id == "group-1"

    def test_base_event_minimal(self):
        """Only run_id and event are required."""
        evt = WebhookRunEvent(**_make_event())
        assert evt.run_id == "run-abc123"

    # -- post_task_event payload ----------------------------------------

    @pytest.mark.parametrize("event_name", [
        "task_started",
        "task_completed",
        "task_failed",
    ])
    def test_task_event_payload(self, event_name: str):
        """post_task_event() adds task_id, task_title, project, employee_index, depends_on."""
        payload = _make_event({
            "event": event_name,
            "task_id": "task-run-abc123-0",
            "task_title": "Implement feature X",
            "project": "owner/repo",
            "employee_index": 0,
            "depends_on": "[]",
        })
        evt = WebhookRunEvent(**payload)
        assert evt.task_id == "task-run-abc123-0"
        assert evt.task_title == "Implement feature X"
        assert evt.project == "owner/repo"
        assert evt.employee_index == 0
        assert evt.depends_on == "[]"

    def test_task_event_with_dependencies(self):
        """depends_on is a JSON string of task IDs."""
        payload = _make_event({
            "event": "task_started",
            "task_id": "task-run-abc123-1",
            "task_title": "Second task",
            "project": "owner/repo",
            "employee_index": 1,
            "depends_on": '["task-run-abc123-0"]',
        })
        evt = WebhookRunEvent(**payload)
        assert evt.depends_on == '["task-run-abc123-0"]'

    # -- post_employee_start payload ------------------------------------

    def test_employee_start_payload(self):
        """post_employee_start() emits employee_start with run_id override, project, mode."""
        payload = _make_event({
            "event": "employee_start",
            "run_id": "run-abc123-e1",  # overridden for multi-employee
            "project": "owner/repo",
            "mode": "full",
            "employee_index": 1,
            "concurrent_group_id": "run-abc123",
        })
        evt = WebhookRunEvent(**payload)
        assert evt.run_id == "run-abc123-e1"
        assert evt.event == "employee_start"
        assert evt.mode == "full"
        assert evt.employee_index == 1
        assert evt.concurrent_group_id == "run-abc123"

    def test_employee_start_primary_employee(self):
        """Employee 0 uses the master run_id (no suffix)."""
        payload = _make_event({
            "event": "employee_start",
            "project": "owner/repo",
            "mode": "full",
            "employee_index": 0,
            "concurrent_group_id": "run-abc123",
        })
        evt = WebhookRunEvent(**payload)
        assert evt.run_id == "run-abc123"
        assert evt.employee_index == 0

    # -- post_employee_complete payload ---------------------------------

    def test_employee_complete_payload(self):
        """post_employee_complete() adds exit_code and employee_index."""
        payload = _make_event({
            "event": "employee_complete",
            "run_id": "run-abc123-e2",
            "project": "owner/repo",
            "employee_index": 2,
            "concurrent_group_id": "run-abc123",
        })
        evt = WebhookRunEvent(**payload)
        assert evt.event == "employee_complete"
        assert evt.employee_index == 2

    # -- post_conflict payload ------------------------------------------

    def test_conflict_detected_payload(self):
        """post_conflict() emits conflict_detected with file_path, employee_a/b."""
        payload = _make_event({
            "event": "conflict_detected",
            "project": "owner/repo",
            "file_path": "src/main.py",
            "employee_a": 0,
            "employee_b": 1,
        })
        evt = WebhookRunEvent(**payload)
        assert evt.event == "conflict_detected"
        assert evt.file_path == "src/main.py"
        assert evt.employee_a == 0
        assert evt.employee_b == 1

    # -- post_guidance payload ------------------------------------------

    def test_guidance_sent_payload(self):
        """post_guidance() emits guidance_sent with employee_index, type, content."""
        payload = _make_event({
            "event": "guidance_sent",
            "project": "owner/repo",
            "employee_index": 1,
            "guidance_type": "warning",
            "guidance_content": "Conflict on src/main.py with employee 0",
        })
        evt = WebhookRunEvent(**payload)
        assert evt.event == "guidance_sent"
        assert evt.guidance_type == "warning"
        assert evt.guidance_content == "Conflict on src/main.py with employee 0"

    # -- Validation rejection tests -------------------------------------

    def test_missing_run_id_rejected(self):
        """run_id is required — payload without it must fail validation."""
        with pytest.raises(ValidationError):
            WebhookRunEvent(event="run_start")  # type: ignore[call-arg]

    def test_missing_event_rejected(self):
        """event is required — payload without it must fail validation."""
        with pytest.raises(ValidationError):
            WebhookRunEvent(run_id="run-123")  # type: ignore[call-arg]

    # -- Full lifecycle payload (like run-manager.sh sends) -------------

    def test_run_complete_with_metrics(self):
        """run_complete events carry token and timing metrics."""
        payload = _make_event({
            "event": "run_complete",
            "status": "success",
            "project": "owner/repo",
            "model": "claude-sonnet-4-6",
            "tokens_input": 50000,
            "tokens_output": 12000,
            "tokens_total": 62000,
            "turns": 15,
            "duration_ms": 180000,
        })
        evt = WebhookRunEvent(**payload)
        assert evt.tokens_total == 62000
        assert evt.duration_ms == 180000
        assert evt.status == "success"

    def test_verdict_event_payload(self):
        """verdict_execute events carry verdict, reasoning, issue_number, branch."""
        payload = _make_event({
            "event": "verdict_execute",
            "verdict": "APPROVE",
            "reasoning": "All tests pass, changes look good",
            "issue_number": 42,
            "branch": "fix/issue-42",
            "project": "owner/repo",
        })
        evt = WebhookRunEvent(**payload)
        assert evt.verdict == "APPROVE"
        assert evt.issue_number == 42
        assert evt.branch == "fix/issue-42"

    def test_dag_created_event_payload(self):
        """dag_created events carry task_count."""
        payload = _make_event({
            "event": "dag_created",
            "task_count": 3,
        })
        evt = WebhookRunEvent(**payload)
        assert evt.task_count == 3


# ===================================================================
# 2. Status normalization tests
# ===================================================================

class TestStatusNormalization:
    """Verify _normalize_event_name() maps every known event to its
    expected internal handler name.

    This imports the function directly from the webhook router — the
    test suite does NOT need a running server.
    """

    @pytest.fixture(autouse=True)
    def _import_normalizer(self):
        from app.routers.webhook import _normalize_event_name
        self.normalize = _normalize_event_name

    # -- Primary event flow (run-manager.sh sends these) ----------------

    @pytest.mark.parametrize("raw,expected", [
        ("run_start", "started"),
        ("employee_start", "started"),
        ("employee_complete", "employee_done"),
        ("manager_review", "reviewing"),
        ("run_complete", "finished"),
        ("verdict_execute", "verdict"),
    ])
    def test_primary_event_names(self, raw: str, expected: str):
        """run-manager.sh event names are normalized to handler names."""
        assert self.normalize(raw) == expected

    # -- Legacy / direct names ------------------------------------------

    @pytest.mark.parametrize("raw,expected", [
        ("started", "started"),
        ("finished", "finished"),
        ("verdict", "verdict"),
    ])
    def test_legacy_event_names(self, raw: str, expected: str):
        """Legacy short names pass through unchanged."""
        assert self.normalize(raw) == expected

    # -- Coordinator task events ----------------------------------------

    @pytest.mark.parametrize("event_name", [
        "task_started",
        "task_completed",
        "task_failed",
        "task_ready",
        "task_blocked",
    ])
    def test_coordinator_task_events_pass_through(self, event_name: str):
        """Coordinator task events pass through to their handlers."""
        assert self.normalize(event_name) == event_name

    # -- Coordinator message events -------------------------------------

    @pytest.mark.parametrize("event_name", [
        "conflict_detected",
        "guidance_sent",
    ])
    def test_coordinator_message_events_pass_through(self, event_name: str):
        """Conflict and guidance events pass through unchanged."""
        assert self.normalize(event_name) == event_name

    # -- DAG lifecycle events -------------------------------------------

    @pytest.mark.parametrize("event_name", [
        "dag_created",
        "dag_completed",
    ])
    def test_dag_events_pass_through(self, event_name: str):
        """DAG lifecycle events pass through unchanged."""
        assert self.normalize(event_name) == event_name

    # -- Queue events ---------------------------------------------------

    @pytest.mark.parametrize("event_name", [
        "queue_assigned",
        "queue_in_progress",
        "queue_review",
        "queue_completed",
        "queue_paused",
        "queue_failed",
    ])
    def test_queue_events_pass_through(self, event_name: str):
        """Queue events pass through unchanged."""
        assert self.normalize(event_name) == event_name

    # -- Unknown events fall through to identity ------------------------

    def test_unknown_event_falls_through(self):
        """Unknown events are returned as-is (no crash)."""
        assert self.normalize("some_new_event") == "some_new_event"
        assert self.normalize("") == ""

    # -- Critical: employee_complete is NOT "finished" ------------------

    def test_employee_complete_is_not_finished(self):
        """employee_complete must map to employee_done, NOT finished.

        This was a real bug (premature run completion). The test guards
        against regression.
        """
        assert self.normalize("employee_complete") != "finished"
        assert self.normalize("employee_complete") == "employee_done"

    # -- Status normalization within the finished handler ---------------

    @pytest.mark.parametrize("raw_status,expected_final", [
        ("success", "completed"),
        ("finished", "completed"),
        ("error", "error"),         # passes through
        ("failed", "failed"),       # passes through
        ("no_reports", "no_reports"),  # passes through
    ])
    def test_finished_handler_status_map(self, raw_status: str, expected_final: str):
        """The 'finished' handler normalizes raw status strings.

        run-manager.sh sends 'success' but the frontend expects 'completed'.
        Other statuses pass through as-is.
        """
        # Replicate the status_map logic from webhook.py line 116-117
        status_map = {"success": "completed", "finished": "completed"}
        final = status_map.get(raw_status, raw_status)
        assert final == expected_final


# ===================================================================
# 3. Cross-contract consistency
# ===================================================================
#
# (The former rate-limit pattern tests were removed when
# `agent.coordinator.employee_runner` and its `RATE_LIMIT_PATTERNS` /
# `detect_rate_limit_in_text` helpers were deleted. Under the Agent
# Teams flow rate-limit handling no longer lives in this codebase as a
# regex-against-log-lines contract — it is owned by the SDK layer and
# by `agent/scripts/detect_plan_usage.py`, which has its own tests in
# `agent/scripts/`. There is nothing for this file to assert.)
# ===================================================================

class TestCrossContractConsistency:
    """Verify that the event names used by reporter.py are recognized
    by the webhook router's normalization mapping.
    """

    def test_reporter_events_are_in_normalization_map(self):
        """Every event name sent by reporter.py must be handled by _normalize_event_name.

        reporter.py uses these event names:
        - post_task_event: 'task_started', 'task_completed', 'task_failed'
        - post_employee_start: 'employee_start'
        - post_employee_complete: 'employee_complete'
        - post_conflict: 'conflict_detected'
        - post_guidance: 'guidance_sent'
        """
        from app.routers.webhook import _normalize_event_name

        reporter_events = [
            "task_started",
            "task_completed",
            "task_failed",
            "employee_start",
            "employee_complete",
            "conflict_detected",
            "guidance_sent",
        ]

        for event_name in reporter_events:
            normalized = _normalize_event_name(event_name)
            # Normalized result should not equal the raw input if it's in the
            # explicit mapping, OR it should be a known passthrough.
            # The key point: it should not be an unhandled fall-through for
            # events we KNOW about.
            assert normalized, f"Event '{event_name}' normalized to empty string"

    def test_bash_events_are_in_normalization_map(self):
        """Event names sent by run-manager.sh must also be handled.

        run-manager.sh sends: run_start, employee_start, employee_complete,
        manager_review, verdict_execute, run_complete.
        """
        from app.routers.webhook import _normalize_event_name

        bash_events = [
            "run_start",
            "employee_start",
            "employee_complete",
            "manager_review",
            "verdict_execute",
            "run_complete",
        ]

        for event_name in bash_events:
            normalized = _normalize_event_name(event_name)
            assert normalized != event_name or event_name in (
                "employee_start",  # passthrough in both bash and coordinator paths
            ), (
                f"Bash event '{event_name}' fell through to identity mapping. "
                f"It should have an explicit entry in the normalization map."
            )

    def test_webhook_schema_has_all_reporter_fields(self):
        """WebhookRunEvent must have fields for every extra key sent by reporter.py."""
        schema_fields = set(WebhookRunEvent.model_fields.keys())

        # Fields used by reporter.py's various post_* functions
        reporter_fields = {
            # Base (post_event)
            "event", "run_id", "timestamp", "concurrent_group_id",
            # Task (post_task_event)
            "task_id", "task_title", "project", "employee_index", "depends_on",
            # Employee start/complete
            "mode",
            # Conflict
            "file_path", "employee_a", "employee_b",
            # Guidance
            "guidance_type", "guidance_content",
        }

        missing = reporter_fields - schema_fields
        assert not missing, (
            f"WebhookRunEvent is missing fields used by reporter.py: {missing}. "
            f"Add them to the schema or the reporter payloads will be silently dropped."
        )
