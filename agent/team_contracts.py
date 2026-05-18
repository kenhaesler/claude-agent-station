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
import re
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

    return _parse_text(text)


def _split_sections(text: str) -> dict[str, list[str]]:
    """Split markdown into ``{section_heading: [body_lines]}``.

    Only ``## Heading`` (level-2) headings are recognised as section
    delimiters. Content before the first ``##`` heading is discarded.
    Recognised section names are the entries of ``CONTRACT_SECTIONS``;
    other section names are kept in the dict (so the test can verify
    they don't pollute parsed fields) but ignored by the consumers.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## ") and not line.startswith("### "):
            current = line[3:].strip()
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(line)
    return sections


_ROUTE_LINE_RE = re.compile(
    r"^\s*-\s*(?P<method>GET|POST|PUT|PATCH|DELETE)\s+(?P<path>\S+)"
    r"(?:\s*\(owner:\s*(?P<owner>[^)]+)\))?"
    r"(?:\s*[-—]\s*(?P<shape>.+))?$"
)
_KV_LINE_RE = re.compile(r"^\s*-\s*(?P<key>[^:]+):\s*(?P<value>.+)$")


def _parse_routes(lines: list[str]) -> list[Route]:
    routes: list[Route] = []
    for line in lines:
        match = _ROUTE_LINE_RE.match(line)
        if not match:
            continue
        routes.append(Route(
            method=match.group("method").strip(),
            path=match.group("path").strip(),
            owner=(match.group("owner") or "").strip(),
            response_shape=(match.group("shape") or "").strip(),
        ))
    return routes


def _parse_kv(lines: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in lines:
        match = _KV_LINE_RE.match(line)
        if not match:
            continue
        out[match.group("key").strip()] = match.group("value").strip()
    return out


def _parse_enums(lines: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in lines:
        match = _KV_LINE_RE.match(line)
        if not match:
            continue
        values = [v.strip() for v in match.group("value").split(",") if v.strip()]
        out[match.group("key").strip()] = values
    return out


def _parse_text(text: str) -> TeamContracts:
    sections = _split_sections(text)
    return TeamContracts(
        routes=_parse_routes(sections.get("API Routes", [])),
        field_names=_parse_kv(sections.get("Field Names", [])),
        response_shapes=_parse_kv(sections.get("Response Shapes", [])),
        enum_values=_parse_enums(sections.get("Enum Values", [])),
        route_ownership=_parse_kv(sections.get("Route Ownership", [])),
    )
