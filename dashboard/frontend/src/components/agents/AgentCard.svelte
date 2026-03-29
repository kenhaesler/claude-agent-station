<script lang="ts">
  import AgentAvatar from './AgentAvatar.svelte';
  import { formatDuration, formatCompact } from '../../lib/chart-utils';

  let {
    name = '',
    role = '',
    color = 'var(--color-info)',
    status = 'idle',
    currentTool = null,
    turns = 0,
    tokens = 0,
    elapsed = 0,
    issueNumber = null,
    projectRepo = null,
    compact = false,
    onclick,
  }: {
    name: string;
    role?: string;
    color?: string;
    status?: 'active' | 'thinking' | 'idle' | 'error';
    currentTool?: { name: string; summary: string } | null;
    turns?: number;
    tokens?: number;
    elapsed?: number;
    issueNumber?: number | null;
    projectRepo?: string | null;
    compact?: boolean;
    onclick?: () => void;
  } = $props();
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
  class="glass rounded-lg p-3 transition-all duration-normal hover:bg-surface-2/50
         {onclick ? 'cursor-pointer' : ''} {compact ? '' : 'min-w-[220px]'}"
  {onclick}
  style="border-left: 3px solid {color}"
>
  <!-- Header -->
  <div class="flex items-center gap-2 mb-2">
    <AgentAvatar {name} {color} {status} size={compact ? 'sm' : 'md'} />
    <div class="min-w-0 flex-1">
      <div class="text-sm font-medium text-primary truncate">{name}</div>
      {#if role}
        <div class="text-[10px] text-tertiary uppercase tracking-wider">{role}</div>
      {/if}
    </div>
    {#if status === 'active'}
      <span class="text-[10px] px-1.5 py-0.5 rounded bg-status-active/20 text-status-active uppercase tracking-wider">live</span>
    {/if}
  </div>

  {#if !compact}
    <!-- Current tool -->
    {#if currentTool}
      <div class="text-xs text-secondary truncate mb-2 font-mono">
        <span class="text-tertiary">{currentTool.name}:</span> {currentTool.summary}
      </div>
    {:else if status === 'thinking'}
      <div class="text-xs text-status-thinking mb-2">Thinking...</div>
    {/if}

    <!-- Issue context -->
    {#if issueNumber || projectRepo}
      <div class="flex items-center gap-2 text-[10px] text-tertiary mb-2">
        {#if projectRepo}
          <span class="truncate">{projectRepo}</span>
        {/if}
        {#if issueNumber}
          <span class="text-indigo">#{issueNumber}</span>
        {/if}
      </div>
    {/if}

    <!-- Metrics -->
    <div class="flex items-center gap-3 text-[10px] text-tertiary font-mono">
      <span title="Turns">{turns} turns</span>
      <span title="Tokens">{formatCompact(tokens)} tok</span>
      {#if elapsed > 0}
        <span title="Elapsed">{formatDuration(elapsed)}</span>
      {/if}
    </div>
  {/if}
</div>
