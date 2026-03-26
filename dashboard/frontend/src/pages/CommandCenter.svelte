<script lang="ts">
  import { agentPresence, getAgentColor } from '../lib/agent-presence.svelte';
  import { intelligenceCache } from '../lib/intelligence-cache.svelte';
  import { listRuns, getQueueStats, getTokenUsage, getSystemStatus, getAnalytics, getBackpressure } from '../lib/api';
  import { navigate } from '../lib/router.svelte';
  import { formatCompact, formatDuration } from '../lib/chart-utils';
  import type { Run, QueueStats, TokenUsageData, SystemStatus, AnalyticsData, BackpressureStatus } from '../lib/types';
  import AgentCard from '../components/agents/AgentCard.svelte';
  import BackpressureGauge from '../components/queue/BackpressureGauge.svelte';
  import BurndownChart from '../components/charts/BurndownChart.svelte';
  import LineChart from '../components/charts/LineChart.svelte';

  let {
    triggering = false,
    onTrigger,
  }: {
    triggering?: boolean;
    onTrigger?: () => void;
  } = $props();

  // Data state
  let recentRuns = $state<Run[]>([]);
  let queueStats = $state<QueueStats | null>(null);
  let tokenUsage = $state<TokenUsageData | null>(null);
  let systemStatus = $state<SystemStatus | null>(null);
  let analyticsData = $state<AnalyticsData | null>(null);
  let backpressure = $state<BackpressureStatus | null>(null);

  // Derived
  let activeAgents = $derived(agentPresence.agents.filter(a => a.status === 'active' || a.status === 'thinking'));
  let phase = $derived(agentPresence.phase);

  let verdictCounts = $derived.by(() => {
    if (!analyticsData?.verdict_distribution) return { approve: 0, pr: 0, reject: 0, total: 0 };
    const dist = analyticsData.verdict_distribution;
    return {
      approve: dist.find(v => v.verdict === 'APPROVE')?.count ?? 0,
      pr: dist.find(v => v.verdict === 'PR')?.count ?? 0,
      reject: dist.find(v => v.verdict === 'REJECT')?.count ?? 0,
      total: dist.reduce((s, v) => s + v.count, 0) || 1,
    };
  });

  let successRate = $derived(
    verdictCounts.total > 0
      ? Math.round(((verdictCounts.approve + verdictCounts.pr) / verdictCounts.total) * 100)
      : 0
  );

  let dailyTokens = $derived(analyticsData?.daily_token_usage?.map(d => d.tokens_total) ?? []);
  let dailyLabels = $derived(analyticsData?.daily_token_usage?.map(d => d.date.slice(5)) ?? []);

  // Fetch data
  async function loadData() {
    const [runsRes, qRes, tRes, sRes, aRes, bRes] = await Promise.allSettled([
      listRuns({ limit: 20 }),
      getQueueStats(),
      getTokenUsage(),
      getSystemStatus(),
      getAnalytics({ days: 7 }),
      getBackpressure(),
    ]);
    if (runsRes.status === 'fulfilled') recentRuns = runsRes.value.runs;
    if (qRes.status === 'fulfilled') queueStats = qRes.value;
    if (tRes.status === 'fulfilled') tokenUsage = tRes.value;
    if (sRes.status === 'fulfilled') systemStatus = sRes.value;
    if (aRes.status === 'fulfilled') analyticsData = aRes.value;
    if (bRes.status === 'fulfilled') backpressure = bRes.value;
  }

  $effect(() => {
    loadData();
    const interval = setInterval(loadData, 30_000);
    return () => clearInterval(interval);
  });
</script>

