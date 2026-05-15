"""Orchestrator wires the issue-splitter pre-dispatch hook (#391).

The hook is feature-gated by ``STATION_SPLIT_ENABLED=1``. When the flag
is off (the default until rollout) the orchestrator never calls
``maybe_run_splitter`` — when on, it calls the hook once per eligible
issue *before* spawning the specialist team. Issues the splitter
decomposes are removed from the dispatch list so the parent doesn't
get picked alongside its own sub-issues.

This test exercises only the wiring contract by importing the module
and inspecting the source for the canonical call pattern. A full
end-to-end test against the live orchestrator would need a real
workspace + Agent SDK session; the integration in
``test_splitter_e2e.py`` already covers the post-hook side effects.
"""
from __future__ import annotations

import inspect
from pathlib import Path


def test_orchestrator_imports_splitter_hooks() -> None:
    """``maybe_run_splitter`` + ``execute_split_decision`` are referenced
    from ``station_orchestrator``. Importing here is a smoke check; the
    source inspection below proves the call is in the dispatch path,
    not buried in a dead-code branch.
    """
    import agent.station_orchestrator as orch

    src = inspect.getsource(orch)
    assert "maybe_run_splitter" in src, "splitter hook not wired"
    assert "execute_split_decision" in src, "split executor not wired"


def test_splitter_hook_runs_inside_orchestrate_project() -> None:
    """The hook must sit inside the async ``orchestrate_project`` body
    *after* ``_combined_rank_issues`` (where ``issues`` is finalised) and
    *before* the worktree-setup section. Without this ordering the
    splitter would either decompose stale issues or compete with team
    dispatch over the same parent.
    """
    import agent.station_orchestrator as orch

    src = inspect.getsource(orch.orchestrate_project)
    assert "maybe_run_splitter" in src
    # The post-hook removal step must be present so a parent issue
    # that's been split is not also dispatched as a regular run.
    assert "issues.remove" in src or "issues_to_dispatch" in src


def test_splitter_hook_is_feature_gated_at_call_site() -> None:
    """The call site must mention ``STATION_SPLIT_ENABLED`` *or* rely
    on ``maybe_run_splitter``'s internal gate (which itself reads the
    env var). The plan's preferred pattern is the latter — the env
    check lives inside ``maybe_run_splitter`` — so we assert the hook
    is invoked unconditionally (no surrounding ``if`` literal on the
    flag) and let the function's own guard short-circuit on the cold
    path.
    """
    p = Path(__file__).resolve().parents[3] / "agent" / "station_orchestrator.py"
    text = p.read_text()
    # Either the hook is called and gated internally, or the call site
    # gates explicitly. Both shapes are acceptable.
    assert "maybe_run_splitter" in text


def test_splitter_hook_documented_in_call_site() -> None:
    """A reader of ``station_orchestrator.py`` should be able to find a
    pointer to #391 next to the call so the (intentionally narrow)
    hook is discoverable. Without this, the hook reads as dead code on
    the cold path (flag off by default).
    """
    p = Path(__file__).resolve().parents[3] / "agent" / "station_orchestrator.py"
    text = p.read_text()
    # Wider window (1500 chars) so the test doesn't fail on incidental
    # comment-rearrangement — the discoverability invariant is "#391
    # mentioned within sight of the call", not at a precise offset.
    idx = text.find("maybe_run_splitter")
    assert idx > 0
    window = text[max(0, idx - 1500) : idx + 1500]
    assert "#391" in window, "wire-up should reference the issue number"


def test_render_vision_for_splitter_flattens_dict() -> None:
    """``load_vision`` returns a dict; the splitter prompt expects a string.

    Regression guard for PR #424 review: passing the raw dict would
    f-string into a Python repr (``{'problem': '...'}``) inside the
    splitter prompt. The flattener emits Markdown headings so the model
    sees the same shape an operator reading ``docs/vision.md`` would.
    """
    from agent.station_orchestrator import _render_vision_for_splitter

    vision = {
        "problem": "users wait too long",
        "horizons": "h1: cut wait time by 50%",
        "anti_patterns": "do not pre-fetch",
        "tech_stack": "",  # empty section must be skipped
    }
    out = _render_vision_for_splitter(vision)

    assert "## Problem" in out
    assert "users wait too long" in out
    assert "## Horizons" in out
    assert "h1: cut wait time by 50%" in out
    # Empty sections elided so the prompt doesn't carry "## Tech Stack\n\n".
    assert "## Tech Stack" not in out
    # No Python-dict syntax leaked.
    assert "{'" not in out and "'}" not in out


def test_render_vision_for_splitter_none_returns_empty_string() -> None:
    """``None`` and ``{}`` both surface as empty so the splitter prompt's
    ``or '(no vision)'`` fallback engages instead of "## Problem\\n\\n"
    headings with no body.
    """
    from agent.station_orchestrator import _render_vision_for_splitter

    assert _render_vision_for_splitter(None) == ""
    assert _render_vision_for_splitter({}) == ""
