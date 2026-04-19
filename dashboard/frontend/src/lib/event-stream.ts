// ============================================
// SSE Client — /api/events/stream
// Auto-reconnect with exponential backoff + jitter
// ============================================

import { getStoredApiKey } from './api';

export type ConnectionState = 'connected' | 'disconnected' | 'reconnecting';

export type EventCallback = (data: Record<string, unknown>) => void;

export interface EventStreamOptions {
  /** Called when any event arrives. The `type` field is included in data. */
  onEvent?: (data: Record<string, unknown>) => void;
  /** Called when connection state changes */
  onStatusChange?: (state: ConnectionState) => void;
  /** Per-event-type callbacks */
  handlers?: Record<string, EventCallback>;
}

const MAX_RECONNECT_DELAY = 30_000;
const INITIAL_RECONNECT_DELAY = 1_000;

export class AgentEventStream {
  private source: EventSource | null = null;
  private options: EventStreamOptions;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private shouldReconnect = false;
  private reconnectDelay = INITIAL_RECONNECT_DELAY;
  private _state: ConnectionState = 'disconnected';

  constructor(options: EventStreamOptions) {
    this.options = options;
  }

  get state(): ConnectionState {
    return this._state;
  }

  connect(): void {
    this.shouldReconnect = true;
    this.doConnect();
  }

  disconnect(): void {
    this.shouldReconnect = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.source) {
      this.source.close();
      this.source = null;
    }
    this.setState('disconnected');
  }

  private setState(state: ConnectionState): void {
    this._state = state;
    this.options.onStatusChange?.(state);
  }

  private doConnect(): void {
    if (this.source) {
      this.source.close();
      this.source = null;
    }

    const base = import.meta.env.VITE_API_URL || '';
    const apiKey = getStoredApiKey();
    const sseUrl = apiKey
      ? `${base}/api/events/stream?token=${encodeURIComponent(apiKey)}`
      : `${base}/api/events/stream`;

    this.source = new EventSource(sseUrl);

    this.source.onopen = () => {
      this.reconnectDelay = INITIAL_RECONNECT_DELAY;
      this.setState('connected');
    };

    this.source.onerror = () => {
      this.source?.close();
      this.source = null;

      if (this.shouldReconnect) {
        this.setState('reconnecting');
        // Exponential backoff with jitter
        const jitter = Math.random() * 1000;
        const delay = Math.min(this.reconnectDelay + jitter, MAX_RECONNECT_DELAY);
        this.reconnectTimer = setTimeout(() => this.doConnect(), delay);
        this.reconnectDelay = Math.min(this.reconnectDelay * 2, MAX_RECONNECT_DELAY);
      } else {
        this.setState('disconnected');
      }
    };

    // Listen for generic "message" events (default SSE event type)
    this.source.onmessage = (e: MessageEvent) => {
      this.handleRawEvent(e);
    };

    // Also register for all named event types if handlers are provided
    if (this.options.handlers) {
      for (const eventType of Object.keys(this.options.handlers)) {
        this.source.addEventListener(eventType, (e) => {
          this.handleRawEvent(e as MessageEvent, eventType);
        });
      }
    }

    // Register for well-known event types that may come as named SSE events
    const knownTypes = [
      'run_start', 'employee_start', 'employee_complete', 'manager_review',
      'verdict_execute', 'run_complete', 'run_interrupted',
      'started', 'finished', 'verdict',
      'task_started', 'task_completed', 'task_failed', 'task_ready', 'task_blocked',
      'conflict_detected', 'guidance_sent', 'dag_created', 'dag_completed',
      'queue_pending', 'queue_claimed', 'queue_assigned', 'queue_in_progress',
      'queue_review', 'queue_approved', 'queue_rejected', 'queue_completed', 'queue_failed',
      'intelligence_decision', 'notification',
      'permission_request', 'permission_resolved',
    ];

    for (const eventType of knownTypes) {
      // Skip if already registered via handlers
      if (this.options.handlers?.[eventType]) continue;
      this.source.addEventListener(eventType, (e) => {
        this.handleRawEvent(e as MessageEvent, eventType);
      });
    }
  }

  private handleRawEvent(e: MessageEvent, forcedType?: string): void {
    try {
      const data = JSON.parse(e.data);
      const eventType = forcedType || data.type || 'message';
      const enriched = { ...data, type: eventType };

      // Route to specific handler if registered
      this.options.handlers?.[eventType]?.(enriched);

      // Always call onEvent for centralized processing
      this.options.onEvent?.(enriched);
    } catch {
      // Malformed JSON -- ignore
    }
  }
}
