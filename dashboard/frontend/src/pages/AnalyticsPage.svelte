<script lang="ts">
  import { getAnalytics } from '../lib/api';
  import type { AnalyticsData } from '../lib/types';
  import GlassCard from '../components/GlassCard.svelte';
  import BarChart from '../components/BarChart.svelte';
  import DonutChart from '../components/DonutChart.svelte';
  import LineChart from '../components/LineChart.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';

  type TimeWindow = '7' | '30' | '90';
  let selectedWindow: TimeWindow = $state('30');
  let loading = $state(true);
  let error = $state<string | null>(null);
  let data = $state<AnalyticsData | null>(null);

  const windowOptions: { value: TimeWindow; label: string }[] = [
    { value: '7', label: '7d' },
    { value: '30', label: '30d' },
    { value: '90', label: '90d' },
  ];

  async function loadAnalytics() {
    loading = true;
    error = null;
    try {
      data = await getAnalytics({ days: parseInt(selectedWindow) });
    } catch (e: any) {
      error = e.message || 'Failed to load analytics';
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    // Re-fetch whenever selectedWindow changes
    const _trigger = selectedWindow;
    loadAnalytics();
  });

  // Format large token numbers
  function formatTokens(v: number): string {
    if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
    if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`;
    return v.toLocaleString();
  }

  // Format date labels (MM/DD)
  function formatDate(dateStr: string): string {
    const parts = dateStr.split('-');
    if (parts.length >= 3) return `${parts[1]}/${parts[2]}`;
    return dateStr;
  }

  // Daily token usage bar chart data
  let tokenBarData = $derived(
    (data?.daily_token_usage ?? []).map(d => ({
      label: formatDate(d.date),
      value: d.tokens_total,
    }))
  );

  // Verdict donut chart segments
  const verdictColors: Record<string, string> = {
    approved: '#10b981',
    rejected: '#ef4444',
    partial: '#f59e0b',
    none: '#64748b',
    skipped: '#8b5cf6',
    error: '#dc2626',
  };

  let verdictSegments = $derived(
    (data?.verdict_distribution ?? []).map(d => ({
      label: d.verdict === 'none' ? 'No Verdict' : d.verdict.charAt(0).toUpperCase() + d.verdict.slice(1),
      value: d.count,
      color: verdictColors[d.verdict] ?? '#94a3b8',
    }))
  );

  let successRate = $derived(() => {
    if (!data || data.total_runs === 0) return '0';
    const approved = data.verdict_distribution.find(v => v.verdict === 'approved')?.count ?? 0;
    return Math.round((approved / data.total_runs) * 100).toString();
  });

  // Project token usage (horizontal bar) data
  let projectBarData = $derived(
    (data?.project_token_usage ?? []).map(d => ({
      label: d.project_repo.split('/').pop() ?? d.project_repo,
      value: d.tokens_total,
    }))
  );

  // Run frequency line chart data
  let runFrequencyData = $derived(
    (data?.daily_run_counts ?? []).map(d => ({
      label: formatDate(d.date),
      value: d.total,
    }))
  );
</script>

<div class="space-y-6 animate-fade-in-up">
  <!-- Header with time window selector -->
  <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
    <div>
      <h1 class="text-xl font-bold ai-text text-glow-cyan">Analytics</h1>
      <p class="text-xs text-text-dim mt-1">Token usage, run statistics, and project metrics</p>
    </div>

    <div class="flex items-center gap-1 glass rounded-lg p-1">
      {#each windowOptions as opt}
        <button
          onclick={() => selectedWindow = opt.value}
          class="px-3 py-1.5 text-xs font-data rounded-md transition-all duration-200 cursor-pointer
            {selectedWindow === opt.value
              ? 'bg-accent-cyan/20 text-accent-cyan shadow-[0_0_8px_rgba(6,182,212,0.2)]'
              : 'text-text-dim hover:text-text hover:bg-white/[0.04]'}"
        >
          {opt.label}
        </button>
      {/each}
    </div>
  </div>

  {#if loading}
    <div class="flex items-center justify-center h-64">
      <LoadingSpinner />
    </div>
  {:else if error}
    <GlassCard glow="red" class="p-6">
      <div class="text-center">
        <p class="text-red-400 text-sm">{error}</p>
        <button
          onclick={loadAnalytics}
          class="mt-3 px-4 py-1.5 text-xs glass rounded-md text-text-dim hover:text-text cursor-pointer transition-colors"
        >
          Retry
        </button>
      </div>
    </GlassCard>
  {:else if data}
    <!-- Summary Stats -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
      <GlassCard class="p-4">
        <div class="text-[10px] text-text-dim uppercase tracking-wider mb-1">Total Runs</div>
        <div class="text-2xl font-bold font-data text-text">{data.total_runs.toLocaleString()}</div>
      </GlassCard>
      <GlassCard class="p-4">
        <div class="text-[10px] text-text-dim uppercase tracking-wider mb-1">Total Tokens</div>
        <div class="text-2xl font-bold font-data text-accent-cyan">{formatTokens(data.total_tokens)}</div>
      </GlassCard>
      <GlassCard class="p-4">
        <div class="text-[10px] text-text-dim uppercase tracking-wider mb-1">Input Tokens</div>
        <div class="text-2xl font-bold font-data text-emerald-400">{formatTokens(data.total_tokens_input)}</div>
      </GlassCard>
      <GlassCard class="p-4">
        <div class="text-[10px] text-text-dim uppercase tracking-wider mb-1">Failed Runs</div>
        <div class="text-2xl font-bold font-data {data.failed_runs > 0 ? 'text-red-400' : 'text-text'}">{data.failed_runs}</div>
      </GlassCard>
    </div>

    <!-- Charts Grid -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <!-- Daily Token Usage Bar Chart -->
      <GlassCard glow="cyan" class="p-4">
        <h2 class="text-sm font-semibold ai-text mb-3">Daily Token Usage</h2>
        {#if tokenBarData.length > 0}
          <BarChart
            data={tokenBarData}
            valueFormatter={formatTokens}
            barColor="#06b6d4"
            height={220}
          />
        {:else}
          <div class="flex items-center justify-center h-[220px] text-text-dim text-xs">
            No token data for this period
          </div>
        {/if}
      </GlassCard>

      <!-- Run Success Rate Donut Chart -->
      <GlassCard glow="cyan" class="p-4">
        <h2 class="text-sm font-semibold ai-text mb-3">Run Verdicts</h2>
        {#if verdictSegments.length > 0}
          <div class="flex justify-center">
            <DonutChart
              segments={verdictSegments}
              size={200}
              thickness={28}
              centerValue="{successRate()}%"
              centerLabel="approved"
            />
          </div>
        {:else}
          <div class="flex items-center justify-center h-[220px] text-text-dim text-xs">
            No run data for this period
          </div>
        {/if}
      </GlassCard>

      <!-- Tokens by Project Horizontal Bar Chart -->
      <GlassCard glow="cyan" class="p-4">
        <h2 class="text-sm font-semibold ai-text mb-3">Tokens by Project (Top 10)</h2>
        {#if projectBarData.length > 0}
          <BarChart
            data={projectBarData}
            valueFormatter={formatTokens}
            barColor="#8b5cf6"
            height={220}
          />
        {:else}
          <div class="flex items-center justify-center h-[220px] text-text-dim text-xs">
            No project data for this period
          </div>
        {/if}
      </GlassCard>

      <!-- Run Frequency Line Chart -->
      <GlassCard glow="cyan" class="p-4">
        <h2 class="text-sm font-semibold ai-text mb-3">Run Frequency</h2>
        {#if runFrequencyData.length > 0}
          <LineChart
            data={runFrequencyData}
            color="#10b981"
            height={220}
            valueFormatter={(v) => v.toString()}
          />
        {:else}
          <div class="flex items-center justify-center h-[220px] text-text-dim text-xs">
            No run data for this period
          </div>
        {/if}
      </GlassCard>
    </div>
  {/if}
</div>
