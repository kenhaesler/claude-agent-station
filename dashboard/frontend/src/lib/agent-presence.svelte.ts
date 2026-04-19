/**
 * AgentPresence — central real-time state layer for the agentic UI.
 * Merges WebSocket log events + SSE system events + polling into a unified store.
 * Replaces live-activity.svelte.ts as the single source of truth.
 */

import { LogWebSocket } from './ws';
import { parseLogLine, formatToolInput, truncate } from './log-parser';
import type { ParsedLogEvent } from './log-parser';
import { getActiveEmployees, getLatestRun, listPlans, listRuns, getStoredApiKey } from './api';
import type { ActiveEmployee, Run } from './types';
import { AgentEventStream } from './event-stream';
import { handleStreamEvent as permissionTrayHandleEvent } from './permission-tray.svelte';

// --- Agent Identity ---

export type AgentRole = 'manager' | 'employee' | 'coordinator' | 'analyst' | 'planner' | 'assigner';

export interface AgentIdentity {
  role: AgentRole;
  name: string;
  color: string;
  employeeIndex: number | null;
  status: 'active' | 'thinking' | 'idle' | 'error';
  currentAction: string | null;
}

export type RunPhase = 'idle' | 'coordinating' | 'employee' | 'plan_review' | 'manager_review' | 'executing_verdict';

export interface ConversationEntry {
  id: number;
  timestamp: number;
  agentName: string;
  agentColor: string;
  type: 'tool_use' | 'thinking' | 'text' | 'result' | 'guidance' | 'phase' | 'system';
  content: string;
  toolName?: string;
  isError?: boolean;
}

// --- Role color map ---
// Uses CSS variables from the active theme. The themeStore.getRoleColors()
// method returns the current theme's agent colors so they update when the
// user switches themes. The inline fallback map is kept only for server-side
// or pre-hydration contexts where the theme store hasn't initialised.

import { themeStore } from './theme.svelte';

// Mirrors theme.svelte.ts ROLE_COLORS so the first paint uses the same palette
// as subsequent renders — prevents a cold-load colour flash when the theme
// store is evaluated a few ms after the component mounts.
const THEME_ROLE_COLORS: Record<string, string> = {
  manager: '#B06030',
  'dev-0': '#2E7D32',
  'dev-1': 'rgba(99,102,180,1)',
  'dev-2': '#06B6D4',
  coordinator: '#4A3728',
  analyst: '#B06030',
  planner: '#2E7D32',
  assigner: '#B06030',
};

function getRoleColors(): Record<string, string> {
  try {
    return themeStore.getRoleColors();
  } catch {
    return { ...THEME_ROLE_COLORS };
  }
}

export function getAgentColor(name: string): string {
  const colors = getRoleColors();
  return colors[name.toLowerCase()] ?? colors['dev-0'];
}

export function getAgentName(employeeIndex: number | null, mode?: string | null): string {
  if (mode === 'manager') return 'Manager';
  if (mode === 'plan_review' || mode === 'plan_reviewing') return 'Manager';
  if (mode === 'analyst') return 'Analyst';
  if (mode === 'coordinator') return 'Coordinator';
  if (mode === 'planner' || mode === 'plan') return 'Planner';
  if (mode === 'assigner') return 'Assigner';
  if (mode === 'agent-teams') return 'Lead';
  if (employeeIndex != null) return `Teammate ${employeeIndex + 1}`;
  return 'Teammate 1';
}

// --- State ---

interface AgentPresenceState {
  // Active run state
  activeRuns: ActiveEmployee[];
  latestRun: Run | null;
  latestRunId: string | null;
  phase: RunPhase;

  // Agent identities
  agents: AgentIdentity[];

  // Conversation log
  conversationLog: ConversationEntry[];

  // Live metrics
  currentTool: { name: string; summary: string } | null;
  activityIntensity: number;
  turnCount: number;
  tokensBurned: number;

