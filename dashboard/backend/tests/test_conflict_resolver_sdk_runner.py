"""Source-level test pinning the localised stream-close timeout setter.

After issue #392 the launcher stops setting CLAUDE_CODE_STREAM_CLOSE_TIMEOUT
globally. Modules that still use SDK `query()` must own the setter
themselves.
"""

import inspect

from agent.conflict_resolver import sdk_runner


def test_sdk_runner_sets_stream_close_timeout_locally():
    src = inspect.getsource(sdk_runner)
    assert "CLAUDE_CODE_STREAM_CLOSE_TIMEOUT" in src, (
        "sdk_runner must set CLAUDE_CODE_STREAM_CLOSE_TIMEOUT locally "
        "now that agent.launcher no longer does (issue #392)."
    )
    # And the comment must explain why so future-us doesn't yank it again.
    assert "PR #371" in src or "stream-close" in src.lower()
