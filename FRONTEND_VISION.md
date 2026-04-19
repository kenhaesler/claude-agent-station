# Frontend Vision: Claude Agent Station

> ⚠️ **Status: aspirational, not shipped.** This document captures a
> design-in-progress vision for a from-scratch rebuild of the dashboard. The
> shipping dashboard lives in `dashboard/frontend/` and does not match every
> section of this doc. Use this as a north-star reference — treat the API
> surface tables and component inventories as *a direction*, not a guarantee
> of what exists in `main` right now. When in doubt, read the code first.

A comprehensive design document for a state-of-the-art agentic dashboard, built from scratch based on deep analysis of every backend capability.

---

## 1. Backend Capability Map

### 1.1 API Surface (22 Routers, 90+ Endpoints)

| Domain | Prefix | Key Capabilities |
|--------|--------|-----------------|
| **Projects** | `/api/projects` | CRUD for managed GitHub repositories. Fields: repo, priority, mode (full/analyze/plan/triage/review/fix), enabled, branch, custom_instructions, setup_script, security_review_enabled |
| **Runs** | `/api/runs` | Paginated run history with filters (project, status, verdict). Active employee/teammate tracking. Full unified context endpoint (`/runs/{id}/full`) returns run + coordinator tasks + messages + queue item + plan + intelligence decisions + team summary in one call. Git diff viewer (`/runs/{id}/diff`). Manual trigger and log rescan. |
| **Analytics** | `/api/analytics` | Time-windowed aggregations: daily token usage, verdict distribution, per-project token usage, daily run counts (success/fail). Supports 1-365 day windows and per-project filtering. |
| **Logs** | `/api/logs` | WebSocket live log streaming (`/stream`) with file tailing. Per-run log retrieval. Full-text search across all log files. Path traversal protection. |
| **Events** | `/api/events` | SSE real-time event stream. All webhook events are broadcast: run lifecycle, coordinator tasks, guidance, queue changes, Agent Teams events. Subscriber count monitoring. |
| **Webhook** | `/api/webhook` | Agent event ingestion from run-manager.sh. Handles 30+ event types: run lifecycle, employee phases, coordinator DAG events, Agent Teams orchestration, progress updates. Creates/updates Run, CoordinatorTask, CoordinatorMessage, Notification records. |
| **Coordinator** | `/api/coordinator` | Task DAG visualization. Task listing/filtering by run and status. Task detail with employee report and log excerpt. DAG summary per run. Coordinator message history. **Human-in-the-loop guidance sending** to live employees (warning/redirect/stop/info). |
| **Queue** | `/api/queue` | Work queue with full state machine (pending -> claimed -> in_progress -> review -> approved -> completed). Priority ordering. Atomic work-stealing claim. Batch pause/resume. Stats and pressure monitoring. Purge completed items. Deduplication. Escalation tracking. |
| **Plans** | `/api/plans` | Implementation plan CRUD. Status workflow (draft -> approved -> implementing -> completed -> rejected). Plan approval auto-creates queue items. Plan implementation writes to workspace and triggers agent service. |
| **Config** | `/api/config` | Full station config read/write (models, limits, schedule, notifications, logging, intelligence, integration, sprint). Key-value DB store for overrides. Usage tracking. Token consumption (daily/monthly). Test notification sender. |
| **System** | `/api/system` | Systemd service/timer status. Service control (start/stop/restart/enable/disable). System resources (memory, load, disk). |
| **Auth (Claude)** | `/api/oauth` | PKCE OAuth flow for Claude CLI authentication. Token refresh (automatic + manual). Credentials management. |
| **Auth (GitHub)** | `/api/oauth/github` | GitHub Device Authorization Flow. Token storage. Connection status with live validation. Disconnect. |
| **GitHub Webhook** | `/api/github-webhook` | Receives GitHub events (issues.opened, issues.labeled, pull_request). Auto-creates queue items with mode/priority from labels. HMAC-SHA256 signature verification. |
| **Prompts** | `/api/prompts` | System prompt management for 6 agent roles (manager, employee, analyst, planner, assigner, security-reviewer). Default content + custom overrides. Overrides persist to both DB and filesystem. |
| **Agent Events** | `/api/agent-events` | Append-only structured audit trail (ESAA pattern). Workflow-scoped event chains with parent references. Event type stats. Filterable by workflow, agent, run, type. |
| **Intelligence** | `/api/intelligence` | Learning loop insights: success rates by mode/model, confidence calibration (reported vs actual), token efficiency trends, escalation statistics. Task outcome recording. Intelligence decision history (filtered agent events). |
| **Brainstorm** | `/api/brainstorm` | Interactive AI expert collaboration. Session management (create, list, get, delete). 4 expert personas (architect, security, performance, devops). Streaming responses via Claude CLI subprocess. SSE-based real-time message delivery. |
| **Plan Usage** | `/api/plan-usage` | Claude plan tier tracking (max_5x, pro, team). Session and weekly token limits. Per-model breakdown. Throttle recommendations. Historical snapshots. |
| **Integration** | `/api/integration` | Dev branch feature tracking. Feature lifecycle (merged_to_dev -> validated -> promoted/excluded). Cross-table queue auto-completion. Promotion/sync/validate triggers. Exclude/re-include features. |
| **Sprint** | `/api/sprint` | Sprint cycle status per project. Sprint brief reading. Role progress tracking. Sprint findings by role. Sprint completion history. |
| **Backpressure** | `/api/queue/pressure` | Graduated load management: GREEN (<70%), YELLOW (70-85%), RED (85-95%), BLACK (>95%). Affects concurrency, model selection, turn caps. |
| **Health** | `/api/health` | Simple health check (always public). |

### 1.2 Database Schema (16 Tables)

- **projects** -- Managed repositories with mode, priority, branch, custom instructions
- **runs** -- Execution history with token metrics, verdicts, trace IDs, Agent Teams fields
- **config** -- Key-value settings store
- **plans** -- Implementation plans with step-by-step instructions and file lists
- **coordinator_tasks** -- DAG task records with dependencies, status, employee assignment
- **coordinator_messages** -- Guidance and conflict messages between coordinator and employees
- **notifications** -- Run completion alerts (approve/reject/pr/error)
- **task_queue** -- Work queue with state machine, priority, escalation, handoff context
- **plan_usage_history** -- Token usage tracking snapshots over time
- **agent_events** -- Structured audit trail (ESAA) with causal chains
- **task_outcomes** -- Adaptive scheduling learning data (mode, model, success, confidence)
- **brainstorm_sessions** -- AI brainstorm conversations
- **brainstorm_messages** -- Individual messages in brainstorm sessions
- **integration_features** -- Feature branches merged to dev, validation status
- **prompt_versions** -- Prompt A/B testing with success rate tracking
- **integration_features** -- Features merged into integration branch

### 1.3 Real-Time Channels

| Channel | Protocol | Purpose |
|---------|----------|---------|
| Agent Events | SSE (`/api/events/stream`) | All run lifecycle events, coordinator events, queue changes, Agent Teams orchestration |
| Log Streaming | WebSocket (`/api/logs/stream`) | Live agent log tailing with per-file selection |
| Brainstorm | SSE (per-message endpoint) | Streaming AI responses during brainstorm sessions |

### 1.4 Agent Architecture

The system manages autonomous Claude Code agents in a Manager/Employee/Analyst hierarchy:

- **Manager** -- Reviews employee work, issues verdicts (APPROVE/PR/REJECT)
- **Employee** -- Implements features/fixes from GitHub issues, commits locally
- **Analyst** -- Analyzes codebase, creates/refines GitHub issues
- **Planner** -- Creates detailed implementation plans
- **Assigner** -- Distributes issues among parallel employees
- **Security Reviewer** -- Dedicated security review of code changes
- **Coordinator** -- Python-based multi-employee orchestration with DAG scheduling
- **Agent Teams** -- Claude's native multi-agent system with shared task lists and mailbox messaging

---

## 2. Design Philosophy: "Orbital Command"