  // Connection health
  wsConnected: boolean;
  sseConnected: boolean;

  // Decision badge
  pendingDecisionCount: number;

  // Panel state
  panelOpen: boolean;
  selectedAgent: string | null;

  // Mission Control (Phase A): which runs have an operator-requested pause
  // pending, and whether the global kill-switch is engaged. Updated by SSE.
  // Stored as a plain record keyed by run_id — Svelte 5 $state does not track
  // mutations on a raw Set, so $derived reads never update.
  pausedRuns: Record<string, boolean>;
  globalPause: boolean;
}

const MAX_CONVERSATION = 200;
const CURRENT_TOOL_TIMEOUT = 10_000;
const EWMA_ALPHA = 0.1;
const SAMPLE_INTERVAL = 1_000;
const POLL_INTERVAL = 10_000;

let entryId = 0;

export const agentPresence = $state<AgentPresenceState>({
  activeRuns: [],
  latestRun: null,
  latestRunId: null,
  phase: 'idle',
  agents: [],
  conversationLog: [],
  currentTool: null,
  activityIntensity: 0,
  turnCount: 0,
  tokensBurned: 0,
  wsConnected: false,
  sseConnected: false,
  pendingDecisionCount: 0,
  panelOpen: false,
  selectedAgent: null,
  pausedRuns: {},
  globalPause: false,
});

// --- Internal ---

let ws: LogWebSocket | null = null;
let sse: AgentEventStream | null = null;
let currentToolTimer: ReturnType<typeof setTimeout> | null = null;
let sampleTimer: ReturnType<typeof setInterval> | null = null;
let pollTimer: ReturnType<typeof setInterval> | null = null;
let eventsSinceLastSample = 0;
let rawIntensity = 0;

function addConversationEntry(entry: Omit<ConversationEntry, 'id' | 'timestamp'>) {
  agentPresence.conversationLog.push({
    ...entry,
    id: ++entryId,
    timestamp: Date.now(),
  });
  if (agentPresence.conversationLog.length > MAX_CONVERSATION) {
    agentPresence.conversationLog.splice(0, agentPresence.conversationLog.length - MAX_CONVERSATION);
  }
}

function derivePhase(runs: ActiveEmployee[]): RunPhase {
  if (runs.length === 0) return 'idle';
  const hasPlanReview = runs.some(r => r.status === 'plan_reviewing');
  if (hasPlanReview) return 'plan_review';
  const hasReviewing = runs.some(r => r.status === 'reviewing');
  const hasManager = runs.some(r => r.mode === 'manager');
  if (hasReviewing || hasManager) return 'manager_review';
  return 'employee';
}

function deriveAgents(runs: ActiveEmployee[]): AgentIdentity[] {
  if (runs.length === 0) return [];
  const agents: AgentIdentity[] = [];

  // In agent-teams mode, the lead IS the coordinator — don't add a phantom Manager
  const hasAgentTeams = runs.some(r => r.mode === 'agent-teams');
  if (!hasAgentTeams) {
    // Traditional mode: always show manager when there are active runs
    agents.push({
      role: 'manager',
      name: 'Manager',
      color: getRoleColors().manager,
      employeeIndex: null,
      status: runs.some(r => r.mode === 'manager' || r.status === 'reviewing' || r.status === 'plan_reviewing') ? 'active' : 'idle',
      currentAction: null,
    });
  }

  for (const run of runs) {
    if (run.mode === 'manager') continue; // Already added above
    const idx = run.employee_index ?? runs.indexOf(run);
    const modeRoleMap: Record<string, AgentRole> = {
      analyst: 'analyst',
      planner: 'planner',
      plan: 'planner',
      assigner: 'assigner',
    };
    const modeNameMap: Record<string, string> = {
      analyst: 'Analyst',
      planner: 'Planner',
      plan: 'Planner',
      assigner: 'Assigner',
      'agent-teams': 'Lead',
    };
    const role = modeRoleMap[run.mode ?? ''] ?? 'employee';
    const agentName = modeNameMap[run.mode ?? ''] ?? `Teammate ${idx + 1}`;
    agents.push({
      role,
      name: agentName,
      color: getAgentColor(agentName.toLowerCase()),
      employeeIndex: idx,
      status: (
        run.status === 'running' ||
        run.status === 'started' ||
        run.status === 'reviewing' ||
        run.status === 'plan_reviewing' ||
        run.status === 'employee_done'
      ) ? 'active' : 'idle',
      currentAction: null,
    });
  }

  return agents;
}

