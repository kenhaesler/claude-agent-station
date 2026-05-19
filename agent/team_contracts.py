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


def validate_verdict_against_contracts(
    verdict, contracts: TeamContracts, workspace_path: Path,
) -> list[Violation]:
    """Inspect ``verdict.reasoning`` and return contract violations.

    Heuristic by design. The validator scans the manager's prose for:
      1. **Field-name conflicts**: any contracted field-name value
         (the right-hand side of ``- key: value``) that appears in the
         reasoning alongside a non-contract alternative also referenced
         in the reasoning. Captures the run-20260517T191757Z pattern
         ("backend returns purchasePrice while QA expects purchaseCost").
      2. **Route-ownership conflicts**: any contracted route path that
         appears in the reasoning with a sibling role name (backend /
         frontend / qa) other than its contracted owner.
      3. **Enum-value conflicts**: any quoted token in the reasoning
         that isn't in the contracted allowed list for any enum named
         in the reasoning.

    Branch-checkout / diff inspection is intentionally NOT done here —
    the heuristic on manager prose is sufficient for the observed
    real-world conflicts and avoids a worktree dependency. The
    ``workspace_path`` argument is reserved for future expansion.

    Returns an empty list on no violations.
    """
    violations: list[Violation] = []
    if contracts is None or verdict is None:
        return violations

    reasoning = getattr(verdict, "reasoning", "") or ""

    # 1. Field-name conflicts. Look for any "alt vs contracted" pattern
    #    or any non-contracted name appearing where a contracted one
    #    should ("returns X instead of Y" / "uses X while expects Y").
    contracted_names = set(contracts.field_names.values())
    for canonical, chosen in contracts.field_names.items():
        if not chosen:
            continue
        if chosen in reasoning:
            # The chosen (canonical contracted) name is mentioned —
            # scan for any nearby substring that looks like a sibling
            # field name. Heuristic: a true camelCase token that is
            # NOT itself a contracted name and IS in the reasoning is
            # suspicious IF a conflict-signal word appears between them.
            for token in _candidate_field_names(reasoning):
                if token == chosen:
                    continue
                if token in contracted_names:
                    # Another contracted name appearing alongside is not
                    # itself a conflict — both are legitimate references.
                    continue
                if _looks_like_field_name_conflict(reasoning, chosen, token):
                    violations.append(Violation(
                        section="field_names",
                        expected=chosen,
                        found=token,
                        context=f"Contract field '{canonical}' chose '{chosen}'; "
                                f"reasoning also references '{token}'.",
                    ))

    # 2. Route ownership conflicts.
    for route_path, owner in contracts.route_ownership.items():
        if not route_path or route_path not in reasoning:
            continue
        for role in ("backend", "frontend", "qa"):
            if role == owner:
                continue
            if role in reasoning.lower() and _route_implicated(reasoning, route_path, role):
                violations.append(Violation(
                    section="route_ownership",
                    expected=owner,
                    found=role,
                    context=f"Route '{route_path}' is owned by '{owner}' per "
                            f"contract; reasoning implicates '{role}'.",
                ))

    # 3. Enum value conflicts. Look for quoted tokens that are not in
    #    the contracted allowed list for an enum named in the reasoning.
    for enum_name, allowed in contracts.enum_values.items():
        if not enum_name:
            continue
        # We scan quoted tokens regardless of whether the enum name is
        # mentioned, because the heuristic relies on family-member
        # matching (_is_enum_family_member) for precision.
        for token in _quoted_tokens(reasoning):
            if token in allowed:
                continue
            # Only flag if the reasoning suggests a status/state change
            # and the token resembles the contracted family.
            if _is_enum_family_member(token, allowed):
                violations.append(Violation(
                    section="enum_values",
                    expected=", ".join(allowed),
                    found=token,
                    context=f"Enum '{enum_name}' allows {allowed}; "
                            f"reasoning references '{token}'.",
                ))

    return violations


# ----- helpers (private) ----------------------------------------------------

# Require at least one uppercase in positions 1+ — true camelCase.
# Excludes 'should', 'correctly', 'previously', etc.
_FIELD_NAME_TOKEN_RE = re.compile(r"\b[a-z][a-z]*[A-Z][A-Za-z]+\b")
_QUOTED_TOKEN_RE = re.compile(r"['\"]([A-Za-z_][\w]*)['\"]")


def _candidate_field_names(text: str) -> list[str]:
    """True camelCase identifiers (lowercase start, at least one inner uppercase).

    Heuristic for field-name detection. The uppercase requirement excludes
    common English words like ``should`` / ``correctly`` that previously
    matched the looser length-only rule.
    """
    return list(set(_FIELD_NAME_TOKEN_RE.findall(text)))


def _looks_like_field_name_conflict(text: str, chosen: str, candidate: str) -> bool:
    """True iff text references both names AND a conflict signal word
    appears in the text span BETWEEN them (not anywhere)."""
    if chosen not in text or candidate not in text:
        return False
    lower = text.lower()
    chosen_idx = lower.find(chosen.lower())
    cand_idx = lower.find(candidate.lower())
    if chosen_idx < 0 or cand_idx < 0:
        return False
    # Span between them (in either order).
    lo = min(chosen_idx, cand_idx)
    hi = max(chosen_idx, cand_idx)
    span = lower[lo:hi]
    signals = ("while", "vs", "instead of", "rather than", "expects", "conflicts")
    # Note: dropped 'but' — too common in benign prose. The signals
    # left all imply an actual contrast/disagreement.
    return any(sig in span for sig in signals)


def _route_implicated(text: str, route_path: str, role: str) -> bool:
    """True iff text suggests the named role is doing something to this route.

    Heuristic: the role name must appear within 30 chars BEFORE the route
    path (verb-position, e.g. 'frontend created /api/...'). Roles AFTER the
    route path are typically consumers (e.g. 'frontend consumes it'), not
    owners, and should not flag.
    """
    lower = text.lower()
    pidx = lower.find(route_path.lower())
    if pidx < 0:
        return False
    # Look backwards from the route path for the role name.
    window_start = max(0, pidx - 30)
    return role in lower[window_start:pidx]


def _quoted_tokens(text: str) -> list[str]:
    """Tokens appearing in single or double quotes in the text."""
    return _QUOTED_TOKEN_RE.findall(text)


def _is_enum_family_member(candidate: str, allowed: list[str]) -> bool:
    """Loose: shares prefix or shares root with at least one allowed value."""
    if not allowed:
        return False
    for a in allowed:
        if not a:
            continue
        if candidate.startswith(a[:4]) or a.startswith(candidate[:4]):
            return True
    return False
