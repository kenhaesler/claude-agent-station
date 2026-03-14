<script lang="ts">
  import { agentPresence, getAgentName, getAgentColor } from '../lib/agent-presence.svelte';
  import { navigate } from '../lib/router.svelte';
  import Badge from './Badge.svelte';

  let activeRuns = $derived(
    agentPresence.activeRuns.filter(r => r.status === 'running' || r.status === 'reviewing')
  );
</script>

{#if activeRuns.length > 0}
  <div class="flex gap-2 overflow-x-auto pb-2 scrollbar-none">
    {#each activeRuns as run, i}
      {@const name = getAgentName(run.employee_index ?? i, run.mode)}
      {@const color = getAgentColor(name)}
      <button
        onclick={() => navigate(`/stream/${run.run_id}`)}
        class="shrink-0 flex items-center gap-2 px-3 py-2 rounded-lg glass border border-border/50 cursor-pointer hover:bg-white/[0.04] transition-colors"
      >
        <div class="w-2 h-2 rounded-full animate-pulse" style="background: {color}"></div>
        <span class="text-xs font-semibold" style="color: {color}">{name}</span>
        {#if run.issue_number}
          <span class="text-[10px] text-text-muted font-data">#{run.issue_number}</span>
        {/if}
        <Badge
          label={run.status === 'reviewing' ? 'Review' : 'Working'}
          variant={run.status === 'reviewing' ? 'warning' : 'info'}
          size="sm"
        />
        {#if run.turns}
          <span class="text-[10px] text-text-muted font-data">{run.turns}t</span>
        {/if}
      </button>
    {/each}
  </div>
{/if}
