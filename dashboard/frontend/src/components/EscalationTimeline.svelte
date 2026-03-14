<script lang="ts">
  import type { EscalationStat } from '../lib/types';
  import GlassCard from './GlassCard.svelte';

  interface Props {
    data: EscalationStat[];
  }

  let { data }: Props = $props();

  const rungLabels: Record<number, string> = {
    0: 'Fix (Sonnet)',
    1: 'Full (Sonnet+)',
    2: 'Full (Opus)',
    3: 'Full (Opus+)',
  };
</script>

<GlassCard class="p-3">
  <h4 class="text-xs font-medium text-text mb-2">Escalation Ladder</h4>
  <div class="flex items-end gap-1">
    {#each data as stat, idx}
      <div class="flex-1 flex flex-col items-center gap-1">
        <!-- Bar -->
        <div class="w-full flex flex-col items-center">
          <span class="text-[10px] font-data {stat.success_rate >= 0.7 ? 'text-approve' : stat.success_rate >= 0.4 ? 'text-warning' : 'text-reject'}">
            {(stat.success_rate * 100).toFixed(0)}%
          </span>
          <div
            class="w-full rounded-t transition-all {
              stat.success_rate >= 0.7 ? 'bg-approve/30' : stat.success_rate >= 0.4 ? 'bg-warning/30' : 'bg-reject/30'
            }"
            style="height: {Math.max(stat.success_rate * 60, 4)}px"
          ></div>
        </div>
        <!-- Connector line -->
        {#if idx < data.length - 1}
          <div class="absolute"></div>
        {/if}
        <!-- Rung label -->
        <div class="text-center">
          <div class="text-[10px] font-data text-text-dim">{stat.total}</div>
          <div class="text-[9px] text-text-muted truncate max-w-[60px]">{rungLabels[stat.rung] ?? `Rung ${stat.rung}`}</div>
        </div>
      </div>
    {/each}
  </div>
</GlassCard>