// --- WebSocket handler ---

function handleWsMessage(data: string) {
  const parsed = parseLogLine(data);
  if (!parsed) return;

  const events = Array.isArray(parsed) ? parsed : [parsed];
  // Determine current agent name from active agents.
  const isManagerPhase = agentPresence.phase === 'plan_review' || agentPresence.phase === 'manager_review' || agentPresence.phase === 'executing_verdict';
  const activeAgent = isManagerPhase
    ? agentPresence.agents.find(a => a.role === 'manager')
    : (agentPresence.agents.find(a => a.status === 'active' && a.role !== 'manager')
       ?? agentPresence.agents.find(a => a.status === 'active')
       ?? agentPresence.agents[0]); // fallback to first agent if none active
  const agentName = activeAgent?.name ?? 'Lead';
  const agentColor = activeAgent?.color ?? getRoleColors()['dev-0'];

  for (const evt of events) {
    if (evt.type === 'assistant_tool_use') {
      eventsSinceLastSample++;
      const summary = evt.toolName
        ? truncate(formatToolInput(evt.toolName, evt.toolInput), 60)
        : '';
      agentPresence.currentTool = { name: evt.toolName ?? 'Unknown', summary };

      // Update agent's current action
      if (activeAgent) {
        activeAgent.currentAction = `${evt.toolName}: ${summary}`;
      }

      addConversationEntry({
        agentName,
        agentColor,
        type: 'tool_use',
        content: summary,
        toolName: evt.toolName,
      });

      if (currentToolTimer) clearTimeout(currentToolTimer);
      currentToolTimer = setTimeout(() => {
        agentPresence.currentTool = null;
        if (activeAgent) activeAgent.currentAction = null;
      }, CURRENT_TOOL_TIMEOUT);
    }

    if (evt.type === 'tool_result') {
      eventsSinceLastSample++;
      if (evt.isError) {
        addConversationEntry({
          agentName,
          agentColor,
          type: 'result',
          content: truncate(evt.toolResultContent ?? 'Error', 200),
          isError: true,
        });
      }
    }

    if (evt.type === 'assistant_thinking') {
      agentPresence.turnCount++;
      if (activeAgent) activeAgent.status = 'thinking';
      addConversationEntry({
        agentName,
        agentColor,
        type: 'thinking',
        // Mission Control: operators need the whole reasoning to judge
        // whether to intervene. Cap at 4k chars so a runaway block can't
        // blow the UI, but 4k >> a paragraph.
        content: truncate(evt.thinking ?? '', 4000),
      });
    }

    if (evt.type === 'assistant_text') {
      agentPresence.turnCount++;
      if (activeAgent) activeAgent.status = 'active';
      addConversationEntry({
        agentName,
        agentColor,
        type: 'text',
        content: truncate(evt.text ?? '', 4000),
      });
    }

    if (evt.type === 'result') {
      if (evt.tokensTotal) {
        agentPresence.tokensBurned += evt.tokensTotal;
      }
      addConversationEntry({
        agentName,
        agentColor,
        type: 'system',
        content: `Completed — ${evt.numTurns ?? '?'} turns`,
      });
    }
  }
}

// --- SSE handler ---

