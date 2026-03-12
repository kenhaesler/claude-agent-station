<script lang="ts">
  import { agentPresence } from '../lib/agent-presence.svelte';
  import type { ConversationEntry } from '../lib/agent-presence.svelte';

  interface Props {
    maxItems?: number;
    compact?: boolean;
  }

  let { maxItems = 20, compact = false }: Props = $props();

  let entries = $derived(
    agentPresence.conversationLog
      .filter(e => e.type === 'phase' || e.type === 'system' || e.type === 'guidance')
      .slice(-maxItems)
  );

  function narrativeText(entry: ConversationEntry): string {
    return entry.content;
  }

  function timeLabel(ts: number): string {
    const d = new Date(ts);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
</script>

<div class="space-y-1">
  {#if entries.length === 0}
    <p class="text-xs text-text-muted py-2">No activity yet.</p>
  {:else}
    {#each entries as entry (entry.id)}
      <div class="flex items-start gap-2 py-1 activity-enter {compact ? '' : 'px-2'}">
        <span class="shrink-0 w-2 h-2 mt-1.5 rounded-full" style="background: {entry.agentColor}"></span>
        <div class="min-w-0 flex-1">
          <div class="flex items-baseline gap-1.5">
            <span class="text-xs font-medium" style="color: {entry.agentColor}">{entry.agentName}</span>
            <span class="text-[10px] text-text-muted">{timeLabel(entry.timestamp)}</span>
          </div>
          <p class="text-xs text-text-dim leading-relaxed mt-0.5">{narrativeText(entry)}</p>
        </div>
      </div>
    {/each}
  {/if}
</div>
