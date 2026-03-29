<script lang="ts">
  import { route, getPageTitle, navigate } from '../../lib/router.svelte';
  import { agentPresence, getAgentColor, getAgentName } from '../../lib/agent-presence.svelte';
  import Breadcrumbs from './Breadcrumbs.svelte';
  import Icon from '../ui/Icon.svelte';

  let {
    onTrigger,
    triggering = false,
    sseConnected = false,
    activeCount = 0,
  }: {
    onTrigger?: () => void;
    triggering?: boolean;
    sseConnected?: boolean;
    activeCount?: number;
  } = $props();

  function getInitials(name: string): string {
    return name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
  }
</script>

<header class="flex items-center gap-3 px-5 h-12 shrink-0 z-40"
  style="background: rgba(10, 10, 18, 0.5); backdrop-filter: blur(20px) saturate(1.3); -webkit-backdrop-filter: blur(20px) saturate(1.3); border-bottom: 1px solid rgba(255,255,255,0.04); box-shadow: inset 0 -1px 0 rgba(255,255,255,0.02);">
  <!-- Breadcrumbs -->
  <Breadcrumbs />

  <div class="flex-1"></div>

  <!-- Status & Actions -->
  <div class="flex items-center gap-3">
    <!-- Team Presence Avatars -->
    {#if agentPresence.agents.length > 0}
      <div class="flex items-center gap-1">
        <div class="flex -space-x-1.5">
          {#each agentPresence.agents.slice(0, 5) as agent}
            <button
              class="relative w-6 h-6 rounded-full border-2 border-surface-0 flex items-center justify-center
                     text-[8px] font-mono font-bold text-white cursor-pointer
                     transition-transform duration-150 hover:scale-125 hover:z-10"
              style="background: {agent.color}"
              title="{agent.name}{agent.currentAction ? ` — ${agent.currentAction}` : ''}"
              onclick={() => navigate('/agents')}
            >
              {getInitials(agent.name)}
              {#if agent.status === 'active'}
                <span class="absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full bg-emerald border border-surface-0 animate-pulse"></span>
              {:else if agent.status === 'thinking'}
                <span class="absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full bg-violet border border-surface-0 animate-pulse"></span>
              {/if}
            </button>
          {/each}
        </div>
        {#if agentPresence.agents.length > 5}
          <span class="text-[10px] font-mono text-tertiary ml-1">+{agentPresence.agents.length - 5}</span>
        {/if}
        <span class="text-[10px] font-mono text-violet ml-1 font-medium">{agentPresence.agents.length} active</span>
      </div>
    {/if}

    <!-- SSE connection -->
    <div class="flex items-center gap-1.5" title="Event stream: {sseConnected ? 'connected' : 'disconnected'}">
      <span class="status-dot {sseConnected ? 'online' : 'offline'}"></span>
      <span class="text-[11px] text-tertiary font-mono">SSE</span>
    </div>

    <!-- Trigger run -->
    {#if onTrigger}
      <button
        onclick={onTrigger}
        disabled={triggering}
        class="btn btn-primary btn-sm"
      >
        {#if triggering}
          <span class="animate-spin-slow inline-block"><Icon name="spinner" size={14} /></span>
          <span>Triggering...</span>
        {:else}
          <Icon name="play" size={14} />
          <span>Run</span>
        {/if}
      </button>
    {/if}
  </div>
</header>
