"""Smoke test that the manager prompt advertises APPROVE_INTEGRATION."""

from pathlib import Path

PROMPT = Path(__file__).resolve().parents[3] / "agent" / "prompts" / "manager.md"


def test_manager_prompt_documents_approve_integration():
    text = PROMPT.read_text(encoding="utf-8")
    # Verdict ladder heading
    assert "### APPROVE_INTEGRATION" in text, "ladder heading missing"
    # Decision tree branch
    assert "APPROVE_INTEGRATION" in text and "sensitive" in text.lower()
    # Confidence table updated — the 0.7-0.9 row no longer says "Consider PR"
    lines = [l for l in text.splitlines() if "0.7-0.9" in l]
    assert lines, "0.7-0.9 confidence row not found"
    assert "APPROVE_INTEGRATION" in lines[0], (
        "0.7-0.9 row must recommend APPROVE_INTEGRATION; got: " + lines[0]
    )