This is NOT a generic dark dashboard. This is a **mission control for autonomous intelligence** -- a living observatory where humans oversee a constellation of AI agents writing code in real-time. Every surface, every glow, every particle exists to make the operator feel like they are commanding a deep space network of code-writing machines.

### 2.1 Core Principles

**Principle 1: The Station is Alive.**
This is not a static dashboard showing historical data. It is a living control room for autonomous agents that are actively writing code, creating PRs, and making decisions. Every pixel should communicate whether the station is idle, working, or needs attention. The Station Pulse -- a particle constellation rendered on canvas -- is the heartbeat of this principle. When agents work, the station breathes with them.

**Principle 2: Progressive Disclosure.**
The station manages enormous complexity (DAG scheduling, multi-agent coordination, escalation ladders, confidence calibration). The default view should show only what matters right now. Details unfold on demand through drill-down, expansion, and contextual panels. Information density is high -- like a Bloomberg Terminal -- but layered so that the surface is calm and the depths are rich.

**Principle 3: Opinionated Defaults, Escape Hatches.**
The dashboard should surface the right view automatically. If agents are running, show live activity. If the queue is backed up, surface the queue. If usage is hitting limits, show the budget. But always let the user override and navigate freely.

**Principle 4: Agency-Aware Design.**
Traditional dashboards show what happened. An agentic dashboard must also show: what the agent decided, why it decided it, what it plans to do next, and how confident it is. Decision transparency is a first-class UI concern. Glow intensity, particle velocity, and color temperature all encode meaning -- not just labels and badges.

**Principle 5: Keyboard-First, Terminal-Bred.**
Power users managing autonomous agents need speed. Every major action should be reachable via keyboard shortcut. Command palette for everything. Vim-style navigation where appropriate. The aesthetic pays homage to terminal culture: monospace data, information density, no wasted whitespace on decorative elements. This is a tool, not a marketing page.

### 2.2 Design Language: Deep Space Observatory meets Bloomberg Terminal

The visual language draws from four deliberate sources:

- **NASA JPL Deep Space Network control rooms** -- Data density with calm authority. Telemetry displays, confidence indicators, status boards that have run 24/7 for decades. The feeling of overseeing something important operating autonomously at great distance.
- **Bloomberg Terminal** -- Information-rich, professional, keyboard-driven. No hand-holding. Respects the operator's intelligence. Data-forward with zero fluff.
- **Blade Runner 2049 color palette** -- Warm amber and cold cyan against deep blacks. A color language that feels futuristic without being cartoonish. Glow as a design primitive, not a gimmick.
- **Eve Online UI** -- Sci-fi data visualization with personality. Particle systems, orbital mechanics as metaphor, holographic panels floating in void. UI that tells a story about the world it operates in.

The result is an interface that looks like it was built to monitor a constellation of autonomous spacecraft -- because conceptually, it was.

### 2.3 Emotional Arc

The dashboard communicates three emotional states through color, motion, and luminance:

- **Calm confidence** (default) -- Void-dark surfaces, soft cyan accents, the Station Pulse orbiting slowly in a gentle ring. The warm white text against blue-black creates a serene observatory atmosphere. Agents are working, system is healthy, ambient glow is low.
- **Active attention** -- Violet light blooms across active elements. The Station Pulse accelerates, trailing light. Running agent cards emit soft violet glow. The interface feels electric but controlled -- something important is happening and the station knows it.
- **Requires intervention** -- Amber saturates the Station Pulse, which contracts and pulses like a heartbeat. Warning elements glow with warm amber halos. The emotional shift is unmistakable: the station is asking for human judgment. Rose glow appears only for failures -- a deliberate alarm that demands resolution.

### 2.4 The Unforgettable Element: Station Pulse

The Station Pulse is not a status dot. It is a **particle constellation** rendered on an HTML5 canvas -- the visual signature of the entire application.

- **Idle**: Softly orbiting particles in a ring, slow breathing opacity, cyan tint. Electric but peaceful. "All quiet."
- **Working**: Particles accelerate, trail violet light. One bright node per active agent. Orbital speed increases with agent count. Faint electric connections (lines) form between nodes when agents coordinate. The more agents running, the more alive the constellation becomes.
- **Attention**: Particles cluster and pulse amber. The constellation contracts and expands like a heartbeat. The rhythm is biological, urgent -- it demands the operator's gaze.
- **Scanline overlay**: Very subtle horizontal line pattern on the canvas (a nod to CRT monitors). Barely visible, but subconsciously reinforces the "command center terminal" feel.

---

## 3. Information Architecture

```
Station
|
+-- Command Center (/)
|   Home view. System pulse, active runs, recent outcomes, usage gauges, alerts.
|
+-- Runs (/runs)
|   +-- Run List (filterable, sortable)
|   +-- Run Detail (/runs/:id)
|       +-- Overview tab (status, verdict, tokens, duration)
|       +-- DAG tab (coordinator task graph visualization)
|       +-- Team tab (Agent Teams member status, when applicable)
|       +-- Diff tab (side-by-side code changes)
|       +-- Logs tab (live streaming or historical)
|       +-- Intelligence tab (decisions, confidence, escalation)
|
+-- Queue (/queue)
|   Board view (Kanban) or list view of work items.
|   Drag-and-drop priority. Inline state transitions.
|   Backpressure gauge. Escalation ladder visualization.
|
+-- Plans (/plans)
|   Plan list with status indicators.
|   Plan detail with markdown rendering, step checklist, file list.
|   Approve / reject / implement actions.
|
+-- Projects (/projects)
|   +-- Project List (cards with health indicators)
|   +-- Project Detail (/projects/:id)
|       +-- Config (mode, priority, branch, instructions)
|       +-- Run History (filtered to this project)
|       +-- Plans (filtered)
|       +-- Integration Status (dev branch, features, validation)
|       +-- Sprint Status (current sprint, findings)
|       +-- Intelligence (per-project success rates)
|
+-- Intelligence (/intelligence)
|   Learning loop dashboard. Success rates, confidence calibration,
|   token efficiency, escalation stats. Prompt version A/B comparison.
|
+-- Brainstorm (/brainstorm)
|   Chat interface with expert personas.
|   Session list. Streaming AI responses. Project context.
|
+-- Settings (/settings)
|   +-- Station Config (models, limits, schedule)
|   +-- Notifications (webhook targets, test button)
|   +-- Prompts (editor with diff against defaults)
|   +-- Auth (Claude OAuth, GitHub OAuth)
|   +-- System (service control, resources)
```

---

## 4. Component Catalog

### 4.1 Global Components

| Component | Description |
|-----------|-------------|
| **AppShell** | Root layout: collapsible sidebar, top bar, content area. Sidebar shows navigation + system status (service active, auth status, backpressure level). |
| **CommandPalette** | Cmd+K overlay. Search runs, projects, queue items, plans. Quick actions: trigger run, pause queue, navigate. Fuzzy matching. |
| **NotificationCenter** | Bell icon in top bar. Stacks notifications by type. Unread count badge. Mark read/dismiss. Links to relevant run. |
| **SystemStatusBar** | Persistent bottom bar or sidebar widget. Shows: agent service status (running/stopped/timer), Claude auth status (OK/expiring/expired), GitHub connection, backpressure level (color-coded), SSE connection status, active subscriber count. |
| **ToastSystem** | Non-blocking success/error/info messages. Auto-dismiss with undo option. Used for action confirmations. |
| **BreadcrumbNav** | Context-aware breadcrumbs. E.g., Projects > claude-agent-station > Run 20260326T... > DAG |
| **KeyboardShortcutOverlay** | ? key opens shortcut reference sheet. |

### 4.2 Command Center Components

