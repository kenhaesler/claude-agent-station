"""Render a structured vision document to markdown using a fixed template."""

from __future__ import annotations

from datetime import datetime

SECTIONS = [
    ("problem", "Problem"),
    ("users", "Users"),
    ("end_state", "End-state"),
    ("non_goals", "Non-goals"),
    ("principles", "Principles"),
    ("horizons", "Horizons"),
    ("anti_patterns", "Anti-patterns"),
]


def render_vision_doc(doc: dict, repo: str, refined_at: datetime) -> str:
    """Render a vision_doc dict to the canonical markdown template.

    Empty/missing sections become a `_(not specified)_` placeholder so the
    file always has all seven H2 headings — orchestrator hooks rely on a
    consistent shape.
    """
    parts = [f"# Vision — {repo}", ""]
    parts.append(f"*Last refined: {refined_at.isoformat()} via Claude Station*")
    parts.append("")
    for key, heading in SECTIONS:
        parts.append(f"## {heading}")
        parts.append("")
        body = (doc.get(key) or "").strip()
        parts.append(body if body else "_(not specified)_")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"
