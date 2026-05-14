"""Grep-style test pinning the spec's grep assertions in code (#392).

The spec requires:
  - `grep -rn CLAUDE_CODE_STREAM_CLOSE_TIMEOUT agent/launcher.py` → empty
  - `grep -rn CLAUDE_CODE_STREAM_CLOSE_TIMEOUT dashboard/backend/tests/` → empty
    EXCEPT for our own audit/negative-assertion tests which reference the
    name in a docstring/comparison only.
"""

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _grep(rel_path: str) -> list[str]:
    result = subprocess.run(
        ["grep", "-rn", "CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", rel_path],
        cwd=REPO, capture_output=True, text=True,
    )
    return [l for l in result.stdout.splitlines() if l]


def test_launcher_has_no_stream_close_timeout_refs():
    hits = _grep("agent/launcher.py")
    assert hits == [], f"unexpected references in agent/launcher.py: {hits}"


def test_dashboard_tests_only_reference_env_var_in_negative_assertions():
    """The only places that may name the env var under
    ``dashboard/backend/tests/`` are the negative-assertion test added in
    Task 3 and the audit doc presence test.
    """
    hits = _grep("dashboard/backend/tests")
    allowed_files = {
        "test_orchestrator_wiring.py",          # negative assertion + docstring
        "test_issue_392_orphan_refs.py",        # this file
        "test_conflict_resolver_sdk_runner.py", # pins the localised setter
    }
    for line in hits:
        path = line.split(":", 1)[0]
        fname = path.split("/")[-1]
        assert fname in allowed_files, (
            f"unexpected reference: {line} (allowed files: {allowed_files})"
        )
