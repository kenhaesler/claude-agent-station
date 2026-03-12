<script lang="ts">
  import { agentPresence } from '../lib/agent-presence.svelte';
  import AgentAvatar from './AgentAvatar.svelte';
  import AgentThinking from './AgentThinking.svelte';
  import GuidanceInput from './GuidanceInput.svelte';

  interface Props {
    onClose: () => void;
  }

  let { onClose }: Props = $props();

  let feedContainer: HTMLDivElement;
  let autoScroll = $state(true);

  // Auto-scroll to bottom on new entries
  $effect(() => {
    // Track length to trigger effect
    const _len = agentPresence.conversationLog.length;
    if (autoScroll && feedContainer) {
      requestAnimationFrame(() => {
        feedContainer.scrollTop = feedContainer.scrollHeight;
      });
    }
  });

  function handleScroll() {
    if (!feedContainer) return;
    const { scrollTop, scrollHeight, clientHeight } = feedContainer;
    autoScroll = scrollHeight - scrollTop - clientHeight < 60;
  }

  let phaseLabel = $derived(
    agentPresence.phase === 'idle' ? 'Idle' :
    agentPresence.phase === 'coordinating' ? 'Coordinating' :
    agentPresence.phase === 'employee' ? 'Working' :
    agentPresence.phase === 'manager_review' ? 'Reviewing' :
    agentPresence.phase === 'executing_verdict' ? 'Verdict' : 'Unknown'
  );

  function getEntryIcon(type: string): string {
    switch (type) {
      case 'tool_use': return '>';
      case 'thinking': return '~';
      case 'text': return '#';
      case 'result': return '=';
      case 'guidance': return '<';
      case 'phase': return '*';
      case 'system': return '!';
      default: return '-';
    }
  }
</script>

<!-- Mobile overlay backdrop -->
<div
  class="fixed inset-0 bg-black/40 z-30 md:hidden"
  onclick={onClose}
  role="presentation"
></div>

<!-- Panel -->
<aside class="agent-panel-enter fixed md:relative right-0 top-0 md:top-auto h-full w-full md:w-[400px] md:max-w-[400px] bg-surface border-l border-border flex flex-col z-40 md:z-auto shrink-0">
  <!-- Header -->
  <div class="flex items-center justify-between px-3 h-12 border-b border-border shrink-0">
    <div class="flex items-center gap-2">
      <span class="text-sm font-semibold text-text">Agent Panel</span>
      <span class="text-[10px] font-data px-1.5 py-0.5 rounded-full bg-surface-2 text-text-dim">{phaseLabel}</span>
    </div>
    <button
      onclick={onClose}
      class="text-text-dim hover:text-text text-lg cursor-pointer p-1"
      title="Close panel (Esc)"
    >
      &times;
    </button>
  </div>

  <!-- Agent presence bar -->
  {#if agentPresence.agents.length > 0}
    <div class="flex items-center gap-2 px-3 py-2 border-b border-border-subtle">
      {#each agentPresence.agents as agent}
        <AgentAvatar name={agent.name} role={agent.role} color={agent.color} status={agent.status} size="sm" showName />
      {/each}
    </div>
  {/if}

  <!-- Conversation feed -->
  <div
    bind:this={feedContainer}
    onscroll={handleScroll}
    class="flex-1 overflow-auto px-3 py-2 space-y-1"
  >
    {#if agentPresence.conversationLog.length === 0}
      <div class="flex items-center justify-center h-full text-text-muted text-sm">
        <p>No agent activity yet. Trigger a run to begin.</p>
      </div>
    {:else}
      {#each agentPresence.conversationLog as entry (entry.id)}
        <div class="flex gap-2 py-1 animate-slide-in-right {entry.type === 'guidance' ? 'flex-row-reverse' : ''}">
          <!-- Agent indicator -->
          <div class="shrink-0 w-5 text-center">
            <span class="text-[10px] font-data font-bold" style="color: {entry.agentColor}">
              {getEntryIcon(entry.type)}
            </span>
          </div>

          <div class="min-w-0 flex-1">
            <!-- Agent name + type -->
            <div class="flex items-center gap-1.5">
              <span class="text-[11px] font-semibold" style="color: {entry.agentColor}">{entry.agentName}</span>
              {#if entry.toolName}
                <span class="text-[10px] font-data text-text-muted">{entry.toolName}</span>
              {/if}
            </div>

            <!-- Content -->
            {#if entry.type === 'thinking'}
              <div class="text-xs text-text-dim italic mt-0.5 opacity-70 leading-relaxed">
                {entry.content}
              </div>
            {:else if entry.type === 'tool_use'}
              <div class="text-xs text-text-dim font-data mt-0.5 break-all leading-relaxed">
                {entry.content}
              </div>
            {:else if entry.type === 'guidance'}
              <div class="text-xs text-text bg-info/10 rounded px-2 py-1 mt-0.5">
                {entry.content}
              </div>
            {:else if entry.type === 'phase'}
              <div class="text-xs text-text-dim mt-0.5 font-medium">
                {entry.content}
              </div>
            {:else if entry.isError}
              <div class="text-xs text-reject mt-0.5 font-data break-all leading-relaxed">
                {entry.content}
              </div>
            {:else}
              <div class="text-xs text-text-dim mt-0.5 leading-relaxed">
                {entry.content}
              </div>
            {/if}
          </div>
        </div>
      {/each}

      <!-- Thinking indicator -->
      {#if agentPresence.agents.some(a => a.status === 'thinking')}
        {@const thinkingAgent = agentPresence.agents.find(a => a.status === 'thinking')}
        {#if thinkingAgent}
          <div class="flex items-center gap-2 py-1">
            <span class="text-[11px] font-semibold" style="color: {thinkingAgent.color}">{thinkingAgent.name}</span>
            <AgentThinking color={thinkingAgent.color} />
          </div>
        {/if}
      {/if}
    {/if}
  </div>

  <!-- Guidance input -->
  <GuidanceInput />
</aside>
