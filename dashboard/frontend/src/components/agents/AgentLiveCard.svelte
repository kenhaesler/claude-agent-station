<script lang="ts">
  import type { AgentIdentity, ConversationEntry } from '../../lib/agent-presence.svelte';
  import { formatTokens, formatDuration } from '../../lib/format';

  let {
    agent,
    entries = [],
    projectRepo = '',
    issueNumber = null,
    turnCount = 0,
    tokensBurned = 0,
    elapsedMs = 0,
    selected = false,
    onclick,
  }: {
    agent: AgentIdentity;
    entries?: ConversationEntry[];
    projectRepo?: string;
    issueNumber?: number | null;
    turnCount?: number;
    tokensBurned?: number;
    elapsedMs?: number;
    selected?: boolean;
    onclick?: () => void;
  } = $props();

  let recentTools = $derived(
    entries
      .filter(e => e.type === 'tool_use')
      .slice(-5)
      .reverse()
  );

  function statusDot(status: string): string {
    if (status === 'active') return 'bg-emerald';
    if (status === 'thinking') return 'bg-violet animate-pulse';
    if (status === 'error') return 'bg-reject';
    return 'bg-text-muted/30';
  }
</script>

<button
  class="w-full text-left glass rounded-lg overflow-hidden transition-all duration-150
         hover:ring-1 hover:ring-border-hover cursor-pointer
         {selected ? 'ring-1 ring-cyan/50' : ''}"
  style="border-top: 2px solid {agent.color}"
  {onclick}
>
  <!-- Header -->
  <div class="flex items-center justify-between px-3 py-2">
    <div class="flex items-center gap-2 min-w-0">
      <span class="w-2 h-2 rounded-full shrink-0 {statusDot(agent.status)}"></span>
      <span class="text-xs font-medium text-primary truncate" style="color: {agent.color}">{agent.name}</span>
    </div>
    <div class="flex items-center gap-2 shrink-0">
      {#if projectRepo}
        <span class="text-[10px] font-mono text-tertiary truncate max-w-[80px]">{projectRepo.split('/').pop()}</span>
      {/if}
      {#if issueNumber}
        <span class="text-[10px] font-mono text-tertiary">#{issueNumber}</span>
      {/if}
    </div>
  </div>

  <!-- Tool stream -->
  <div class="px-3 pb-1 space-y-0.5 min-h-[60px]">
    {#each recentTools as entry}
      <div class="flex items-center gap-1.5 text-[11px] font-mono text-secondary leading-tight">
        <span class="text-tertiary shrink-0">{'>'}</span>
        <span class="text-tertiary shrink-0">{entry.toolName ?? '?'}</span>
        <span class="truncate text-secondary/60">{entry.content}</span>
      </div>
    {:else}
      <div class="text-[11px] font-mono text-tertiary italic">
        {agent.status === 'thinking' ? 'Thinking...' : 'Waiting...'}
      </div>
    {/each}
  </div>

  <!-- Footer metrics -->
  <div class="flex items-center justify-between px-3 py-1.5 border-t border-border/30 bg-surface-0/30">
    <div class="flex items-center gap-3 text-[10px] font-mono text-tertiary">
      <span>{turnCount} turns</span>
      <span>{formatTokens(tokensBurned)}</span>
    </div>
    <span class="text-[10px] font-mono text-tertiary">{formatDuration(elapsedMs)}</span>
  </div>
</button>
