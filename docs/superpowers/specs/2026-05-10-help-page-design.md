# Help Page Design

**Date:** 2026-05-10
**Status:** Draft — pending user review

## Problem

Claude Agent Station has grown enough surface area (8 dashboard pages, three agent roles, four verdicts, plan-review gating, worktree isolation, plan-tier throttling, audit log, vision pipeline) that the operator — including the project owner — cannot keep the model fully in their head. There is no in-app explanation of how a run flows from trigger to verdict, what each dashboard page is for, or why an issue was skipped. `docs/concepts.md` and `docs/architecture.md` cover the system in prose, but they live outside the dashboard and are not surfaced where a user is looking at a verdict badge or a throttled state.

This page must serve three audiences with one source of truth: the operator (the project owner), future contributors, and end users running their own station.

## Goals

- One canonical, in-dashboard explanation of how the station works.
- Contextual access from the conceptually loaded UI elements (verdict badges, role labels, plan UI, throttled / skipped indicators) — without leaving the current page.
- Layered depth so an end user gets the gist and a contributor can drill into code paths.
- A run-lifecycle flowchart that makes the trigger-to-verdict path legible at a glance.

## Non-goals

- Replacing `docs/concepts.md` or `docs/architecture.md`. Those remain authoritative for code-level details; the Help page reuses content from them but lives in the UI.
- Per-page contextual tutorials beyond the Page-by-page tour section.
- Onboarding wizards, search, or multi-language support.
- Embedding the system architecture diagram (it is already documented in `docs/architecture.md` and is not what the user needs in-dashboard).
- Vision pipeline coverage. Vision is a separate flow and is out of scope for v1.

## Audience and depth model

The page serves all three audiences via a layered structure within each section:

| Layer | Audience | Visibility |
|---|---|---|
| **TL;DR** | End user | Always visible — one sentence at the top of each section |
| **How it works** | Operator | Always visible — short paragraphs of plain prose |
| **Under the hood** | Contributor | Collapsed `<details>` accordion, opt-in |

## Page structure

A new top-level page at `/help`:

- Registered in `dashboard/frontend/src/lib/router.svelte.ts` as `Page = 'help'`, route `/help`, optional `param` for a section anchor.
- Added to the top nav in `Shell.svelte` (or wherever the nav lives) as the 7th item, after Settings.
- No new keyboard shortcut. Number keys 1–6 retain their existing bindings; Help is mouse-or-link only.
- Layout reuses `Shell.svelte`. Body is a two-column layout: sticky left sidebar with anchor links to all sections; main content column. On narrow widths the sidebar collapses into a top dropdown, matching existing responsive patterns in the dashboard.
- Each section has a stable URL anchor (`/help#run-lifecycle`, `/help#verdicts`, …) so contextual `?` drawers and external links can deep-link in.

## Sections

In order, with their anchors:

1. **Run lifecycle** — `#run-lifecycle` *(contains the flowchart)*
2. **The three roles** — `#roles`
3. **Verdicts** — `#verdicts`
4. **Issue eligibility** — `#eligibility`
5. **Plan-tier throttling** — `#throttling`
6. **Plans & worktrees** — `#plans-worktrees`
7. **Page-by-page tour** — `#pages-tour`
8. **Troubleshooting** — `#troubleshooting`

### Per-section content briefs