| Component | Description |
|-----------|-------------|
| **StationPulse** | Hero component. Canvas-rendered particle constellation showing overall station state: Idle (softly orbiting cyan particles in a ring, breathing opacity), Working (particles accelerate with violet trails, one bright node per active agent, electric connections between nodes), Attention Needed (particles cluster and pulse amber, heartbeat contraction/expansion). Scanline overlay for CRT aesthetic. Below it: one-line summary ("3 agents working on 2 projects" or "All quiet. Next run in 47 minutes"). |
| **ActiveAgentStrip** | Horizontal card strip showing each currently running agent/employee. Each card: project avatar, issue number, mode badge, progress indicator (turns used), elapsed time. Click to drill into run detail. Expands to show Agent Teams members when applicable. |
| **RecentOutcomesFeed** | Vertical timeline of the last 10-15 completed runs. Each entry: project, issue, verdict badge (green APPROVE, blue PR, red REJECT), token count, duration. Color-coded left border. Click to expand inline or navigate to detail. |
| **UsageBudgetGauge** | Circular or bar gauge showing weekly plan usage. Segmented by model (Opus, Sonnet, Haiku). Color transitions: green -> yellow -> red as usage approaches threshold. Shows days until reset. |
| **QueuePreview** | Compact queue summary. Shows counts by state (pending, in-progress, review). Top 3 pending items. "View Queue" link. If backpressure is YELLOW/RED/BLACK, shows warning banner. |
| **PlanUsageMiniChart** | Sparkline or small area chart of weekly token consumption trend. Sourced from plan-usage history snapshots. |
| **AlertBanner** | Conditional banner at top of page. Shows: auth expiring, circuit breaker open, BLACK backpressure, stale runs detected. Dismissible but re-appears if condition persists. |

### 4.3 Run Components

| Component | Description |
|-----------|-------------|
| **RunTable** | Paginated table with: status icon, project, issue, mode, model, verdict badge, tokens (formatted as "1.2M"), duration (formatted as "4m 32s"), started_at (relative time). Column sorting. Filter bar: project dropdown, status chips, verdict chips. |
| **RunDetailHeader** | Full-width header for run detail. Project name, issue link (opens GitHub), run ID with copy button, status badge (with animation for running), verdict badge, timing (started, duration, finished). Action buttons: view logs, view diff, trigger re-run. |
| **CoordinatorDAGView** | Interactive directed graph visualization. Nodes are tasks, edges are dependencies. Node color reflects status (gray=pending, blue=ready, spinning=running, green=completed, red=failed, dark=blocked). Click node to expand inline details. Shows employee assignment, touched files, result summary. |
| **TeamMemberGrid** | For Agent Teams runs. Grid of teammate cards. Each card: agent name, status badge, task assignment, turns/tokens used, files touched count. Live-updating via SSE. |
| **RunDiffViewer** | Split-pane or unified diff viewer. File tree sidebar with addition/deletion counts. Syntax highlighting. Line-level navigation. Supports new/deleted/renamed/binary file indicators. |
| **RunLogViewer** | Terminal-style log viewer. Streams live via WebSocket for active runs, loads historical for completed runs. JSONL parsing with structured event rendering. Search within logs. Auto-scroll toggle. Color-coded by event type. |
| **VerdictCard** | Prominent verdict display. Large badge (APPROVE/PR/REJECT) with reasoning text below. For PR verdicts, shows linked PR number. |
| **EmployeeReportPanel** | Collapsible panel showing structured employee report JSON rendered as readable sections: requirements checklist, changes made, test results, confidence score, files modified. |
| **IntelligenceDecisionTimeline** | Chronological list of intelligence.* events for a run. Shows routing decisions, model selection, escalation steps, confidence reports. Each event expandable to see full JSON payload. |
| **GuidanceSender** | Modal or slide-over panel for sending human guidance to a running employee. Dropdown: guidance type (info/warning/redirect/stop). Text area for content. Employee selector (by index). Shows current employee workspace and task. |
| **RunFullContextLayout** | Master layout for the unified run detail view. Uses the `/runs/{id}/full` endpoint to load everything in one call. Tab bar switches between Overview, DAG, Team, Diff, Logs, Intelligence sub-views. |

### 4.4 Queue Components

| Component | Description |
|-----------|-------------|
| **QueueBoard** | Kanban-style board with columns: Pending, In Progress, Review, Completed, Failed. Cards show: project icon, issue number+title, priority badge, mode chip, elapsed time. Drag to reorder within Pending. Click for detail. |
| **QueueListView** | Table alternative to board. Columns: state, project, issue, priority, mode, complexity score, escalation rung, assigned employee, created/updated timestamps. Sortable, filterable. |
| **QueueItemDetail** | Slide-over panel. Full item data: state history, employee report, manager feedback, error message, handoff context, escalation chain (if escalated_from is set). State transition buttons with validation. |
| **BackpressureIndicator** | Visual gauge showing current backpressure level with explanation. GREEN: "Full speed", YELLOW: "Reduced concurrency (Sonnet preferred)", RED: "Single employee, Sonnet only", BLACK: "Paused -- no new work". |
| **QueueActions** | Toolbar: Batch pause, Purge completed, Add item manually, Refresh. Claim work button (for testing). |
| **EscalationLadderView** | Visual representation of an item's escalation history. Shows each rung with the mode/model used, outcome, and confidence. Connected by arrows. |

### 4.5 Plan Components

| Component | Description |
|-----------|-------------|
| **PlanCard** | Card view: title, project, issue reference, status badge, scope estimate (small/medium/large), file count. Click for detail. |
| **PlanDetail** | Full plan view. Markdown rendering of description. Steps rendered as an interactive checklist (visual only, not editable). Files affected shown as a file tree. Action bar: Approve, Reject, Implement. Status history. |
| **PlanApprovalFlow** | Confirmation dialog for plan approval. Shows what will happen: "Approving this plan will create a queue item and make it available for the next agent run." |

### 4.6 Project Components

| Component | Description |
|-----------|-------------|
| **ProjectCard** | Card with: repo name (owner/name format), mode badge, priority badge, enabled/disabled toggle, last run time, success rate mini-bar. |
| **ProjectConfigForm** | Form for editing project settings. Mode selector (dropdown with descriptions). Priority selector. Branch input. Custom instructions (markdown editor). Setup script (code editor). Security review toggle. |
| **IntegrationStatusPanel** | Dev branch health dashboard. Feature count, validated count, conflict count. Feature list with state badges. Actions: trigger sync, trigger validate, trigger promote. Exclude/re-include individual features. |
| **SprintStatusPanel** | Current sprint brief display. Role progress indicators (each role as a card: visionary, architect, security, quality, performance, designer). Findings drill-down by role. Sprint history timeline. |
| **ProjectIntelligencePanel** | Per-project success rates by mode. Token efficiency chart. Historical trend of success rate over time. |

### 4.7 Intelligence Components

| Component | Description |
|-----------|-------------|
| **SuccessRateMatrix** | Heatmap or table showing success rate by mode x model combination. Cell color from red (0%) to green (100%). Sample count shown in each cell. Click for drill-down. |
| **ConfidenceCalibrationChart** | Scatter plot or grouped bar chart. X-axis: reported confidence buckets (0-0.5, 0.5-0.7, 0.7-0.85, 0.85-0.95, 0.95-1.0). Y-axis: actual success rate. Diagonal line shows perfect calibration. Points above the line = overconfident, below = underconfident. |
| **TokenEfficiencyChart** | Grouped bar chart comparing avg tokens on success vs failure by mode. Highlights which modes are token-efficient. |
| **EscalationFunnelChart** | Funnel or stepped bar chart showing success rate at each escalation rung. Visualizes how many tasks need escalation and whether escalation helps. |
| **PromptVersionTable** | Table of prompt versions per role. Columns: version number, active status, content hash, change description, success rate, sample count. Compare button to diff two versions. |
| **IntelligenceInsightCards** | Summary cards at top of intelligence page: total learning samples, overall success rate, most efficient mode, confidence calibration score. |

### 4.8 Brainstorm Components

