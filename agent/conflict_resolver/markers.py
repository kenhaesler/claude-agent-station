"""Parse git conflict markers from a conflicted file.

Pure functions — no git, no I/O. The output feeds the LLM resolver
prompt assembly and the "is this a lockfile-only conflict?" predicate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# Set so callers can membership-test efficiently.
LOCKFILE_NAMES: Final[frozenset[str]] = frozenset({
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Cargo.lock",
})


@dataclass(frozen=True)
class ConflictRegion:
    """One <<<<<<< ... ======= ... >>>>>>> region in a file."""
    ours_lines: list[str]
    theirs_lines: list[str]
    # Line numbers (1-based) of the marker lines themselves, for prompt context.
    start_line: int
    middle_line: int
    end_line: int


def file_has_conflicts(text: str) -> bool:
    """Cheap markerless-fast-path for callers that only need a yes/no answer."""
    return "<<<<<<< " in text and "=======" in text and ">>>>>>> " in text


def parse_conflict_markers(text: str) -> list[ConflictRegion]:
    """Return all conflict regions in `text`, in source order."""
    regions: list[ConflictRegion] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].startswith("<<<<<<< "):
            start = i
            ours: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("======="):
                ours.append(lines[i])
                i += 1
            if i >= len(lines):
                # malformed — bail
                return regions
            middle = i
            theirs: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith(">>>>>>> "):
                theirs.append(lines[i])
                i += 1
            if i >= len(lines):
                return regions
            end = i
            regions.append(ConflictRegion(
                ours_lines=ours, theirs_lines=theirs,
                start_line=start + 1, middle_line=middle + 1, end_line=end + 1,
            ))
        i += 1
    return regions
