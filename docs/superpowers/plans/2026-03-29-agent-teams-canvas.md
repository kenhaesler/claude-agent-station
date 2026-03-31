# Agent Teams Canvas + Soft Sunrise Theme — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the dashboard frontend with the Soft Sunrise Vapor theme and add a new Agent Teams Canvas page that visualizes Claude Agent Teams sessions.

**Architecture:** Replace the existing dark glassmorphism theme with the Soft Sunrise neumorphic palette across all pages. Add a new `agent-teams` route and page that shows team lead status, teammate workstation cards with peer messaging, shared task list sidebar, and activity feed. The page is driven by existing SSE + REST endpoints.

**Tech Stack:** Svelte 5, TailwindCSS, Vite, Outfit font, existing FastAPI backend (no backend changes needed)

**Prototype references:**
- Dashboard: `.superpowers/brainstorm/47-1774801655/content/vapor-improved.html`
- Agent Teams: `.superpowers/brainstorm/47-1774801655/content/canvas-workspace-v1.html`
- Design spec: `docs/superpowers/specs/2026-03-29-agent-teams-canvas-design.md`

---

## File Structure

### Theme (modify existing)
- `dashboard/frontend/src/app.css` — Replace dark theme CSS variables with Soft Sunrise palette, add neumorphic ripple and warm glow styles
- `dashboard/frontend/src/lib/theme.svelte.ts` — Update agent role colors to warm palette

### Layout (modify existing)
- `dashboard/frontend/src/components/layout/HeaderBar.svelte` — Replace dark header with Soft Sunrise glass nav pills
- `dashboard/frontend/src/components/layout/NavRail.svelte` — Replace or remove (prototype uses top nav pills, not side rail)
- `dashboard/frontend/src/components/layout/Shell.svelte` — Add neumorphic ripple field and warm glow background layers

### Shared Components (modify existing)
- `dashboard/frontend/src/components/data-display/MetricCard.svelte` — Neumorphic card style with warm borders
- `dashboard/frontend/src/components/data-display/Badge.svelte` — Warm sage green / sienna / coral badge colors
- `dashboard/frontend/src/components/data-display/StatusOrb.svelte` — Warm green/amber/grey status colors

### Router (modify existing)
- `dashboard/frontend/src/lib/router.svelte.ts` — Add `'agent-teams'` to Page type and route mapping

### Agent Teams Page (new)
- `dashboard/frontend/src/pages/AgentTeamsCanvas.svelte` — Main page component (layout: lead card + teammate grid + right sidebar)

### Agent Teams Components (new)
- `dashboard/frontend/src/components/agent-teams/TeamLeadCard.svelte` — Lead agent card with avatar, status, metrics
- `dashboard/frontend/src/components/agent-teams/TeammateCard.svelte` — Teammate workstation card with task, status, messages, connection chips
- `dashboard/frontend/src/components/agent-teams/TeammateGrid.svelte` — 3-column responsive grid of teammate cards + spawn placeholder
- `dashboard/frontend/src/components/agent-teams/SharedTaskPanel.svelte` — Right sidebar shared task list
- `dashboard/frontend/src/components/agent-teams/ActivityFeed.svelte` — Right sidebar activity/message feed
- `dashboard/frontend/src/components/agent-teams/MessageChip.svelte` — Peer (indigo) / lead (amber) message direction chip
- `dashboard/frontend/src/components/agent-teams/MessageBubble.svelte` — Inline latest message preview bubble

### Background (new)
- `dashboard/frontend/src/components/background/NeumorphicRipples.svelte` — Ripple field with expanding neumorphic circles
- `dashboard/frontend/src/components/background/WarmGlows.svelte` — Three drifting peach/apricot radial gradient blobs
- `dashboard/frontend/src/components/background/VaporBackground.svelte` — Combines bg-base + ripples + glows + grain

---

## Task 1: Soft Sunrise CSS Variables

**Files:**
- Modify: `dashboard/frontend/src/app.css:9-79`

