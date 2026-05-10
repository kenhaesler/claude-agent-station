> **TL;DR** — What each dashboard page is for, and when to open it.

- **Dispatch** (`/`) — the home page. Trigger a run, see live token burn, current throttle state.
- **Mission** (`/mission-control`) — live activity feed across all projects.
- **Fleet** (`/agent-teams`) — visual canvas of the lead and the three teammates for the current run.
- **Queue** (`/queue`) — the work queue: items waiting, in flight, completed.
- **Projects** (`/projects`) — list of repos. Click in to enable/disable, configure, see project-level history.
- **Project Detail** — drill-down for one project: vision, runs, settings.
- **Run Detail** — drill-down for one run: DAG, logs, plan, verdict, diff.
- **Settings** (`/settings`) — auth (OAuth, GitHub App), models, prompts, audit, appearance.
- **Help** (`/help`) — this page.

<!-- under-the-hood -->

- All pages are Svelte 5 components under `dashboard/frontend/src/pages/`.
- Cross-page navigation uses the History API via `lib/router.svelte.ts`. Number keys 1–6 map to the first six tabs.
