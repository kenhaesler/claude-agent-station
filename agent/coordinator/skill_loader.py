"""Load skill files and inject their content into agent prompts."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def load_skills(skill_names: list[str]) -> str:
    """Read skill .md files and concatenate their content.

    Each skill name maps to ``agent/skills/{name}.md``.  Files that do
    not exist are logged as warnings and skipped so a missing skill
    never crashes the coordinator.

    Returns a combined string ready to append to a prompt, or an empty
    string if no skills were loaded.
    """
    if not skill_names:
        return ""

    sections: list[str] = []
    for name in skill_names:
        skill_file = SKILLS_DIR / f"{name}.md"
        if not skill_file.is_file():
            logger.warning("Skill file not found, skipping: %s", skill_file)
            continue
        try:
            content = skill_file.read_text().strip()
        except OSError:
            logger.warning("Failed to read skill file: %s", skill_file, exc_info=True)
            continue
        if content:
            sections.append(content)
            logger.debug("Loaded skill '%s' (%d chars)", name, len(content))

    if not sections:
        return ""

    return "\n\n## Preloaded Skills\n\n" + "\n\n---\n\n".join(sections)