- [ ] **Step 1: Replace dark palette with Soft Sunrise in `@theme` block**

Replace the entire CSS custom properties section. Key changes:

```css
/* Base layers — Soft Sunrise */
--color-void: #FFF5EE;
--color-surface-0: #FFFBF7;
--color-surface-1: rgba(255,251,247,0.65);
--color-surface-2: rgba(255,251,247,0.50);
--color-border: rgba(240,220,200,0.25);
--color-border-hover: rgba(240,220,200,0.40);
--color-border-focus: rgba(224,144,96,0.30);

/* Text — warm browns */
--color-primary: #3D2A1A;
--color-secondary: #7A6652;
--color-tertiary: #8C7A66;
--color-ghost: #A08E7A;

/* Accents */
--color-violet: #B06030;  /* primary accent is now warm sienna */
--color-cyan: rgba(99,102,180,1);  /* peer messaging indigo */
--color-amber: #B06030;
--color-emerald: #2E7D32;
--color-rose: #D06050;
--color-orange: #B06030;

/* Semantic */
--color-success: #2E7D32;
--color-warning: #B06030;
--color-error: #D06050;
--color-info: rgba(99,102,180,1);
--color-running: #2E7D32;
```

- [ ] **Step 2: Replace glass/card/shadow styles**

```css
.glass {
  background: rgba(255,251,247,0.55);
  backdrop-filter: blur(16px) saturate(1.3);
  border: 1px solid rgba(240,220,200,0.30);
  box-shadow: 3px 3px 8px rgba(0,0,0,0.04), -3px -3px 8px rgba(255,255,255,0.40);
}

.card {
  background: rgba(255,251,247,0.50);
  backdrop-filter: blur(14px) saturate(1.3);
  border: 1px solid rgba(240,220,200,0.25);
  box-shadow: 2px 2px 6px rgba(0,0,0,0.03), -2px -2px 6px rgba(255,255,255,0.35);
}
```

- [ ] **Step 3: Add neumorphic ripple and warm glow keyframes**

```css
/* Neumorphic ripples */
.neu-ripple {
  position: absolute; border-radius: 50%;
  box-shadow: inset 3px 3px 8px rgba(255,255,255,0.45), inset -3px -3px 8px rgba(0,0,0,0.10), 3px 3px 10px rgba(0,0,0,0.07), -3px -3px 10px rgba(255,255,255,0.5);
  opacity: 0; animation: ripple-expand var(--duration) ease-out var(--delay) infinite;
}
@keyframes ripple-expand { 0% { transform: scale(0.05); opacity: 0; } 8% { opacity: 1; } 100% { transform: scale(1); opacity: 0; } }

/* Warm glow drift */
@keyframes glow-drift-1 { 0%, 100% { transform: translateX(0); } 50% { transform: translateX(45vw); } }
@keyframes glow-drift-2 { 0%, 100% { transform: translateX(0); } 50% { transform: translateX(-45vw); } }
@keyframes glow-drift-3 { 0%, 100% { transform: translateX(0); } 50% { transform: translateX(40vw); } }
```

- [ ] **Step 4: Update badge colors**

```css
.badge-approve { background: rgba(46,125,50,0.10); color: #2E7D32; }
.badge-reject { background: rgba(208,96,80,0.10); color: #D06050; }
.badge-running { background: rgba(46,125,50,0.08); color: #2E7D32; }
.badge-pending { background: rgba(176,96,48,0.10); color: #B06030; }
```

- [ ] **Step 5: Verify the dev server renders with the new palette**

Run: `cd dashboard/frontend && npm run dev`
Expected: Dashboard loads with peach/cream background instead of dark blue

- [ ] **Step 6: Commit**

```bash
git add dashboard/frontend/src/app.css
git commit -m "feat: replace dark theme with Soft Sunrise palette"
```

---

## Task 2: Vapor Background Component

