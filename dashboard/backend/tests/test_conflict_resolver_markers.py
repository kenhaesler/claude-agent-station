"""Tests for the git-conflict-marker parser."""

from agent.conflict_resolver.markers import (
    ConflictRegion,
    parse_conflict_markers,
    file_has_conflicts,
    LOCKFILE_NAMES,
)


def test_no_markers_returns_empty():
    assert parse_conflict_markers("plain content\nwith no markers\n") == []


def test_single_region_returns_one():
    src = """before
<<<<<<< HEAD
ours
=======
theirs
>>>>>>> base
after
"""
    regions = parse_conflict_markers(src)
    assert len(regions) == 1
    r = regions[0]
    assert isinstance(r, ConflictRegion)
    assert r.ours_lines == ["ours"]
    assert r.theirs_lines == ["theirs"]


def test_two_regions_returned_in_order():
    src = """top
<<<<<<< HEAD
a
=======
b
>>>>>>> base
middle
<<<<<<< HEAD
c
=======
d
>>>>>>> base
bottom
"""
    regions = parse_conflict_markers(src)
    assert len(regions) == 2
    assert regions[0].ours_lines == ["a"]
    assert regions[1].theirs_lines == ["d"]


def test_file_has_conflicts_detects_markers():
    assert file_has_conflicts("<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> base\n") is True
    assert file_has_conflicts("no markers\n") is False


def test_lockfile_names_includes_common_managers():
    # Used by the lockfile-only-conflict predicate.
    assert "package-lock.json" in LOCKFILE_NAMES
    assert "yarn.lock" in LOCKFILE_NAMES
    assert "pnpm-lock.yaml" in LOCKFILE_NAMES
    assert "Cargo.lock" in LOCKFILE_NAMES
