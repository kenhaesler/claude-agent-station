<script lang="ts">
  import type { Run, SystemStatus } from '../lib/types';
  import { getLatestRun, getSystemStatus, listRuns, triggerRun } from '../lib/api';
  import { formatDuration, formatCost, timeAgo } from '../lib/format';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import StatusBadge from '../components/StatusBadge.svelte';
  import CostBar from '../components/CostBar.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';
  import TimeAgo from '../components/TimeAgo.svelte';

  let latest = $state<Run | null>(null);
  let recentRuns = $state<Run[]>([]);
  let system = $state<SystemStatus | null>(null);
  let loading = $state(true);
  let triggering = $state(false);

  async function load() {
    try {
      const [latestRes, runsRes, sysRes] = await Promise.allSettled([
        getLatestRun(),
        listRuns({ limit: 10 }),
        getSystemStatus(),
      ]);
      if (latestRes.status === 'fulfilled') latest = latestRes.value;
      if (runsRes.status === 'fulfilled') recentRuns = runsRes.value.runs;
      if (sysRes.status === 'fulfilled') system = sysRes.value;
    } finally {
      loading = false;
    }
  }

  async function handleTrigger() {
    triggering = true;
    try {
      await triggerRun();
      toastSuccess('Run triggered');
    } catch (e: any) {
      toastError(`Failed to trigger: ${e.message}`);
    } finally {
      triggering = false;
    }
  }

  $effect(() => {
    load();
    const interval = setInterval(load, 30000);
    return () => clearInterval(interval);
  });

  let maxCost = $derived(Math.max(...recentRuns.map(r => r.cost_usd ?? 0), 0.01));
</script>

<div class="space-y-6">
  <div class="flex items-center justify-between">
    <h1 class="text-2xl font-bold">Dashboard</h1>
    <button
      onclick={handleTrigger}
      disabled={triggering}
      class="px-4 py-2 bg-pr text-white rounded-lg text-sm font-medium hover:bg-pr/80 disabled:opacity-50 cursor-pointer"
    >
      {triggering ? 'Triggering...' : 'Trigger Run'}
    </button>
  </div>

  {#if loading}
    <div class="flex justify-center py-12"><LoadingSpinner /></div>
  {:else}
    <!-- Status Cards -->
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
      <!-- Service Status -->
      <div class="bg-surface rounded-xl p-5 border border-border">
        <h3 class="text-xs font-medium text-text-dim uppercase tracking-wide mb-3">Service</h3>
        <div class="flex items-center gap-2">
          <span class="w-2.5 h-2.5 rounded-full {system?.service.active ? 'bg-approve' : 'bg-reject'}"></span>
          <span class="text-lg font-medium">{system?.service.active ? 'Active' : 'Inactive'}</span>
        </div>
        {#if system?.timer.next_trigger}
          <p class="text-xs text-text-dim mt-2">Next: {system.timer.next_trigger}</p>
        {/if}
      </div>

      <!-- Latest Run -->
      <div class="bg-surface rounded-xl p-5 border border-border">
        <h3 class="text-xs font-medium text-text-dim uppercase tracking-wide mb-3">Latest Run</h3>
        {#if latest}
          <div class="flex items-center gap-2">
            <StatusBadge value={latest.verdict} />
            <span class="text-sm text-text-dim">{formatDuration(latest.duration_ms)}</span>
          </div>
          <p class="text-xs text-text-dim mt-2"><TimeAgo date={latest.started_at} /></p>
        {:else}
          <p class="text-text-dim">No runs yet</p>
        {/if}
      </div>

      <!-- Cost Summary -->
      <div class="bg-surface rounded-xl p-5 border border-border">
        <h3 class="text-xs font-medium text-text-dim uppercase tracking-wide mb-3">Total Cost (Recent)</h3>
        <p class="text-lg font-medium">
          {formatCost(recentRuns.reduce((sum, r) => sum + (r.cost_usd ?? 0), 0))}
        </p>
        <p class="text-xs text-text-dim mt-2">{recentRuns.length} recent runs</p>
      </div>
    </div>

    <!-- Recent Runs -->
    <div class="bg-surface rounded-xl border border-border">
      <div class="px-5 py-4 border-b border-border flex items-center justify-between">
        <h2 class="font-semibold">Recent Runs</h2>
        <a href="#/runs" class="text-sm text-pr hover:underline">View all</a>
      </div>
      {#if recentRuns.length === 0}
        <p class="p-5 text-text-dim">No runs yet</p>
      {:else}
        <div class="divide-y divide-border">
          {#each recentRuns as run}
            <a href="#/runs/{run.run_id}" class="flex items-center gap-4 px-5 py-3 hover:bg-surface-2/30 transition-colors no-underline text-text">
              <StatusBadge value={run.verdict} />
              <span class="text-sm flex-1 truncate font-mono">{run.run_id}</span>
              <span class="text-xs text-text-dim hidden sm:block">{run.mode}</span>
              <span class="text-xs text-text-dim">{formatDuration(run.duration_ms)}</span>
              <span class="text-xs text-text-dim w-16 text-right">{formatCost(run.cost_usd)}</span>
              <TimeAgo date={run.started_at} />
            </a>
          {/each}
        </div>
      {/if}
    </div>

    <!-- Cost Chart -->
    {#if recentRuns.length > 0}
      <div class="bg-surface rounded-xl border border-border p-5">
        <h2 class="font-semibold mb-4">Cost per Run</h2>
        <div class="space-y-2">
          {#each recentRuns as run}
            <CostBar cost={run.cost_usd} {maxCost} label={run.run_id.slice(-12)} />
          {/each}
        </div>
      </div>
    {/if}
  {/if}
</div>
