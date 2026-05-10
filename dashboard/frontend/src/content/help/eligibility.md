> **TL;DR** — Issues are skipped if the repo isn't enabled, the issue is closed, or it carries a skip label.

The lead picks GitHub issues that pass these filters:

- The repository is enabled in the dashboard.
- The issue is open.
- The issue is **not** labelled with any of the skip set:
  - `autonomous-agent/in-progress` — already being worked on.
  - `autonomous-agent/needs-help` — flagged for human follow-up.
  - `NO AI` — explicit human-only marker.
  - `backlog` — explicitly out of scope. **The agent will never pick up a backlog issue.**
  - `wontfix` — closed by intent, even if open.
  - `vision-suggested` — produced by the vision pipeline, not yet promoted.

Eligible issues are then decomposed into tasks and routed across the three teammates by specialty.

<!-- under-the-hood -->

- The skip set is the `SKIP_LABELS` constant in `agent/station_orchestrator.py`.
- The "enabled repo" flag is the `enabled` column on the `projects` table.
- A skipped issue is recorded in the run log with the reason it was skipped.
