"""Pin that the call-site audit doc exists and covers every relevant module.

#392 acceptance criterion: "All query() call sites audited and either
migrated or documented".
"""

from pathlib import Path

AUDIT = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "superpowers"
    / "notes"
    / "2026-05-14-issue-392-audit.md"
)


def test_audit_doc_exists_and_covers_all_call_sites():
    assert AUDIT.is_file(), f"audit doc missing at {AUDIT}"
    text = AUDIT.read_text(encoding="utf-8")
    # Every audited module must be named so reviewers can grep.
    assert "agent/station_orchestrator.py" in text
    assert "agent/conflict_resolver/sdk_runner.py" in text
    assert "agent/vision_analyst.py" in text
    # Each row must declare its disposition.
    assert "ClaudeSDKClient" in text  # for the orchestrator
    assert "subprocess" in text or "claude --print" in text  # vision_analyst