function connectSSE() {
  sse = new AgentEventStream({
    onEvent: (event) => {
      handleSSEEvent(event);
      // Forward permission tray events to the tray store (ADR-0001 / P2.T10).
      permissionTrayHandleEvent(event);
    },
    onStatusChange: (state) => { agentPresence.sseConnected = state === 'connected'; },
  });
  sse.connect();
}

function handleSSEEvent(data: any) {
  const eventType = data.event ?? data.type;
  const agentColor = getRoleColors().manager;

  switch (eventType) {
    // Narration — Phase 1 of "The Bridge". The orchestrator emits these for
    // every lead directive and teammate step so the operator always has a
    // plain-English thread of what's happening. Shown on the Bridge landing
    // and anywhere AgentActivityFeed renders.
    case 'narration': {
      const text = (data.narration ?? '').toString().trim();
      if (!text) break;
      const kind = (data.narration_kind ?? 'directive').toString();
      const agentName = (data.agent_name ?? 'Lead').toString();
      const color = getAgentColor(agentName.toLowerCase());
      addConversationEntry({
        agentName,
        agentColor: color,
        type: kind === 'system' ? 'phase' : 'text',
        content: text,
      });
      break;
    }
    // Live token burn — fires ~every 15s from the orchestrator while a run
    // is active. Feeds the always-visible token meter in TopNav so the
    // operator can see cost accumulate without waiting for run completion.
    case 'progress_update': {
      const tokens = Number(data.tokens_total ?? 0);
      if (tokens > 0) {
        agentPresence.tokensBurned = tokens;
      }
      const turns = Number(data.turns ?? 0);
      if (turns > 0) {
        agentPresence.turnCount = turns;
      }
      break;
    }
    case 'run_start':
      addConversationEntry({
        agentName: 'Manager',
        agentColor,
        type: 'phase',
        content: `Run started${data.project ? ` on ${data.project}` : ''}${data.issue ? ` — issue #${data.issue}` : ''}`,
      });
      refreshActiveRuns();
      break;
    case 'employee_start':
      addConversationEntry({
        agentName: `Teammate ${(data.employee_index ?? 0) + 1}`,
        agentColor: getAgentColor(`dev-${data.employee_index ?? 0}`),
        type: 'phase',
        content: `Started working${data.issue ? ` on #${data.issue}` : ''}`,
      });
      refreshActiveRuns();
      break;
    case 'employee_complete':
      addConversationEntry({
        agentName: `Teammate ${(data.employee_index ?? 0) + 1}`,
        agentColor: getAgentColor(`dev-${data.employee_index ?? 0}`),
        type: 'phase',
        content: `Finished — ${data.turns ?? '?'} turns`,
      });
      refreshActiveRuns();
      break;
    case 'plan_review_start':
      agentPresence.phase = 'plan_review';
      addConversationEntry({
        agentName: 'Manager',
        agentColor,
        type: 'phase',
        content: `Plan review${data.project ? ` for ${data.project}` : ''} (employee ${data.employee_index ?? 0})`,
      });
      refreshActiveRuns();
      break;
    case 'plan_review_complete':
      agentPresence.phase = 'employee';
      addConversationEntry({
        agentName: 'Manager',
        agentColor,
        type: 'phase',
        content: `Plan verdict: ${data.verdict ?? 'UNKNOWN'}`,
      });
      refreshActiveRuns();
      break;
    case 'manager_review':
      agentPresence.phase = 'manager_review';
      addConversationEntry({
        agentName: 'Manager',
        agentColor,
        type: 'phase',
        content: `Reviewing${data.project ? ` ${data.project}` : ''}`,
      });
      break;
    case 'verdict':
    case 'verdict_execute':
      agentPresence.phase = 'executing_verdict';
      addConversationEntry({
        agentName: 'Manager',
        agentColor,
        type: 'phase',
        content: `Verdict: ${data.verdict ?? data.action ?? 'UNKNOWN'}`,
      });
      break;
    case 'run_complete':
    case 'orchestrator_complete':
    case 'orchestrator_error': {
      // All three signal terminal state for the run. In Agent Teams mode the
      // orchestrator emits `orchestrator_complete` directly, so without this
      // branch the UI kept showing the run as live for up to 10s until the
      // next poll — long enough for an operator to type and send a message
      // to a dead run. Refresh immediately so Mission Control locks down.
      agentPresence.phase = 'idle';
      const interrupted = data.status === 'interrupted' || data.data?.status === 'interrupted';
      addConversationEntry({
        agentName: 'Manager',
        agentColor,
        type: 'phase',
        content: interrupted
          ? 'Run interrupted by operator'
          : (eventType === 'orchestrator_error' ? 'Run failed' : 'Run completed'),
        isError: interrupted || eventType === 'orchestrator_error',
      });
      refreshActiveRuns();
      break;
    }
    case 'planner_start':
      addConversationEntry({
        agentName: 'Planner',
        agentColor: getRoleColors().planner,
        type: 'phase',
        content: `Started planning${data.project ? ` for ${data.project}` : ''}`,
      });
      refreshActiveRuns();
      break;
    case 'planner_complete':
      addConversationEntry({
        agentName: 'Planner',
        agentColor: getRoleColors().planner,
        type: 'phase',
        content: `Planning finished${data.project ? ` for ${data.project}` : ''}`,
      });
      refreshActiveRuns();
      break;
    case 'assigner_start':
      addConversationEntry({
        agentName: 'Assigner',
        agentColor: getRoleColors().assigner,
        type: 'phase',
        content: `Assigning work${data.project ? ` for ${data.project}` : ''}`,
      });
      break;
    case 'assigner_complete':
      addConversationEntry({
        agentName: 'Assigner',
        agentColor: getRoleColors().assigner,
        type: 'phase',
        content: `Assignment complete${data.project ? ` for ${data.project}` : ''}`,
      });
      break;
    case 'conflict_detected':
      addConversationEntry({
        agentName: 'Coordinator',
        agentColor: getRoleColors().coordinator,
        type: 'system',
        content: `Conflict detected: ${data.message ?? 'overlapping file changes'}`,
      });
      break;
    case 'run_interrupted':
      // Reaper terminated a stale run
      addConversationEntry({
        agentName: 'Manager',
        agentColor,
        type: 'system',
        content: `REAPER: stale run ${data.run_id ?? 'unknown'} terminated`,
      });
      refreshActiveRuns();
      break;

    // Mission Control (Phase A) — operator intervention events.
    // Two event families land here:
    //   run_control_* — fired by the dashboard router the moment the
    //     operator clicks; tells the UI the request has been queued.
    //   run_paused / run_resumed / run_stop_requested — fired by the
    //     orchestrator once it actually consumed the queued row, so the
    //     UI can flip the badge from "requested" to "confirmed".
    case 'run_control_pause':
    case 'run_paused': {
      const rid = data.run_id ?? data.data?.run_id;
      if (rid) agentPresence.pausedRuns[rid] = true;
      addConversationEntry({
        agentName: 'Operator',
        agentColor: getRoleColors().manager,
        type: 'guidance',
        content: eventType === 'run_paused' ? 'Agent confirmed pause' : 'Pause requested',
      });
      break;
    }
    case 'run_control_resume':
    case 'run_resumed': {
      const rid = data.run_id ?? data.data?.run_id;
      if (rid) delete agentPresence.pausedRuns[rid];
      addConversationEntry({
        agentName: 'Operator',
        agentColor: getRoleColors().manager,
        type: 'guidance',
        content: eventType === 'run_resumed' ? 'Agent confirmed resume' : 'Resume requested',
      });
      break;
    }
    case 'run_control_stop':
    case 'run_stop_requested': {
      addConversationEntry({
        agentName: 'Operator',
        agentColor: getRoleColors().manager,
        type: 'guidance',
        content: eventType === 'run_stop_requested' ? 'Agent received stop' : 'Stop requested',
        isError: true,
      });
      // A stop flips the run to 'interrupted' once the orchestrator catches
      // it; refresh so Mission Control shows the transition immediately.
      refreshActiveRuns();
      break;
    }
    case 'run_control_message':
    case 'run_message_queued': {
      // Two distinct events share this branch:
      //   run_control_message — fired by the backend router the instant the
      //     operator clicks Send, so the UI can confirm the row was written.
      //   run_message_queued — fired by the orchestrator once the dedicated
      //     poll task has actually drained the row and accumulated it for
      //     the next turn. This is the "agent received it" signal.
      const text = (data.payload?.text ?? data.text ?? data.data?.text ?? '').toString();
      const queued = eventType === 'run_control_message';
      addConversationEntry({
        agentName: 'Operator',
        agentColor: getRoleColors().manager,
        type: 'guidance',
        content: queued
          ? (text ? `Queued for agent: ${text.slice(0, 200)}` : 'Message queued')
          : (text ? `Agent picked up: ${text.slice(0, 200)}` : 'Agent picked up message'),
      });
      break;
    }
    case 'run_message_expired': {
      // The run terminated before the orchestrator drained the control row.
      // Surface this in red so the operator knows their message was NOT
      // delivered — previously these rows sat in the queue forever.
      const text = (data.text ?? data.data?.text ?? data.payload?.text ?? '').toString();
      addConversationEntry({
        agentName: 'Operator',
        agentColor: getRoleColors().manager,
        type: 'guidance',
        content: text
          ? `Not delivered (run ended): ${text.slice(0, 200)}`
          : 'Pending intervention expired — run ended before pickup',
        isError: true,
      });
      break;
    }
    case 'global_pause_set':
      agentPresence.globalPause = true;
      addConversationEntry({
        agentName: 'Operator',
        agentColor: getRoleColors().manager,
        type: 'guidance',
        content: 'Global pause engaged — every tool call now routes to the tray',
        isError: true,
      });
      break;
    case 'global_pause_cleared':
      agentPresence.globalPause = false;
      addConversationEntry({
        agentName: 'Operator',
        agentColor: getRoleColors().manager,
        type: 'guidance',
        content: 'Global pause released',
      });
      break;
  }
}

