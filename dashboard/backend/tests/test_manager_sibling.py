"""End-to-end tests for the manager-as-sibling refactor (#390)."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
AGENT_DIR = REPO / "agent" / "agents"
MANAGER_AGENT = AGENT_DIR / "manager.md"
MANAGER_PROMPT = REPO / "agent" / "prompts" / "manager.md"


def test_manager_agent_file_exists():
    assert MANAGER_AGENT.is_file(), (
        f"Agent Teams sibling definition missing at {MANAGER_AGENT}. "
        "See spec §Add the manager agent definition."
    )


def test_manager_agent_frontmatter_is_valid():
    text = MANAGER_AGENT.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "must start with YAML frontmatter"
    parts = text.split("---", 2)
    assert len(parts) >= 3, "missing closing frontmatter delimiter"
    fm = parts[1]
    assert "name: manager" in fm
    assert "description:" in fm
    assert "tools:" in fm
    assert "model:" in fm
    # Manager runs sonnet, not opus (cost + speed).
    assert "claude-sonnet-4-6" in fm


def test_manager_agent_body_sources_prompt():
    """The manager.md body must be the prompts/manager.md content,
    adapted for sibling-agent context (not `claude -p`).
    """
    text = MANAGER_AGENT.read_text(encoding="utf-8")
    body = text.split("---", 2)[2]

    # Same verdict literals as the canonical prompt.
    assert "APPROVE" in body
    assert "REJECT" in body
    assert "SKIP" in body
    # No `claude -p` framing.
    assert "claude -p" not in body
    # Sibling framing present.
    assert "sibling" in body.lower() or "agent teams" in body.lower()
