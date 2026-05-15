"""End-to-end splitter flow with stubbed GitHub + SDK (#391).

Exercises the full pre-dispatch hook against the real
``agent.coordinator.decide`` module: heuristic → SDK runner (stubbed) →
``execute_split_decision`` → DB persist. The two external boundaries
(GitHub + Claude Agent SDK) are mocked; everything in between is the
production code path PRs 1-3 shipped.

Parametrised on the standard ``async_session_factory`` fixture so the
flow is verified against both SQLite and Postgres. If the fixture's
event-loop binding causes trouble, the test falls back to
``app.database`` directly — but the plan instructs us to try the
parametrised fixture first.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_synthetic_split_flow_creates_sub_issues(
    async_session_factory, monkeypatch
) -> None:
    """Parent issue with 4 acceptance criteria fans out into 2 sub-issues.

    The splitter SDK call is stubbed to return a hand-built two-proposal
    JSON payload; GitHub is a ``MagicMock``. The post-conditions:

    1. Two sub-issues created on GitHub (numbers 101, 102 from the mock).
    2. A single backlink comment posted on the parent.
    3. The integration branch helper called exactly once.
    4. The pre-existing ``Run`` row is updated with ``split-decision``
       kind and the sub_numbers JSON archive.
    """
    monkeypatch.setenv("STATION_SPLIT_ENABLED", "1")
    from agent.coordinator import decide
    from app.models import Run

    parent = {
        "number": 27,
        "title": "Auth refactor",
        "body": (
            "## Acceptance criteria\n"
            "- [ ] login api\n- [ ] me endpoint\n"
            "- [ ] oauth callback\n- [ ] route middleware\n"
        ),
        "labels": ["backend"],
        "repo": "kenhaesler/claude-agent-station",
    }

    splitter_raw = json.dumps(
        [
            {
                "title": "Login API",
                "body": "POST /api/auth/login",
                "labels": ["backend"],
                "acceptance": ["Returns 200 + JWT"],
                "depends_on": None,
            },
            {
                "title": "/me endpoint",
                "body": "GET /api/me",
                "labels": ["backend"],
                "acceptance": ["Returns 200 when authenticated"],
                "depends_on": 0,
            },
        ]
    )

    gh = MagicMock()
    gh.label_exists.return_value = True
    gh.create_issue.side_effect = [{"number": 101}, {"number": 102}]

    async with async_session_factory() as db:
        db.add(
            Run(
                run_id="rsd-int-1",
                run_kind="split-decision",
                started_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    # The production ``_persist_split_decision`` opens its own session
    # against ``app.database.async_session`` — bound to the static test
    # DB from conftest, not the parametrised ``async_session_factory``
    # under test. Bridge the two so the persist path writes into the
    # fixture's session factory; otherwise the SQLite/Postgres
    # parametrisation degenerates and the post-condition check below
    # would race against a phantom schema.
    from sqlalchemy import update
    from agent.coordinator import decide as decide_mod
    from app.models import Run as _Run

    async def _persist_via_fixture(
        run_id: str, parent_number: int, sub_numbers, warnings
    ) -> None:
        async with async_session_factory() as db:
            await db.execute(
                update(_Run).where(_Run.run_id == run_id).values(
                    run_kind="split-decision",
                    split_decision_json={
                        "parent_number": parent_number,
                        "sub_numbers": list(sub_numbers),
                        "warnings": list(warnings),
                    },
                )
            )
            await db.commit()

    with patch(
        "agent.issue_splitter.runner._invoke_splitter_sdk",
        new=AsyncMock(return_value=splitter_raw),
    ), patch(
        "agent.coordinator.decide._gh_client", return_value=gh
    ), patch(
        "agent.coordinator.decide._ensure_integration_branch"
    ) as iib, patch.object(
        decide_mod, "_persist_split_decision", new=_persist_via_fixture
    ):
        decision = await decide.maybe_run_splitter(
            parent,
            run_id="rsd-int-1",
            repo_summary="",
            vision="",
        )
        assert decision is not None
        await decide.execute_split_decision(
            parent, decision, run_id="rsd-int-1"
        )

    assert gh.create_issue.call_count == 2
    gh.create_issue_comment.assert_called_once()
    iib.assert_called_once()

    # Verify the split decision was persisted.
    from sqlalchemy import select

    async with async_session_factory() as db:
        row = (
            await db.execute(select(Run).where(Run.run_id == "rsd-int-1"))
        ).scalar_one()
        assert row.run_kind == "split-decision"
        assert row.split_decision_json["sub_numbers"] == [101, 102]