// --- Polling ---

async function refreshActiveRuns() {
  try {
    const [employees, latest] = await Promise.allSettled([
      getActiveEmployees(),
      getLatestRun(),
    ]);

    let activeEmployees: ActiveEmployee[] = [];
    if (employees.status === 'fulfilled') {
      activeEmployees = employees.value;
    }

    let latestRun: Run | null = null;
    if (latest.status === 'fulfilled') {
      latestRun = latest.value;
      agentPresence.latestRun = latestRun;
      agentPresence.latestRunId = latestRun.run_id;
    }

    // Fallback: if active-employees returns empty but runs are still active,
    // try listing all running runs first (covers multi-employee cases where
    // project_id is NULL on newly created runs), then fall back to latestRun.
    if (activeEmployees.length === 0) {
      try {
        const runningRuns = await listRuns({ status: 'started', limit: 10 });
        if (runningRuns.runs && runningRuns.runs.length > 0) {
          activeEmployees = runningRuns.runs.map((r: Run, idx: number) => ({
            run_id: r.run_id,
            project_id: r.project_id ?? 0,
            mode: r.mode ?? 'employee',
            status: r.status ?? 'running',
            issue_number: r.issue_number,
            turns: r.turns,
            employee_index: r.employee_index ?? idx,
            concurrent_group_id: r.concurrent_group_id ?? null,
            model: r.model ?? null,
            branch: r.branch ?? null,
          }));
        }
      } catch {
        // Fall through to latestRun synthesis
      }

      // Final fallback: synthesize from latestRun if still nothing
      if (activeEmployees.length === 0 && latestRun && !latestRun.finished_at &&
          (latestRun.status === 'running' || latestRun.status === 'reviewing' || latestRun.status === 'plan_reviewing')) {
        activeEmployees = [{
          run_id: latestRun.run_id,
          project_id: latestRun.project_id ?? 0,
          mode: latestRun.mode ?? 'employee',
          status: latestRun.status ?? 'running',
          issue_number: latestRun.issue_number,
          turns: latestRun.turns,
          employee_index: latestRun.employee_index ?? null,
          concurrent_group_id: latestRun.concurrent_group_id ?? null,
          model: latestRun.model ?? null,
          branch: latestRun.branch ?? null,
        }];
      }
    }

    agentPresence.activeRuns = activeEmployees;
    agentPresence.phase = derivePhase(activeEmployees);
    agentPresence.agents = deriveAgents(activeEmployees);
  } catch {
    // silent
  }
}