**Files:**
- Create: `dashboard/frontend/src/components/background/VaporBackground.svelte`

- [ ] **Step 1: Create VaporBackground.svelte**

```svelte
<script lang="ts">
  // No props needed — this is a global background layer
</script>

<!-- Base color -->
<div class="fixed inset-0 z-0" style="background: #FFF5EE;"></div>

<!-- Neumorphic ripple field -->
<div class="fixed inset-0 z-0 pointer-events-none overflow-hidden">
  {#each [
    { w: 500, h: 500, top: '5%', left: '8%', dur: '7s', delay: '0s' },
    { w: 500, h: 500, top: '5%', left: '8%', dur: '7s', delay: '3.5s' },
    { w: 450, h: 450, top: '15%', left: '28%', dur: '8s', delay: '1.5s' },
    { w: 450, h: 450, top: '15%', left: '28%', dur: '8s', delay: '5.5s' },
    { w: 600, h: 600, top: '30%', left: '35%', dur: '9s', delay: '0.5s' },
    { w: 600, h: 600, top: '30%', left: '35%', dur: '9s', delay: '4.5s' },
    { w: 480, h: 480, top: '20%', left: '65%', dur: '6.5s', delay: '2s' },
    { w: 480, h: 480, top: '20%', left: '65%', dur: '6.5s', delay: '5.2s' },
    { w: 520, h: 520, top: '60%', left: '5%', dur: '8s', delay: '1s' },
    { w: 520, h: 520, top: '60%', left: '5%', dur: '8s', delay: '5s' },
    { w: 500, h: 500, top: '55%', left: '70%', dur: '7.5s', delay: '0s' },
    { w: 500, h: 500, top: '55%', left: '70%', dur: '7.5s', delay: '3.8s' },
  ] as r}
    <div class="neu-ripple" style="width:{r.w}px;height:{r.h}px;top:{r.top};left:{r.left};--duration:{r.dur};--delay:{r.delay};"></div>
  {/each}
</div>

<!-- Warm glows -->
<div class="fixed rounded-full pointer-events-none z-0" style="width:70vw;height:25vh;top:5vh;left:-20vw;background:radial-gradient(ellipse,rgba(255,180,120,0.35) 0%,transparent 65%);filter:blur(40px);animation:glow-drift-1 12s ease-in-out infinite;"></div>
<div class="fixed rounded-full pointer-events-none z-0" style="width:60vw;height:20vh;top:40vh;left:50vw;background:radial-gradient(ellipse,rgba(255,200,160,0.30) 0%,transparent 65%);filter:blur(40px);animation:glow-drift-2 16s ease-in-out infinite;"></div>
<div class="fixed rounded-full pointer-events-none z-0" style="width:75vw;height:22vh;top:70vh;left:-10vw;background:radial-gradient(ellipse,rgba(255,220,180,0.25) 0%,transparent 65%);filter:blur(40px);animation:glow-drift-3 10s ease-in-out infinite;"></div>

<!-- Film grain -->
<div class="fixed inset-0 opacity-[0.025] pointer-events-none z-0" style="background-image:url(&quot;data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E&quot;);"></div>
```

- [ ] **Step 2: Add VaporBackground to Shell.svelte**

In `dashboard/frontend/src/components/layout/Shell.svelte`, import and render before main content:

```svelte
<script lang="ts">
  import VaporBackground from '../background/VaporBackground.svelte';
</script>

<VaporBackground />
<!-- rest of shell layout -->
```

- [ ] **Step 3: Verify ripples and glows render**

Run: `cd dashboard/frontend && npm run dev`
Expected: Background shows expanding neumorphic circles, drifting warm glows, subtle grain

- [ ] **Step 4: Commit**

```bash
git add dashboard/frontend/src/components/background/VaporBackground.svelte
git add dashboard/frontend/src/components/layout/Shell.svelte
git commit -m "feat: add Vapor neumorphic background with ripples and warm glows"
```

---

