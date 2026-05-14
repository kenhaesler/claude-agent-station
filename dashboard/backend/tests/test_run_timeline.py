"""Run timeline API tests (issue #387)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.schemas import RunTimelineEvent, RunTimelinePage


def test_timeline_event_shape():
    ev = RunTimelineEvent(
        t=datetime(2026, 5, 13, 15, 14, 8, tzinfo=timezone.utc),
        kind="lifecycle",
        event="run_start",
        source="runs",
        source_id="run-20260513T151408Z",
        agent=None,
        data={"status": "started"},
    )
    assert ev.kind == "lifecycle"
    assert ev.data == {"status": "started"}


def test_timeline_page_default_empty():
    page = RunTimelinePage(run_id="run-x", events=[], next_cursor=None, has_more=False)
    assert page.events == []
    assert page.has_more is False


from app.services.run_timeline import TimelineCursor


def test_cursor_roundtrip():
    c = TimelineCursor(
        t=datetime(2026, 5, 13, 15, 14, 8, tzinfo=timezone.utc),
        source="audit_log",
        source_id="12345",
    )
    encoded = c.encode()
    assert isinstance(encoded, str)
    decoded = TimelineCursor.decode(encoded)
    assert decoded.t == c.t
    assert decoded.source == c.source
    assert decoded.source_id == c.source_id


def test_cursor_decode_rejects_garbage():
    with pytest.raises(ValueError):
        TimelineCursor.decode("not-base64!!!")
