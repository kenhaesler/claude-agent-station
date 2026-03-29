<script lang="ts">
  import type { AgentIdentity, ConversationEntry } from '../../lib/agent-presence.svelte';
  import { formatTokens, formatDuration } from '../../lib/format';
  // GuidanceInput is rendered by the parent page, not here

  let {
    agent,
    entries = [],
    runId = '',
    employeeIndex = 0,
    turnCount = 0,
    tokensBurned = 0,
    currentTool = null,
  }: {
    agent: AgentIdentity;
    entries?: ConversationEntry[];
    runId?: string;
    employeeIndex?: number;
    turnCount?: number;
    tokensBurned?: number;
    currentTool?: { name: string; summary: string } | null;
  } = $props();

  let container: HTMLDivElement;
  let autoScroll = $state(true);
  let expandedThinking = $state<Set<number>>(new Set());

  function handleScroll() {
    if (!container) return;
    const { scrollTop, scrollHeight, clientHeight } = container;
    autoScroll = scrollHeight - scrollTop - clientHeight < 60;
  }

  function scrollToBottom() {
    if (container) {
      container.scrollTop = container.scrollHeight;
      autoScroll = true;
    }
  }

  function toggleThinking(id: number) {
    const next = new Set(expandedThinking);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    expandedThinking = next;
  }

  // Auto-scroll on new entries
  $effect(() => {
    entries.length; // track
    if (autoScroll && container) {
      requestAnimationFrame(() => {
        if (container) container.scrollTop = container.scrollHeight;
      });
    }
  });

  function entryIcon(type: string): string {
    if (type === 'tool_use') return '>';
    if (type === 'thinking') return '\u2022';
    if (type === 'text') return '\u00B6';
    if (type === 'result') return '\u2713';
    if (type === 'phase') return '\u25B6';
    if (type === 'system') return '\u2022';
    if (type === 'guidance') return '\u2191';
    return '\u2022';
  }
</script>

<div class="flex flex-col h-full">
  <!-- Header -->
  <div class="flex items-center justify-between px-4 py-2 border-b border-border shrink-0"
       style="border-left: 3px solid {agent.color}">
    <div class="flex items-center gap-2">
      <span class="w-2.5 h-2.5 rounded-full {agent.status === 'active' ? 'bg-emerald animate-pulse' : agent.status === 'thinking' ? 'bg-violet animate-pulse' : 'bg-text-muted/30'}"></span>
      <span class="text-sm font-medium" style="color: {agent.color}">{agent.name}</span>
      <span class="text-[10px] font-mono text-tertiary uppercase">{agent.role}</span>
    </div>
    <div class="flex items-center gap-3 text-[10px] font-mono text-tertiary">
      <span>{turnCount} turns</span>
      <span>{formatTokens(tokensBurned)} tokens</span>
      {#if currentTool}
        <span class="text-cyan">{currentTool.name}</span>
      {/if}
    </div>
  </div>

  <!-- Conversation stream -->
  <div
    bind:this={container}
    onscroll={handleScroll}
    class="flex-1 overflow-y-auto overflow-x-hidden px-4 py-2 space-y-1"
  >
    {#each entries as entry (entry.id)}
      <div class="group {entry.isError ? 'bg-reject/5 rounded px-1 -mx-1' : ''}">
        {#if entry.type === 'tool_use'}
          <!-- Tool call -->
          <div class="flex items-start gap-2 text-[12px] font-mono leading-relaxed">
            <span class="text-tertiary shrink-0 mt-0.5" style="color: {entry.agentColor}">{'>'}</span>
            <span class="text-tertiary font-medium shrink-0">{entry.toolName}</span>
            <span class="text-secondary/60 truncate">{entry.content}</span>
          </div>
        {:else if entry.type === 'thinking'}
          <!-- Thinking (collapsible) -->
          <button
            class="flex items-start gap-2 text-[11px] font-mono leading-relaxed text-tertiary/40 italic w-full text-left hover:text-tertiary/60 transition-colors"
            onclick={() => toggleThinking(entry.id)}
          >
            <span class="shrink-0 mt-0.5">{expandedThinking.has(entry.id) ? '\u25BC' : '\u25B6'}</span>
            <span class="{expandedThinking.has(entry.id) ? '' : 'line-clamp-2'}">{entry.content}</span>
          </button>
        {:else if entry.type === 'text'}
          <!-- Agent text output -->
          <div class="text-[12px] text-secondary leading-relaxed pl-4">
            {entry.content}
          </div>
        {:else if entry.type === 'result'}
          <!-- Run result -->
          <div class="flex items-center gap-2 text-[11px] font-mono bg-surface-1 rounded px-2 py-1 mt-1">
            <span class="text-approve">{'\u2713'}</span>
            <span class="text-tertiary">{entry.content}</span>
          </div>
        {:else if entry.type === 'phase' || entry.type === 'system'}
          <!-- Phase change / system message -->
          <div class="text-[10px] font-mono text-tertiary italic py-0.5">
            {entry.content}
          </div>
        {:else if entry.type === 'guidance'}
          <!-- Human guidance -->
          <div class="flex items-center gap-2 text-[11px] font-mono bg-cyan/5 border-l-2 border-cyan rounded-r px-2 py-1 mt-1">
            <span class="text-cyan">{'\u2191'}</span>
            <span class="text-secondary">{entry.content}</span>
          </div>
        {/if}
      </div>
    {:else}
      <div class="flex items-center justify-center h-full text-sm text-tertiary font-mono">
        {agent.status === 'idle' ? 'Waiting for activity...' : 'Connecting...'}
      </div>
    {/each}
  </div>

  <!-- Scroll to bottom button -->
  {#if !autoScroll}
    <button
      onclick={scrollToBottom}
      class="absolute bottom-16 right-6 px-2 py-1 rounded-full text-[10px] font-mono
             bg-surface-2 text-tertiary border border-border hover:bg-surface-3 transition-colors z-10"
    >
      {'\u2193'} latest
    </button>
  {/if}

  <!-- Guidance input is rendered by the parent -->
</div>
