"""Verify orchestrate_project exists and accepts the documented signature (#383)."""
from __future__ import annotations

import inspect


def test_orchestrate_project_signature():
    """orchestrate_project(project, config, run_id, workspaces_dir) -> int coroutine."""
    from agent import station_orchestrator as so

    assert hasattr(so, "orchestrate_project"), (
        "orchestrate_project must be extracted from orchestrate() (issue #383)"
    )
    assert inspect.iscoroutinefunction(so.orchestrate_project), (
        "orchestrate_project must be `async def`"
    )
    sig = inspect.signature(so.orchestrate_project)
    expected_params = ["project", "config", "run_id", "workspaces_dir"]
    assert list(sig.parameters.keys())[:4] == expected_params, (
        f"orchestrate_project signature must start with {expected_params}; "
        f"got {list(sig.parameters.keys())}"
    )
