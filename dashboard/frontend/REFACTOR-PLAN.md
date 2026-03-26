# Full Frontend Refactor: Next-Gen Agentic Collaborative Workspace

## Context

Claude Agent Station's backend is a **multi-agent orchestration control plane** with 23 routers, 70+ endpoints, SSE streaming, WebSocket log tailing, coordinator task DAGs, a 14-state queue, and intelligence/learning loops. The current frontend (59 components, 10 pages) treats this as a traditional dashboard with separate pages for monitoring, configuration, and interaction. This refactor reimagines the frontend as a **collaborative workspace where humans and AI agents work together as a team** — the user is a team lead, not a monitor operator.

---

## Part 1: The Next-Gen Vision (Designed from Backend Capabilities)

### Core Metaphor: The Operations Floor

Not a dashboard. Not a monitoring tool. A **collaborative workspace** where the user stands at the center and autonomous agents work around them. They can see every agent's desk, watch their reasoning, hear agent-to-agent communication, and intervene by walking over and talking.

### 5 Design Principles

1. **Spatial, not paginated** — One continuous workspace with zones, not separate pages behind clicks
2. **Dialogue, not logging** — Human-agent interaction is a conversation, not a log file
3. **Progressive attention** — Calm when idle, vivid when active, urgent when decisions needed
4. **Time is first-class** — Every view has a timeline; scrub backward at any point
5. **Agent identity is persistent** — Agents are characters with names, colors, and history

### The Three-Zone Layout

```
+-------------------------------------------------------------------+
|  StatusBar (connection, health, search Cmd+K, trigger, theme)      |
+------+-----------------------------------+------------------------+
|      |                                   |                        |
| Zone |       Zone B: Main Canvas         |  Zone C: Context       |
|  A   |       (The Floor)                 |  Panel (persistent)    |
|      |                                   |                        |
|  Nav |   Mode 1: Operations Floor        |  C1: Conversation      |
|  +   |   Mode 2: Work Stream             |  C2: Code Activity     |
| Quick|   Mode 3: Queue Board             |  C3: Intelligence      |
| Jump |   Mode 4: Strategy Room           |  C4: Diff Review       |
|      |   Mode 5: Analytics               |                        |
|      |                                   |  Guidance Composer      |
|      |                                   |  (always at bottom)    |
+------+-----------------------------------+------------------------+
|  Timeline Strip (always visible, scrub/replay)                     |
+-------------------------------------------------------------------+
```

- **Zone A** (56px): NavRail with live badges (active agents, pending queue, draft decisions)
- **Zone B** (flex center): The primary workspace, content driven by nav selection
- **Zone C** (400px right): **Always-visible** context panel — conversation, code, diffs, guidance input
- **Timeline Strip** (40px bottom): Activity density heatmap, phase markers, scrub control, LIVE indicator

### Zone B Modes

#### Mode 1: Operations Floor (replaces PulsePage + AgentObservatory)

The nerve center. Three sub-zones:

**Team Board** (top 50%): Spatial layout of agents as "desks"
- Manager desk always centered (persistent identity)
- Employee desks spawn around Manager when created
- Each desk: avatar, name, current action (tool call visible), turn count, mini-progress ring
- Thinking = pulsing glow. Tool use = floating tool name label
- **Communication lines**: When coordinator sends message to employee, an animated "message packet" travels along the connecting line between desks
- **Conflict visualization**: When two employees touch the same file, both desks flash amber and a conflict icon appears on the connection

**Task DAG** (middle, expandable): Interactive dependency graph
- Nodes color-coded by status with smooth CSS transitions
- Click a node → loads that task's context in Zone C
- Animated flow lines (not static arrows) showing dependency direction
- Completion ripple animation on task finish
- During multi-employee runs, shows which employee owns which task node

**Decision Queue + Problem Tray** (bottom, collapsible):
- Pending plans appear as inline cards with approve/reject/review actions
- "Review" loads the diff into Zone C without navigating away
- Pending decisions get a pulsing border + "Attention Requested" banner after configurable timeout
- Failed/escalated/paused items appear at top with retry/investigate/dismiss actions

