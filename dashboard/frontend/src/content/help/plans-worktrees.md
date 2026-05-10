> **TL;DR** — Each teammate writes a plan first, gets it approved by the lead, then works in its own git worktree.

Two protections keep concurrent teammates from clobbering each other:

**Plans.** Every teammate's first deliverable is a short implementation plan. The lead reviews each plan before the teammate is allowed to start writing code. If two plans conflict — e.g. both teammates want to modify the same file — the lead asks the affected teammate to revise.

**Worktrees.** Each teammate runs in its own git worktree at `<workspaces_dir>/<repo>-<role>` (e.g. `/home/claude-agent/workspaces/my-repo-backend`). File edits from one teammate are invisible to the others until the changes are committed and merged. Worktrees are created per run and torn down when the run ends.

<!-- under-the-hood -->

- Workspaces directory is configurable; default `/home/claude-agent/workspaces/`.
- Worktree paths follow `<workspaces_dir>/<repo-name>-<role>`. Roles are `backend`, `frontend`, `qa`.
- The lead's plan-review behaviour is encoded in the orchestrator and the lead prompt.
- Plans are stored in the `plans` table and surfaced on Run Detail.
