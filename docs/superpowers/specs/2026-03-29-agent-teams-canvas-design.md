# Agent Teams Canvas — Design Spec

## Context

Claude Agent Station's dashboard needs a dedicated view for the Claude Agent Teams feature. Agent Teams let multiple Claude Code sessions work together — a lead coordinates teammates who communicate directly with each other through a shared task list and mailbox system. The current dashboard has no visualization for this.

The prototype lives at `.superpowers/brainstorm/47-1774801655/content/canvas-workspace-v1.html` and follows the "Soft Sunrise" Vapor design language established in `vapor-improved.html`.

## Design Language

Consistent with the dashboard's established Vapor theme:

- **Palette**: Soft Sunrise — `#FFF5EE` background, `#3D2A1A` primary text, `#2E7D32` success, `#B06030` amber accent, `#D06050` error, `rgba(99,102,180)` peer messaging
- **Cards**: Neumorphic glass — `rgba(255,251,247,0.50)` bg, `backdrop-filter: blur(14px)`, dual box-shadow (light/dark), `border: 1px solid rgba(240,220,200,0.25)`
- **Typography**: Outfit font, 13-16px body text, 700 weight for names/tasks
- **Background**: Identical to dashboard — `#FFF5EE` base, neumorphic ripple field (expanding concentric circles with inset shadows), three warm glows (peach/apricot radial gradients drifting across viewport), subtle film grain overlay
- **Animations**: Breathing card shadows (4s ease-in-out), pulsing status rings (3s), slide-in activity feed, ripple-expand background (5.5-9s cycles)
- **Active states**: Green left-edge bar for active cards, amber for plan-review, dimmed for blocked

## Architecture (from official Claude Agent Teams docs)

| Component      | Role |
|----------------|------|
| **Team Lead**  | Main Claude Code session — creates team, spawns teammates, coordinates work, reviews plans |
| **Teammates**  | Independent Claude Code sessions — each with own context window, can message each other directly |
| **Shared Task List** | Central coordination — tasks with states (pending/in-progress/completed), dependencies, self-claiming with file locks |
| **Mailbox**    | Direct messaging between any agents — the key differentiator from subagents |

Key differentiator: Teammates message each other directly (peer-to-peer), unlike subagents which only report back to the main agent.

## Page Layout

### Navigation
- Same top bar as all dashboard pages: logo, nav pills (Overview, Runs, Queue, Projects, **Agent Teams**, Settings)
- "Agent Teams" pill is active when on this page
- Right side: team name + "Running" badge with session count

### Three-zone layout

```
+------------------------------------------+----------------+
|  TEAM LEAD CARD (full width)             | SHARED TASKS   |
+------------------------------------------+                |
|  TEAMMATE GRID (3 columns x N rows)     |                |
|  +----------+ +----------+ +----------+ |                |
|  | UX       | | Architect| | Critic   | |----------------|
|  |          | |          | |          | | ACTIVITY FEED  |
|  +----------+ +----------+ +----------+ |                |
|  +----------+ +----------+ +----------+ |                |
|  | Security | | Tester   | | + Spawn  | |                |
|  |          | |          | |          | |                |
|  +----------+ +----------+ +----------+ |                |
+------------------------------------------+----------------+
```

### 1. Team Lead Card (top, full width)
- Lead avatar (48px, warm brown gradient with breathing glow)
- Name: "Team Lead" (18px bold)
- Status: "Coordinating N teammates" (14px)
- Current activity in green: "Reviewing Security's plan submission..." (14px)
- Right-aligned meta: Tasks 0/6, Tokens 18.2K, Duration 7m 14s

### 2. Teammate Grid (main area, 3-col responsive)
Each teammate card contains:
- **Header**: Avatar (38px, neumorphic circle with status ring) + name (16px bold) + model badge (12px, e.g. "claude-sonnet-4-6")
- **Task**: Current task name (15px bold)
- **Status**: Live status text (14px, colored — green for working, amber for plan review, grey for blocked)
- **Detail**: Context-specific info (13px) — worktree path, file changes, read-only mode, dependency list
- **Latest message**: Inline message bubble showing most recent peer or lead communication (13px, indigo bg for peer, amber bg for lead)
- **Message chips**: Bottom row of connection chips showing who this teammate is messaging (12px badges — indigo for peer, amber for lead, with directional arrows)

**Card states:**
- `.active` — green left-edge bar, breathing shadow animation
- `.plan-review` — amber left-edge bar, amber breathing animation
- `.blocked` — 50% opacity, no animation
- `+ Spawn teammate` — dashed border placeholder card

### 3. Right Panel (280px sidebar)

**Shared Tasks section:**
- Task items with neumorphic card styling
- Status badges: In Progress (green), Plan Review (amber), Blocked (grey), Pending (light grey)
- Owner assignment: "-> UX", "-> Architect"
- Dependency info in italic: "Depends on: Architecture, Security"

**Activity Feed section:**
- Chronological message log with timestamps
- Peer messages in indigo: "Critic -> Architect: ..."
- Lead messages in amber: "Security -> Lead: plan submitted"
- System events in green: "Lead assigned tasks"
- Fade-in animation on new items

## Data Model Mapping

Maps to existing backend models:

| UI Element | Backend Source |
|-----------|---------------|
| Team name | `Run.team_name` |
| Teammates | `Run.team_members` (JSON array) |
| Tasks | `CoordinatorTask` table |
| Task status | `CoordinatorTask.status` (pending/ready/running/completed/failed/blocked) |
| Dependencies | `CoordinatorTask.depends_on` (JSON array) |
| Messages | `CoordinatorMessage` table (direction: to_employee/from_monitor) |
| Activity feed | Webhook events + CoordinatorMessages |
| File changes | `CoordinatorTask.touched_files` |

## Real-time Updates

- SSE connection for live updates (already exists in dashboard)
- Teammate status changes update card state
- New messages append to activity feed with slide-in animation
- Task status changes update sidebar badges
- Card breathing animation intensity tied to agent activity

## Interaction

- Hover teammate cards for subtle lift effect
- Cards are clickable — navigate to full teammate session detail (future)
- Task items in sidebar could link to task detail views (future)
- "+ Spawn teammate" card is actionable via API

## Prototype Files

| File | Purpose |
|------|---------|
| `vapor-improved.html` | Dashboard overview — Soft Sunrise palette reference |
| `canvas-workspace-v1.html` | Agent Teams Canvas — this spec's prototype |

Both share the same nav structure, color palette, and neumorphic design language.