| Component | Description |
|-----------|-------------|
| **SessionList** | Sidebar list of brainstorm sessions. Each entry: title (auto-generated from first message), persona icon, project name, message count, last updated. New session button. |
| **ChatInterface** | Main chat area. Message bubbles: user on right, assistant on left. Assistant messages render markdown with syntax highlighting. Streaming indicator (typing dots) during response generation. |
| **PersonaSelector** | Icon grid or dropdown to select expert persona for new sessions: Architect, Security, Performance, DevOps. Each with description tooltip. |
| **ProjectContextBadge** | Chip showing which project (if any) is attached to the session. Clickable to change. |

### 4.9 Settings Components

| Component | Description |
|-----------|-------------|
| **ModelConfigEditor** | Form for configuring employee and manager model selection. Dropdown with model options. |
| **LimitsConfigEditor** | Form for limits: max_employee_turns, max_employee_budget, max_manager_turns, max_usage_percent, reserve_percent, max_concurrent_employees. Visual budget bar preview. |
| **ScheduleConfigEditor** | Schedule input with helper text. Shows next scheduled run based on timer. |
| **NotificationConfigEditor** | Enable/disable toggle. Webhook URL input. Type selector (generic, Slack, Discord, Telegram). Notify-on checkboxes (approve, reject, pr, error). Multi-target support with add/remove. Test button. |
| **PromptEditor** | Side-by-side view: default prompt (read-only) on left, custom override (editable) on right. Diff highlighting between default and custom. Reset to default button. Syntax highlighting for markdown. |
| **AuthPanel** | Two sections: Claude CLI auth (status, expiry countdown, refresh button, login flow) and GitHub auth (connection status, username, scopes, connect/disconnect). |
| **ServiceControlPanel** | Start/stop/restart buttons for agent service and timer. Status indicators. System resources: memory, load average, disk free, uptime. |

---

## 5. Interaction Patterns

### 5.1 Human-in-the-Loop Guidance

This is the most distinctive interaction in an agentic dashboard. When an agent is running, the user can intervene in real-time.

**Flow:**
1. User sees active agent in Command Center or Run Detail
2. Opens Guidance Sender (keyboard shortcut: `G`)
3. Selects guidance type:
   - **Info** -- Additional context or clarification
   - **Warning** -- Alert about a potential issue
   - **Redirect** -- Change approach or focus area
   - **Stop** -- Halt current work immediately
4. Types message content
5. Sends -- guidance is written to employee workspace as a file
6. Dashboard shows guidance in CoordinatorMessage timeline
7. Agent picks up guidance on next tool call

**UI Pattern:** Slide-over panel from the right, pre-populated with run context. Non-modal so user can still observe the live log stream while composing guidance.

### 5.2 Plan Approval Pipeline

**Flow:**
1. Agent creates a plan (visible in Plans list as "draft")
2. User reviews plan in PlanDetail view
3. User clicks Approve or Reject
4. On Approve: backend auto-creates queue item, plan status -> "approved"
5. User can optionally click "Implement Now" to trigger immediate execution
6. Agent service picks up queue item, runs implementation
7. Plan status transitions: implementing -> completed

### 5.3 Queue Management

**Flow:**
1. GitHub webhook creates queue items automatically (issues, PRs)
2. User can manually add items via Queue page
3. Items are prioritized by priority field and creation time
4. Agents claim items via work-stealing pattern
5. User can pause all items for a run (batch pause)
6. User can manually transition item states
7. Escalation: if an item fails, it can be re-queued at a higher escalation rung

### 5.4 Live Activity Monitoring

**Flow:**
1. SSE connection established on page load
2. Command Center shows real-time agent activity
3. New events trigger UI updates without polling:
   - Run started -> agent appears in ActiveAgentStrip
   - Task completed -> DAG node turns green
   - Verdict issued -> notification + feed update
   - Queue item state change -> Kanban board updates
4. WebSocket streams live logs when viewing a running agent's logs
5. Brainstorm responses stream in real-time via SSE

### 5.5 Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Cmd+K` / `Ctrl+K` | Open command palette |
| `G` | Open guidance sender (when on run detail) |
| `T` | Trigger agent run |
| `1-6` | Navigate to main sections (Command Center, Runs, Queue, Plans, Projects, Intelligence) |
| `?` | Show keyboard shortcuts |
| `J/K` | Navigate up/down in lists |
| `Enter` | Open selected item |
| `Esc` | Close modal/panel, go back |
| `R` | Refresh current view |
| `F` | Toggle filter panel |

---

## 6. Real-Time Features

### 6.1 SSE Event Stream

The frontend maintains a persistent SSE connection to `/api/events/stream`. All backend webhook events are broadcast. The client normalizes events and routes them to the appropriate stores.

**Event routing:**
- `run_start`, `employee_start`, `employee_complete`, `run_complete` -> Run store (update active agents, run status)
- `verdict_execute` -> Notification center + recent outcomes feed
- `task_started`, `task_completed`, `task_failed` -> Coordinator DAG store (update node colors)
- `queue_*` -> Queue store (update board/list)
- `guidance_sent`, `conflict_detected` -> Coordinator message store
- `team_created`, `teammate_spawned`, `teammate_completed` -> Team summary store
- `progress_update` -> Active agent metrics (tokens, turns)

**Reconnection:** Exponential backoff with jitter. Visual indicator in SystemStatusBar when disconnected. Automatic catch-up via API poll on reconnect.

### 6.2 WebSocket Log Streaming

For live log viewing, a WebSocket connection is opened to `/api/logs/stream?file=<path>`. Lines arrive as raw text (JSONL). The client parses each line:
- `type: "assistant"` -> Render as agent output (distinct styling)
- `type: "tool_use"` -> Render as tool call (collapsible, shows tool name)
- `type: "tool_result"` -> Render as tool output
- `type: "result"` -> Render as completion summary

Auto-scroll with "stick to bottom" toggle. Pause scrolling when user scrolls up. Resume when user clicks "Jump to latest."

### 6.3 Brainstorm Streaming

The brainstorm chat uses SSE per-message. Events:
- `delta` -> Append text chunk to current assistant message (rendered progressively)
- `error` -> Show error in chat
- `done` -> Finalize message, enable input

Keepalive comments (`: keepalive`) are emitted during Claude CLI thinking time to prevent proxy timeouts.

### 6.4 Polling Fallbacks

Some data changes outside the SSE/WebSocket channels:
- System status (service, timer, resources) -- poll every 30s
- Auth status -- poll every 60s (auto-refresh is server-side)
- Plan usage -- poll every 60s
- Analytics -- poll on page visit, no live update needed

---

## 7. Visual Design System

### 7.1 Color Palette (Dark Mode Only, with Glow Effects)

The dashboard is dark mode only. The use case (autonomous agent monitoring, mission-critical observation) naturally maps to dark interfaces. But this is not generic dark mode -- every color is chosen to evoke a deep space observatory. Pure black is never used. Pure white is never used. Everything has a blue-purple tint that places you in the void.

**Base Layers:**
- Void: `#06060A` (near-black with blue tint -- the deepest background, NOT pure black)
- Surface 0: `#0C0C12` (base card background)
- Surface 1: `#12121A` (elevated surfaces, sidebar)
- Surface 2: `#1A1A24` (modals, popovers, command palette)
- Border: `#1E1E2A` (subtle, low contrast -- visible but never aggressive)
- Border Hover: `#2A2A3A` (brightens on interaction)

**Text:**
- Primary: `#E8E8ED` (warm white -- readable without the harshness of pure white)
- Secondary: `#8888A0` (muted lavender-gray for supporting text)
- Tertiary: `#555568` (timestamps, metadata, de-emphasized content)
- Ghost: `#333344` (placeholder text, disabled states, skeleton text)

**Accent Colors with Glow (the key differentiator):**

Each accent color has a base value AND a glow shadow. Glow is what makes this design system unmistakable. Active elements emit light. Inactive elements are dark. The contrast between glowing and dormant creates immediate visual hierarchy without relying on size or weight alone.

