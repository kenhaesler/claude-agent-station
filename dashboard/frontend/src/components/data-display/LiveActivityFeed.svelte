<script lang="ts">
  import type { ConversationEntry } from '../../lib/agent-presence.svelte';
  import EmptyState from './EmptyState.svelte';

  let {
    entries,
    maxHeight = '400px',
  }: {
    entries: ConversationEntry[];
    maxHeight?: string;
  } = $props();

  let scrollContainer: HTMLDivElement | undefined = $state();
  let autoScroll = $state(true);

  function getInitials(name: string): string {
    return name.split(/\s+/).map(w => w[0]).join('').toUpperCase().slice(0, 2);
  }

  function formatTime(timestamp: number): string {
    const d = new Date(timestamp);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function getTypeIcon(type: ConversationEntry['type']): string {
    switch (type) {
      case 'tool_use': return '>';
      case 'thinking': return '...';
      case 'result': return '\u2713';
      case 'guidance': return '\u2192';
      case 'phase': return '\u25C6';
      case 'system': return '\u24D8';
      default: return '';
    }
  }

  function getTypeColor(type: ConversationEntry['type'], isError?: boolean): string {
    if (isError) return 'var(--color-rose)';
    switch (type) {
      case 'tool_use': return 'var(--color-cyan)';
      case 'thinking': return 'var(--color-tertiary)';
      case 'result': return 'var(--color-emerald)';
      case 'guidance': return 'var(--color-amber)';
      case 'phase': return 'var(--color-violet)';
      case 'system': return 'var(--color-indigo)';
      default: return 'var(--color-secondary)';
    }
  }

  // Auto-scroll on new entries
  $effect(() => {
    // Access entries.length to track changes
    const _len = entries.length;
    if (autoScroll && scrollContainer) {
      requestAnimationFrame(() => {
        if (scrollContainer) {
          scrollContainer.scrollTop = scrollContainer.scrollHeight;
        }
      });
    }
  });

  function handleScroll() {
    if (!scrollContainer) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollContainer;
    // If user scrolled up more than 50px from bottom, disable auto-scroll
    autoScroll = scrollHeight - scrollTop - clientHeight < 50;
  }
</script>

<div class="card flex flex-col overflow-hidden">
  <div
    bind:this={scrollContainer}
    onscroll={handleScroll}
    class="overflow-y-auto"
    style="max-height: {maxHeight};"
  >
    {#if entries.length === 0}
      <EmptyState
        compact
        title="No activity yet"
        description="Activity will appear here when agents start working"
        icon="◉"
      />
    {:else}
      <div class="flex flex-col">
        {#each entries as entry (entry.id)}
          <div
            class="flex gap-3 px-3 py-2 hover:bg-surface-1/50 transition-colors duration-100"
            style={entry.isError ? 'border-left: 2px solid var(--color-rose);' : ''}
          >
            <!-- Mini avatar -->
            <div
              class="w-6 h-6 rounded-full flex items-center justify-center text-[9px] font-semibold shrink-0 mt-0.5"
              style="background: {entry.agentColor}20; color: {entry.agentColor};"
            >
              {getInitials(entry.agentName)}
            </div>

            <!-- Content -->
            <div class="min-w-0 flex-1">
              <!-- Header: agent name + timestamp -->
              <div class="flex items-center gap-2">
                <span class="text-xs font-semibold" style="color: {entry.agentColor};">
                  {entry.agentName}
                </span>
                {#if entry.toolName}
                  <span class="badge text-[9px] py-0 px-1.5" style="background: {getTypeColor(entry.type)}15; color: {getTypeColor(entry.type)};">
                    {entry.toolName}
                  </span>
                {/if}
                <span class="text-[10px] text-tertiary font-mono ml-auto shrink-0">
                  {formatTime(entry.timestamp)}
                </span>
              </div>

              <!-- Content text -->
              <div class="text-xs mt-0.5 leading-relaxed" style="color: {getTypeColor(entry.type, entry.isError)};">
                {#if entry.type !== 'text'}
                  <span class="font-mono mr-1 opacity-60">{getTypeIcon(entry.type)}</span>
                {/if}
                <span class={entry.type === 'thinking' ? 'opacity-50' : ''}>{entry.content}</span>
              </div>
            </div>
          </div>
        {/each}
      </div>
    {/if}
  </div>

  <!-- Auto-scroll indicator -->
  {#if !autoScroll && entries.length > 0}
    <button
      class="flex items-center justify-center gap-1 py-1.5 text-[10px] font-mono text-cyan bg-surface-1 border-t border-border hover:bg-surface-2 transition-colors"
      onclick={() => { autoScroll = true; if (scrollContainer) scrollContainer.scrollTop = scrollContainer.scrollHeight; }}
    >
      New activity below
    </button>
  {/if}
</div>
