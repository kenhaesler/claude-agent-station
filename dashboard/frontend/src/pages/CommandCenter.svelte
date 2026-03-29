<script lang="ts">
  import { listRuns, getQueueStats, getTokenUsage, getSystemStatus, getAnalytics, getBackpressure, getActiveEmployees, listQueue } from '../lib/api';
  import { navigate } from '../lib/router.svelte';
  import { agentPresence } from '../lib/agent-presence.svelte';
  import { formatTokens, formatDuration, timeAgo, formatPercent } from '../lib/format';
  import type { Run, QueueStats, TokenUsage, SystemStatus, AnalyticsResponse, BackpressureStatus, ActiveEmployee, QueueItem } from '../lib/types';
  import AgentPresenceStrip from '../components/data-display/AgentPresenceStrip.svelte';
  import AgentLiveCard from '../components/agents/AgentLiveCard.svelte';
  import LiveActivityFeed from '../components/data-display/LiveActivityFeed.svelte';
  import CompactKanban from '../components/data-display/CompactKanban.svelte';
  import StatusBar from '../components/data-display/StatusBar.svelte';
  import MetricCard from '../components/data-display/MetricCard.svelte';
  import DonutChart from '../components/charts/DonutChart.svelte';
  import SkeletonLoader from '../components/data-display/SkeletonLoader.svelte';

  let {
    triggering = false,
    onTrigger,
  }: {
    triggering?: boolean;
    onTrigger?: () => void;
  } = $props();

  // Data state
  let recentRuns = $state<Run[]>([]);
  let activeEmployees = $state<ActiveEmployee[]>([]);
  let queueStats = $state<QueueStats | null>(null);
  let queueItems = $state<QueueItem[]>([]);
  let tokenUsage = $state<TokenUsage | null>(null);
  let systemStatus = $state<SystemStatus | null>(null);
  let analyticsData = $state<AnalyticsResponse | null>(null);
  let backpressure = $state<BackpressureStatus | null>(null);
  let loading = $state(true);

  // Derived
  let stationPhase = $derived<'idle' | 'working' | 'attention'>(
    activeEmployees.length > 0 ? 'working' :
    (queueStats?.by_state?.review ?? 0) > 0 ? 'attention' : 'idle'
  );

  let stationSummary = $derived.by(() => {
    if (activeEmployees.length > 0) {
      const projects = new Set(activeEmployees.map(e => e.project_id)).size;
      return `${activeEmployees.length} agent${activeEmployees.length > 1 ? 's' : ''} working across ${projects} project${projects > 1 ? 's' : ''}`;
    }
    if ((queueStats?.by_state?.review ?? 0) > 0) {
      return `${queueStats!.by_state.review} item${queueStats!.by_state.review > 1 ? 's' : ''} need review`;
    }
    if (systemStatus?.timer?.next) {
      return `Next run ${timeAgo(systemStatus.timer.next)}`;
    }
    return 'All systems nominal';
  });

  let verdictCounts = $derived.by(() => {
    if (!analyticsData?.verdict_distribution) return { approve: 0, pr: 0, reject: 0, skip: 0, total: 0 };
    const dist = analyticsData.verdict_distribution;
    return {
      approve: dist.find(v => v.verdict === 'APPROVE')?.count ?? 0,
      pr: dist.find(v => v.verdict === 'PR')?.count ?? 0,
      reject: dist.find(v => v.verdict === 'REJECT')?.count ?? 0,
      skip: dist.find(v => v.verdict === 'SKIP')?.count ?? 0,
      total: dist.reduce((s, v) => s + v.count, 0) || 1,
    };
  });

  let successRate = $derived(
    verdictCounts.total > 0
      ? Math.round(((verdictCounts.approve + verdictCounts.pr) / verdictCounts.total) * 100)
      : 0
  );

  // Donut chart segments (exclude "none" — it's not a meaningful verdict)
  let donutSegments = $derived.by(() => {
    if (!analyticsData?.verdict_distribution) return [];
    return analyticsData.verdict_distribution
      .filter(v => v.verdict !== 'none' && v.count > 0)
      .map(v => ({
        value: v.count,
        color: v.verdict === 'APPROVE' ? 'var(--color-emerald)'
             : v.verdict === 'PR' ? 'var(--color-indigo)'
             : v.verdict === 'REJECT' ? 'var(--color-rose)'
             : v.verdict === 'SKIP' ? 'var(--color-tertiary)'
             : 'var(--color-ghost)',
        label: v.verdict,
      }));
  });

  let queuePending = $derived(
    (queueStats?.by_state?.pending ?? 0) +
    (queueStats?.by_state?.assigned ?? 0) +
    (queueStats?.by_state?.claimed ?? 0) +
    (queueStats?.by_state?.planning ?? 0)
  );

  // Fetch data
  async function loadData() {
    const [runsRes, empRes, qRes, tRes, sRes, aRes, bRes, qiRes] = await Promise.allSettled([
      listRuns({ limit: 15 }),
      getActiveEmployees(),
      getQueueStats(),
      getTokenUsage(),
      getSystemStatus(),
      getAnalytics({ days: 7 }),
      getBackpressure(),
      listQueue({ limit: 50 }),
    ]);
    if (runsRes.status === 'fulfilled') recentRuns = runsRes.value.runs;
    if (empRes.status === 'fulfilled') activeEmployees = empRes.value;
    if (qRes.status === 'fulfilled') queueStats = qRes.value;
    if (tRes.status === 'fulfilled') tokenUsage = tRes.value;
    if (sRes.status === 'fulfilled') systemStatus = sRes.value;
    if (aRes.status === 'fulfilled') analyticsData = aRes.value;
    if (bRes.status === 'fulfilled') backpressure = bRes.value;
    if (qiRes.status === 'fulfilled') queueItems = qiRes.value.items;
    loading = false;
  }

  $effect(() => {
    loadData();
    const interval = setInterval(loadData, 30_000);
    return () => clearInterval(interval);
  });

  function getVerdictBadge(verdict: string | null): string {
    if (!verdict) return '';
    const map: Record<string, string> = { 'APPROVE': 'badge-approve', 'PR': 'badge-pr', 'REJECT': 'badge-reject' };
    return map[verdict] ?? '';
  }

  function getModeBadge(mode: string | null): string {
    if (!mode) return '';
    return `badge-${mode}`;
  }

  function getRunLabel(run: Run): string {
    if (run.issue_number) return `#${run.issue_number}`;
    return run.run_id?.slice(0, 20) ?? `Run #${run.id}`;
  }

  function getRowTint(run: Run): string {
    if (run.verdict === 'APPROVE' || run.verdict === 'PR') return 'background: rgba(16,185,129,0.03);';
    if (run.verdict === 'REJECT') return 'background: rgba(244,63,94,0.03);';
    if (run.status === 'started') return 'background: rgba(139,92,246,0.03);';
    return '';
  }

  function getStatusBadge(run: Run): { label: string; cls: string } {
    if (run.verdict) return { label: run.verdict, cls: getVerdictBadge(run.verdict) };
    if (run.status === 'started') return { label: 'RUNNING', cls: 'badge-running' };
    if (run.status === 'finished') return { label: 'DONE', cls: 'badge-completed' };
    return { label: 'PENDING', cls: 'badge-pending' };
  }
