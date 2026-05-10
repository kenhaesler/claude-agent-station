"""Tests for prompt assembly."""

from agent.conflict_resolver.prompts import (
    build_resolver_prompt,
    AdvisoryTier,
)


def test_includes_branch_and_base():
    out = build_resolver_prompt(
        branch="feature/foo", base_branch="autonomous/dev",
        conflicted_files=["src/a.ts"], advisory_tiers=set(),
        prior_failure_reason=None,
    )
    assert "feature/foo" in out
    assert "autonomous/dev" in out
    assert "src/a.ts" in out


def test_advisory_large_diff_adds_warning():
    out = build_resolver_prompt(
        branch="b", base_branch="dev", conflicted_files=["a"],
        advisory_tiers={AdvisoryTier.LARGE_DIFF},
        prior_failure_reason=None,
    )
    assert "large" in out.lower()


def test_advisory_stale_pr_adds_divergence_warning():
    out = build_resolver_prompt(
        branch="b", base_branch="dev", conflicted_files=["a"],
        advisory_tiers={AdvisoryTier.STALE_PR},
        prior_failure_reason=None,
    )
    assert "stale" in out.lower() or "diverged" in out.lower()


def test_prior_failure_is_included():
    out = build_resolver_prompt(
        branch="b", base_branch="dev", conflicted_files=["a"],
        advisory_tiers=set(),
        prior_failure_reason="tests failed: TypeError in foo()",
    )
    assert "TypeError" in out


def test_no_prior_failure_omits_section():
    out = build_resolver_prompt(
        branch="b", base_branch="dev", conflicted_files=["a"],
        advisory_tiers=set(), prior_failure_reason=None,
    )
    # Don't emit a "previous attempt" section when there was no previous attempt.
    assert "previous attempt" not in out.lower()