| Name | Hex | Glow Shadow | Usage |
|------|-----|-------------|-------|
| Cyan (Primary) | `#00E5FF` | `0 0 20px rgba(0,229,255,0.3)` | Active states, primary actions, focused inputs, the Station Pulse at idle |
| Violet (Agent Activity) | `#8B5CF6` | `0 0 20px rgba(139,92,246,0.3)` | Running agents, in-progress states, working indicators |
| Amber (Warning/Attention) | `#F59E0B` | `0 0 15px rgba(245,158,11,0.25)` | Warnings, pending items, attention-needed states |
| Emerald (Success) | `#10B981` | `0 0 15px rgba(16,185,129,0.25)` | Approved verdicts, completed tasks, healthy status |
| Rose (Error/Reject) | `#F43F5E` | `0 0 15px rgba(244,63,94,0.25)` | Failures, rejections, critical alerts |
| Indigo (Info/PR) | `#6366F1` | `0 0 15px rgba(99,102,241,0.25)` | PR verdicts, informational states, review actions |

**Semantic Mapping:**
- Success/Approve: Emerald `#10B981` with emerald glow
- Warning: Amber `#F59E0B` with amber glow
- Error/Reject: Rose `#F43F5E` with rose glow
- Info/PR: Indigo `#6366F1` with indigo glow
- Running: Violet `#8B5CF6` with violet glow
- Neutral: `#555568` (no glow -- dormant by design)

**Backpressure:**
- GREEN: Emerald `#10B981`
- YELLOW: Amber `#F59E0B`
- RED: Rose `#F43F5E`
- BLACK: Surface 0 `#0C0C12` with Rose `#F43F5E` border and faint rose glow

**Verdict Badges:**
- APPROVE: Emerald background `#10B981`, void text `#06060A`, emerald glow on hover
- PR: Indigo background `#6366F1`, white text `#E8E8ED`, indigo glow on hover
- REJECT: Rose background `#F43F5E`, void text `#06060A`, rose glow on hover

**Mode Badges (distinctive, with personality):**
- full: Violet `#8B5CF6` with violet glow -- the most capable mode glows the brightest
- analyze: Cyan `#00E5FF` -- observation, scanning, reading
- plan: Amber `#F59E0B` -- deliberation, strategy
- triage: Orange `#F97316` -- urgency, sorting
- review: Indigo `#6366F1` -- judgment, evaluation
- fix: Emerald `#10B981` -- resolution, healing

### 7.2 Typography (Characterful, Not Generic)

The type system is deliberately NOT Inter. Inter is the new Arial -- ubiquitous, invisible, personality-free. Orbital Command has a distinct typographic voice.

**Font Stack:**

| Role | Typeface | Weight Range | Rationale |
|------|----------|-------------|-----------|
| **Headings** | `Syne` | 400-800 (variable) | Geometric, futuristic, characterful. Immediately signals "this is not another SaaS dashboard." Wide letterforms with personality. |
| **Body** | `DM Sans` | 400, 500, 700 | Clean, modern, excellent readability at small sizes. Slightly more geometric than Inter, slightly warmer. Not overused. |
| **Monospace/Data** | `JetBrains Mono` | 400, 700 | Best-in-class monospace for code and data. Ligatures enabled (`font-feature-settings: "liga" 1`). Used for ALL numerical data, run IDs, log output, diff viewer, code blocks, token counts, file paths. |

**Type Scale:**

| Element | Font | Size | Weight | Line Height | Letter Spacing |
|---------|------|------|--------|-------------|---------------|
| H1 (Page Title) | Syne | 32px | 700 | 1.2 | -0.02em |
| H2 (Section Title) | Syne | 24px | 600 | 1.3 | -0.01em |
| H3 (Card Title) | Syne | 18px | 600 | 1.4 | 0 |
| H4 (Subsection) | DM Sans | 16px | 700 | 1.4 | 0 |
| Body | DM Sans | 14px | 400 | 1.6 | 0 |
| Body Compact | DM Sans | 13px | 400 | 1.5 | 0 |
| Caption | DM Sans | 12px | 400 | 1.4 | 0.01em |
| Data/Numbers | JetBrains Mono | 14px | 400 | 1.5 | 0 |
| Data Large | JetBrains Mono | 24px | 700 | 1.2 | -0.02em |
| Code Block | JetBrains Mono | 13px | 400 | 1.6 | 0 |

**Number Treatment:**
- ALL numbers use `font-variant-numeric: tabular-nums` for column alignment
- ALL numerical data (token counts, durations, run counts, percentages) renders in JetBrains Mono regardless of surrounding body font
- Key metrics on the Command Center use the Data Large style (24px JetBrains Mono bold) with counting animations on first load (odometer-style)

**Font Loading:**
- Google Fonts with `display=swap` for Syne and DM Sans
- Self-hosted JetBrains Mono (critical for data-heavy views, no FOUT acceptable)
- Subset to latin + latin-ext for smaller payload

### 7.3 Atmospheric Effects

These effects elevate the interface from "dark dashboard" to "deep space observatory." Each is subtle in isolation but together they create an unmistakable atmosphere.

**Noise Texture Overlay:**
Subtle 2% opacity grain applied via CSS on all surfaces. Creates the perception of physical material -- screens in a control room have texture. Implementation: CSS `background-image` with inline SVG noise pattern (no external image request).

```css
.surface-noise::after {
  content: '';
  position: absolute;
  inset: 0;
  opacity: 0.02;
  background-image: url("data:image/svg+xml,..."); /* inline SVG turbulence */
  pointer-events: none;
  mix-blend-mode: overlay;
}
```

**Gradient Mesh Backgrounds:**
Page backgrounds use subtle radial gradients to create depth. Never flat -- the void has dimension.
- Command Center: Radial gradient from dark indigo center (`#0A0A1A`) to void edges (`#06060A`)
- Run Detail: Faint violet gradient wash when run is active
- Settings: Neutral, minimal gradient (this page is functional, not atmospheric)

**Glass Panels:**
Key stat cards on the Command Center use `backdrop-filter: blur(12px)` with 60% opacity background. This creates depth when layered over the gradient mesh. The glass effect is reserved for hero-level components only (Station Pulse area, key metric cards) -- overuse would cheapen it.

```css
.glass-panel {
  background: rgba(12, 12, 18, 0.6);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(30, 30, 42, 0.5);
}
```

**Glow Emission:**
Active and running elements emit light via `box-shadow`. This is the single most distinctive visual feature of the design system. Glow creates hierarchy without size changes.
- Status dots: Active states glow their accent color
- Active nav items: Faint cyan glow on the left border
- Focused inputs: Cyan glow replaces the typical focus ring
- Running agent cards: Violet glow border
- Mode badges: Each mode emits its assigned glow color

### 7.4 Spacing

8px base grid. All spacing is multiples of 8: 4px (half), 8px, 12px, 16px, 24px, 32px, 48px, 64px.

**Orbital Command-specific spacing:**
- **Card gutters**: 24px between cards (generous -- breathing room is authority)
- **Section spacing**: 32px between major sections
- **Sidebar collapsed**: 64px wide (icons only, glow indicator on active item)
- **Sidebar expanded**: 240px wide. Background: Surface 0 with subtle left-edge glow on active nav item.

### 7.5 Spatial Composition

**Asymmetric Hero (Command Center):**
The Command Center does NOT use a symmetric grid. The Station Pulse particle constellation occupies 40% of the left side (a living, breathing canvas), while the metrics grid occupies 60% on the right. This asymmetry creates visual tension and draws the eye to the pulse first -- the most important status indicator.

**Depth via Elevation:**
Three elevation levels, differentiated by background lightness AND shadow:
- **Flat** (Level 0): Surface 0 `#0C0C12`, no shadow. Default cards.
- **Raised** (Level 1): Surface 1 `#12121A`, `0 4px 12px rgba(0,0,0,0.3)`. Hovered cards, active panels.
- **Floating** (Level 2): Surface 2 `#1A1A24`, `0 16px 48px rgba(0,0,0,0.5)`. Modals, command palette, popovers.

### 7.6 Radius