</script>

<div class="space-y-6 animate-fade-in">

  <!-- ==================== LOADING STATE ==================== -->
  {#if loading}
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
      {#each Array(4) as _}
        <div class="card px-4 py-3"><SkeletonLoader lines={2} /></div>
      {/each}
    </div>
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div class="lg:col-span-2 card p-4"><SkeletonLoader lines={8} /></div>
      <div class="card p-4"><SkeletonLoader lines={6} /></div>
    </div>

  <!-- ==================== AGENT STAGE (WORKING) ==================== -->
  {:else if stationPhase !== 'idle'}
    <div class="space-y-3">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-3">
          <span class="status-dot running"></span>
          <h2 class="font-heading text-lg text-primary">{agentPresence.agents.length} Agent{agentPresence.agents.length > 1 ? 's' : ''} Working</h2>
        </div>
        <button onclick={() => navigate('/agents')} class="text-xs text-violet hover:text-primary transition-colors font-mono cursor-pointer">
          Watch Team →
        </button>
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {#each agentPresence.agents as agent}
          <AgentLiveCard
            {agent}
            entries={agentPresence.conversationLog.filter(e => e.agentName === agent.name)}
            onclick={() => navigate('/agents')}
          />
        {/each}
      </div>
    </div>

    <!-- Live Activity + Runs + Kanban when working -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div class="lg:col-span-2 space-y-4">
        <div class="flex items-center justify-between">
          <h3 class="section-header">Live Activity</h3>
          <button onclick={() => navigate('/agents')} class="text-xs text-violet hover:text-primary transition-colors font-mono cursor-pointer">
            View All Comms →
          </button>
        </div>
        <LiveActivityFeed entries={agentPresence.conversationLog} maxHeight="420px" />
      </div>
      <div class="space-y-4">
        <CompactKanban items={queueItems} onItemClick={(item) => navigate(`/queue/${item.id}`)} />
      </div>
    </div>

  <!-- ==================== IDLE STATE (MOST COMMON) ==================== -->
  {:else}

    <!-- Metrics Row -->
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <MetricCard
        label="Runs (7d)"
        value={analyticsData?.total_runs ?? 0}
        sublabel={stationSummary}
        accent="violet"
      />
      <MetricCard
        label="Success Rate"
        value="{successRate}%"
        sublabel="{verdictCounts.approve + verdictCounts.pr} approved of {verdictCounts.total}"
        accent={successRate >= 80 ? 'emerald' : successRate >= 50 ? 'amber' : 'rose'}
      />
      <MetricCard
        label="Tokens Today"
        value={formatTokens(tokenUsage?.daily?.tokens_total ?? null)}
        sublabel="{formatPercent(tokenUsage?.max_usage_percent ?? 0)} of budget"
        accent={
          (tokenUsage?.max_usage_percent ?? 0) > 90 ? 'rose' :
          (tokenUsage?.max_usage_percent ?? 0) > 70 ? 'amber' : 'cyan'
        }
      />
      <MetricCard
        label="Queue"
        value={queuePending}
        sublabel="{queueStats?.total ?? 0} total · {queueStats?.by_state?.review ?? 0} in review"
        accent={queuePending > 0 ? 'amber' : 'default'}
      />
    </div>
  {/if}

  <!-- ==================== MAIN CONTENT ==================== -->
  {#if !loading}
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">

      <!-- Left: Recent Runs -->
      <div class="lg:col-span-2 space-y-3">
        <div class="flex items-center justify-between">
          <h3 class="section-header">Recent Runs</h3>
          <button
            onclick={() => navigate('/runs')}
            class="text-xs text-violet hover:text-primary transition-colors font-mono cursor-pointer"
          >
            View All →
          </button>
        </div>
        <div class="space-y-1">
          {#each recentRuns.slice(0, stationPhase === 'idle' ? 10 : 6) as run, i (run.id)}
            {@const status = getStatusBadge(run)}
            <button
              class="w-full flex items-center gap-4 px-4 py-2.5 rounded-xl
                     border border-transparent
                     hover:border-border-hover transition-all duration-200
                     text-left cursor-pointer"
              style="{getRowTint(run)} backdrop-filter: blur(12px);"
              onclick={() => navigate(`/runs/${run.run_id}`)}
            >
              <span class="status-dot {run.verdict === 'APPROVE' || run.verdict === 'PR' ? 'online' : run.verdict === 'REJECT' ? 'error' : run.status === 'started' ? 'running' : 'offline'}"></span>
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2">
                  <span class="text-sm text-primary truncate">{getRunLabel(run)}</span>
                </div>
              </div>
              {#if run.mode}
                <span class="badge {getModeBadge(run.mode)}">{run.mode}</span>
              {/if}
              <span class="badge {status.cls}">{status.label}</span>
              <div class="flex items-center gap-3 text-[11px] font-mono text-tertiary">
                {#if run.tokens_total}
                  <span>{formatTokens(run.tokens_total)}</span>
                {/if}
                {#if run.duration_ms}
                  <span>{formatDuration(run.duration_ms)}</span>
                {/if}
                <span class="w-14 text-right">{timeAgo(run.started_at)}</span>
              </div>
            </button>
          {/each}
          {#if recentRuns.length === 0}
            <div class="card p-12 text-center">
              <div class="text-3xl opacity-30 mb-3">▶</div>
              <p class="text-secondary text-sm mb-1">No runs yet</p>
              <p class="text-tertiary text-xs">Trigger your first agent run to see results here</p>
            </div>
          {/if}
        </div>
      </div>

      <!-- Right: Kanban + Verdicts -->
      <div class="space-y-4">
        <CompactKanban
          items={queueItems}
          onItemClick={(item) => navigate(`/queue/${item.id}`)}
        />

        <!-- Verdict Distribution (DonutChart) -->
        {#if donutSegments.length > 0}
          <div class="card p-4 space-y-3">
            <h3 class="section-header">Verdicts (7d)</h3>
            <div class="flex justify-center">
              <DonutChart
                segments={donutSegments}
                size={130}
                thickness={16}
                centerValue="{successRate}%"
                centerLabel="success"
              />
            </div>
          </div>
        {/if}
      </div>
    </div>
  {/if}

  <!-- ==================== STATUS BAR ==================== -->
  <StatusBar
    activeCount={activeEmployees.length}
    {tokenUsage}
    {backpressure}
    {systemStatus}
    sseConnected={agentPresence.sseConnected}
    {successRate}
  />
</div>
