<script lang="ts">
  import { getIntelligenceInsights, getAnalytics, getIntelligenceDecisions } from '../lib/api';
  import type { IntelligenceInsights, AnalyticsData } from '../lib/types';
  import HeatMap from '../components/charts/HeatMap.svelte';
  import ScatterPlot from '../components/charts/ScatterPlot.svelte';
  import DonutChart from '../components/charts/DonutChart.svelte';
  import BarChart from '../components/charts/BarChart.svelte';

  let insights = $state<IntelligenceInsights | null>(null);
  let analytics = $state<AnalyticsData | null>(null);
  let activeTab = $state<'overview' | 'success' | 'calibration' | 'efficiency' | 'escalation'>('overview');

  $effect(() => {
    loadData();
  });

  async function loadData() {
    const [iRes, aRes] = await Promise.allSettled([
      getIntelligenceInsights(),
      getAnalytics({ days: 30 }),
    ]);
    if (iRes.status === 'fulfilled') insights = iRes.value;
    if (aRes.status === 'fulfilled') analytics = aRes.value;
  }

  // Success rate heat map data
  let heatRows = $derived(
    [...new Set(insights?.success_rates.map(r => r.mode) ?? [])]
  );
  let heatCols = $derived(
    [...new Set(insights?.success_rates.map(r => r.model) ?? [])]
  );
  let heatData = $derived.by(() => {
    if (!insights?.success_rates) return [];
    return heatRows.map(mode =>
      heatCols.map(model => {
        const entry = insights!.success_rates.find(r => r.mode === mode && r.model === model);
        return entry?.success_rate ?? 0;
      })
    );
  });

  // Calibration scatter data
  let calibrationPoints = $derived(
    (insights?.calibration ?? []).map(b => ({
      x: b.avg_reported_confidence,
      y: b.actual_success_rate,
      label: b.bucket,
      size: Math.max(3, Math.min(12, b.total / 2)),
    }))
  );

  // Escalation bar data
  let escalationData = $derived(
    (insights?.escalation_stats ?? []).map(e => e.success_rate)
  );
  let escalationLabels = $derived(
    (insights?.escalation_stats ?? []).map(e => `Rung ${e.rung}`)
  );
</script>

