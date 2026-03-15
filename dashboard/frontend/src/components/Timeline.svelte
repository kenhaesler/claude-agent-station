<script lang="ts">
  import type { ConversationEntry } from '../lib/agent-presence.svelte';
  import { agentPresence } from '../lib/agent-presence.svelte';
  import { timeAgo } from '../lib/format';

  interface Props {
    maxItems?: number;
    class?: string;
  }

  let { maxItems = 30, class: className = '' }: Props = $props();

  let entries = $derived(
    agentPresence.conversationLog.slice(-maxItems).reverse()
  );

  function typeIcon(type: ConversationEntry['type']): string {
    switch (type) {
      case 'tool_use': return '⚡';
      case 'thinking': return '💭';
      case 'text': return '💬';
      case 'result': return '📋';
      case 'guidance': return '📡';
      case 'phase': return '◈';
      case 'system': return '⚙';
      default: return '●';
    }
  }
</script>

<div class="space-y-0 {className}" role="log" aria-label="Activity timeline" aria-live="polite">
  {#each entries as entry (entry.id)}
    <div class="flex gap-3 py-2 border-b border-border-subtle/50 last:border-0 activity-enter">
      <!-- Timeline line + avatar -->
      <div class="flex flex-col items-center shrink-0">
        <div
          class="w-6 h-6 rounded-full flex items-center justify-center text-[10px] border border-border-subtle"
          style="background: {entry.agentColor}20; color: {entry.agentColor}"
        >
          {typeIcon(entry.type)}
        </div>
        <div class="w-px flex-1 bg-border-subtle/50 mt-1"></div>
      </div>

      <!-- Content -->
      <div class="flex-1 min-w-0 pb-1">
        <div class="flex items-center gap-2">
          <span class="text-[11px] font-semibold" style="color: {entry.agentColor}">{entry.agentName}</span>
          {#if entry.toolName}
            <span class="text-[10px] text-text-muted font-data">{entry.toolName}</span>
          {/if}
          <span class="text-[9px] text-text-muted ml-auto shrink-0">
            {timeAgo(new Date(entry.timestamp).toISOString())}
          </span>
        </div>
        <p class="text-[11px] text-text-dim leading-relaxed mt-0.5 break-words {entry.isError ? 'text-reject' : ''}">
          {entry.content}
        </p>
      </div>
    </div>
  {/each}

  {#if entries.length === 0}
    <div class="text-center py-6 text-xs text-text-muted">No activity yet</div>
  {/if}
</div>
