> **TL;DR** — Every run ends in one of four verdicts. The verdict decides what happens to the branch.

After the manager reviews the teammates' work, the run terminates with exactly one verdict:

| Verdict | What happens to the work |
|---|---|
| **APPROVE** | Branch is pushed and merged into `dev`. |
| **PR** | A pull request is opened against `dev` for human review. |
| **REJECT** | Branch is discarded. The run is marked failed. |
| **SKIP** | Nothing to merge — usually because there was no eligible work for this project. The queue item is marked completed (not failed). |

You'll see verdicts on the Mission Control feed, on Run Detail, and in the run lists across the dashboard.

<!-- under-the-hood -->

- Verdicts are written to the `verdict` column on the `runs` table by `run-manager.sh`.
- The audit log (`audit_log` table) records each tool call the manager makes during review.
- The reasoning behind a verdict is stored in `verdict_detail` and surfaced on Run Detail.
