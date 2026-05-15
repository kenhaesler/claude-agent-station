"""Unit tests for :class:`agent.issue_splitter.gh_adapter.GhAdapter` (#391).

Subprocess is mocked at the ``gh_run`` / ``gh_json`` seam; no live
``gh`` binary is required.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.gh_client import GhError, GhResult
from agent.issue_splitter.gh_adapter import GhAdapter


def _err(stderr: str, rc: int = 1) -> GhError:
    return GhError(cmd=["gh", "api"], returncode=rc, stderr=stderr)


def test_label_exists_returns_true_on_success():
    with patch("agent.issue_splitter.gh_adapter.gh_json", return_value={"name": "split"}):
        assert GhAdapter().label_exists("o", "r", "split") is True


def test_label_exists_returns_false_on_404():
    with patch("agent.issue_splitter.gh_adapter.gh_json",
               side_effect=_err("HTTP 404: Not Found")):
        assert GhAdapter().label_exists("o", "r", "split") is False


def test_label_exists_reraises_other_errors():
    with patch("agent.issue_splitter.gh_adapter.gh_json",
               side_effect=_err("HTTP 500: server boom")):
        with pytest.raises(GhError):
            GhAdapter().label_exists("o", "r", "split")


def test_create_issue_parses_number_from_url():
    ok = GhResult(cmd=["gh"], returncode=0,
                  stdout="https://github.com/o/r/issues/42\n", stderr="")
    with patch("agent.issue_splitter.gh_adapter.gh_run", return_value=ok) as run:
        out = GhAdapter().create_issue("o", "r", title="t", body="b",
                                       labels=["a", "b"])
    assert out == {"number": 42, "url": "https://github.com/o/r/issues/42"}
    argv = run.call_args.args[0]
    assert "--label" in argv
    assert argv[argv.index("--label") + 1] == "a,b"


def test_create_issue_raises_on_failure():
    bad = GhResult(cmd=["gh"], returncode=1, stdout="", stderr="auth required")
    with patch("agent.issue_splitter.gh_adapter.gh_run", return_value=bad):
        with pytest.raises(GhError):
            GhAdapter().create_issue("o", "r", title="t", body="b", labels=[])


def test_add_labels_invokes_gh_issue_edit():
    ok = GhResult(cmd=["gh"], returncode=0, stdout="", stderr="")
    with patch("agent.issue_splitter.gh_adapter.gh_run", return_value=ok) as run:
        GhAdapter().add_labels("o", "r", 27, ["split"])
    argv = run.call_args.args[0]
    assert argv[:3] == ["issue", "edit", "27"]
    assert argv[argv.index("--repo") + 1] == "o/r"
    assert argv[argv.index("--add-label") + 1] == "split"


def test_add_labels_noop_when_empty():
    with patch("agent.issue_splitter.gh_adapter.gh_run") as run:
        GhAdapter().add_labels("o", "r", 27, [])
    run.assert_not_called()


def test_ensure_branch_skips_when_already_present():
    with patch("agent.issue_splitter.gh_adapter.gh_json",
               return_value={"object": {"sha": "deadbeef"}}) as gj, \
         patch("agent.issue_splitter.gh_adapter.gh_run") as gr:
        GhAdapter().ensure_branch("o/r", "integration/issue-27", from_branch="dev")
    assert gj.call_count == 1  # only the existence check
    gr.assert_not_called()


def test_ensure_branch_creates_when_missing():
    # First gh_json call (existence) raises 404; second (HEAD lookup)
    # returns the dev SHA.
    def gj_side_effect(args, **_kw):
        if "integration/issue-27" in "/".join(args):
            raise _err("HTTP 404: Not Found")
        return {"object": {"sha": "feedface"}}

    ok = GhResult(cmd=["gh"], returncode=0, stdout="", stderr="")
    with patch("agent.issue_splitter.gh_adapter.gh_json",
               side_effect=gj_side_effect), \
         patch("agent.issue_splitter.gh_adapter.gh_run", return_value=ok) as gr:
        GhAdapter().ensure_branch("o/r", "integration/issue-27", from_branch="dev")

    argv = gr.call_args.args[0]
    assert argv[:2] == ["api", "repos/o/r/git/refs"]
    assert "ref=refs/heads/integration/issue-27" in argv
    assert "sha=feedface" in argv