<div class="space-y-4 animate-fade-in-up">
  <!-- Metric Strip -->
  <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
    <!-- Active Agents -->
    <div class="glass rounded-lg px-4 py-3">
      <div class="text-[10px] text-text-muted uppercase tracking-wider mb-1">Active Agents</div>
      <div class="text-2xl font-semibold text-text data-readout">{agentPresence.activeRuns.length}</div>
      <div class="text-[10px] text-text-muted capitalize">{phase}</div>
    </div>

    <!-- Queue Depth -->
    <div class="glass rounded-lg px-4 py-3">
      <div class="text-[10px] text-text-muted uppercase tracking-wider mb-1">Queue</div>
      <div class="text-2xl font-semibold text-text data-readout">{queueStats?.total ?? 0}</div>
      <div class="text-[10px] text-text-muted">
        {queueStats?.by_state?.in_progress ?? 0} active
      </div>
    </div>

    <!-- Token Budget -->
    <div class="glass rounded-lg px-4 py-3">
      <div class="text-[10px] text-text-muted uppercase tracking-wider mb-1">Tokens Today</div>
      <div class="text-2xl font-semibold text-text data-readout">
        {tokenUsage ? formatCompact(tokenUsage.daily.tokens_total) : '-'}
      </div>
      <div class="text-[10px] text-text-muted">
        {tokenUsage ? formatCompact(tokenUsage.monthly.tokens_total) + ' this month' : ''}
      </div>
    </div>

    <!-- Success Rate -->
    <div class="glass rounded-lg px-4 py-3">
      <div class="text-[10px] text-text-muted uppercase tracking-wider mb-1">Success Rate</div>
      <div class="text-2xl font-semibold data-readout {successRate >= 70 ? 'text-approve' : successRate >= 40 ? 'text-warning' : 'text-reject'}">
        {successRate}%
      </div>
      <div class="text-[10px] text-text-muted">{verdictCounts.total} verdicts (7d)</div>
    </div>

    <!-- Backpressure -->
    <div class="glass rounded-lg px-4 py-3 flex items-center gap-3">
      {#if backpressure}
        <BackpressureGauge level={backpressure.level} usagePercent={backpressure.usage_percent} compact />
        <div>
          <div class="text-[10px] text-text-muted uppercase tracking-wider">Load</div>
          <div class="text-sm font-medium text-text-dim">{backpressure.level}</div>
        </div>
      {:else}
        <div class="text-[10px] text-text-muted">No data</div>
      {/if}
    </div>

    <!-- System -->
    <div class="glass rounded-lg px-4 py-3">
      <div class="text-[10px] text-text-muted uppercase tracking-wider mb-1">System</div>
      <div class="flex items-center gap-2">
        <span class="w-2 h-2 rounded-full {systemStatus?.service.active ? 'bg-status-active' : 'bg-status-inactive'}"></span>
        <span class="text-sm text-text-dim">{systemStatus?.service.active ? 'Running' : 'Stopped'}</span>
      </div>
      {#if systemStatus?.timer.next_trigger}
        <div class="text-[10px] text-text-muted mt-0.5">
          Next: {new Date(systemStatus.timer.next_trigger).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </div>
      {/if}
    </div>
  </div>

  <!-- Main Grid: 3 columns -->
  <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
    <!-- Left: Live Agents -->
    <div class="space-y-3">
      <div class="flex items-center justify-between">
        <h2 class="text-sm font-semibold text-text-dim">Live Agents</h2>
        <button
          onclick={() => navigate('/theater')}
          class="text-[10px] text-info hover:text-text transition-colors"
        >
          Theater →
        </button>
      </div>

      {#if agentPresence.agents.length > 0}
        {#each agentPresence.agents as agent (agent.name)}
          <AgentCard
            name={agent.name}
            role={agent.role}
            color={agent.color}
            status={agent.status}
            currentTool={agent.currentAction ? { name: '', summary: agent.currentAction } : null}
            turns={agentPresence.turnCount}
            tokens={agentPresence.tokensBurned}
            compact
            onclick={() => navigate('/theater')}
          />
        {/each}
      {:else}
        <div class="glass rounded-lg p-6 text-center">
          <div class="text-2xl mb-2 opacity-30">◉</div>
          <div class="text-sm text-text-muted">No agents active</div>
          {#if onTrigger}
            <button
              onclick={onTrigger}
              disabled={triggering}
              class="mt-3 px-4 py-1.5 rounded-lg text-xs font-medium bg-accent-blue/20 text-accent-blue hover:bg-accent-blue/30 transition-colors"
            >
              {triggering ? 'Triggering...' : 'Trigger Run'}
            </button>
          {/if}
        </div>
      {/if}
    </div>

    <!-- Center: Recent Activity -->
    <div class="space-y-3">
      <h2 class="text-sm font-semibold text-text-dim">Recent Runs</h2>

      <div class="space-y-2 max-h-[400px] overflow-y-auto">
        {#each recentRuns.slice(0, 12) as run (run.id)}
          <button
            class="glass rounded-lg px-3 py-2 w-full text-left hover:bg-surface-2/50 transition-colors"
            onclick={() => navigate(`/runs/${run.run_id}`)}
          >
            <div class="flex items-center justify-between mb-1">
              <span class="text-xs font-medium text-text truncate">
                {run.run_id}
              </span>
              {#if run.verdict}
                <span class="text-[10px] px-1.5 py-0.5 rounded font-medium
                  {run.verdict === 'APPROVE' ? 'bg-approve/20 text-approve' :
                   run.verdict === 'PR' ? 'bg-pr/20 text-pr' :
                   run.verdict === 'REJECT' ? 'bg-reject/20 text-reject' :
                   'bg-surface-2 text-text-muted'}">
                  {run.verdict}
                </span>
              {:else if run.status === 'running'}
                <span class="text-[10px] px-1.5 py-0.5 rounded bg-status-active/20 text-status-active animate-pulse">LIVE</span>
              {:else}
                <span class="text-[10px] text-text-muted">{run.status}</span>
              {/if}
            </div>
            <div class="flex items-center gap-3 text-[10px] text-text-muted data-readout">
              {#if run.mode}<span>{run.mode}</span>{/if}
              {#if run.turns}<span>{run.turns} turns</span>{/if}
              {#if run.tokens_total}<span>{formatCompact(run.tokens_total)} tok</span>{/if}
              {#if run.duration_ms}<span>{formatDuration(run.duration_ms)}</span>{/if}
            </div>
          </button>
        {/each}

        {#if recentRuns.length === 0}
          <div class="text-center py-6 text-sm text-text-muted">No runs yet</div>
        {/if}
      </div>
    </div>

    <!-- Right: Token Burndown + System Health -->
    <div class="space-y-4">
      <div>
        <h2 class="text-sm font-semibold text-text-dim mb-2">Token Usage (7 days)</h2>
        <div class="glass rounded-lg p-3">
          {#if dailyTokens.length > 0}
            <BurndownChart
              used={dailyTokens}
              labels={dailyLabels}
              width={340}
              height={160}
            />
          {:else}
            <div class="text-center py-8 text-sm text-text-muted">No data</div>
          {/if}
        </div>
      </div>

      <!-- Verdict Distribution -->
      {#if analyticsData?.verdict_distribution}
        <div>
          <h2 class="text-sm font-semibold text-text-dim mb-2">Verdicts (7 days)</h2>
          <div class="glass rounded-lg p-3 space-y-2">
            {#each analyticsData.verdict_distribution as v}
              {@const pct = (v.count / verdictCounts.total) * 100}
              <div class="flex items-center gap-2 text-xs">
                <span class="w-14 text-text-muted">{v.verdict}</span>
                <div class="flex-1 h-2 rounded-full bg-surface-2 overflow-hidden">
                  <div
                    class="h-full rounded-full transition-all duration-slow"
                    style="width: {pct}%; background: {v.verdict === 'APPROVE' ? 'var(--color-approve)' : v.verdict === 'PR' ? 'var(--color-pr)' : v.verdict === 'REJECT' ? 'var(--color-reject)' : 'var(--color-text-muted)'}"
                  ></div>
                </div>
                <span class="w-8 text-right text-text-dim data-readout">{v.count}</span>
              </div>
            {/each}
          </div>
        </div>
      {/if}

      <!-- System Resources -->
      {#if systemStatus?.resources}
        <div>
          <h2 class="text-sm font-semibold text-text-dim mb-2">Resources</h2>
          <div class="glass rounded-lg p-3 space-y-2 text-xs">
            {#if systemStatus.resources.memory_used_mb && systemStatus.resources.memory_total_mb}
              <div class="flex items-center gap-2">
                <span class="w-14 text-text-muted">Memory</span>
                <div class="flex-1 h-1.5 rounded-full bg-surface-2">
                  <div class="h-full rounded-full bg-info" style="width: {(systemStatus.resources.memory_used_mb / systemStatus.resources.memory_total_mb) * 100}%"></div>
                </div>
                <span class="text-text-dim data-readout">{Math.round(systemStatus.resources.memory_used_mb)}M</span>
              </div>
            {/if}
            {#if systemStatus.resources.disk_used_gb && systemStatus.resources.disk_total_gb}
              <div class="flex items-center gap-2">
                <span class="w-14 text-text-muted">Disk</span>
                <div class="flex-1 h-1.5 rounded-full bg-surface-2">
                  <div class="h-full rounded-full bg-accent-purple" style="width: {(systemStatus.resources.disk_used_gb / systemStatus.resources.disk_total_gb) * 100}%"></div>
                </div>
                <span class="text-text-dim data-readout">{Math.round(systemStatus.resources.disk_used_gb)}G</span>
              </div>
            {/if}
            {#if systemStatus.resources.load_avg}
              <div class="flex items-center gap-2">
                <span class="w-14 text-text-muted">Load</span>
                <span class="text-text-dim data-readout">{systemStatus.resources.load_avg.map((l: number) => l.toFixed(1)).join(' / ')}</span>
              </div>
            {/if}
          </div>
        </div>
      {/if}
    </div>
  </div>
</div>
