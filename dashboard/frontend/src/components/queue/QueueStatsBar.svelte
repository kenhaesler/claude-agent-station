<script lang="ts">
  import type { QueueStats, BackpressureStatus } from '../../lib/types';
  import { formatDuration } from '../../lib/chart-utils';
  import BackpressureGauge from './BackpressureGauge.svelte';

  let {
    stats = null,
    backpressure = null,
  }: {
    stats: QueueStats | null;
    backpressure: BackpressureStatus | null;
  } = $props();

  const stateColors: Record<string, string> = {
    pending: 'var(--color-queue-pending)',
    assigned: 'var(--color-queue-assigned)',
    claimed: 'var(--color-queue-assigned)',
    planning: 'var(--color-queue-active)',
    in_progress: 'var(--color-queue-active)',
    review: 'var(--color-queue-review)',
    verifying: 'var(--color-queue-review)',
    approved: 'var(--color-queue-completed)',
    completed: 'var(--color-queue-completed)',
    rejected: 'var(--color-queue-failed)',
    escalated: 'var(--color-warning)',
    failed: 'var(--color-queue-failed)',
    paused: 'var(--color-text-muted)',
    cancelled: 'var(--color-text-muted)',
  };
</script>

<div class="flex items-center gap-4 px-4 py-2 glass rounded-lg text-xs">
  <!-- Total -->
  <div class="flex items-center gap-1.5">
    <span class="text-text-muted">Total:</span>
    <span class="text-text font-medium data-readout">{stats?.total ?? 0}</span>
  </div>

  <!-- State distribution -->
  {#if stats?.by_state}
    <div class="flex items-center gap-2 flex-wrap">
      {#each Object.entries(stats.by_state) as [state, count]}
        {#if count > 0}
          <div class="flex items-center gap-1">
            <span class="w-1.5 h-1.5 rounded-full" style="background: {stateColors[state] ?? 'var(--color-text-muted)'}"></span>
            <span class="text-text-muted">{state}:</span>
            <span class="text-text-dim data-readout">{count}</span>
          </div>
        {/if}
      {/each}
    </div>
  {/if}

  <div class="flex-1"></div>

  <!-- Avg completion time -->
  {#if stats?.avg_time_to_complete_ms}
    <div class="flex items-center gap-1">
      <span class="text-text-muted">Avg:</span>
      <span class="text-text-dim data-readout">{formatDuration(stats.avg_time_to_complete_ms)}</span>
    </div>
  {/if}

  <!-- Backpressure gauge -->
  {#if backpressure}
    <BackpressureGauge level={backpressure.level} usagePercent={backpressure.usage_percent} compact />
  {/if}
</div>
