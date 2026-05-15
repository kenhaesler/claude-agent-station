"""RunComplete aggregation fields (#391, gated on #385)."""
from __future__ import annotations


def test_run_complete_schema_has_sub_runs_field() -> None:
    """``RunComplete`` must surface the splitter's parent/child linkage.

    The lead agent calls ``RunComplete`` once per run; for split-decision
    parent runs it lists the spawned sub-run IDs, and for sub-runs it
    points back at the parent. Both fields default to "empty" so existing
    single-issue runs (the common case) don't need to populate them.
    """
    from agent.tools.run_complete import RunCompleteInput  # delivered by #385

    fields = RunCompleteInput.model_fields  # pydantic v2
    assert "sub_runs" in fields
    assert "parent_run" in fields


def test_run_complete_accepts_split_payload() -> None:
    """Parent split-decision payload validates end-to-end."""
    from agent.tools.run_complete import RunCompleteInput

    payload = RunCompleteInput.model_validate({
        "status": "success",
        "summary": "split decomposed",
        "sub_runs": ["run-sub-a", "run-sub-b"],
        "parent_run": None,
    })
    assert payload.sub_runs == ["run-sub-a", "run-sub-b"]
    assert payload.parent_run is None


def test_run_complete_accepts_subrun_payload() -> None:
    """Sub-run payload references its parent and leaves ``sub_runs`` empty."""
    from agent.tools.run_complete import RunCompleteInput

    payload = RunCompleteInput.model_validate({
        "status": "success",
        "summary": "implemented sub-issue",
        "parent_run": "run-parent-1",
    })
    assert payload.sub_runs == []
    assert payload.parent_run == "run-parent-1"