async function refreshDecisionCount() {
  try {
    const plans = await listPlans({ status: 'draft', limit: 1 });
    agentPresence.pendingDecisionCount = plans.total;
  } catch {
    // silent
  }
}

function sampleIntensity() {
  const rate = Math.min(eventsSinceLastSample / 5, 1);
  rawIntensity = EWMA_ALPHA * rate + (1 - EWMA_ALPHA) * rawIntensity;
  agentPresence.activityIntensity = rawIntensity;
  eventsSinceLastSample = 0;
}

// --- Lifecycle ---

export function connect() {
  if (ws) return;

  // WebSocket for log stream — pass API key as query param when configured
  const apiKey = getStoredApiKey();
  const wsUrl = apiKey
    ? `/api/logs/stream?token=${encodeURIComponent(apiKey)}`
    : '/api/logs/stream';
  ws = new LogWebSocket(
    wsUrl,
    handleWsMessage,
    (status) => { agentPresence.wsConnected = status; }
  );
  ws.connect();

  // SSE for system events
  connectSSE();

  // Intensity sampling
  sampleTimer = setInterval(sampleIntensity, SAMPLE_INTERVAL);

  // Polling for active runs + decision count
  refreshActiveRuns();
  refreshDecisionCount();
  pollTimer = setInterval(() => {
    refreshActiveRuns();
    refreshDecisionCount();
  }, POLL_INTERVAL);
}