- Small elements (badges, chips): 6px
- Cards, inputs: 8px
- Modals: 12px
- Full round (avatars, status dots): 9999px

### 7.7 Animations and Motion Design

Motion in Orbital Command is deliberate. Nothing moves without purpose. Speed conveys urgency. Glow conveys state. Stagger conveys sequence.

- **Page transitions:** Crossfade with 150ms ease-out. No sliding -- pages materialize.
- **List item stagger:** Items enter with 30ms stagger, slide-up 12px + fade. Creates the feeling of data arriving from a feed.
- **Status morphs:** Color transitions use 400ms ease with simultaneous glow intensity change. When a run completes, the violet glow fades as emerald glow rises -- a smooth handoff of visual energy.
- **Running indicators:** Smooth orbital animation using CSS `@keyframes orbit`. A dot traces an elliptical path around the status indicator. Speed increases with agent activity.
- **Hover states:** Cards lift 2px (`transform: translateY(-2px)`) with increased border brightness and subtle glow appearance. The card "wakes up" on hover.
- **Skeleton loaders:** Dark shimmer with cyan highlight sweep (left-to-right, 1.5s). The loading state feels like a scanner beam, consistent with the observatory theme.
- **Number counting:** Key metrics on first load use an odometer-style counting animation (200ms per digit, ease-out). Token counts, run counts, success percentages all count up from zero on page entry.
- **Station Pulse:** Canvas-rendered particle system. Idle: 3s breathing cycle. Working: orbital velocity scales with agent count. Attention: contraction/expansion heartbeat at 1.5s intervals.
- **Chart data updates:** Smooth interpolation (500ms) with data point glow flash on change.

```css
@keyframes orbit {
  from { transform: rotate(0deg) translateX(8px) rotate(0deg); }
  to   { transform: rotate(360deg) translateX(8px) rotate(-360deg); }
}

@keyframes glow-pulse {
  0%, 100% { box-shadow: 0 0 15px rgba(0, 229, 255, 0.2); }
  50%      { box-shadow: 0 0 25px rgba(0, 229, 255, 0.4); }
}

@keyframes shimmer {
  from { background-position: -200% 0; }
  to   { background-position: 200% 0; }
}
```

### 7.8 Icons

**Lucide** (MIT licensed) -- clean, consistent stroke weight, extensive coverage.
- Default: 18px
- Compact/inline: 16px
- Hero actions: 24px
- Navigation: 20px with 40px hitbox (generous touch target)

**Icon Behavior:**
- Active navigation icons receive their accent color + glow
- Inactive icons use Tertiary text color `#555568`
- Hover: icons brighten to Secondary `#8888A0`
- Icons in status contexts inherit their status color (emerald for success, rose for error, etc.)

---

## 8. Technology Stack

### 8.1 Core Framework

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| **Framework** | Svelte 5 (SvelteKit) | Existing project uses Svelte 5. Runes for reactive state. Compiled output, no runtime. SvelteKit provides file-based routing, SSR capability for initial load. |
| **Build** | Vite | Already in project. Fast HMR, optimized production builds. |
| **Styling** | Tailwind CSS 4 | Already in project. Utility-first for rapid iteration. Custom theme via config. |
| **Language** | TypeScript (strict) | Type safety for complex data models. Matches backend Pydantic schemas. |

### 8.2 Key Libraries

| Library | Purpose |
|---------|---------|
| **bits-ui** | Headless accessible UI primitives (dialog, popover, dropdown, combobox for command palette). Svelte-native. |
| **@tanstack/svelte-query** | Async data fetching with caching, invalidation, optimistic updates. Reduces SSE->refetch boilerplate. |
| **d3** (selective imports) | DAG visualization (force-directed or Dagre layout for coordinator task graphs), charts (token usage, calibration). Only import needed modules. |
| **shiki** | Syntax highlighting for diff viewer and code blocks in brainstorm. WASM-based, fast. |
| **marked** + **DOMPurify** | Markdown rendering for plans, brainstorm messages, employee reports. XSS-safe. |
| **fuse.js** | Client-side fuzzy search for command palette. |
| **cmdk-sv** | Command palette component (Svelte port of cmdk). |
| **date-fns** | Date formatting (relative times, durations). Tree-shakeable. |

### 8.3 State Architecture

```
stores/
  connection.svelte.ts    -- SSE + WebSocket connection state
  runs.svelte.ts          -- Run list, active agents, run detail cache
  queue.svelte.ts         -- Queue items, stats, backpressure
  projects.svelte.ts      -- Project list and config
  plans.svelte.ts         -- Plan list and detail
  coordinator.svelte.ts   -- DAG tasks, messages per run
  intelligence.svelte.ts  -- Learning insights, outcomes
  notifications.svelte.ts -- Notification stack, unread count
  system.svelte.ts        -- Service status, auth, resources
  brainstorm.svelte.ts    -- Sessions, messages, streaming state
  ui.svelte.ts            -- Sidebar collapsed, active modals, theme
```

Each store is a Svelte 5 rune-based reactive store. SSE events are dispatched to the appropriate store via a central event router. Stores use `@tanstack/svelte-query` for server state and Svelte 5 `$state` runes for client state.

### 8.4 API Client

Typed API client generated from backend Pydantic schemas (manual TypeScript interfaces mirroring `schemas.py`). Centralized fetch wrapper with:
- Bearer token injection
- Error handling with toast notifications
- Request deduplication
- Timeout handling

---

## 9. Page-by-Page Specifications

### 9.1 Command Center (`/`)

**Layout:** Full-width, no sidebar content area.

**Sections (top to bottom):**

1. **Alert Banner** (conditional) -- Full-width. Appears when: auth expiring (<1h), backpressure RED/BLACK, service stopped, no GitHub connection. Dismissible but returns if condition persists.

2. **Station Pulse** (asymmetric hero, left 40%) -- Canvas-rendered particle constellation + summary text. Three states:
   - Idle: Softly orbiting cyan particles in a ring, breathing opacity, scanline overlay. "All quiet. Next run in {time}."
   - Working: Particles accelerate with violet trails, one bright node per active agent, electric connections between coordinating nodes. "{N} agents working across {M} projects."
   - Attention: Particles cluster and pulse amber, heartbeat contraction/expansion. "{N} items need review."

3. **Active Agent Strip** (horizontal scroll) -- Each active agent/employee as a compact card (180px wide). Shows: project short name, issue #, mode badge, turn counter, elapsed timer. For Agent Teams runs, shows team name with expandable member list. Empty state: "No agents running" with "Trigger Run" button.

4. **Two-Column Layout:**
   - Left (60%): **Recent Outcomes Feed** -- Scrollable timeline. Each entry is a row: time (relative), project, issue title (truncated), verdict badge, token count, duration. Click row to navigate to run detail. Max 15 items.
   - Right (40%): Stacked widgets:
     - **Usage Budget Gauge** -- Circular gauge for weekly plan usage. Model breakdown as stacked segments. "Resets in {N} days".
     - **Queue Summary** -- State counts as horizontal bar segments. Top 3 pending items as compact list. Backpressure indicator if not GREEN.
     - **System Health** -- Compact: service status dot, timer next trigger, memory/disk usage bars.

**SSE Integration:** Active Agent Strip and Recent Outcomes Feed update in real-time. Usage gauge polls every 60s.

---

### 9.2 Runs Page (`/runs`)

**Layout:** List with optional detail panel.

**Filter Bar (sticky):** Status chips (running, completed, failed, interrupted). Verdict chips (APPROVE, PR, REJECT). Project dropdown. Date range. Search by run ID or issue number.

