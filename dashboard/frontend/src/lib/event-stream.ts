/**
 * SSE client for receiving real-time agent events from /api/events/stream.
 *
 * Usage:
 *   const stream = new AgentEventStream({
 *     onEvent: (event) => console.log(event),
 *     onStatusChange: (connected) => console.log('SSE:', connected),
 *   });
 *   stream.connect();
 *   // later:
 *   stream.disconnect();
 */

export interface AgentEvent {
  type: string;
  run_id?: string;
  event?: string;
  normalized?: string;
  project?: string;
  status?: string;
  verdict?: string;
  issue_number?: number;
  branch?: string;
  mode?: string;
  model?: string;
  timestamp?: string;
}

export interface AgentEventStreamOptions {
  onEvent: (event: AgentEvent) => void;
  onStatusChange?: (connected: boolean) => void;
}

/** Known event types emitted by run-manager.sh and coordinator */
const EVENT_TYPES = [
  'run_start',
  'employee_start',
  'employee_complete',
  'manager_review',
  'verdict_execute',
  'run_complete',
  // Legacy names
  'started',
  'finished',
  'verdict',
  // Coordinator events
  'task_started',
  'task_completed',
  'task_failed',
  'task_ready',
  'task_blocked',
  'conflict_detected',
  'guidance_sent',
  'dag_created',
  'dag_completed',
];

import { getStoredApiKey } from './api';

export class AgentEventStream {
  private source: EventSource | null = null;
  private options: AgentEventStreamOptions;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private shouldReconnect = false;

  constructor(options: AgentEventStreamOptions) {
    this.options = options;
  }

  connect(): void {
    this.shouldReconnect = true;
    this.doConnect();
  }

  private doConnect(): void {
    if (this.source) {
      this.source.close();
    }

    const base = import.meta.env.VITE_API_URL || '';
    const apiKey = getStoredApiKey();
    const sseUrl = apiKey
      ? `${base}/api/events/stream?token=${encodeURIComponent(apiKey)}`
      : `${base}/api/events/stream`;
    this.source = new EventSource(sseUrl);

    this.source.onopen = () => {
      this.options.onStatusChange?.(true);
    };

    this.source.onerror = () => {
      this.options.onStatusChange?.(false);
      this.source?.close();
      this.source = null;

      if (this.shouldReconnect) {
        this.reconnectTimer = setTimeout(() => this.doConnect(), 3000);
      }
    };

    // Listen for all known event types
    for (const eventType of EVENT_TYPES) {
      this.source.addEventListener(eventType, (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          this.options.onEvent({
            type: eventType,
            ...data,
          });
        } catch {
          // Malformed JSON — ignore
        }
      });
    }

    // Also listen for generic "message" events as fallback
    this.source.onmessage = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        this.options.onEvent({
          type: data.type || 'message',
          ...data,
        });
      } catch {
        // Malformed JSON — ignore
      }
    };
  }

  disconnect(): void {
    this.shouldReconnect = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.source?.close();
    this.source = null;
    this.options.onStatusChange?.(false);
  }
}