## Task 3: Header Bar — Soft Sunrise Nav Pills

**Files:**
- Modify: `dashboard/frontend/src/components/layout/HeaderBar.svelte`

- [ ] **Step 1: Replace dark header with Soft Sunrise glass nav**

Replace the header styling and structure to match prototype. Key elements:
- Background: `rgba(255,245,238,0.60)` with `backdrop-filter: blur(20px)`
- Border: `1px solid rgba(240,220,200,0.25)`
- Logo mark: `linear-gradient(135deg, #4A3728, #5C4435)` with `#FFF5EE` text
- Nav pills in a glass container: `rgba(255,255,255,0.60)` bg, `border-radius: 14px`
- Active pill: white bg, `#4A3728` text, `font-weight: 700`
- Inactive pill: `#9E8872` text
- Add "Agent Teams" as a nav pill between Projects and Settings

- [ ] **Step 2: Update router Page type**

In `dashboard/frontend/src/lib/router.svelte.ts`, add `'agent-teams'` to the `Page` type union and add route mapping:

```typescript
export type Page =
  | 'command-center'
  | 'theater'
  | 'team-comms'
  | 'runs'
  | 'run-detail'
  | 'queue'
  | 'queue-detail'
  | 'projects'
  | 'project-detail'
  | 'agent-teams'
  | 'settings';
```

Add to `routeMap`:
```typescript
'agent-teams': 'agent-teams',
```

- [ ] **Step 3: Verify nav renders with warm palette and Agent Teams pill**

Run: `cd dashboard/frontend && npm run dev`
Expected: Top nav has warm cream background, glass pill container, "Agent Teams" tab visible

- [ ] **Step 4: Commit**

```bash
git add dashboard/frontend/src/components/layout/HeaderBar.svelte
git add dashboard/frontend/src/lib/router.svelte.ts
git commit -m "feat: Soft Sunrise header bar with Agent Teams nav pill"
```

---

## Task 4: Agent Teams Page — Lead Card + Layout

**Files:**
- Create: `dashboard/frontend/src/pages/AgentTeamsCanvas.svelte`
- Create: `dashboard/frontend/src/components/agent-teams/TeamLeadCard.svelte`

- [ ] **Step 1: Create TeamLeadCard.svelte**

Props: `teamName: string`, `teammateCount: number`, `activity: string`, `tasksCompleted: number`, `tasksTotal: number`, `tokens: string`, `duration: string`

Key styling from prototype:
- Container: neumorphic glass card, `padding: 18px 22px`, `border-radius: 18px`
- Lead avatar: 48px, `linear-gradient(135deg, #4A3728, #5C4435)`, diamond icon, breathing glow animation
- Name: 18px bold, `#3D2A1A`
- Status: 14px, `#7A6652`
- Activity: 14px, `#2E7D32`, bold
- Right-aligned meta: 14px, Tasks/Tokens/Duration

- [ ] **Step 2: Create AgentTeamsCanvas.svelte with lead card + grid + sidebar layout**

```svelte
<script lang="ts">
  import TeamLeadCard from '../components/agent-teams/TeamLeadCard.svelte';
</script>

<div class="fixed top-[44px] left-0 right-0 bottom-0 z-1 grid" style="grid-template-columns: 1fr 280px;">
  <div class="relative overflow-hidden p-5 flex flex-col gap-4">
    <TeamLeadCard
      teamName="todo-tracker-review"
      teammateCount={5}
      activity="Reviewing Security's plan submission..."
      tasksCompleted={0}
      tasksTotal={6}
      tokens="18.2K"
      duration="7m 14s"
    />
    <!-- TeammateGrid goes here in Task 5 -->
  </div>
  <div class="flex flex-col" style="background:rgba(255,251,247,0.40);backdrop-filter:blur(16px);border-left:1px solid rgba(240,220,200,0.20);">
    <!-- SharedTaskPanel + ActivityFeed go here in Task 6 -->
  </div>
</div>
```

