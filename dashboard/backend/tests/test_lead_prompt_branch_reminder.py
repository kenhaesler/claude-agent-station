"""Tests for the lead's per-teammate spawn-prompt branch reminder.

Background: ``agent/agents/issue-worker.md`` Step 4 already mandates
``git checkout -b autonomous/issue-<n>`` before any commit. That file
is the teammate's *system prompt*; under load the model can drift past
it if no other signal reinforces the rule.

Run-20260521T203532Z confirmed the drift: all three teammates
committed straight on ``worktree/<role>-20260521`` despite the
issue-worker prompt mandate, and the manager (correctly) hard-rejected
every verdict with ``BRANCH_ISOLATION_LEAK``. A stream-log grep showed
the lead's per-spawn user prompt never mentioned branches at all.

This test pins the new contract: the lead's spawn-prompt template in
``build_team_prompt`` must instruct the lead to include a branch
reminder in every teammate spawn prompt, so the rule appears in *both*
the agent system prompt and the per-spawn user prompt.
"""

from __future__ import annotations


def test_lead_prompt_includes_branch_hygiene_paragraph_for_teammates():
    """The lead's spawn-prompt template must carry a branch-hygiene
    paragraph that names the worktree branch pattern, the recovery
    path, and the BRANCH_ISOLATION_LEAK rejection reason."""
    from agent.station_orchestrator import build_team_prompt

    prompt = build_team_prompt(
        repo="owner/repo",
        issues=[{"number": 1, "title": "t", "body": ""}],
        config={"projects": []},
        run_id="20260521T203532Z",
        workspace="/ws",
        worktree_paths={
            "backend": "/ws-backend",
            "frontend": "/ws-frontend",
            "qa": "/ws-qa",
        },
        review_package_path="/log/r.md",
        verdicts_file_path="/log/v.json",
        manager_max_turns=30,
    )

    # The paragraph must come inside the spawn-prompt block the lead
    # passes to each teammate. The block opens with "When spawning a
    # teammate, include in their prompt:".
    open_marker = "When spawning a teammate, include in their prompt:"
    assert open_marker in prompt
    block_start = prompt.index(open_marker)
    # The block extends through to the next top-level heading.
    block_end = prompt.find("\n## ", block_start)
    block = prompt[block_start:block_end if block_end > 0 else len(prompt)]

    # Required content of the reinforcement paragraph.
    assert "BRANCH HYGIENE" in block, (
        "spawn-prompt template missing the BRANCH HYGIENE reinforcement"
    )
    assert "worktree/" in block, (
        "spawn-prompt template must name the worktree branch pattern"
    )
    assert "git checkout -b" in block, (
        "spawn-prompt template must show the recovery command"
    )
    assert "BRANCH_ISOLATION_LEAK" in block, (
        "spawn-prompt template must name the manager's rejection reason "
        "so teammates know the consequence"
    )
    # And the rule must appear BEFORE the QA-specific paragraph so the
    # ordering reflects priority — branch hygiene applies to every
    # teammate, the QA rules are role-specific.
    bh_idx = block.find("BRANCH HYGIENE")
    qa_idx = block.find("For the QA teammate")
    assert bh_idx != -1 and qa_idx != -1
    assert bh_idx < qa_idx, (
        "branch-hygiene paragraph must precede the QA-specific paragraph "
        "in the spawn-prompt template"
    )


def test_lead_prompt_branch_reminder_offers_both_naming_conventions():
    """The reminder shows ``autonomous/issue-<n>`` and mentions the
    ``feature/issue-<n>-<slug>`` alternative so teammates pick whatever
    the project's CLAUDE.md prefers."""
    from agent.station_orchestrator import build_team_prompt

    prompt = build_team_prompt(
        repo="owner/repo",
        issues=[{"number": 1, "title": "t", "body": ""}],
        config={"projects": []},
        run_id="r",
        workspace="/ws",
        worktree_paths={"backend": "/x"},
        review_package_path="/r",
        verdicts_file_path="/v",
        manager_max_turns=30,
    )

    assert "autonomous/issue-<number>" in prompt
    assert "feature/issue-<number>" in prompt