#### Mode 2: Work Stream (replaces WorkStreamPage)
- Chronological run list — compact rows: status dot, project, issue, verdict badge, mini phase-timeline, tokens, duration
- Active runs at top with live badge and real-time mini-timeline updates
- Click a run → Zone C loads full run context (employee report, diff, logs)
- **No queue or backpressure here** (that's Mode 3)

#### Mode 3: Queue Board (NEW — extracted from WorkStreamPage)
- **Kanban view** (default): Columns for Pending | In Progress | Review | Completed | Failed+Escalated
- Cards: issue title, project, priority badge, complexity score, mode, escalation rung
- Drag-to-transition (API validates state machine)
- **List view** (toggle): Sortable/filterable table
- Stats bar: counts by state, avg completion time, BackpressureGauge
- Create item button, bulk pause, purge old

#### Mode 4: Strategy Room (replaces ConfigPage + BrainstormPage)
- **Brainstorm**: Streaming AI chat with persona tabs (architect/security/performance/devops) — conversation renders in Zone C
- **Projects**: Card grid with CRUD, per-project drill-down (runs, queue, plans)
- **Settings**: 9 focused tabs (General, Models, Limits, Intelligence, Notifications, Prompts, Auth, Integration, Sprint)

#### Mode 5: Analytics
- 8 chart types: token trend (line), run frequency (stacked bar), verdict donut, project tokens (horizontal bar), success rate heatmap (mode x model), confidence calibration (scatter), escalation funnel, token efficiency (grouped bar)
- Time range selector (7/14/30/90d), project filter
- Intelligence insights integrated (not a separate page)

### Zone C: The Context Panel (The Key Innovation)

This is **always visible on desktop** (not a slide-out). It has 4 modes + the guidance composer:

#### C1: Conversation View (default when agents active)
**This is the biggest change from the current UI.** Replace the flat reverse-chronological log feed with a structured dialogue:

- **Message bubbles** with agent avatar + name + timestamp
- **Tool calls** as compact code blocks: `> Read: src/lib/api.ts (1-50)` with expand arrow
- **Thinking** as collapsible dimmed italic blocks (collapsed by default)
- **Human guidance** as right-aligned blue bubbles (like iMessage/WhatsApp)
- **Phase transitions** as centered separator lines: `── Manager Review Started ──`
- **Thread connections**: When user sends guidance and agent subsequently acts on it, those actions are visually indented/connected with a thin line showing cause→effect
- **Inline decision cards**: When a plan needs approval, a card appears in the conversation flow with approve/reject/view-diff buttons
- **Agent-to-agent messages**: Coordinator→employee messages appear as a distinct style (system-tinted bubbles with directional indicators)

#### C2: Code Activity View (when watching agent file work)
- **Live file activity**: Which files the agent is reading/writing right now
- Files being written have a subtle glow animation
- **Streaming diff**: As agent writes code, diff updates in real-time
- **Inline code comments**: Click a diff line → type a note → it becomes guidance to the agent
- File tree on left showing all touched files

#### C3: Intelligence View (agent reasoning)
- Decision chain: mode selection rationale, model choice, confidence gating
- Alternatives considered and why rejected
- Escalation history

#### C4: Diff Review (for verdict/plan review)
- Side-by-side or unified diff (toggle)
- File tree navigation
- "Copy to guidance" — select code → send as context to agent

#### Guidance Composer (always at bottom of Zone C)
- Rich text input with Shift+Enter for multi-line
- Typing `/` shows slash command menu: `/redirect`, `/stop`, `/prioritize`, `/deprioritize`, `/focus`
- `@Agent-Name` targeting when multiple agents active
- Guidance type pills above input: info (blue), warning (amber), redirect (purple), stop (red)
- Send immediately delivers to the targeted agent's next tool call

### Attention Escalation System

| Level | Trigger | UI Response |
|-------|---------|-------------|
| 0 — Ambient | Decision pending | Badge count on nav icon |
| 1 — Contextual | Decision pending + user on Ops Floor | Card border pulses, subtle glow |
| 2 — Banner | Decision pending > 5min | Top banner: "Plan awaiting review [Review Now]" |
| 3 — Notification | Critical (failure, conflict, stuck) | Toast with action buttons + webhook (Slack/Discord) |
| 4 — System | Tab not focused + critical | Browser Notification API desktop alert |

### Ambient Awareness Layer

- **Background**: NeuralAurora/AmbientGlow responds to activity (idle=slow breathing, working=blue pulses, reviewing=amber, conflict=red flash, decision pending=golden glow)
- **Audio cues** (opt-in): Phase change chime, decision request tone, completion sound
- **Tab title**: Dynamic — "Working (3 agents)" / "(1) Review Needed" / "Claude Station"
- **Favicon**: Canvas-generated dot (green=active, amber=decisions, red=errors)

### Timeline Strip (always visible, bottom)

- Activity density heatmap (events/minute as bar height)
- Phase transition markers (colored vertical lines)
- Playhead at rightmost edge in live mode
- Click anywhere to scrub → entire workspace replays to that point in time
- "LIVE" badge when at real-time head
- Play/pause/step controls (uses existing ReplayController)
- During replay: Zone B shows agent states at that time, Zone C shows conversation at that point, DAG shows historical task statuses

---

## Part 2: Comparison — New Vision vs Current State

### Fundamental Architecture Delta

| Aspect | New Vision | Current State | Gap |
|--------|-----------|---------------|-----|
| **Layout model** | 3-zone persistent workspace (nav + canvas + context panel + timeline) | Page-based with slide-out panel | **Complete layout rewrite** |
| **Agent interaction** | Conversational dialogue with bubbles, threading, inline decisions | Flat reverse-chronological log feed | **Complete conversation rewrite** |
| **Context panel** | Always visible, 4 modes, guidance always accessible | Slide-out AgentPanel, hidden by default | **Persistent panel with mode switching** |
| **Agent visualization** | Spatial "desk" layout with communication lines | Status bars and agent cards | **New TeamBoard component** |
| **Timeline** | Always-visible strip driving workspace-wide replay | RunReplay component on one page only | **Universal timeline strip** |
| **Attention system** | 5-level escalation (ambient → system notification) | Badge count on nav icon only | **New attention state machine** |
| **Guidance input** | Rich composer with slash commands + @mentions + type pills | Simple textarea + type dropdown | **Enhanced guidance composer** |
| **Queue** | Dedicated kanban board page | Embedded tab in WorkStreamPage | **Extract and build kanban** |
| **Agent-to-agent comms** | Visible in conversation + animated lines on TeamBoard | Only visible on RunDetail coordinator tab | **Surface everywhere** |
| **Code activity** | Live file tracking + streaming diff + inline comments | DiffViewer on RunDetailPage only | **New CodeActivity panel mode** |

### Page-Level Mapping

| New Mode | Replaces | Key Changes |
|----------|----------|-------------|
| Operations Floor | PulsePage + AgentObservatoryPage | TeamBoard, LiveDAG, Decision Queue, Problem Tray |
| Work Stream | WorkStreamPage | Remove queue/backpressure, simplify to run list |
| Queue Board | (part of WorkStreamPage) | **New page**: kanban + list, stats, backpressure |
| Strategy Room | ConfigPage + BrainstormPage | Split settings into 9 tabs, extract projects, inline brainstorm |
| Analytics | AnalyticsPage | Add 4 new chart types, integrate intelligence |
| Integration | IntegrationPage | Keep, minor enhancements |
| Run Detail | RunDetailPage | Now loads into Zone C context (not separate page) OR can be a Zone B sub-mode |

### Component-Level Delta

#### Keep (reuse with modifications)
- `Modal.svelte` — portal-based, works well
- `Toast.svelte` — notification system
- `CommandPalette.svelte` — Cmd+K (update page list)
- `DiffViewer.svelte` — syntax diffs (integrate into Zone C)
- `MarkdownRenderer.svelte` — markdown rendering
- `StatusBadge.svelte` / `Badge.svelte` — semantic indicators
- `GlassCard.svelte` — surface primitive
- `ProjectForm.svelte` — form fields
- `TimeAgo.svelte` — timestamps
- `LineChart.svelte`, `BarChart.svelte`, `DonutChart.svelte` — existing charts

#### Refactor Significantly
- `App.svelte` → WorkspaceLayout with 3-zone architecture
- `NavRail.svelte` → Updated routes, live badges for queue/decisions/agents
- `HeaderBar.svelte` → Simplified StatusBar (connection indicator, health, search, trigger)
- `router.svelte.ts` → New route types + contextMode for Zone C
- `agent-presence.svelte.ts` → Extract event bus, add file activity tracking, add thread relationships in conversation, add replay mode
- `AgentPanel.svelte` → **Replace** with ContextPanel (persistent, 4 modes)
- `GuidanceInput.svelte` → **Replace** with GuidanceComposer (slash commands, @mentions)
- `PulsePage.svelte` → **Rewrite** as OperationsFloor
- `WorkStreamPage.svelte` → **Simplify** to run list only
- `ConfigPage.svelte` → **Split** into SettingsPage (9 tabs) + ProjectsPage
- `CoordinatorDAG.svelte` → **Enhance** as LiveDAG with animations + click-to-focus
- `RunCard.svelte` → **Simplify** to RunRow (detail now in Zone C)

#### New Components to Build

**Layout:**
- `WorkspaceLayout.svelte` — 3-zone container replacing App routing
- `ContextPanel.svelte` — Zone C, persistent right panel with mode tabs
- `TimelineStrip.svelte` — Bottom timeline with heatmap + scrubber

**Operations Floor:**
- `OperationsFloor.svelte` — Main canvas for live workspace
- `TeamBoard.svelte` — Spatial agent desk layout with communication lines
- `AgentDesk.svelte` — Single agent desk (avatar, status, action, progress)
- `CommunicationLine.svelte` — Animated SVG line between desks
- `LiveDAG.svelte` — Enhanced animated task DAG
- `DecisionQueue.svelte` — Inline pending decisions with actions
- `ProblemTray.svelte` — Collapsible failed/escalated items
- `MetricStrip.svelte` — Horizontal status pills
- `MetricPill.svelte` — Single metric with label + value + color

**Conversation:**
- `ConversationView.svelte` — Structured dialogue replacing flat log
- `ConversationMessage.svelte` — Single bubble (tool call, thinking, text, guidance)
- `ConversationThread.svelte` — Threaded cause→effect visualization
- `InlineDecisionCard.svelte` — Approve/reject within conversation flow
- `AgentMessage.svelte` — Agent-to-agent message style
- `GuidanceComposer.svelte` — Rich input with slash commands + @mentions
- `SlashCommandMenu.svelte` — Autocomplete for /commands

**Code Activity:**
- `CodeActivityView.svelte` — Live file tracking + streaming diff
- `FileActivityList.svelte` — Files being read/written with glow
- `StreamingDiff.svelte` — Real-time diff updates
- `InlineComment.svelte` — Click-on-diff-line → guidance

**Queue Board:**
- `QueuePage.svelte` — Standalone queue page
- `KanbanBoard.svelte` — Drag-and-drop state columns
- `KanbanColumn.svelte` — Single state column
- `KanbanCard.svelte` — Queue item card

**Analytics (new chart types):**
- `HeatmapChart.svelte` — Mode x model success rate
- `FunnelChart.svelte` — Escalation funnel
- `ScatterChart.svelte` — Confidence calibration

**Attention:**
- `attention.svelte.ts` — Attention level state machine with timers
- `AttentionBanner.svelte` — Persistent top banner for Level 2+

**Infrastructure:**
- `event-bus.svelte.ts` — Central SSE event dispatcher (extracted from agent-presence)
- `context-mode.svelte.ts` — Zone C mode state management
- `guidance-commands.ts` — Slash command parser + API mapping

#### Remove
- `AmbientGlow.svelte` — Replace with simplified version OR keep as opt-in
- `NeuralAurora.svelte` — Keep but optimize (lazy-load Three.js)
- `AgentObservatoryPage.svelte` — Merged into Operations Floor
- `BackpressureGauge.svelte` (from Pulse) — Moves to Queue Board
- `QueueStatsBar.svelte` (from WorkStream) — Moves to Queue Board
- `QueueItemCard.svelte` (from WorkStream) — Replaced by KanbanCard

---

## Part 3: Execution Plan (8 Phases)

### Phase 1: Layout Foundation
**Goal**: Replace page-based routing with 3-zone workspace layout.

**New files:**
- `src/components/WorkspaceLayout.svelte` — 3-zone container
- `src/components/ContextPanel.svelte` — Zone C with mode tabs
- `src/components/TimelineStrip.svelte` — Bottom timeline bar
- `src/lib/context-mode.svelte.ts` — Zone C mode state
- `src/lib/event-bus.svelte.ts` — Central SSE event dispatcher

**Modified files:**
- `src/App.svelte` — Replace page routing with WorkspaceLayout
- `src/lib/router.svelte.ts` — New route types: `'ops' | 'runs' | 'run-detail' | 'queue' | 'plans' | 'projects' | 'analytics' | 'integration' | 'brainstorm' | 'settings'`
- `src/components/NavRail.svelte` — New items (Queue, Projects), updated routes, live badges
- `src/components/HeaderBar.svelte` → Rename to StatusBar, simplify
- `src/lib/agent-presence.svelte.ts` — Extract event bus, add `contextMode` coordination

### Phase 2: Conversation View (Zone C Core)
**Goal**: Replace flat log feed with structured dialogue.

**New files:**
- `src/components/ConversationView.svelte`
- `src/components/ConversationMessage.svelte`
- `src/components/ConversationThread.svelte`
- `src/components/InlineDecisionCard.svelte`
- `src/components/AgentMessage.svelte`
- `src/components/GuidanceComposer.svelte`
- `src/components/SlashCommandMenu.svelte`
- `src/lib/guidance-commands.ts`

**Modified files:**
- `src/lib/agent-presence.svelte.ts` — Add thread tracking (which guidance → which subsequent actions)
- `src/lib/log-parser.ts` — Extract richer structured data (file paths from tool calls)

### Phase 3: Operations Floor (Zone B Primary)
**Goal**: Build the spatial agent workspace.

**New files:**
- `src/components/OperationsFloor.svelte`
- `src/components/TeamBoard.svelte`
- `src/components/AgentDesk.svelte`
- `src/components/CommunicationLine.svelte`
- `src/components/LiveDAG.svelte`
- `src/components/DecisionQueue.svelte`
- `src/components/ProblemTray.svelte`
- `src/components/MetricStrip.svelte`
- `src/components/MetricPill.svelte`

**Modified files:**
- `src/pages/PulsePage.svelte` — Rewrite to use OperationsFloor
- `src/components/CoordinatorDAG.svelte` — Enhance or replace with LiveDAG

### Phase 4: Queue Board (New Zone B Mode)
**Goal**: Standalone kanban queue management.

**New files:**
- `src/pages/QueuePage.svelte`
- `src/components/KanbanBoard.svelte`
- `src/components/KanbanColumn.svelte`
- `src/components/KanbanCard.svelte`
- `src/components/QueueTable.svelte` (list view toggle)

**Modified files:**
- `src/pages/WorkStreamPage.svelte` — Remove queue tab, backpressure, simplify to run list
- `src/lib/router.svelte.ts` — Add `/queue` route

### Phase 5: Code Activity + Diff (Zone C Modes)
**Goal**: Live file tracking and streaming diffs.

**New files:**
- `src/components/CodeActivityView.svelte`
- `src/components/FileActivityList.svelte`
- `src/components/StreamingDiff.svelte`
- `src/components/InlineComment.svelte`

**Modified files:**
- `src/lib/agent-presence.svelte.ts` — Add per-agent file activity tracking from parsed tool calls
- `src/lib/log-parser.ts` — Promote file path extraction to structured `fileActivity` data

### Phase 6: Attention + Ambient
**Goal**: Multi-level attention escalation and ambient awareness.

**New files:**
- `src/lib/attention.svelte.ts` — Attention level state machine
- `src/components/AttentionBanner.svelte`

**Modified files:**
- `src/App.svelte` — Dynamic tab title, favicon badge, browser notification permission
- `src/components/NeuralAurora.svelte` — Nuanced responses to event types
- `src/lib/audio-engine.ts` — Distinct sounds per event type
- `src/lib/agent-presence.svelte.ts` — Trigger attention levels on SSE events

### Phase 7: Strategy Room + Analytics
**Goal**: Settings restructure, projects extraction, analytics enhancement.

**New files:**
- `src/pages/ProjectsPage.svelte`
- `src/components/HeatmapChart.svelte`
- `src/components/FunnelChart.svelte`
- `src/components/ScatterChart.svelte`

**Modified files:**
- `src/pages/ConfigPage.svelte` → Rename to SettingsPage, remove projects tab, split into 9 tabs
- `src/pages/AnalyticsPage.svelte` — Add 4 new chart types, integrate intelligence
- `src/lib/router.svelte.ts` — Add `/projects` route

### Phase 8: Timeline + Replay + Polish
**Goal**: Universal timeline, workspace-wide replay, mobile, performance.

**Modified files:**
- `src/components/TimelineStrip.svelte` — Full implementation with heatmap, scrubber, phase markers
- `src/lib/replay-controller.ts` — Drive workspace-wide state during replay (not just a renderer)
- `src/lib/agent-presence.svelte.ts` — Add replay mode that overrides live data
- `src/components/NavRail.svelte` — Mobile: 5-tab bottom bar (Ops, Runs, Queue, Analytics, More)
- `src/components/CommandPalette.svelte` — Update page list and actions

**Polish:**
- Virtualized conversation list (200-entry circular buffer is fine, but render only visible)
- Debounce rapid WebSocket events
- Lazy-load Three.js for NeuralAurora
- Error boundaries on all Zone B modes
- Keyboard: number keys update for new nav order

---

## Critical Files Reference

### Must Read Before Implementation
- `src/lib/agent-presence.svelte.ts` — Central state layer (500+ lines), the backbone
- `src/lib/event-stream.ts` — SSE client, event types
- `src/lib/ws.ts` — WebSocket client
- `src/lib/log-parser.ts` — Structured log parsing
- `src/lib/replay-controller.ts` — Replay state machine
- `src/lib/api.ts` — All 70+ API functions (no changes needed)
- `src/lib/types.ts` — All TypeScript types (no changes needed)
- `src/App.svelte` — Current layout and routing
- `src/components/AgentPanel.svelte` — Current conversation panel (being replaced)
- `src/components/GuidanceInput.svelte` — Current guidance (being enhanced)
- `src/components/CoordinatorDAG.svelte` — Current DAG (being enhanced)
- `src/pages/PulsePage.svelte` — Current operations view (being rewritten)

### Backend (no changes needed)
The backend already supports everything in this design:
- SSE streaming with 20+ event types
- WebSocket log streaming
- Coordinator messages (to_employee/from_monitor/system)
- Send guidance API
- Task DAG API
- Run full context API (single request)
- Active employee tracking
- Queue with full state machine
- Intelligence insights
- Brainstorm streaming

---

## Verification Plan

1. **Layout test**: Three zones render correctly, Zone C stays visible, responsive on mobile
2. **Conversation test**: Send guidance → see right-aligned bubble → agent responds → threaded response appears connected
3. **Operations Floor test**: Start a run → agent desk appears → communication lines animate → task DAG updates live
4. **Queue test**: Create item → kanban card appears → drag to new column → API validates transition
5. **Timeline test**: Click in timeline → workspace replays to that point → all zones show historical state
6. **Attention test**: Create draft plan → wait 5min → banner appears → approve → banner dismisses
7. **Code activity test**: Agent writes file → Zone C shows file glow + streaming diff
8. **Build test**: `cd dashboard/frontend && npm run build` — no errors, measure bundle size
9. **Real-time test**: Open two browser tabs → trigger run → both update simultaneously via SSE