| Section | TL;DR | How it works covers | Under the hood covers |
|---|---|---|---|
| Run lifecycle | A run takes a GitHub issue from picked-up to merged or PR'd, in 7 phases. | Walks the flowchart in prose; what each phase produces. | Trigger sources (systemd timer, webhook intake); orchestrator entrypoint (`agent/station_orchestrator.py`); manager review pass in `agent/scripts/run-manager.sh`. |
| The three roles | Lead coordinates, three teammates implement, manager reviews. | Who runs which model and decides what; one paragraph per role. | Agent definitions in `agent/agents/`, prompts in `agent/prompts/`, model overrides in config. |
| Verdicts | Every run ends in APPROVE, PR, REJECT, or SKIP — that decides what happens to the branch. | Cheat-sheet table mapping each verdict to its branch action and when to expect it. | Where verdicts get written (DB column, audit log), how `run-manager.sh` enforces them. |
| Issue eligibility | Issues are skipped if the repo isn't enabled, the issue is closed, or it carries a skip label. | The full filter rules in plain English; the skip-label set with one-line rationale per label; the `backlog` rule. | Source of truth: `SKIP_LABELS` in `agent/station_orchestrator.py`. |
| Plan-tier throttling | When weekly Claude usage gets too high, runs are paused before they start so we don't run out of budget mid-task. | The threshold concept, the per-model fallback chain (Opus → Sonnet → Haiku), what the throttled UI state means. | `plan_usage_history` table; `agent/scripts/detect_plan_usage.py`; throttle decision API. |
| Plans & worktrees | Each teammate writes a plan first, gets it approved by the lead, then works in its own git worktree. | Why plans exist (conflict prevention); why worktrees (isolation); life of a plan. | Worktree path scheme (`<workspaces_dir>/<repo>-<role>`); plan storage; lead's plan-review prompt. |
| Page-by-page tour | What each dashboard page is for, and when to open it. | One short paragraph per page: Command Center, Mission Control, Agent Teams, Run Detail, Queue, Projects, Project Detail, Settings, Help. | Notable cross-page navigation patterns. |
| Troubleshooting | Common "why isn't it doing what I expected?" answers. | Q&A list: why no run started, why an issue was skipped, why a verdict was REJECT, where logs live, how to pause/resume. | Pointers to the audit log, log files, run control API. |

V1 prose is authored by the implementer, sourced from `docs/concepts.md`, `docs/architecture.md`, and the code. The user edits afterward.

## Content source and rendering

Content lives in **one place** so the full Help page and the contextual drawer share a single source of truth.

- New directory `dashboard/frontend/src/content/help/` holds one markdown file per section: `run-lifecycle.md`, `roles.md`, `verdicts.md`, `eligibility.md`, `throttling.md`, `plans-worktrees.md`, `pages-tour.md`, `troubleshooting.md`.
- Each file follows this structure:
  ```markdown
  > **TL;DR** — one sentence.

  How-it-works prose, paragraphs and tables as needed.

  <!-- under-the-hood -->

  Deep-dive content with code/file references.
  ```
- A new component `dashboard/frontend/src/components/help/HelpSection.svelte` accepts an `id` prop, imports the matching markdown file via Vite `?raw`, splits on the `<!-- under-the-hood -->` marker, and renders:
  - The TL;DR blockquote as a styled callout.
  - The "How it works" body via the markdown renderer.
  - The "Under the hood" body inside a `<details>` accordion (closed by default).
- A markdown renderer is added if the project does not already have one. Preferred: `marked` (smaller, simpler) unless an existing renderer is already in use elsewhere in the frontend, in which case reuse it.
- The `/help` page composes all 8 `<HelpSection>` instances in section order; each is wrapped in a `<section id="…">` so anchors work.
- The drawer renders one `<HelpSection>`.

### Mermaid

- Add `mermaid` (npm). Lazy-loaded only on `/help` and on first drawer open of a Mermaid-bearing section.
- A new component `dashboard/frontend/src/components/help/MermaidDiagram.svelte` accepts a Mermaid source string, calls `mermaid.render()` on mount, and inserts the resulting SVG.
- `HelpSection.svelte` detects fenced ` ```mermaid ` blocks and substitutes `<MermaidDiagram>` for them.
- Mermaid theme variables are configured to match the dashboard palette (cyan/void Tailwind tokens), set once at module load.

## Run-lifecycle flowchart

The flowchart lives in `run-lifecycle.md` as a fenced ` ```mermaid ` block:

```
flowchart TD
    Trigger["systemd timer<br/>or webhook"]
    Throttle{"Plan tier<br/>throttled?"}
    Halt["Run skipped<br/>no work started"]
    Eligible["Lead fetches<br/>eligible issues"]
    NoneEligible{"Any eligible<br/>issues?"}
    Decompose["Lead decomposes issues<br/>into tasks (by specialty)"]
    Spawn["Spawn 3 teammates:<br/>backend / frontend / qa<br/>(each in own worktree)"]
    Plans["Each teammate writes<br/>an implementation plan"]
    Review{"Lead reviews plans<br/>conflicts?"}
    Implement["Teammates implement,<br/>test, commit locally"]
    Manager["Manager reviews<br/>all completed work"]
    Verdict{"Verdict"}
    Approve["APPROVE<br/>push & merge to dev"]
    PR["PR<br/>open against dev"]
    Reject["REJECT<br/>discard branch"]
    Skip["SKIP<br/>no eligible work"]

    Trigger --> Throttle
    Throttle -->|yes| Halt
    Throttle -->|no| Eligible
    Eligible --> NoneEligible
    NoneEligible -->|no| Skip
    NoneEligible -->|yes| Decompose
    Decompose --> Spawn
    Spawn --> Plans
    Plans --> Review
    Review -->|conflict| Plans
    Review -->|approved| Implement
    Implement --> Manager
    Manager --> Verdict
    Verdict --> Approve
    Verdict --> PR
    Verdict --> Reject
    Verdict --> Skip

    click Throttle call openHelpDrawer("throttling")
    click Eligible call openHelpDrawer("eligibility")
    click NoneEligible call openHelpDrawer("eligibility")
    click Spawn call openHelpDrawer("roles")
    click Plans call openHelpDrawer("plans-worktrees")
    click Review call openHelpDrawer("plans-worktrees")
    click Manager call openHelpDrawer("roles")
    click Verdict call openHelpDrawer("verdicts")
    click Approve call openHelpDrawer("verdicts")
    click PR call openHelpDrawer("verdicts")
    click Reject call openHelpDrawer("verdicts")
    click Skip call openHelpDrawer("verdicts")
```

`MermaidDiagram.svelte` exposes `openHelpDrawer` on `window` (or via a Mermaid `securityLevel: 'loose'` callback registry) so node clicks reach the drawer store.

**Deliberate simplifications:**

- Three teammates run concurrently but are drawn as one "Spawn 3 teammates" node, not three lanes — chosen for legibility. A second, more detailed diagram may be added under "Plans & worktrees" later.
- The plan-review loop is shown as a single backedge.
- Audit log writes are not on the chart.
- Vision pipeline is not on the chart.

## Contextual `?` drawer

A reusable Svelte component `dashboard/frontend/src/components/help/HelpHint.svelte`:

- Props: `section: string` — the help section anchor to open.
- Renders a small (~12–14 px) `?` icon, low contrast at rest, slightly brighter on hover. Reuses the existing icon system (Lucide or whatever the dashboard uses; check `components/ui/`).
- Sits *next to* the labelled element, not inside it, so it does not break existing badge/label layouts.
- On click, calls `openHelpDrawer(section)` against the drawer store.

### Drawer store

`dashboard/frontend/src/lib/help-drawer.svelte.ts` — a small svelte rune store:

```ts
export const helpDrawer = $state<{ openSection: string | null }>({ openSection: null });
export function openHelpDrawer(section: string) { helpDrawer.openSection = section; }
export function closeHelpDrawer() { helpDrawer.openSection = null; }
```

### Drawer component

`dashboard/frontend/src/components/help/HelpDrawer.svelte`, mounted once at the app root (sibling of `Shell` or inside it):

- Watches `helpDrawer.openSection`.
- Slides in from the right, ~480 px wide on desktop, full width on mobile.
- Translucent backdrop closes the drawer on click. `Esc` also closes.
- Header: section title + close button.
- Body: a single `<HelpSection id={openSection} />`.
- Footer: a "View full Help page →" link that closes the drawer and navigates to `/help#<openSection>`.
- Only one drawer open at a time; opening a different `?` swaps content (no animation between).

### `?` icon placements (v1)

