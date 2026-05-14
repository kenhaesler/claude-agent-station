"""Pin that docs/configuration.md documents APPROVE_INTEGRATION (issue #388)."""

from pathlib import Path

DOC = Path(__file__).resolve().parents[3] / "docs" / "configuration.md"


def test_configuration_doc_mentions_approve_integration():
    text = DOC.read_text(encoding="utf-8")
    assert "APPROVE_INTEGRATION" in text
    assert "auto-merge" in text.lower()
    # Prerequisite: branch protection must require checks for auto-merge
    # to be meaningful.
    assert "required check" in text.lower() or "branch protection" in text.lower()