- [ ] **Step 3: Wire up route in App.svelte**

Add the page to the router switch in `App.svelte`:
```svelte
{:else if route.page === 'agent-teams'}
  <AgentTeamsCanvas />
```

- [ ] **Step 4: Verify page loads at /agent-teams**

Run: `cd dashboard/frontend && npm run dev`
Navigate to: `http://localhost:5173/agent-teams`
Expected: Lead card renders with warm styling on Vapor background

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/pages/AgentTeamsCanvas.svelte
git add dashboard/frontend/src/components/agent-teams/TeamLeadCard.svelte
git add dashboard/frontend/src/App.svelte
git commit -m "feat: Agent Teams Canvas page with lead card"
```

---

## Task 5: Teammate Cards + Grid

**Files:**
- Create: `dashboard/frontend/src/components/agent-teams/TeammateCard.svelte`
- Create: `dashboard/frontend/src/components/agent-teams/MessageChip.svelte`
- Create: `dashboard/frontend/src/components/agent-teams/MessageBubble.svelte`
- Create: `dashboard/frontend/src/components/agent-teams/TeammateGrid.svelte`

- [ ] **Step 1: Create MessageChip.svelte**

Props: `direction: 'in' | 'out' | 'both'`, `target: string`, `type: 'peer' | 'lead'`

Styling:
- Peer: `background: rgba(99,102,180,0.08); color: rgba(80,82,150,0.8)`
- Lead: `background: rgba(176,96,48,0.08); color: #B06030`
- Arrows: `->` for out, `<-` for in, `<->` for both
- Font: 12px bold, `padding: 5px 12px`, `border-radius: 8px`

- [ ] **Step 2: Create MessageBubble.svelte**

Props: `sender: string`, `message: string`, `time: string`, `type: 'peer' | 'lead'`

Styling:
- Peer bg: `rgba(99,102,180,0.04)`, border: `rgba(99,102,180,0.08)`
- Lead bg: `rgba(176,96,48,0.04)`, border: `rgba(176,96,48,0.08)`
- Font: 13px body, sender in bold with type color, timestamp 11px `#A08E7A`
- `border-radius: 8px`, `padding: 8px 10px`

- [ ] **Step 3: Create TeammateCard.svelte**

Props: `name: string`, `model: string`, `task: string`, `status: string`, `statusType: 'working' | 'reviewing' | 'idle' | 'blocked'`, `detail: string`, `latestMessage?: { sender, message, time, type }`, `connections?: { direction, target, type }[]`

Card states via `statusType`:
- `working`: `.active` class — green left-edge bar (3px, `rgba(46,125,50,0.4)`), breathing shadow animation
- `reviewing`: `.plan-review` class — amber left-edge bar, amber breathing
- `blocked`: 50% opacity, no animation
- `idle`: same as blocked

Status text colors:
- `working`: `#2E7D32`
- `reviewing`: `#B06030`
- `idle` / `blocked`: `#8C7A66`

Avatar: 38px, neumorphic circle, status ring animation for active/reviewing

Layout: header (avatar + name + model) -> task (15px bold) -> status (14px colored) -> detail (13px) -> MessageBubble (if latestMessage) -> MessageChips row (pushed to bottom with `margin-top: auto`)

- [ ] **Step 4: Create TeammateGrid.svelte**

Props: `teammates: TeammateData[]`

Grid: `grid-template-columns: repeat(3, 1fr)`, `grid-template-rows: 1fr 1fr`, `gap: 14px`, `flex: 1`

Renders TeammateCard for each teammate. After all cards, renders a "Spawn teammate" placeholder card with dashed border:
- `+` icon (28px, 0.3 opacity)
- "Spawn teammate" (15px, `#8C7A66`)
- "Or let lead auto-scale" (13px, `#A08E7A`)

- [ ] **Step 5: Wire TeammateGrid into AgentTeamsCanvas.svelte**

Import TeammateGrid and pass teammate data (initially hardcoded from prototype, later from API).

