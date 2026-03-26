<script lang="ts">
  import { agentPresence, type ConversationEntry } from '../../lib/agent-presence.svelte';

  let {
    maxEntries = 100,
    filter = null,
  }: {
    maxEntries?: number;
    filter?: string | null;
  } = $props();

  let feedEl: HTMLDivElement | undefined = $state();
  let autoScroll = $state(true);

  let entries = $derived.by(() => {
    let log = agentPresence.conversationLog;
    if (filter) {
      log = log.filter(e => e.agentName.toLowerCase().includes(filter!.toLowerCase()));
    }
    return log.slice(-maxEntries);
  });

  // Auto-scroll to bottom on new entries
  $effect(() => {
    if (entries.length && autoScroll && feedEl) {
      requestAnimationFrame(() => {
        feedEl!.scrollTop = feedEl!.scrollHeight;
      });
    }
  });

  function handleScroll() {
    if (!feedEl) return;
    const { scrollTop, scrollHeight, clientHeight } = feedEl;
    autoScroll = scrollHeight - scrollTop - clientHeight < 40;
  }

  const typeIcons: Record<string, string> = {
    tool_use: '⚡',
    thinking: '💭',
    text: '💬',
    result: '📋',
    phase: '🔄',
    system: '⚙',
    guidance: '📢',
  };
</script>

<div
  bind:this={feedEl}
  onscroll={handleScroll}
  class="flex flex-col gap-0.5 overflow-y-auto h-full text-xs font-data"
>
  {#each entries as entry (entry.id)}
    <div class="flex gap-2 px-2 py-1 rounded hover:bg-surface/40 activity-enter
                {entry.isError ? 'bg-reject/5' : ''}">
      <span class="text-text-muted shrink-0 w-4 text-center">{typeIcons[entry.type] ?? '·'}</span>
      <span class="shrink-0 w-16 truncate" style="color: {entry.agentColor}">{entry.agentName}</span>
      <span class="text-text-dim flex-1 break-words min-w-0">
        {#if entry.toolName}
          <span class="text-text-muted">{entry.toolName}:</span>
        {/if}
        {entry.content}
      </span>
    </div>
  {/each}

  {#if entries.length === 0}
    <div class="flex items-center justify-center h-full text-text-muted text-sm">
      No activity yet
    </div>
  {/if}
</div>

{#if !autoScroll}
  <button
    onclick={() => { autoScroll = true; feedEl?.scrollTo({ top: feedEl.scrollHeight, behavior: 'smooth' }); }}
    class="absolute bottom-2 right-4 px-2 py-1 rounded bg-surface-2 text-text-muted text-[10px] hover:text-text transition-colors"
  >
    ↓ Auto-scroll
  </button>
{/if}
