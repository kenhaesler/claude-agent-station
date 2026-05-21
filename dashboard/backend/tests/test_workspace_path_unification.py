"""Tests for the workspace-path unification in
:func:`agent.station_orchestrator.orchestrate_project`.

Background: before this change, ``orchestrate_project`` derived the
workspace path from ``repo.split('/')[-1]`` (the bare repo name, e.g.
``LCM``) while ``project_loop.iterate_projects`` independently resolved
it via ``ensure_workspace`` which uses ``_slug(repo)`` (e.g.
``laboef1900__LCM``). The two paths pointed at *different clones on
disk*: teammates committed their feature branches in the bare-name
clone's worktrees, but ``execute_verdict`` then ran ``git push`` from
the slug-name clone — which had no such branches and failed with
``src refspec does not match any``. Run-20260521T192218Z hit this on
every verdict.

The fix makes ``iterate_projects`` pass its already-resolved
``workspace_path`` through to ``orchestrate_project`` so both halves
of the run share one working tree.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import patch


def test_orchestrate_project_accepts_workspace_path_kwarg():
    """The signature must accept ``workspace_path`` as a keyword arg
    so callers can pin the path explicitly."""
    from agent.station_orchestrator import orchestrate_project

    sig = inspect.signature(orchestrate_project)
    assert "workspace_path" in sig.parameters, (
        "orchestrate_project must accept workspace_path so the caller "
        "can keep the SDK session and verdict execution on one path"
    )
    param = sig.parameters["workspace_path"]
    assert param.kind == inspect.Parameter.KEYWORD_ONLY
    assert param.default is None


def test_iterate_projects_passes_resolved_workspace_path():
    """``iterate_projects`` must forward the ``ensure_workspace`` result
    into ``orchestrate_project`` as ``workspace_path``. Without this the
    two halves of the run resolve to different filesystem paths."""
    import textwrap
    from agent import project_loop

    src = inspect.getsource(project_loop.iterate_projects)
    # Trim leading indentation so the assertions are about lexical
    # ordering, not column alignment.
    src = textwrap.dedent(src)

    # ``workspace_path`` is assigned from ``ensure_workspace`` and then
    # passed as a kwarg into ``orchestrate_project``.
    assert "workspace_path = ensure_workspace(" in src
    assert "workspace_path=workspace_path" in src


def test_orchestrate_project_uses_supplied_path_not_repo_name(
    tmp_path, monkeypatch,
):
    """When workspace_path is supplied, the function MUST use it verbatim
    — not re-derive from ``repo.split('/')[-1]``. Stub ``_ensure_workspace``
    (the first function called after path selection) and inspect the
    path argument it receives."""
    from agent import station_orchestrator as so

    seen: dict = {}

    def _spy(workspace, repo, branch):  # noqa: ANN001
        seen["workspace"] = workspace
        seen["repo"] = repo
        # Raise to short-circuit the rest of orchestrate_project.
        raise RuntimeError("stop-here")

    monkeypatch.setattr(so, "_ensure_workspace", _spy)

    project = {"repo": "laboef1900/LCM", "branch": "main"}
    supplied = str(tmp_path / "explicit_slug" / "laboef1900__LCM")

    try:
        import asyncio
        asyncio.run(so.orchestrate_project(
            project, config={}, run_id="t",
            workspaces_dir=str(tmp_path / "wsdir"),
            workspace_path=supplied,
        ))
    except Exception:
        pass

    assert seen.get("workspace") == supplied, (
        f"orchestrate_project must hand the supplied workspace_path "
        f"to _ensure_workspace; got {seen.get('workspace')!r} "
        f"(expected {supplied!r})"
    )
    # Sanity: this is the right repo so we know the spy actually fired.
    assert seen.get("repo") == "laboef1900/LCM"


def test_orchestrate_project_falls_back_to_repo_name_when_path_missing(
    tmp_path, monkeypatch,
):
    """Without ``workspace_path``, the deprecated derivation must still
    work — but with the WARNING (asserted in the next test). Pin the
    derivation shape so future refactors don't silently switch to
    ``_slug``."""
    from agent import station_orchestrator as so

    seen: dict = {}

    def _spy(workspace, repo, branch):  # noqa: ANN001
        seen["workspace"] = workspace
        raise RuntimeError("stop-here")

    monkeypatch.setattr(so, "_ensure_workspace", _spy)

    project = {"repo": "laboef1900/LCM", "branch": "main"}
    wsdir = str(tmp_path / "wsdir")

    try:
        import asyncio
        asyncio.run(so.orchestrate_project(
            project, config={}, run_id="t", workspaces_dir=wsdir,
        ))
    except Exception:
        pass

    import os
    assert seen.get("workspace") == os.path.join(wsdir, "LCM"), (
        "without workspace_path, fallback must use bare repo_name "
        "for backward compatibility"
    )


def test_orchestrate_project_warns_when_workspace_path_missing(
    monkeypatch, caplog,
):
    """The bare-repo-name fallback is deprecated — calling without
    ``workspace_path`` must emit a WARNING so tooling that hasn't been
    migrated is visible in logs."""
    from agent import station_orchestrator as so

    # Same short-circuit trick as the previous test.
    original_run = so.subprocess.run

    def _capture(cmd, *args, **kwargs):  # noqa: ANN001
        if "fetch" in (cmd if isinstance(cmd, list) else []):
            raise RuntimeError("stop-here")
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(so.subprocess, "run", _capture)

    project = {"repo": "laboef1900/LCM", "branch": "main"}

    try:
        import asyncio
        with caplog.at_level("WARNING", logger="agent.station_orchestrator"):
            asyncio.run(so.orchestrate_project(
                project, config={}, run_id="t",
                workspaces_dir="/tmp/x",
            ))
    except Exception:
        pass

    assert any(
        "without workspace_path" in rec.message
        and "laboef1900/LCM" in rec.message
        for rec in caplog.records
    ), "missing workspace_path must produce a deprecation WARNING"
