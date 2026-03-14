<script lang="ts">
  import type { AgentIdentity } from '../lib/agent-presence.svelte';
  import AgentAvatar from './AgentAvatar.svelte';
  import Badge from './Badge.svelte';
  import GlassCard from './GlassCard.svelte';

  interface Props {
    agent: AgentIdentity;
    onclick?: () => void;
  }

  let { agent, onclick }: Props = $props();

  let statusVariant = $derived<'success' | 'warning' | 'error' | 'muted'>(
    agent.status === 'active' ? 'success' :
    agent.status === 'thinking' ? 'warning' :
    agent.status === 'error' ? 'error' : 'muted'
  );
</script>

<GlassCard interactive {onclick} class="p-3">
  <div class="flex items-center gap-3">
    <AgentAvatar name={agent.name} role={agent.role} color={agent.color} status={agent.status} size="md" />
    <div class="flex-1 min-w-0">
      <div class="flex items-center gap-2">
        <span class="text-sm font-semibold" style="color: {agent.color}">{agent.name}</span>
        <Badge label={agent.role} variant={agent.role === 'manager' ? 'warning' : agent.role === 'coordinator' ? 'purple' : 'info'} />
      </div>
      <div class="flex items-center gap-2 mt-1">
        <Badge label={agent.status} variant={statusVariant} dot size="sm" />
        {#if agent.currentAction}
          <span class="text-[10px] text-text-muted truncate font-data">{agent.currentAction}</span>
        {/if}
      </div>
    </div>
  </div>
</GlassCard>
