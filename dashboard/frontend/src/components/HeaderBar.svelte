<script lang="ts">
  import StatusOrb from './StatusOrb.svelte';
  import ArcGauge from './ArcGauge.svelte';
  import AgentAvatar from './AgentAvatar.svelte';
  import { agentPresence } from '../lib/agent-presence.svelte';

  interface Props {
    serviceActive: boolean;
    authOk: boolean;
    usagePercent: number;
    sessionsUsed: number;
    sessionLimit: number;
    onTrigger: () => void;
    triggering: boolean;
    onPanelToggle?: () => void;
  }

  let {
    serviceActive,
    authOk,
    usagePercent,
    sessionsUsed,
    sessionLimit,
    onTrigger,
    triggering,
    onPanelToggle,
  }: Props = $props();

  let usageColor = $derived(
    usagePercent > 80 ? '#ef4444' :
    usagePercent > 60 ? '#f59e0b' : '#6366f1'
  );

  let phaseLabel = $derived(
    agentPresence.phase === 'idle' ? null :
    agentPresence.phase === 'employee' ? 'Working' :
    agentPresence.phase === 'manager_review' ? 'Reviewing' :
    agentPresence.phase === 'executing_verdict' ? 'Verdict' :
    agentPresence.phase === 'coordinating' ? 'Coordinating' : null
  );

  let phaseColor = $derived(
    agentPresence.phase === 'employee' ? 'text-info' :
    agentPresence.phase === 'manager_review' ? 'text-warning' :
    agentPresence.phase === 'executing_verdict' ? 'text-approve' :
    agentPresence.phase === 'coordinating' ? 'text-accent-purple' : ''
  );
</script>

<header class="h-12 w-full flex items-center justify-between px-3 md:px-4 bg-surface border-b border-border shrink-0">
  <!-- Left: phase + agent presence -->
  <div class="flex items-center gap-3">
    <!-- Phase indicator -->
    {#if phaseLabel}
      <div class="flex items-center gap-1.5">
        <div class="w-2 h-2 rounded-full animate-pulse {agentPresence.phase === 'employee' ? 'bg-info' : agentPresence.phase === 'manager_review' ? 'bg-warning' : agentPresence.phase === 'executing_verdict' ? 'bg-approve' : 'bg-accent-purple'}"></div>
        <span class="text-xs font-medium {phaseColor}">{phaseLabel}</span>
      </div>
    {:else}
      <span class="text-xs text-text-muted">Idle</span>
    {/if}

    <!-- Agent presence dots (desktop) -->
    <div class="hidden md:flex items-center gap-1">
      {#each agentPresence.agents.slice(0, 4) as agent}
        <button
          onclick={() => onPanelToggle?.()}
          class="cursor-pointer opacity-80 hover:opacity-100 transition-opacity"
          title="{agent.name} — {agent.status}"
        >
          <AgentAvatar name={agent.name} role={agent.role} color={agent.color} status={agent.status} size="sm" />
        </button>
      {/each}
    </div>
  </div>

  <!-- Right: status + trigger + panel toggle -->
  <div class="flex items-center gap-2 md:gap-3">
    <!-- Service status -->
    <div class="flex items-center gap-1">
      <StatusOrb active={serviceActive} />
      <span class="text-[10px] text-text-dim hidden sm:inline">
        {serviceActive ? 'Active' : 'Down'}
      </span>
    </div>

    <!-- Auth status -->
    <div class="flex items-center gap-1 hidden sm:flex">
      <StatusOrb active={authOk} />
      <span class="text-[10px] text-text-dim hidden md:inline">
        {authOk ? 'Auth' : 'Auth Err'}
      </span>
    </div>

    <!-- Usage (desktop) -->
    <div class="hidden md:flex items-center gap-1">
      <span class="text-[10px] text-text-dim font-data hidden lg:inline">{sessionsUsed}/{sessionLimit}</span>
      <ArcGauge value={usagePercent} size={28} color={usageColor} />
    </div>

    <!-- Trigger Run -->
    <button
      onclick={onTrigger}
      disabled={triggering}
      class="px-2.5 py-1 text-xs font-medium rounded-md text-white transition-all cursor-pointer
        bg-accent-blue hover:opacity-90
        {triggering ? 'opacity-50 cursor-not-allowed' : 'active:scale-95'}"
    >
      {triggering ? '...' : 'Run'}
    </button>

    <!-- Panel toggle -->
    <button
      onclick={() => onPanelToggle?.()}
      class="p-1.5 rounded-md text-text-dim hover:text-text hover:bg-white/5 cursor-pointer transition-colors
        {agentPresence.panelOpen ? 'bg-white/5 text-text' : ''}"
      title="Agent Panel (Cmd+K)"
    >
      <svg class="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <rect x="1" y="2" width="14" height="12" rx="1.5" />
        <line x1="10" y1="2" x2="10" y2="14" />
      </svg>
    </button>
  </div>
</header>