- [ ] **Step 6: Verify full teammate grid renders**

Run: `cd dashboard/frontend && npm run dev`
Navigate to: `http://localhost:5173/agent-teams`
Expected: 3x2 grid of teammate cards with lead card above, breathing animations, message chips

- [ ] **Step 7: Commit**

```bash
git add dashboard/frontend/src/components/agent-teams/
git add dashboard/frontend/src/pages/AgentTeamsCanvas.svelte
git commit -m "feat: teammate cards grid with message chips and bubbles"
```

---

## Task 6: Shared Task Panel + Activity Feed

**Files:**
- Create: `dashboard/frontend/src/components/agent-teams/SharedTaskPanel.svelte`
- Create: `dashboard/frontend/src/components/agent-teams/ActivityFeed.svelte`

- [ ] **Step 1: Create SharedTaskPanel.svelte**

Props: `tasks: { name, status, owner, dependency? }[]`

Section header: "SHARED TASKS" — 11px bold uppercase, `#B06030`, `letter-spacing: 0.08em`

Task items: neumorphic cards, `padding: 10px 12px`, `border-radius: 10px`
- Task name: 13px bold, `#3D2A1A`
- Status badge: 10px bold, colors per status:
  - `progress`: `rgba(46,125,50,0.08)` bg, `#2E7D32` text
  - `plan-review`: `rgba(176,96,48,0.08)` bg, `#B06030` text
  - `blocked`: `rgba(160,142,122,0.08)` bg, `#8C7A66` text
  - `pending`: `rgba(160,142,122,0.06)` bg, `#A08E7A` text
- Owner: 11px, `#7A6652`
- Dependency: 10px italic, `#A08E7A`
- Blocked/pending tasks: 45% opacity

- [ ] **Step 2: Create ActivityFeed.svelte**

Props: `events: { type: 'peer' | 'lead' | 'system', sender?, target?, message, time }[]`

Section header: "ACTIVITY" — same style as SharedTaskPanel header

Event items: `padding: 8px 0`, `font-size: 12px`, `border-bottom: 1px solid rgba(240,220,200,0.10)`
- Peer: sender/target in `rgba(80,82,150,0.8)` bold
- Lead: sender/target in `#B06030` bold
- System: text in `#2E7D32` bold
- Timestamps: 10px, `#A08E7A`
- Fade-in animation: `from { opacity: 0; translateY(4px) } to { opacity: 1; translateY(0) }`

- [ ] **Step 3: Wire both into AgentTeamsCanvas.svelte right panel**

```svelte
<div class="flex flex-col" style="background:rgba(255,251,247,0.40);backdrop-filter:blur(16px);border-left:1px solid rgba(240,220,200,0.20);overflow-y:auto;">
  <SharedTaskPanel tasks={taskData} />
  <ActivityFeed events={activityData} />
</div>
```

- [ ] **Step 4: Verify complete page**

