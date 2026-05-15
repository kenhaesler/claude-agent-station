"""Splitter agent role + prompt presence (#391)."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_agent_role_file_exists():
    p = REPO_ROOT / "agent/agents/issue-splitter.md"
    assert p.exists(), p
    text = p.read_text()
    assert "name: issue-splitter" in text
    assert "tools:" in text
    # Read-only: no Edit / Write tools granted.
    assert "Edit" not in text
    assert "Write" not in text
    assert "model:" in text


def test_prompt_file_lists_required_sections():
    p = REPO_ROOT / "agent/prompts/issue-splitter.md"
    assert p.exists(), p
    text = p.read_text()
    for section in ("## Inputs", "## Task", "## Constraints", "## Output"):
        assert section in text, section
    # JSON schema sketch must be present.
    assert '"title"' in text
    assert '"acceptance"' in text
    assert '"depends_on"' in text


def test_prompt_documents_empty_array_as_no_split():
    """Downstream parser treats ``[]`` as "run as-is"; the prompt must say so."""
    p = REPO_ROOT / "agent/prompts/issue-splitter.md"
    text = p.read_text()
    assert "[]" in text


def test_architecture_doc_has_issue_decomposition_section() -> None:
    """Architecture doc must explain the splitter's role (#391)."""
    p = REPO_ROOT / "docs/architecture.md"
    text = p.read_text()
    assert "## Issue decomposition" in text
    assert "issue-splitter" in text
    assert "STATION_SPLIT_ENABLED" in text


def test_configuration_doc_documents_split_envs() -> None:
    """Configuration doc must list the splitter env var + label semantics (#391)."""
    p = REPO_ROOT / "docs/configuration.md"
    text = p.read_text()
    for token in ("STATION_SPLIT_ENABLED", "splitter-proposed", "split-me", "do-not-split"):
        assert token in text, token
