<script lang="ts">
  import { getAnalytics, listProjects } from '../lib/api';
  import type { AnalyticsData, Project } from '../lib/types';
  import GlassCard from '../components/GlassCard.svelte';
  import MetricPanel from '../components/MetricPanel.svelte';
  import DonutChart from '../components/DonutChart.svelte';
  import BarChart from '../components/BarChart.svelte';
  import LineChart from '../components/LineChart.svelte';
  import IntelligencePanel from '../components/IntelligencePanel.svelte';
  import { formatTokens } from '../lib/format';

  let analytics = $state<AnalyticsData | null>(null);
  let projects = $state<Project[]>([]);
  let loading = $state(true);
  let days = $state(14);

  const timeRanges = [
    { value: 7, label: '7d' },
    { value: 14, label: '14d' },
    { value: 30, label: '30d' },
    { value: 90, label: '90d' },
  ];

  async function loadData() {
    loading = true;
    try {
      const [analyticsRes, projRes] = await Promise.allSettled([
        getAnalytics({ days }),
        listProjects(),
      ]);
      if (analyticsRes.status === 'fulfilled') analytics = analyticsRes.value;
      if (projRes.status === 'fulfilled') projects = projRes.value;
    } catch { /* silent */ }
    loading = false;
  }

  $effect(() => {
    days; // re-run on range change
    loadData();
  });

  let approvalRate = $derived(() => {
    if (!analytics?.verdict_distribution) return 0;
    const approved = analytics.verdict_distribution.find(v => v.verdict === 'APPROVE')?.count ?? 0;
    const total = analytics.verdict_distribution.reduce((sum, v) => sum + v.count, 0);
    return total > 0 ? Math.round((approved / total) * 100) : 0;
  });

  // Chart data transforms
  let verdictData = $derived(
    analytics?.verdict_distribution?.map(v => ({
      label: v.verdict,
      value: v.count,
      color: v.verdict === 'APPROVE' ? '#22c55e' : v.verdict === 'REJECT' ? '#ef4444' : v.verdict === 'PR' ? '#a855f7' : '#f59e0b',
    })) ?? []
  );

  let dailyRunData = $derived(
    analytics?.daily_run_counts?.map(d => ({
      label: d.date.slice(5), // MM-DD
      value: d.total,
      success: d.success,
      failed: d.failed,
    })) ?? []
  );

  let tokenData = $derived(
    analytics?.daily_token_usage?.map(d => ({
      label: d.date.slice(5),
      value: d.tokens_total,
    })) ?? []
  );

  let projectData = $derived(
    analytics?.project_token_usage?.map(p => ({
      label: p.project_repo.split('/').pop() ?? p.project_repo,
      value: p.tokens_total,
      runs: p.run_count,
    })) ?? []
  );

  let avgDuration = $derived(() => {
    if (!analytics || analytics.total_runs === 0) return 0;
    // Approximate from token data
    return analytics.total_tokens / analytics.total_runs;
  });
</script>

<div class="space-y-4 animate-fade-in-up">
  <div class="flex items-center justify-between">
    <h1 class="text-lg font-semibold text-text">Analytics</h1>
    <!-- Time range selector -->
    <div class="flex items-center gap-1 glass rounded-lg p-0.5">
      {#each timeRanges as range}
        <button
          onclick={() => days = range.value}
          class="px-3 py-1 text-xs font-medium rounded-md transition-all cursor-pointer
            {days === range.value ? 'bg-info/15 text-info' : 'text-text-dim hover:text-text hover:bg-white/[0.03]'}"
        >
          {range.label}
        </button>
      {/each}
    </div>
  </div>

  {#if loading && !analytics}
    <div class="text-center py-12 text-text-muted">Loading analytics...</div>
  {:else if analytics}
    <!-- Summary metrics -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
      <MetricPanel
        label="Total Runs"
        value={analytics.total_runs}
        glow="blue"
        subtitle="{days} days"
      />
      <MetricPanel
        label="Tokens Used"
        value={analytics.total_tokens}
        format={formatTokens}
        glow="purple"
        subtitle="{formatTokens(analytics.total_tokens_input)} in / {formatTokens(analytics.total_tokens_output)} out"
      />
      <MetricPanel
        label="Approval Rate"
        value={approvalRate()}
        format={(n) => `${n}%`}
        glow="emerald"
      />
      <MetricPanel
        label="Failed Runs"
        value={analytics.failed_runs}
        glow={analytics.failed_runs > 0 ? 'red' : 'none'}
      />
    </div>

    <!-- Charts row -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
      <!-- Verdict distribution -->
      <GlassCard class="p-4">
        <h3 class="text-sm font-semibold text-text mb-3">Verdict Distribution</h3>
        {#if verdictData.length > 0}
          <div class="h-[200px] flex items-center justify-center">
            <DonutChart segments={verdictData} centerLabel="Verdicts" centerValue={String(analytics?.total_runs ?? 0)} />
          </div>
        {:else}
          <div class="h-[200px] flex items-center justify-center text-xs text-text-muted">No data</div>
        {/if}
      </GlassCard>

      <!-- Daily runs -->
      <GlassCard class="p-4">
        <h3 class="text-sm font-semibold text-text mb-3">Daily Runs</h3>
        {#if dailyRunData.length > 0}
          <div class="h-[200px]">
            <BarChart data={dailyRunData} />
          </div>
        {:else}
          <div class="h-[200px] flex items-center justify-center text-xs text-text-muted">No data</div>
        {/if}
      </GlassCard>
    </div>

    <!-- Token usage over time -->
    <GlassCard class="p-4">
      <h3 class="text-sm font-semibold text-text mb-3">Token Usage Over Time</h3>
      {#if tokenData.length > 0}
        <div class="h-[200px]">
          <LineChart data={tokenData} />
        </div>
      {:else}
        <div class="h-[200px] flex items-center justify-center text-xs text-text-muted">No data</div>
      {/if}
    </GlassCard>

    <!-- Per-project breakdown -->
    {#if projectData.length > 0}
      <GlassCard class="p-4">
        <h3 class="text-sm font-semibold text-text mb-3">Per-Project Token Usage</h3>
        <div class="space-y-2">
          {#each projectData as proj}
            {@const maxTokens = Math.max(1, ...projectData.map(p => p.value))}
            <div class="flex items-center gap-3">
              <span class="text-xs text-text-dim w-24 truncate">{proj.label}</span>
              <div class="flex-1 bg-surface-2 rounded-full h-2 overflow-hidden">
                <div
                  class="h-full bg-info/60 rounded-full"
                  style="width: {(proj.value / maxTokens) * 100}%"
                ></div>
              </div>
              <span class="text-[10px] text-text-muted font-data w-16 text-right">{formatTokens(proj.value)}</span>
              <span class="text-[10px] text-text-muted font-data w-12 text-right">{proj.runs} runs</span>
            </div>
          {/each}
        </div>
      </GlassCard>
    {/if}

    <!-- Intelligence deep dive -->
    <IntelligencePanel />
  {/if}
</div>