| Location | Component | Section linked |
|---|---|---|
| Verdict badges in run lists and Run Detail | `dashboard/frontend/src/components/badges/Verdict*.svelte` (or current location) | `verdicts` |
| Role labels (`backend` / `frontend` / `qa` / `lead`) on Agent Teams Canvas | `pages/AgentTeamsCanvas.svelte` (header & legend area) | `roles` |
| Role labels on Run Detail | `pages/RunDetail.svelte` | `roles` |
| Plan-related blocks on Run Detail (the "Plan reviewed" / "Plan approved" UI) | `pages/RunDetail.svelte` | `plans-worktrees` |
| Throttled / plan-tier indicator on Command Center | `pages/CommandCenter.svelte` | `throttling` |
| Skipped-verdict and skip-label callouts wherever they appear | wherever surfaced (audit, run list) | `eligibility` |

The implementer scans the existing components for these elements and adds `<HelpHint>` next to each. A short file-by-file checklist is part of the implementation plan.

## File and component summary

New files:

- `dashboard/frontend/src/content/help/{run-lifecycle,roles,verdicts,eligibility,throttling,plans-worktrees,pages-tour,troubleshooting}.md`
- `dashboard/frontend/src/components/help/HelpSection.svelte`
- `dashboard/frontend/src/components/help/HelpHint.svelte`
- `dashboard/frontend/src/components/help/HelpDrawer.svelte`
- `dashboard/frontend/src/components/help/MermaidDiagram.svelte`
- `dashboard/frontend/src/lib/help-drawer.svelte.ts`
- `dashboard/frontend/src/pages/HelpPage.svelte`

Modified files:

- `dashboard/frontend/src/lib/router.svelte.ts` — add `'help'` to `Page`, route `/help` with optional anchor param.
- `dashboard/frontend/src/App.svelte` — register the route, mount `<HelpDrawer>` once at the app root.
- `dashboard/frontend/src/components/layout/Shell.svelte` (or the TopNav component it imports) — add Help nav item.
- The components listed in the `?` icon placement table — each gains a `<HelpHint>` next to the relevant label.
- `package.json` — add `mermaid`, and `marked` if no markdown renderer exists yet.

## Behaviour and edge cases

- `/help` with no anchor scrolls to the top.
- `/help#<unknown>` falls back to scrolling to top; no error.
- Opening the drawer with an unknown section logs a console warning (dev) and renders a fallback "Section not found" body. Should not happen in practice — `?` icons reference a closed set of section IDs.
- Mermaid render failure (parser error) renders a fallback `<pre>` with the raw Mermaid source so the diagram is still readable.
- The drawer traps focus while open and restores focus to the trigger `?` icon on close. Close button is keyboard-reachable.
- The `?` icon has `aria-label="Help: <section title>"`.
- The drawer is announced to screen readers (`role="dialog"`, `aria-modal="true"`, `aria-labelledby` on the header).

## Testing

- **Unit (Vitest):**
  - `HelpSection` correctly splits on the `<!-- under-the-hood -->` marker; renders both layers; renders only TL;DR + how-it-works when the marker is absent.
  - Mermaid block detection in `HelpSection` swaps fenced blocks for `<MermaidDiagram>`.
  - Drawer store: `openHelpDrawer`, `closeHelpDrawer`, swapping sections.
- **Component snapshot:** drawer markup with a representative section.
- **Manual browser verification (mandatory before claiming done):**
  - `/help` renders all 8 sections, sidebar anchors scroll correctly.
  - The flowchart renders; clicking each labelled node opens the drawer with the right section.
  - `<HelpHint>` placements: verify each entry in the placement table opens the right drawer section.
  - "View full Help page →" link from the drawer navigates to `/help#<section>` and the section is in view.
  - Esc and backdrop click close the drawer.
  - Narrow viewport: sidebar collapses into the top dropdown.

## Out of scope (v1)

- Search across the Help content.
- Per-page contextual `?` icons in nav tabs and page headers.
- Hover tooltips with TL;DR previews (would be option 3 from brainstorming).
- A second, parallel-lanes flowchart for the worktree-isolation story.
- Vision pipeline section.
- Plan-tier throttling implementation changes — the section only documents existing behaviour.

## Open questions

None — all decisions resolved during brainstorming.
