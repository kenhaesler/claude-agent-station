"""Tests for agent.gh_client (#362)."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch, MagicMock


def _completed(returncode: int, stdout: str = "", stderr: str = ""):
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


def test_gh_json_returns_parsed_payload_on_success():
    from agent.gh_client import gh_json

    expected = [{"number": 1, "title": "x"}]
    with patch("agent.gh_client.subprocess.run",
               return_value=_completed(0, stdout=json.dumps(expected))):
        result = gh_json(["issue", "list", "--repo", "a/b"])
    assert result == expected


def test_gh_json_raises_on_non_zero_exit():
    from agent.gh_client import gh_json, GhError

    with patch("agent.gh_client.subprocess.run",
               return_value=_completed(1, stderr="auth required")):
        try:
            gh_json(["issue", "list"])
        except GhError as exc:
            assert exc.returncode == 1
            assert "auth required" in exc.stderr
        else:
            raise AssertionError("expected GhError")


def test_gh_json_returns_none_on_empty_stdout():
    """Some gh subcommands legitimately produce empty stdout (e.g. a
    --json query on a paginated endpoint that returned zero rows). We
    surface ``None`` rather than raising JSONDecodeError so callers can
    treat the empty-set case uniformly.
    """
    from agent.gh_client import gh_json

    with patch("agent.gh_client.subprocess.run",
               return_value=_completed(0, stdout="")):
        assert gh_json(["issue", "list"]) is None


def test_gh_run_returns_result_without_raising_on_nonzero():
    """gh_run is for verdict-execution paths where stderr is part of
    the operator-visible payload. It MUST NOT raise on non-zero exit.
    """
    from agent.gh_client import gh_run

    with patch("agent.gh_client.subprocess.run",
               return_value=_completed(1, stderr="branch exists")):
        result = gh_run(["pr", "create", "--head", "x"])
    assert result.ok is False
    assert result.returncode == 1
    assert "branch exists" in result.stderr


def test_gh_run_returns_ok_result_on_success():
    from agent.gh_client import gh_run

    with patch("agent.gh_client.subprocess.run",
               return_value=_completed(0, stdout="https://github.com/x/y/pull/1")):
        result = gh_run(["pr", "create", "--head", "x"])
    assert result.ok is True
    assert "github.com" in result.stdout
