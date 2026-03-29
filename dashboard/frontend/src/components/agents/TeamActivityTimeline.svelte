<script lang="ts">
  import type { ConversationEntry } from '../../lib/agent-presence.svelte';
  import type { CoordinatorMessage } from '../../lib/types';

  let {
    conversationLog = [],
    messages = [],
  }: {
    conversationLog?: ConversationEntry[];
    messages?: CoordinatorMessage[];
  } = $props();

  interface TimelineEntry {
    id: string;
    timestamp: number;
    senderName: string;
    senderColor: string;
    recipientName: string | null;
    type: 'message' | 'task_claim' | 'task_complete' | 'guidance' | 'conflict' | 'phase' | 'system';
    content: string;
  }

  // Merge coordinator messages + conversation log phase/system/guidance entries into unified timeline
  let timeline = $derived.by(() => {
    const items: TimelineEntry[] = [];

    // From conversation log: phase changes, system messages, guidance
    for (const entry of conversationLog) {
      if (entry.type === 'phase' || entry.type === 'system' || entry.type === 'guidance') {
        items.push({
          id: `log-${entry.id}`,
          timestamp: entry.timestamp,
          senderName: entry.agentName,
          senderColor: entry.agentColor,
          recipientName: null,
          type: entry.type === 'guidance' ? 'guidance' : entry.type === 'phase' ? 'phase' : 'system',
          content: entry.content,
        });
      }
    }

    // From coordinator messages
    for (const msg of messages) {
      const ts = msg.created_at ? new Date(msg.created_at).getTime() : Date.now();
      const content = typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content);
      items.push({
        id: `msg-${msg.id}`,
        timestamp: ts,
        senderName: msg.direction === 'to_employee' ? 'Lead' : `Teammate ${(msg.employee_index ?? 0) + 1}`,
        senderColor: msg.direction === 'to_employee' ? '#f59e0b' : '#3b82f6',
        recipientName: msg.direction === 'to_employee' ? `Teammate ${(msg.employee_index ?? 0) + 1}` : 'Lead',
        type: msg.message_type === 'guidance' ? 'guidance' : msg.message_type === 'conflict' ? 'conflict' : 'message',
        content,
      });
    }

    return items.sort((a, b) => a.timestamp - b.timestamp).slice(-100);
  });

  let container: HTMLDivElement;
  let autoScroll = $state(true);

  function handleScroll() {
    if (!container) return;
    const { scrollTop, scrollHeight, clientHeight } = container;
    autoScroll = scrollHeight - scrollTop - clientHeight < 40;
  }

  $effect(() => {
    timeline.length;
    if (autoScroll && container) {
      requestAnimationFrame(() => { if (container) container.scrollTop = container.scrollHeight; });
    }
  });

  function formatTime(ts: number): string {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function typeIcon(type: string): string {
    if (type === 'guidance') return '\u2191';
    if (type === 'conflict') return '\u26A0';
    if (type === 'task_claim') return '\u25B6';
    if (type === 'task_complete') return '\u2713';
    if (type === 'phase') return '\u25C6';
    return '\u2022';
  }

  function typeBorder(type: string): string {
    if (type === 'guidance') return 'border-l-cyan';
    if (type === 'conflict') return 'border-l-amber';
    if (type === 'phase') return 'border-l-violet';
    return 'border-l-border-subtle';
  }
</script>

<div class="flex flex-col h-full">
  <div class="flex items-center justify-between px-3 py-2 border-b border-border shrink-0">
    <span class="text-[10px] text-tertiary uppercase tracking-wider font-mono">Team Activity</span>
    <span class="text-[10px] text-tertiary font-mono">{timeline.length} events</span>
  </div>

  <div
    bind:this={container}
    onscroll={handleScroll}
    class="flex-1 overflow-y-auto px-3 py-2 space-y-1"
  >
    {#each timeline as entry (entry.id)}
      <div class="flex items-start gap-2 text-[11px] font-mono border-l-2 pl-2 py-0.5 {typeBorder(entry.type)}">
        <span class="text-tertiary/40 shrink-0 w-[52px]">{formatTime(entry.timestamp)}</span>
        <span class="shrink-0" style="color: {entry.senderColor}">{entry.senderName}</span>
        {#if entry.recipientName}
          <span class="text-tertiary/30">{'\u2192'}</span>
          <span class="text-tertiary shrink-0">{entry.recipientName}</span>
        {/if}
        <span class="text-secondary/60 truncate">{entry.content}</span>
      </div>
    {:else}
      <div class="text-[11px] font-mono text-tertiary italic text-center py-4">
        No team activity yet
      </div>
    {/each}
  </div>
</div>
