/**
 * AgentPresence — central real-time state layer for the agentic UI.
 * Merges WebSocket log events + SSE system events + polling into a unified store.
 * Replaces live-activity.svelte.ts as the single source of truth.
 */

import { LogWebSocket } from './ws';
import { parseLogLine, formatToolInput, truncate } from './log-parser';
import type { ParsedLogEvent } from './log-parser';
import { getActiveEmployees, getLatestRun, listPlans } from './api';
import type { ActiveEmployeeData, Run } from './types';

// --- Agent Identity ---

export type AgentRole = 'manager' | 'employee' | 'coordinator' | 'analyst';

export interface AgentIdentity {
  role: AgentRole;
  name: string;
  color: string;
  employeeIndex: number | null;
  status: 'active' | 'thinking' | 'idle' | 'error';
  currentAction: string | null;
}

export type RunPhase = 'idle' | 'coordinating' | 'employee' | 'manager_review' | 'executing_verdict';

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

const ROLE_COLORS: Record<string, string> = {
  manager: '#f59e0b',
  'dev-0': '#3b82f6',
  'dev-1': '#6366f1',
  'dev-2': '#06b6d4',
  coordinator: '#a855f7',
  analyst: '#8b5cf6',
};

export function getAgentColor(name: string): string {
  return ROLE_COLORS[name.toLowerCase()] ?? ROLE_COLORS['dev-0'];
}

export function getAgentName(employeeIndex: number | null, mode?: string | null): string {
  if (mode === 'manager') return 'Manager';
  if (mode === 'analyst') return 'Analyst';
  if (mode === 'coordinator') return 'Coordinator';
  if (employeeIndex != null) return `Dev-${employeeIndex}`;
  return 'Dev-0';
}

// --- State ---

interface AgentPresenceState {
  // Active run state
  activeRuns: ActiveEmployeeData[];
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
});

// --- Internal ---

let ws: LogWebSocket | null = null;
let sse: EventSource | null = null;
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

function derivePhase(runs: ActiveEmployeeData[]): RunPhase {
  if (runs.length === 0) return 'idle';
  const hasReviewing = runs.some(r => r.status === 'reviewing');
  const hasManager = runs.some(r => r.mode === 'manager');
  if (hasReviewing || hasManager) return 'manager_review';
  return 'employee';
}

function deriveAgents(runs: ActiveEmployeeData[]): AgentIdentity[] {
  if (runs.length === 0) return [];
  const agents: AgentIdentity[] = [];

  // Always show manager when there are active runs
  agents.push({
    role: 'manager',
    name: 'Manager',
    color: ROLE_COLORS.manager,
    employeeIndex: null,
    status: runs.some(r => r.mode === 'manager' || r.status === 'reviewing') ? 'active' : 'idle',
    currentAction: null,
  });

  for (const run of runs) {
    const name = getAgentName(run.issue_number !== null ? runs.indexOf(run) : 0, run.mode);
    if (name === 'Manager') continue; // Already added
    const agentName = run.mode === 'analyst' ? 'Analyst' : `Dev-${runs.indexOf(run)}`;
    agents.push({
      role: run.mode === 'analyst' ? 'analyst' : 'employee',
      name: agentName,
      color: getAgentColor(agentName),
      employeeIndex: runs.indexOf(run),
      status: run.status === 'running' ? 'active' : 'idle',
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
  // Determine current agent name from active agents
  const activeAgent = agentPresence.agents.find(a => a.status === 'active' && a.role !== 'manager');
  const agentName = activeAgent?.name ?? 'Dev-0';
  const agentColor = activeAgent?.color ?? ROLE_COLORS['dev-0'];

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
        content: truncate(evt.thinking ?? '', 300),
      });
    }

    if (evt.type === 'assistant_text') {
      agentPresence.turnCount++;
      if (activeAgent) activeAgent.status = 'active';
      addConversationEntry({
        agentName,
        agentColor,
        type: 'text',
        content: truncate(evt.text ?? '', 500),
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
  const base = import.meta.env.VITE_API_URL || '';
  sse = new EventSource(`${base}/api/events/stream`);

  sse.onopen = () => {
    agentPresence.sseConnected = true;
  };

  sse.onerror = () => {
    agentPresence.sseConnected = false;
  };

  sse.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      handleSSEEvent(data);
    } catch {
      // ignore parse errors
    }
  };
}

function handleSSEEvent(data: any) {
  const eventType = data.event ?? data.type;
  const agentColor = ROLE_COLORS.manager;

  switch (eventType) {
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
        agentName: `Dev-${data.employee_index ?? 0}`,
        agentColor: getAgentColor(`dev-${data.employee_index ?? 0}`),
        type: 'phase',
        content: `Started working${data.issue ? ` on #${data.issue}` : ''}`,
      });
      break;
    case 'employee_complete':
      addConversationEntry({
        agentName: `Dev-${data.employee_index ?? 0}`,
        agentColor: getAgentColor(`dev-${data.employee_index ?? 0}`),
        type: 'phase',
        content: `Finished — ${data.turns ?? '?'} turns`,
      });
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
      agentPresence.phase = 'idle';
      addConversationEntry({
        agentName: 'Manager',
        agentColor,
        type: 'phase',
        content: 'Run completed',
      });
      refreshActiveRuns();
      break;
    case 'conflict_detected':
      addConversationEntry({
        agentName: 'Coordinator',
        agentColor: ROLE_COLORS.coordinator,
        type: 'system',
        content: `Conflict detected: ${data.message ?? 'overlapping file changes'}`,
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

    if (employees.status === 'fulfilled') {
      agentPresence.activeRuns = employees.value;
      agentPresence.phase = derivePhase(employees.value);
      agentPresence.agents = deriveAgents(employees.value);
    }

    if (latest.status === 'fulfilled') {
      agentPresence.latestRun = latest.value;
      agentPresence.latestRunId = latest.value.run_id;
    }
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

  // WebSocket for log stream
  ws = new LogWebSocket(
    '/api/logs/stream',
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
  if (sse) { sse.close(); sse = null; }
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
