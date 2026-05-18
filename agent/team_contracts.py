"""Team contract coordination for Agent Teams sibling teammates.

The lead agent writes ``.claude-team-contracts.md`` to the workspace
before spawning role-specialized teammates (backend / frontend / qa).
The file documents cross-team contracts (field names, route ownership,
response shapes, enum values) so siblings don't pick conflicting names.

Issue: #456. Spec:
``docs/superpowers/specs/2026-05-18-sibling-coordination-design.md``.

This module is fail-soft: missing or malformed files yield ``None``
rather than raising. Callers degrade to current behavior when the
contract is absent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

CONTRACTS_FILENAME = ".claude-team-contracts.md"
CONTRACT_SECTIONS = (
    "API Routes",
    "Field Names",
    "Response Shapes",
    "Enum Values",
    "Route Ownership",
)


@dataclass
class Route:
    method: str
    path: str
    owner: str
    response_shape: str = ""


@dataclass
class TeamContracts:
    routes: list[Route] = field(default_factory=list)
    field_names: dict[str, str] = field(default_factory=dict)
    response_shapes: dict[str, str] = field(default_factory=dict)
    enum_values: dict[str, list[str]] = field(default_factory=dict)
    route_ownership: dict[str, str] = field(default_factory=dict)


@dataclass
class Violation:
    section: str
    expected: str
    found: str
    context: str


def parse_contracts(workspace_path: Path) -> TeamContracts | None:
    """Parse ``.claude-team-contracts.md`` from the workspace.

    Returns a :class:`TeamContracts` instance on success or ``None`` if
    the file is missing or unreadable. Lenient by design — missing
    sections become empty containers; unknown sections are ignored.
    """
    file_path = Path(workspace_path) / CONTRACTS_FILENAME
    try:
        text = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("contracts: unreadable file at %s: %s", file_path, exc)
        return None

    # Task 2 fills in the actual parsing.
    return TeamContracts()
