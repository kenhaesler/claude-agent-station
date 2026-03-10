<script lang="ts">
  import type { AgentEvent } from '../lib/event-stream';
  import { AgentEventStream } from '../lib/event-stream';
  import TimeAgo from './TimeAgo.svelte';

  interface Props {
    /** Called when an event arrives that should trigger a data refresh */
    onRefresh?: () => void;
    /** Maximum number of events to display in the ticker */
    maxEvents?: number;
  }

  let { onRefresh, maxEvents = 8 }: Props = $props();

  interface TickerEvent {
    id: number;
    type: string;
    message: string;
    project?: string;
    timestamp: Date;
    color: string;
    icon: string;
  }

  let events = $state<TickerEvent[]>([]);
  let connected = $state(false);
  let nextId = 0;

  /** Events that should trigger an immediate dashboard data refresh */
  const REFRESH_EVENTS = new Set([
    'run_start',
    'employee_start',
    'employee_complete',
    'manager_review',
    'verdict_execute',
    'run_complete',
    'started',
    'finished',
    'verdict',
  ]);

  function eventToTicker(event: AgentEvent): TickerEvent {
    const config = EVENT_CONFIG[event.type] || EVENT_CONFIG.default;
    let message = config.label;

    if (event.project) {
      const short = event.project.includes('/') ? event.project.split('/').pop() : event.project;
      message += ` [${short}]`;
    }
    if (event.issue_number) {
      message += ` #${event.issue_number}`;
    }
    if (event.verdict) {
      message += ` - ${event.verdict}`;
    }

    return {
      id: nextId++,
      type: event.type,
      message,
      project: event.project,
      timestamp: new Date(),
      color: config.color,
      icon: config.icon,
    };
  }

  const EVENT_CONFIG: Record<string, { label: string; color: string; icon: string }> = {
    run_start:          { label: 'Run started',         color: 'text-accent-blue',    icon: '>' },
    employee_start:     { label: 'Employee working',    color: 'text-accent-emerald', icon: '#' },
    employee_complete:  { label: 'Employee done',       color: 'text-accent-emerald', icon: '+' },
    manager_review:     { label: 'Manager reviewing',   color: 'text-accent-purple',  icon: '?' },
    verdict_execute:    { label: 'Verdict',             color: 'text-warning',        icon: '!' },
    run_complete:       { label: 'Run complete',        color: 'text-accent-blue',    icon: '*' },
    started:            { label: 'Run started',         color: 'text-accent-blue',    icon: '>' },
    finished:           { label: 'Run finished',        color: 'text-accent-blue',    icon: '*' },
    verdict:            { label: 'Verdict issued',      color: 'text-warning',        icon: '!' },
    default:            { label: 'Event',               color: 'text-text-dim',       icon: '-' },
  };

  function handleEvent(event: AgentEvent) {
    const ticker = eventToTicker(event);
    events = [ticker, ...events].slice(0, maxEvents);

    // Trigger refresh for events that change run state
    if (REFRESH_EVENTS.has(event.type) && onRefresh) {
      onRefresh();
    }
  }

  let stream: AgentEventStream | null = null;

  $effect(() => {
    stream = new AgentEventStream({
      onEvent: handleEvent,
      onStatusChange: (c) => { connected = c; },
    });
    stream.connect();

    return () => {
      stream?.disconnect();
      stream = null;
    };
  });
</script>

<div class="glass rounded-lg px-3 py-2 text-xs border-l-2 border-l-accent-cyan/20">
  <!-- Header -->
  <div class="flex items-center justify-between mb-1.5">
    <div class="flex items-center gap-1.5">
      <span class="ai-text hud-sweep-line font-medium">Live Events</span>
      <span
        class="w-1.5 h-1.5 rounded-full {connected ? 'bg-accent-emerald animate-pulse' : 'bg-reject'}"
        title={connected ? 'Connected' : 'Disconnected'}
      ></span>
    </div>
    {#if events.length > 0}
      <span class="text-text-dim/50 text-[10px]">{events.length} event{events.length !== 1 ? 's' : ''}</span>
    {/if}
  </div>

  <!-- Event list -->
  {#if events.length === 0}
    <div class="text-text-dim/40 text-center py-1">
      {connected ? 'Waiting for events...' : 'Connecting...'}
    </div>
  {:else}
    <div class="space-y-0.5 max-h-32 overflow-y-auto">
      {#each events as ev (ev.id)}
        <div class="flex items-center gap-1.5 py-0.5 animate-fade-in-up">
          <span class="font-data {ev.color} w-3 text-center shrink-0">{ev.icon}</span>
          <span class="text-text truncate font-data">{ev.message}</span>
          <span class="text-text-dim/40 ml-auto shrink-0">
            <TimeAgo date={ev.timestamp.toISOString()} />
          </span>
        </div>
      {/each}
    </div>
  {/if}
</div>
