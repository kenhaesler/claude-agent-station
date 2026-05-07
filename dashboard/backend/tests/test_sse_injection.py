"""Tests for SSE event_type injection hardening (issue #187).

Two layers of defense:

1. ``WebhookRunEvent.event`` validator rejects payloads whose ``event`` field
   contains ASCII control characters (CR/LF/null/etc.) or exceeds the length
   cap. This blocks injection at the HTTP boundary.

2. The SSE generator in ``app.routers.events`` strips CR/LF from the
   ``event_type`` before interpolation, so an internal publisher that bypasses
   the schema can never inject extra SSE protocol lines either.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.database import Base, engine
from app.main import app
from app.schemas import WebhookRunEvent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(setup_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Schema validator — WebhookRunEvent.event
# ---------------------------------------------------------------------------

class TestEventFieldValidator:
    """The schema rejects malicious or malformed event names."""

    def test_rejects_newline(self) -> None:
        with pytest.raises(ValidationError):
            WebhookRunEvent(
                run_id="r1",
                event="run_start\ndata: injected\n\nevent:",
            )

    def test_rejects_carriage_return(self) -> None:
        with pytest.raises(ValidationError):
            WebhookRunEvent(run_id="r1", event="run_start\rinjected")

    def test_rejects_null_byte(self) -> None:
        with pytest.raises(ValidationError):
            WebhookRunEvent(run_id="r1", event="run\x00start")

    def test_rejects_other_control_chars(self) -> None:
        # \x01 (start-of-heading) shouldn't appear in legitimate event names
        with pytest.raises(ValidationError):
            WebhookRunEvent(run_id="r1", event="run\x01start")

    def test_rejects_too_long(self) -> None:
        with pytest.raises(ValidationError):
            WebhookRunEvent(run_id="r1", event="a" * 101)

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValidationError):
            WebhookRunEvent(run_id="r1", event="")

    def test_accepts_known_events(self) -> None:
        for name in (
            "run_start",
            "run_complete",
            "verdict",
            "verdict_execute",
            "employee_complete",
            "task_started",
            "teammate_progress",
            "vision_misalignment",
        ):
            ev = WebhookRunEvent(run_id="r1", event=name)
            assert ev.event == name

    def test_accepts_max_length(self) -> None:
        ev = WebhookRunEvent(run_id="r1", event="x" * 100)
        assert len(ev.event) == 100


# ---------------------------------------------------------------------------
# HTTP boundary — POST /api/webhook/run-event returns 422 for bad event field
# ---------------------------------------------------------------------------

class TestWebhookRejectsInjection:
    """The webhook endpoint returns HTTP 422 for malicious event fields."""

    @pytest.mark.asyncio
    async def test_newline_injection_returns_422(self, client: AsyncClient) -> None:
        with patch("app.routers.webhook.settings") as mock_settings:
            mock_settings.webhook_secret = None
            resp = await client.post(
                "/api/webhook/run-event",
                json={
                    "run_id": "run-inject-1",
                    "event": "run_start\ndata: pwned\n\nevent:",
                    "project": "owner/repo",
                },
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_null_byte_returns_422(self, client: AsyncClient) -> None:
        with patch("app.routers.webhook.settings") as mock_settings:
            mock_settings.webhook_secret = None
            resp = await client.post(
                "/api/webhook/run-event",
                json={
                    "run_id": "run-inject-2",
                    "event": "run\x00start",
                    "project": "owner/repo",
                },
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_overlong_event_returns_422(self, client: AsyncClient) -> None:
        with patch("app.routers.webhook.settings") as mock_settings:
            mock_settings.webhook_secret = None
            resp = await client.post(
                "/api/webhook/run-event",
                json={
                    "run_id": "run-inject-3",
                    "event": "a" * 200,
                    "project": "owner/repo",
                },
            )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# SSE generator — defense in depth at the protocol boundary
# ---------------------------------------------------------------------------

class TestSSEGeneratorSanitizes:
    """Even if the schema is bypassed, the SSE generator drops CR/LF from
    event_type so the on-the-wire frames stay well-formed."""

    @pytest.mark.asyncio
    async def test_stream_strips_newlines_from_event_type(
        self,
        client: AsyncClient,
    ) -> None:
        async def _mock_subscribe():
            yield {
                "type": "run_start\ndata: injected\n\nevent: phantom",
                "data": {"run_id": "r-inj"},
            }

        with patch("app.routers.events.subscribe", _mock_subscribe):
            resp = await client.get("/api/events/stream")

        body = resp.text
        assert resp.status_code == 200

        # The literal injected payload must not break out into its own frame.
        # We expect exactly two SSE frames in the body: the ": connected"
        # comment and the single ``event: ...`` frame from the malicious payload
        # (with CR/LF stripped). No "phantom" event line should be emitted.
        event_lines = [ln for ln in body.split("\n") if ln.startswith("event:")]
        assert len(event_lines) == 1, (
            f"Expected exactly one event: line, got {event_lines!r}"
        )
        # The single event: line should contain the sanitized value all on
        # one line (no embedded newlines).
        line = event_lines[0]
        assert "\n" not in line
        assert "\r" not in line
        # The "phantom" tail should appear inside the single event-name (now
        # safe — it's just a weird single-line value), not as a second frame.
        assert "phantom" in line

    @pytest.mark.asyncio
    async def test_stream_strips_carriage_returns(
        self,
        client: AsyncClient,
    ) -> None:
        async def _mock_subscribe():
            yield {"type": "run\r\nstart", "data": {"x": 1}}

        with patch("app.routers.events.subscribe", _mock_subscribe):
            resp = await client.get("/api/events/stream")

        # No bare CR should appear in the event: frame
        body = resp.text
        event_lines = [ln for ln in body.split("\n") if ln.startswith("event:")]
        assert len(event_lines) == 1
        assert "\r" not in event_lines[0]

    @pytest.mark.asyncio
    async def test_stream_handles_normal_event(
        self,
        client: AsyncClient,
    ) -> None:
        """Sanity check: a normal event is unchanged."""
        async def _mock_subscribe():
            yield {"type": "run_start", "data": {"run_id": "r-normal"}}

        with patch("app.routers.events.subscribe", _mock_subscribe):
            resp = await client.get("/api/events/stream")

        body = resp.text
        assert "event: run_start" in body
        assert "r-normal" in body