export function disconnect() {
  if (ws) { ws.disconnect(); ws = null; }
  if (sse) { sse.disconnect(); sse = null; }
  if (currentToolTimer) { clearTimeout(currentToolTimer); currentToolTimer = null; }
  if (sampleTimer) { clearInterval(sampleTimer); sampleTimer = null; }
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }

  agentPresence.activeRuns = [];
  agentPresence.latestRun = null;
  agentPresence.latestRunId = null;
  agentPresence.phase = 'idle';
  agentPresence.agents = [];
  agentPresence.conversationLog.length = 0;
  agentPresence.currentTool = null;
  agentPresence.activityIntensity = 0;
  agentPresence.turnCount = 0;
  agentPresence.tokensBurned = 0;
  agentPresence.wsConnected = false;
  agentPresence.sseConnected = false;
  agentPresence.pendingDecisionCount = 0;
  for (const k of Object.keys(agentPresence.pausedRuns)) delete agentPresence.pausedRuns[k];
  agentPresence.globalPause = false;
  rawIntensity = 0;
  eventsSinceLastSample = 0;
}

export function reset() {
  agentPresence.conversationLog.length = 0;
  agentPresence.currentTool = null;
  agentPresence.activityIntensity = 0;
  agentPresence.turnCount = 0;
  agentPresence.tokensBurned = 0;
  rawIntensity = 0;
  eventsSinceLastSample = 0;
  if (currentToolTimer) { clearTimeout(currentToolTimer); currentToolTimer = null; }
}

export function togglePanel(agentName?: string) {
  if (agentName) {
    agentPresence.selectedAgent = agentName;
    agentPresence.panelOpen = true;
  } else {
    agentPresence.panelOpen = !agentPresence.panelOpen;
  }
}
