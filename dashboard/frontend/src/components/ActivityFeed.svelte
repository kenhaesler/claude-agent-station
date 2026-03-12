<script lang="ts">
  import type { Run } from '../lib/types';
  import { formatDuration, formatTokens } from '../lib/format';
  import StatusBadge from './StatusBadge.svelte';
  import TimeAgo from './TimeAgo.svelte';
  import StatusOrb from './StatusOrb.svelte';

  interface Props {
    runs: Run[];
    projectMap: Record<number, string>;
  }

  let { runs, projectMap }: Props = $props();
</script>

<div class="glass rounded-xl overflow-hidden">
  <div class="px-4 py-3 border-b border-border/50 flex items-center justify-between">
    <h3 class="text-xs font-medium text-text-dim uppercase tracking-wide">Activity Feed</h3>
    <a href="#/runs" class="text-xs text-accent-blue hover:text-accent-blue/80 transition-colors">View all</a>
  </div>

  {#if runs.length === 0}
    <p class="px-4 py-6 text-sm text-text-dim text-center">No activity yet</p>
  {:else}
    <div class="divide-y divide-border/30 max-h-[340px] overflow-y-auto">
      {#each runs as run, i}
        <a
          href="#/runs/{run.run_id}"
          class="flex items-center gap-2 md:gap-3 px-4 py-2.5 hover:bg-white/[0.02] transition-colors no-underline text-text animate-fade-in-up"
          style="animation-delay: {i * 30}ms"
        >
          <StatusOrb active={run.status === 'running' || run.status === 'reviewing'} color={
            run.verdict === 'APPROVE' ? '#22c55e' :
            run.verdict === 'REJECT' ? '#ef4444' :
            run.verdict === 'PR' ? '#a855f7' :
            run.status === 'reviewing' ? '#f59e0b' :
            run.status === 'running' ? '#3b82f6' :
            '#64748b'
          } />
          <span class="text-xs truncate max-w-24 md:max-w-40 text-text-dim">
            {run.project_id ? (projectMap[run.project_id] ?? `#${run.project_id}`).split('/').pop() : '-'}
          </span>
          <StatusBadge value={run.verdict} />
          <span class="text-xs text-text-dim ml-auto hidden sm:block">{formatDuration(run.duration_ms)}</span>
          <span class="text-xs text-text-dim w-14 text-right font-data">{formatTokens(run.tokens_total)}</span>
          <span class="text-[10px] text-text-dim/60 hidden md:block w-16 text-right"><TimeAgo date={run.started_at} /></span>
        </a>
      {/each}
    </div>
  {/if}
</div>
