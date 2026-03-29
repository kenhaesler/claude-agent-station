<script lang="ts">
  import AgentCard from './AgentCard.svelte';
  import type { AgentIdentity } from '../../lib/agent-presence.svelte';
  import { agentPresence } from '../../lib/agent-presence.svelte';

  let {
    agents = [],
    onAgentClick,
  }: {
    agents?: AgentIdentity[];
    onAgentClick?: (name: string) => void;
  } = $props();

  let displayAgents = $derived(agents.length > 0 ? agents : agentPresence.agents);
</script>

<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
  {#each displayAgents as agent (agent.name)}
    <AgentCard
      name={agent.name}
      role={agent.role}
      color={agent.color}
      status={agent.status}
      currentTool={agent.currentAction ? { name: '', summary: agent.currentAction } : null}
      onclick={onAgentClick ? () => onAgentClick(agent.name) : undefined}
    />
  {/each}

  {#if displayAgents.length === 0}
    <div class="col-span-full text-center py-8 text-sm text-tertiary">
      No active agents
    </div>
  {/if}
</div>
