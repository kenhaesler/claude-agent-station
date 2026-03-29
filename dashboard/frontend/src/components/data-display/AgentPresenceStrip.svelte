<script lang="ts">
  import type { AgentIdentity } from '../../lib/agent-presence.svelte';
  import type { ActiveEmployee } from '../../lib/types';

  let {
    agents,
    activeRuns,
    onAgentClick,
  }: {
    agents: AgentIdentity[];
    activeRuns: ActiveEmployee[];
    onAgentClick?: (name: string) => void;
  } = $props();

  function getInitials(name: string): string {
    return name.split(/\s+/).map(w => w[0]).join('').toUpperCase().slice(0, 2);
  }

  function getStatusDotClass(status: AgentIdentity['status']): string {
    switch (status) {
      case 'active': return 'status-dot running';
      case 'thinking': return 'status-dot running';
      case 'error': return 'status-dot error';
      default: return 'status-dot offline';
    }
  }

  function getRunForAgent(agent: AgentIdentity): ActiveEmployee | undefined {
    if (agent.employeeIndex != null) {
      return activeRuns.find(r => r.employee_index === agent.employeeIndex);
    }
    if (agent.role === 'manager') {
      return activeRuns.find(r => r.mode === 'manager');
    }
    return undefined;
  }

  function getModeBadgeClass(mode: string | null): string {
    if (!mode) return 'badge';
    const map: Record<string, string> = {
      full: 'badge badge-full',
      analyze: 'badge badge-analyze',
      plan: 'badge badge-plan',
      triage: 'badge badge-triage',
      review: 'badge badge-review',
      fix: 'badge badge-fix',
    };
    return map[mode] ?? 'badge badge-running';
  }
</script>

<div class="flex gap-3 overflow-x-auto no-scrollbar py-1">
  {#if agents.length === 0}
    <div class="flex items-center justify-center w-full py-4 text-sm text-tertiary font-mono">
      All quiet
    </div>
  {:else}
    {#each agents as agent}
      {@const run = getRunForAgent(agent)}
      <button
        class="card card-interactive w-56 shrink-0 p-3 flex flex-col gap-2 text-left transition-all duration-200"
        style={agent.status === 'active' ? `box-shadow: 0 0 20px ${agent.color}26, 0 0 60px ${agent.color}0d;` : ''}
        onclick={() => onAgentClick?.(agent.name)}
      >
        <!-- Top row: avatar + info -->
        <div class="flex items-center gap-3">
          <!-- Avatar with status dot -->
          <div class="relative shrink-0">
            <div
              class="w-10 h-10 rounded-full flex items-center justify-center text-sm font-semibold"
              style="border: 3px solid {agent.color}; background: {agent.color}15; color: {agent.color};"
            >
              {getInitials(agent.name)}
            </div>
            <div class="absolute -top-0.5 -right-0.5">
              <div class={getStatusDotClass(agent.status)}></div>
            </div>
          </div>

          <!-- Name + action -->
          <div class="min-w-0 flex-1">
            <div class="text-sm font-medium text-primary truncate">{agent.name}</div>
            {#if agent.currentAction}
              <div class="text-xs text-secondary truncate font-mono mt-0.5">{agent.currentAction}</div>
            {:else}
              <div class="text-xs text-tertiary truncate mt-0.5">
                {agent.status === 'active' ? 'Working...' : agent.status === 'thinking' ? 'Thinking...' : 'Idle'}
              </div>
            {/if}
          </div>
        </div>

        <!-- Bottom row: mode badge + turns -->
        <div class="flex items-center gap-2">
          {#if run?.mode}
            <span class={getModeBadgeClass(run.mode)}>{run.mode}</span>
          {/if}
          {#if run?.turns != null}
            <span class="text-[10px] font-mono text-tertiary">{run.turns} turns</span>
          {/if}
        </div>
      </button>
    {/each}
  {/if}
</div>