**Run Table:** Columns: Status icon + running animation, Project (short name), Issue (# + title truncated), Mode badge, Model (short name), Verdict badge, Tokens (formatted), Duration (formatted), Started (relative time). Pagination: 20 per page with total count.

**Master-Detail:** Clicking a row opens a slide-over detail panel (or navigates to full page on narrow screens). The detail uses the `/runs/{id}/full` endpoint for a single-request load.

---

### 9.3 Run Detail (`/runs/:id`)

**Layout:** Full-page with tabbed content.

**Header:** RunDetailHeader component. Status with live animation for running. Verdict with reasoning excerpt. Project link (opens GitHub). Issue link. Timing stats. Action buttons: View Logs, View Diff, Send Guidance (if running), Trigger Re-run.

**Tabs:**

**Overview Tab:**
- Left column: Employee report rendered as structured sections (requirements checklist with check/cross icons, changes summary, confidence meter, test results).
- Right column: Verdict card with full reasoning. Queue item info if exists. Plan reference if exists.
- Bottom: Token breakdown (input/output/total as bar chart).

**DAG Tab** (if coordinator tasks exist):
- Full CoordinatorDAGView. Interactive graph. Click nodes for detail. Legend showing status colors. Summary: "4/6 tasks completed, 1 running, 1 blocked."

**Team Tab** (if Agent Teams run):
- TeamMemberGrid. Each member card shows name, assigned task, status, tokens. Live updating.

**Diff Tab:**
- RunDiffViewer. File tree on left, diff content on right. Additions highlighted green, deletions red. File stats in tree (e.g., "+45 -12").

**Logs Tab:**
- RunLogViewer. For running agents: WebSocket streaming. For completed: historical load with pagination. Search within logs. Color-coded event types. Collapsible tool calls.

**Intelligence Tab** (if intelligence events exist):
- IntelligenceDecisionTimeline. Shows routing decisions, model selection rationale, confidence reports, escalation steps.

**Messages Sub-section** (within Overview or separate):
- Coordinator messages timeline. Guidance sent (with type badge), conflict detections, progress reports.

---

### 9.4 Queue Page (`/queue`)

**Layout:** Toggle between Board (Kanban) and List views.

**Board View:**
- Columns: Pending, In Progress, Review, Completed (collapsed by default), Failed (collapsed).
- Cards: Project icon, issue #, title (2-line truncated), priority badge (color-coded), mode chip, time in current state.
- Pending column: drag to reorder priority.
- Click card -> slide-over detail panel.

**List View:**
- Table with all queue item fields. Sortable by priority, state, created_at, updated_at.
- Inline state transition buttons (only valid transitions shown).

**Toolbar:** Add item, Batch pause (by run), Purge completed, Refresh, Backpressure gauge.

**Backpressure Banner:** When not GREEN, shows prominent banner with level, explanation, and effects on scheduling.

---

### 9.5 Plans Page (`/plans`)

**Layout:** Card grid or list, with detail panel.

**Plan Cards:** Title, project, issue reference, status badge (draft=gray, approved=green, implementing=violet, completed=green-outline, rejected=red), scope badge (small/medium/large), files affected count, created date.

**Filters:** Status, project, scope.

**Plan Detail:**
- Markdown-rendered description.
- Steps as visual checklist (step number, description, status indicator).
- Files affected as collapsible file tree.
- Action bar: Approve (if draft/rejected), Reject (if draft), Implement (if approved/draft), Delete (if draft).
- Linked run (if implementation_run_id exists) -- click to navigate.

---

### 9.6 Projects Page (`/projects`)

**Layout:** Card grid with drill-down.

**Project Cards:** Repo name (owner/name), mode badge, priority badge, enabled/disabled indicator (subtle opacity when disabled), branch name, last run time, quick stats (total runs, success rate as mini-bar).

**Add Project:** Modal form with repo input (owner/name), mode selector, priority, branch, custom instructions textarea.

**Project Detail (`/projects/:id`):**

Sub-navigation tabs:
- **Configuration:** ProjectConfigForm. All editable fields. Save button syncs to config JSON.
- **Runs:** RunTable filtered to this project.
- **Plans:** PlanList filtered to this project.
- **Integration:** IntegrationStatusPanel. Dev branch health. Feature list. Actions.
- **Sprint:** SprintStatusPanel. Current sprint, role findings, history.
- **Intelligence:** ProjectIntelligencePanel. Per-project success rates and trends.

---

### 9.7 Intelligence Page (`/intelligence`)

**Layout:** Dashboard grid.

**Top Row (Insight Cards):**
- Total Learning Samples (number with trend arrow)
- Overall Success Rate (percentage with mini sparkline)
- Most Efficient Mode (mode name + avg tokens)
- Calibration Score (how well-calibrated confidence is)

**Row 2:**
- SuccessRateMatrix (heatmap, left 60%)
- EscalationFunnelChart (right 40%)

**Row 3:**
- ConfidenceCalibrationChart (left 50%)
- TokenEfficiencyChart (right 50%)

**Row 4:**
- PromptVersionTable (full width). Shows A/B test results across prompt versions.

---

### 9.8 Brainstorm Page (`/brainstorm`)

**Layout:** Two-panel: session list sidebar (280px) + chat main area.

**Session List:**
- New Session button (top). Opens persona selector + optional project picker.
- Session entries: title, persona icon, project chip, message count, "2 hours ago". Active session highlighted.
- Delete session (swipe or context menu).

**Chat Area:**
- Header: Session title (editable), persona badge, project context chip.
- Message list: User messages (right-aligned, surface color), Assistant messages (left-aligned, subtle border, markdown-rendered with syntax highlighting).
- Streaming indicator: Three-dot animation during response generation. Keepalive comment handling (suppress UI noise).
- Input: Multi-line textarea with Cmd+Enter to send. Disabled during streaming.

---

### 9.9 Settings Page (`/settings`)

**Layout:** Vertical sections with collapsible accordions.

**Sections:**

1. **Authentication**
   - Claude CLI: Status indicator (Connected/Expiring/Expired), expiry countdown, Refresh Token button, Start Login Flow button (opens OAuth flow in new tab).
   - GitHub: Connection status, username, scopes. Connect button (starts Device Flow with user code display). Disconnect button.

2. **Agent Configuration**
   - Model selection: employee model, manager model dropdowns.
   - Limits: max turns, max budget, max concurrent, max usage percent, reserve percent. Each with description tooltip.
   - Schedule: interval input with preview of next run.

3. **Notifications**
   - Enable/disable toggle. Webhook type selector. URL inputs. Multi-target support (add/remove targets). Notify-on checkboxes per target. Test Notification button with result feedback.

4. **Prompts**
   - Role selector (tabs or dropdown). Side-by-side editor: default (read-only, syntax-highlighted markdown) vs custom override (editable). Diff view toggle. Reset to Default button. Character count.

5. **System**
   - Service control: Start/Stop/Restart buttons for agent service and timer. Status dots.
   - Resources: Memory bar, Disk bar, Load average display, Uptime.
   - Database: Path display, size.

---

## 10. Implementation Roadmap

### Phase 1: Foundation (Week 1-2)

**Goal:** Core infrastructure, navigation, and the two most important views.

**Tasks:**
1. SvelteKit project setup with Tailwind 4, TypeScript strict, Orbital Command dark theme
2. Design system tokens (Orbital Command palette with glow variables, Syne/DM Sans/JetBrains Mono typography, spacing, atmospheric effects)
3. AppShell with sidebar navigation, routing
4. API client layer (typed fetch wrapper, auth handling)
5. SSE connection manager with reconnection logic
6. Command Center page (StationPulse, ActiveAgentStrip, RecentOutcomesFeed)
7. Runs page (RunTable with pagination, filters)
8. Run Detail page (Overview tab with RunFullContext endpoint)
9. SystemStatusBar (service status, auth status polling)
10. ToastSystem and basic error handling

**Backend API usage:** `/api/health`, `/api/runs`, `/api/runs/{id}/full`, `/api/runs/active-employees`, `/api/system/status`, `/api/system/auth`, `/api/events/stream`

### Phase 2: Core Workflows (Week 3-4)

**Goal:** Queue management, plan pipeline, and live features.

**Tasks:**
1. Queue page (Board view + List view)
2. QueueItemDetail slide-over
3. Plans page (card grid + detail)
4. Plan approval/reject/implement flow
5. Run Detail Diff tab (RunDiffViewer with syntax highlighting)
6. Run Detail Logs tab (WebSocket streaming + historical)
7. NotificationCenter component
8. Backpressure gauge and banner
9. Command palette (Cmd+K) with fuzzy search
10. Keyboard shortcuts system

**Backend API usage:** `/api/queue`, `/api/plans`, `/api/runs/{id}/diff`, `/api/logs`, `/api/queue/pressure`

### Phase 3: Agent Coordination (Week 5-6)

**Goal:** DAG visualization, guidance, Agent Teams, real-time coordination.

**Tasks:**
1. CoordinatorDAGView (D3 graph visualization)
2. Run Detail DAG tab
3. GuidanceSender slide-over panel
4. TeamMemberGrid for Agent Teams runs
5. Run Detail Team tab
6. CoordinatorMessage timeline
7. IntelligenceDecisionTimeline
8. Active agent real-time updates (SSE -> store -> UI)
9. EmployeeReportPanel structured rendering
10. VerdictCard with reasoning

**Backend API usage:** `/api/coordinator`, `/api/coordinator/guidance`, `/api/runs/active-teammates`, `/api/agent-events`

### Phase 4: Projects & Configuration (Week 7-8)

**Goal:** Project management, integration, settings.

**Tasks:**
1. Projects page (card grid)
2. Project detail with sub-navigation
3. ProjectConfigForm (all settings)
4. IntegrationStatusPanel
5. SprintStatusPanel
6. Settings page (all sections)
7. PromptEditor (side-by-side with diff)
8. AuthPanel (Claude OAuth + GitHub Device Flow)
9. NotificationConfigEditor with multi-target
10. ServiceControlPanel

**Backend API usage:** `/api/projects`, `/api/config`, `/api/prompts`, `/api/oauth`, `/api/oauth/github`, `/api/integration`, `/api/sprint`, `/api/system/service`

### Phase 5: Intelligence & Brainstorm (Week 9-10)

**Goal:** Learning loop visualization, AI brainstorm, analytics.

**Tasks:**
1. Intelligence page (all charts)
2. SuccessRateMatrix heatmap
3. ConfidenceCalibrationChart
4. TokenEfficiencyChart
5. EscalationFunnelChart
6. PromptVersionTable
7. Brainstorm page (session list + chat interface)
8. Brainstorm streaming with keepalive handling
9. PersonaSelector
10. Analytics integration into Command Center (UsageBudgetGauge, PlanUsageMiniChart)

**Backend API usage:** `/api/intelligence`, `/api/brainstorm`, `/api/analytics`, `/api/plan-usage`

### Phase 6: Polish & Edge Cases (Week 11-12)

**Goal:** Production readiness, error handling, performance.

**Tasks:**
1. Empty states for all views (no runs, no projects, no queue items)
2. Loading skeletons for all async content
3. Error boundaries with retry buttons
4. Responsive layout for tablet screens (sidebar collapses)
5. Connection loss handling (SSE/WebSocket reconnection UI)
6. Accessibility audit (ARIA labels, keyboard navigation, focus management)
7. Performance optimization (virtual scrolling for long lists, lazy chart loading)
8. Bundle size audit (tree-shaking verification, code splitting per route)
9. Integration testing with mock backend
10. Deploy pipeline (build -> static files served by FastAPI)

---

## Appendix A: API Endpoint Inventory

```
GET    /api/health
GET    /api/projects
GET    /api/projects/{id}
POST   /api/projects
PUT    /api/projects/{id}
DELETE /api/projects/{id}
GET    /api/runs
GET    /api/runs/active-employees
GET    /api/runs/active-teammates
GET    /api/runs/latest
GET    /api/runs/{run_id}
GET    /api/runs/{run_id}/full
GET    /api/runs/{run_id}/diff
POST   /api/runs/rescan
POST   /api/runs/trigger
GET    /api/analytics
GET    /api/logs/search
GET    /api/logs/{run_id}
WS     /api/logs/stream
GET    /api/events/stream (SSE)
GET    /api/events/subscribers
POST   /api/webhook/run-event
GET    /api/coordinator/tasks
GET    /api/coordinator/tasks/{task_id}
GET    /api/coordinator/tasks/{task_id}/details
GET    /api/coordinator/dag/{run_id}
GET    /api/coordinator/messages
POST   /api/coordinator/guidance
GET    /api/queue
GET    /api/queue/stats
GET    /api/queue/pressure
GET    /api/queue/{item_id}
POST   /api/queue
POST   /api/queue/claim
PUT    /api/queue/{item_id}
DELETE /api/queue/{item_id}
POST   /api/queue/batch-pause
POST   /api/queue/purge
GET    /api/plans
GET    /api/plans/{plan_id}
POST   /api/plans
PUT    /api/plans/{plan_id}
DELETE /api/plans/{plan_id}
POST   /api/plans/{plan_id}/approve
POST   /api/plans/{plan_id}/reject
POST   /api/plans/{plan_id}/implement
GET    /api/config
PUT    /api/config
GET    /api/config/db
PUT    /api/config/{key}
GET    /api/config/usage
GET    /api/config/token-usage
POST   /api/config/test-notification
GET    /api/system/status
POST   /api/system/service/{action}
GET    /api/system/auth
POST   /api/oauth/start
POST   /api/oauth/callback
POST   /api/oauth/refresh
POST   /api/oauth/github/device/start
POST   /api/oauth/github/device/poll
GET    /api/oauth/github/status
DELETE /api/oauth/github
GET    /api/prompts
GET    /api/prompts/{role}
PUT    /api/prompts/{role}
DELETE /api/prompts/{role}
POST   /api/agent-events
GET    /api/agent-events/{workflow_id}
GET    /api/agent-events
GET    /api/agent-events/stats/summary
GET    /api/intelligence/insights
POST   /api/intelligence/outcomes
GET    /api/intelligence/decisions
POST   /api/brainstorm/sessions
GET    /api/brainstorm/sessions
GET    /api/brainstorm/sessions/{session_id}
DELETE /api/brainstorm/sessions/{session_id}
POST   /api/brainstorm/sessions/{session_id}/messages (SSE response)
GET    /api/plan-usage
GET    /api/plan-usage/history
POST   /api/plan-usage/snapshot
GET    /api/integration/status/{repo}
GET    /api/integration/features
GET    /api/integration/features/{feature_id}
POST   /api/integration/features
PUT    /api/integration/features/{feature_id}
POST   /api/integration/promote
POST   /api/integration/sync/{repo}
POST   /api/integration/validate/{repo}
POST   /api/integration/exclude/{feature_id}
DELETE /api/integration/exclude/{feature_id}
GET    /api/sprint/status/{repo}
GET    /api/sprint/findings/{sprint_id}
GET    /api/sprint/findings/{sprint_id}/{role}
GET    /api/sprint/history/{repo}
POST   /api/github-webhook
```

## Appendix B: SSE Event Types

```
run_start, employee_start, employee_complete, manager_review,
verdict_execute, run_complete, plan_review_start, plan_review_complete,
task_started, task_completed, task_failed, task_ready, task_blocked,
conflict_detected, guidance_sent, dag_created, dag_completed,
queue_pending, queue_assigned, queue_claimed, queue_in_progress,
queue_review, queue_paused, queue_completed, queue_failed,
orchestrator_start, orchestrator_complete, orchestrator_error,
team_created, teammate_spawned, task_claimed, teammate_completed,
team_cleanup, progress_update
```

## Appendix C: Queue State Machine

```
pending -> assigned | claimed | planning | paused | failed | cancelled
claimed -> in_progress | pending | paused
assigned -> in_progress | pending | paused | failed | cancelled
planning -> in_progress | paused | failed | pending
in_progress -> review | verifying | paused | failed | pending
verifying -> approved | rejected | pending
review -> approved | rejected | pending
approved -> completed
rejected -> pending | failed | escalated
escalated -> pending
paused -> pending
failed -> pending
cancelled -> (terminal)
completed -> (terminal)
```
