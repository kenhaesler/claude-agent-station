> **TL;DR** — Common "why isn't it doing what I expected?" answers.

**No run started after the timer fired.**
Check the throttle state on Dispatch. If weekly usage is high, runs are deliberately paused. See Plan-tier throttling.

**My issue was skipped.**
Check the issue's labels. Anything in the skip set (`backlog`, `wontfix`, `NO AI`, …) blocks it. Also check the project is enabled. See Issue eligibility.

**The verdict was REJECT.**
The manager judged the work incomplete or wrong. Open Run Detail and read `verdict_detail` for the reasoning.

**The verdict was SKIP and nothing happened.**
Usually means there was no eligible work — common after the queue empties. Not a failure.

**A teammate looks stuck.**
Open Run Detail and check the audit log. Long-running tool calls (build, test) are normal; an unmoving log for many minutes likely means a hung subprocess.

**Where are the logs?**
Agent logs live at `/var/log/claude-agent/`. The dashboard surfaces them via WebSocket on Run Detail.

**How do I pause everything?**
The "Stop" button in the top nav engages a global pause when there are active runs.

<!-- under-the-hood -->

- Audit log: the `audit_log` table records every tool call. Retention defaults to 30 days.
- Run control: pause/resume/stop is implemented in `agent/run_control.py` and exposed via the dashboard API.
- Live logs: WebSocket served by `dashboard/backend/app/routers/logs.py`.
