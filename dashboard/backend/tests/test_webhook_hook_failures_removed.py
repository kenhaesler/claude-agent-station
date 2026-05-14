"""Pin that the hook_failures webhook event is no longer handled (#389)."""

import inspect

from app.routers import webhook


def test_hook_failures_event_is_no_longer_handled():
    src = inspect.getsource(webhook)
    # The router must no longer have an "elif event_name == 'hook_failures'"
    # branch (or a `hook_failures` string compare).
    assert "hook_failures" not in src, (
        "webhook router still references the hook_failures event; #389 "
        "deleted the inline audit hook so this event no longer fires."
    )
