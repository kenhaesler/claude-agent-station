"""Render a structured vision document to markdown using a fixed template."""

from __future__ import annotations

from datetime import datetime

SECTIONS = [
    ("problem", "Problem"),
    ("users", "Users"),
    ("end_state", "End-state"),
    # Issue #335: tech stack and runtime target slot between End-state and
    # Non-goals. Both are optional on VisionDoc; empty bodies render as
    # `_(not specified)_` like the existing seven.
    ("tech_stack", "Tech Stack"),
    ("runtime_target", "Runtime Target"),
    ("non_goals", "Non-goals"),
    ("principles", "Principles"),
    ("horizons", "Horizons"),
    ("anti_patterns", "Anti-patterns"),
]


def render_vision_doc(
    doc: dict,
    repo: str,
    refined_at: datetime,
    references: list[dict] | None = None,
) -> str:
    """Render a vision_doc dict to the canonical markdown template.

    Empty/missing sections become a `_(not specified)_` placeholder so the
    file always has all nine H2 headings — orchestrator hooks rely on a
    consistent shape.

    ``references``: optional list of ``{"filename": str, "size_bytes": int}``;
    when non-empty, appends a ``## References`` section.
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
    if references:
        parts.append("## References")
        parts.append("")
        parts.append("Reference files for this vision are in [`vision-refs/`](vision-refs/):")
        parts.append("")
        for ref in references:
            kb = max(1, round(ref["size_bytes"] / 1024))
            parts.append(f"- [`{ref['filename']}`](vision-refs/{ref['filename']}) — {kb} KB")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"
