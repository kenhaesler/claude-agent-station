<script lang="ts">
  import { agentPresence, togglePanel } from '../lib/agent-presence.svelte';
  import { navigate } from '../lib/router.svelte';
  import AgentCard from '../components/AgentCard.svelte';
  import Timeline from '../components/Timeline.svelte';
  import GlassCard from '../components/GlassCard.svelte';

  let hasAgents = $derived(agentPresence.agents.length > 0);
</script>

<div class="space-y-4 animate-fade-in-up">
  <div class="flex items-center justify-between">
    <h1 class="text-lg font-semibold text-text">Agent Observatory</h1>
    <span class="text-xs text-text-muted font-data">{agentPresence.agents.length} agents</span>
  </div>

  <!-- Agent grid -->
  {#if hasAgents}
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
      {#each agentPresence.agents as agent (agent.name)}
        <AgentCard
          {agent}
          onclick={() => togglePanel(agent.name)}
        />
      {/each}
    </div>
  {:else}
    <GlassCard class="p-8 text-center">
      <p class="text-sm text-text-muted">No active agents. The Cortex backdrop shows the agent network when agents are running.</p>
      <p class="text-xs text-text-muted mt-2">Press <kbd class="px-1.5 py-0.5 rounded border border-border-subtle text-[10px]">Space</kbd> to focus the Cortex visualization.</p>
    </GlassCard>
  {/if}

  <!-- Unified Activity Feed -->
  <GlassCard class="p-4">
    <h2 class="text-sm font-semibold text-text mb-3">Activity Feed</h2>
    <div class="max-h-[400px] overflow-auto">
      <Timeline maxItems={40} />
    </div>
  </GlassCard>
</div>