Run: `cd dashboard/frontend && npm run dev`
Navigate to: `http://localhost:5173/agent-teams`
Expected: Full page with lead card, teammate grid, shared tasks sidebar, activity feed — all matching prototype

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/components/agent-teams/SharedTaskPanel.svelte
git add dashboard/frontend/src/components/agent-teams/ActivityFeed.svelte
git add dashboard/frontend/src/pages/AgentTeamsCanvas.svelte
git commit -m "feat: shared task panel and activity feed for Agent Teams"
```

---

## Task 7: Wire to Live Data

**Files:**
- Modify: `dashboard/frontend/src/pages/AgentTeamsCanvas.svelte`

- [ ] **Step 1: Fetch team data from existing API**

Use existing endpoints:
- `GET /api/runs?status=running` — find active team runs (filter by `team_name` not null)
- `GET /api/runs/{run_id}/full-context` — get team members, tasks, messages
- `GET /api/coordinator/tasks` — shared task list
- `GET /api/coordinator/messages` — message history

- [ ] **Step 2: Map API response to component props**

```typescript
// Map Run.team_members JSON to TeammateCard props
interface TeammateData {
  name: string;
  model: string;
  task: string;
  status: string;
  statusType: 'working' | 'reviewing' | 'idle' | 'blocked';
  detail: string;
  latestMessage?: { sender: string; message: string; time: string; type: 'peer' | 'lead' };
  connections?: { direction: 'in' | 'out' | 'both'; target: string; type: 'peer' | 'lead' }[];
}
```

Map `CoordinatorTask.status`:
- `running` -> `working`
- `pending` with plan submitted -> `reviewing`
- `blocked` -> `blocked`
- `ready` but unclaimed -> `idle`

Map `CoordinatorMessage`:
- `direction: 'to_employee'` + `message_type: 'guidance'` -> lead message
- Messages between teammates -> peer message (indigo)

- [ ] **Step 3: Subscribe to SSE for real-time updates**

Use existing SSE connection from `dashboard/frontend/src/lib/` to update teammate states, task statuses, and append new activity feed events.

- [ ] **Step 4: Test with a real running team**

Start an agent team run, navigate to `/agent-teams`, verify:
- Lead card shows correct teammate count
- Teammate cards update as agents work
- Task statuses change in sidebar
- Activity feed shows real messages

- [ ] **Step 5: Commit**

```bash
git add dashboard/frontend/src/pages/AgentTeamsCanvas.svelte
git commit -m "feat: wire Agent Teams Canvas to live API data"
```

---

## Task 8: Dashboard Overview — Soft Sunrise Rebuild

**Files:**
- Modify: `dashboard/frontend/src/pages/CommandCenter.svelte`

- [ ] **Step 1: Update metric cards to match Vapor prototype**

Reference: `vapor-improved.html` lines 252-285

- Card bg: `rgba(255,251,247,0.65)`, border: `rgba(240,220,200,0.6)`, `border-radius: 18px`
- Card hover: `translateY(-4px)` with deeper shadow
- Stat numbers: 48px, `font-weight: 800`, `letter-spacing: -0.05em`, `#3D2A1A`
- Labels: 14px uppercase, `#7A6652`, `letter-spacing: 0.06em`
- Success rate color: `#D84315` (warm red-orange)
- Queue accent: `#B06030`
- Positive change indicator: `#2E7D32`
- Icon containers: 32px, colored with 0.08 opacity bg
- Card entrance animation: staggered `card-in` with `translateY(16px)` to `0`

- [ ] **Step 2: Update runs table**

- Table bg: same glass card style
- Row borders: `rgba(0,0,0,0.03)`
- Approved badge: `rgba(46,125,50,0.14)` bg, `#2E7D32` text
- Failed text: `#A08E7A`
- Success dots: `#2E7D32` with `0 0 6px rgba(46,125,50,0.35)` glow
- Failed dots: `#C4AA90`

- [ ] **Step 3: Update secondary cards (Active Projects, Queue Preview, System Health)**

- Same glass card style as metric cards
- Active badge: `rgba(46,125,50,0.14)` bg, `#2E7D32`
- Disabled badge: `rgba(0,0,0,0.04)` bg, `#8C7A66`
- Online/GREEN labels: `#2E7D32`

- [ ] **Step 4: Add welcome greeting**

- "Good evening" (or time-appropriate): 24px, `font-weight: 800`, `#3D2A1A`
- Sub-status: 14px, `#8C7A66`
- Greeting entrance animation: `translateY(12px)` to `0`

- [ ] **Step 5: Verify dashboard overview matches prototype**

Compare `http://localhost:5173/` against `vapor-improved.html` screenshot
Expected: Metric cards, runs table, secondary cards all render with Soft Sunrise palette

- [ ] **Step 6: Commit**

```bash
git add dashboard/frontend/src/pages/CommandCenter.svelte
git commit -m "feat: Soft Sunrise dashboard overview rebuild"
```
