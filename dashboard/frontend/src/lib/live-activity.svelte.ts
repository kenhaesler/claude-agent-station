/**
 * LiveActivityStore — shared reactive data layer for real-time agent activity.
 * Connects to WebSocket /api/logs/stream and parses JSONL events into dashboard state.
 *
 * Uses a single mutable $state object since Svelte 5 modules cannot export
 * reassigned state variables. All consumers read properties from `liveActivity`.
 */

import { LogWebSocket } from './ws';
import { parseLogLine, formatToolInput, truncate } from './log-parser';
import type { ParsedLogEvent } from './log-parser';

const MAX_ACTIONS = 50;
const SPARKLINE_POINTS = 60;
const CURRENT_TOOL_TIMEOUT = 10_000;
const EWMA_ALPHA = 0.1;
const SAMPLE_INTERVAL = 1_000;

interface LiveActivityState {
  recentActions: ParsedLogEvent[];
  currentTool: { name: string; summary: string } | null;
  activityIntensity: number;
  tokensBurned: number;
  turnCount: number;
  connected: boolean;
  sparklineData: number[];
}

// Single mutable state object — properties are mutated, never reassigned
export const liveActivity = $state<LiveActivityState>({
  recentActions: [],
  currentTool: null,
  activityIntensity: 0,
  tokensBurned: 0,
  turnCount: 0,
  connected: false,
  sparklineData: [],
});

// --- Internal state ---
let ws: LogWebSocket | null = null;
let currentToolTimer: ReturnType<typeof setTimeout> | null = null;
let sampleTimer: ReturnType<typeof setInterval> | null = null;
let eventsSinceLastSample = 0;
let rawIntensity = 0;

function handleMessage(data: string) {
  const parsed = parseLogLine(data);
  if (!parsed) return;

  const events = Array.isArray(parsed) ? parsed : [parsed];

  for (const evt of events) {
    if (evt.type === 'assistant_tool_use') {
      // Add to recent actions (mutate array in place)
      liveActivity.recentActions.push(evt);
      if (liveActivity.recentActions.length > MAX_ACTIONS) {
        liveActivity.recentActions.splice(0, liveActivity.recentActions.length - MAX_ACTIONS);
      }
      eventsSinceLastSample++;

      // Update current tool
      const summary = evt.toolName
        ? truncate(formatToolInput(evt.toolName, evt.toolInput), 60)
        : '';
      liveActivity.currentTool = { name: evt.toolName ?? 'Unknown', summary };

      // Reset timeout for clearing current tool
      if (currentToolTimer) clearTimeout(currentToolTimer);
      currentToolTimer = setTimeout(() => {
        liveActivity.currentTool = null;
      }, CURRENT_TOOL_TIMEOUT);
    }

    if (evt.type === 'tool_result') {
      eventsSinceLastSample++;
    }

    if (evt.type === 'assistant_text' || evt.type === 'assistant_thinking') {
      liveActivity.turnCount++;
    }

    if (evt.type === 'result') {
      if (evt.tokensTotal) {
        liveActivity.tokensBurned += evt.tokensTotal;
      }
    }
  }
}

function sampleIntensity() {
  const rate = Math.min(eventsSinceLastSample / 5, 1);
  rawIntensity = EWMA_ALPHA * rate + (1 - EWMA_ALPHA) * rawIntensity;
  liveActivity.activityIntensity = rawIntensity;
  eventsSinceLastSample = 0;

  liveActivity.sparklineData.push(rawIntensity);
  if (liveActivity.sparklineData.length > SPARKLINE_POINTS) {
    liveActivity.sparklineData.splice(0, liveActivity.sparklineData.length - SPARKLINE_POINTS);
  }
}

export function connect() {
  if (ws) return;

  ws = new LogWebSocket(
    '/api/logs/stream',
    handleMessage,
    (status) => { liveActivity.connected = status; }
  );
  ws.connect();

  sampleTimer = setInterval(sampleIntensity, SAMPLE_INTERVAL);
}

/** Clear accumulated state without disconnecting. Call when runs finish. */
export function reset() {
  liveActivity.recentActions.length = 0;
  liveActivity.currentTool = null;
  liveActivity.activityIntensity = 0;
  liveActivity.tokensBurned = 0;
  liveActivity.turnCount = 0;
  liveActivity.sparklineData.length = 0;
  rawIntensity = 0;
  eventsSinceLastSample = 0;
  if (currentToolTimer) {
    clearTimeout(currentToolTimer);
    currentToolTimer = null;
  }
}

export function disconnect() {
  if (ws) {
    ws.disconnect();
    ws = null;
  }
  if (currentToolTimer) {
    clearTimeout(currentToolTimer);
    currentToolTimer = null;
  }
  if (sampleTimer) {
    clearInterval(sampleTimer);
    sampleTimer = null;
  }

  // Reset state by mutating properties
  liveActivity.recentActions.length = 0;
  liveActivity.currentTool = null;
  liveActivity.activityIntensity = 0;
  liveActivity.tokensBurned = 0;
  liveActivity.turnCount = 0;
  liveActivity.connected = false;
  liveActivity.sparklineData.length = 0;
  rawIntensity = 0;
  eventsSinceLastSample = 0;
}