<div class="space-y-4 animate-fade-in-up">
  <div class="flex items-center justify-between">
    <h1 class="text-lg font-semibold text-text">Intelligence Hub</h1>
    <span class="text-xs text-text-muted data-readout">{insights?.total_samples ?? 0} samples</span>
  </div>

  <!-- Tabs -->
  <div class="flex gap-1 border-b border-border-subtle">
    {#each [
      { id: 'overview', label: 'Overview' },
      { id: 'success', label: 'Success Rates' },
      { id: 'calibration', label: 'Calibration' },
      { id: 'efficiency', label: 'Efficiency' },
      { id: 'escalation', label: 'Escalation' },
    ] as tab}
      <button
        class="px-3 py-2 text-xs font-medium transition-colors
               {activeTab === tab.id ? 'text-text border-b-2 border-accent-blue' : 'text-text-muted hover:text-text-dim'}"
        onclick={() => activeTab = tab.id as any}
      >
        {tab.label}
      </button>
    {/each}
  </div>

  {#if activeTab === 'overview'}
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      <!-- Summary metrics -->
      <div class="glass rounded-lg p-4">
        <h3 class="text-xs font-semibold text-text-dim mb-3 uppercase tracking-wider">Overall</h3>
        <div class="space-y-2 text-sm">
          <div class="flex justify-between"><span class="text-text-muted">Total samples</span><span class="text-text data-readout">{insights?.total_samples ?? 0}</span></div>
          <div class="flex justify-between"><span class="text-text-muted">Intelligence events</span><span class="text-text data-readout">{insights?.intelligence_event_count ?? 0}</span></div>
          <div class="flex justify-between"><span class="text-text-muted">Runs (30d)</span><span class="text-text data-readout">{analytics?.total_runs ?? 0}</span></div>
          <div class="flex justify-between"><span class="text-text-muted">Failed (30d)</span><span class="text-reject data-readout">{analytics?.failed_runs ?? 0}</span></div>
        </div>
      </div>

      <!-- Success rate mini -->
      {#if heatRows.length > 0}
        <div class="glass rounded-lg p-4">
          <h3 class="text-xs font-semibold text-text-dim mb-3 uppercase tracking-wider">Success by Mode/Model</h3>
          <HeatMap rows={heatRows} columns={heatCols} data={heatData} />
        </div>
      {/if}

      <!-- Calibration mini -->
      {#if calibrationPoints.length > 0}
        <div class="glass rounded-lg p-4">
          <h3 class="text-xs font-semibold text-text-dim mb-3 uppercase tracking-wider">Confidence Calibration</h3>
          <ScatterPlot points={calibrationPoints} width={250} height={200} xLabel="Reported" yLabel="Actual" />
        </div>
      {/if}
    </div>

  {:else if activeTab === 'success'}
    <div class="glass rounded-lg p-6">
      {#if heatRows.length > 0}
        <HeatMap rows={heatRows} columns={heatCols} data={heatData} />
      {:else}
        <div class="text-sm text-text-muted text-center py-8">No data yet</div>
      {/if}
    </div>

    <!-- Detailed rates table -->
    {#if insights?.success_rates}
      <div class="glass rounded-lg overflow-hidden">
        <table class="w-full text-xs">
          <thead>
            <tr class="border-b border-border-subtle text-text-muted">
              <th class="px-4 py-2 text-left">Mode</th>
              <th class="px-4 py-2 text-left">Model</th>
              <th class="px-4 py-2 text-right">Samples</th>
              <th class="px-4 py-2 text-right">Success Rate</th>
              <th class="px-4 py-2 text-right">Avg Tokens</th>
            </tr>
          </thead>
          <tbody>
            {#each insights.success_rates as rate}
              <tr class="border-b border-border-subtle/50">
                <td class="px-4 py-2 text-text-dim capitalize">{rate.mode}</td>
                <td class="px-4 py-2 text-text-dim font-mono">{rate.model}</td>
                <td class="px-4 py-2 text-right text-text-dim data-readout">{rate.total}</td>
                <td class="px-4 py-2 text-right data-readout {rate.success_rate >= 0.7 ? 'text-approve' : rate.success_rate >= 0.4 ? 'text-warning' : 'text-reject'}">
                  {(rate.success_rate * 100).toFixed(0)}%
                </td>
                <td class="px-4 py-2 text-right text-text-dim data-readout">{rate.avg_tokens ? Math.round(rate.avg_tokens).toLocaleString() : '-'}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}

  {:else if activeTab === 'calibration'}
    <div class="glass rounded-lg p-6 flex justify-center">
      <ScatterPlot points={calibrationPoints} width={400} height={400} xLabel="Reported Confidence" yLabel="Actual Success Rate" />
    </div>

  {:else if activeTab === 'efficiency'}
    <div class="glass rounded-lg p-6">
      {#if insights?.token_efficiency}
        <div class="space-y-3">
          {#each insights.token_efficiency as eff}
            <div class="flex items-center gap-4 text-sm">
              <span class="w-20 text-text-dim capitalize">{eff.mode}</span>
              <div class="flex-1 flex items-center gap-2">
                <span class="text-approve data-readout">{eff.avg_tokens_success ? Math.round(eff.avg_tokens_success).toLocaleString() : '-'}</span>
                <span class="text-text-muted text-xs">success</span>
                <span class="text-reject data-readout ml-4">{eff.avg_tokens_failure ? Math.round(eff.avg_tokens_failure).toLocaleString() : '-'}</span>
                <span class="text-text-muted text-xs">failure</span>
                <span class="text-text-muted text-xs ml-auto">({eff.total} samples)</span>
              </div>
            </div>
          {/each}
        </div>
      {:else}
        <div class="text-sm text-text-muted text-center py-8">No data yet</div>
      {/if}
    </div>

  {:else if activeTab === 'escalation'}
    <div class="glass rounded-lg p-6">
      {#if escalationData.length > 0}
        <BarChart data={escalationData} labels={escalationLabels} width={500} height={250}
          colors={escalationData.map(v => v >= 0.7 ? 'var(--color-approve)' : v >= 0.4 ? 'var(--color-warning)' : 'var(--color-reject)')} />
      {:else}
        <div class="text-sm text-text-muted text-center py-8">No escalation data</div>
      {/if}
    </div>
  {/if}
</div>
