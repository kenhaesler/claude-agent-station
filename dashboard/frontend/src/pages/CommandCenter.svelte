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
      return `All quiet. Next run ${timeAgo(systemStatus.timer.next)}`;
    }
    return 'All systems nominal';
  });

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
</script>

<div class="space-y-6 animate-fade-in">
  <!-- ==================== AGENT STAGE (HERO) ==================== -->
  {#if agentPresence.agents.length > 0}
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
  {:else}
    <!-- Idle State -->
    <div class="card p-8 text-center" style="background: rgba(14,14,22,0.35);">
      <div class="flex items-center justify-center gap-3 mb-3">
        <span class="status-dot online"></span>
        <span class="font-heading text-lg text-primary">Station Idle</span>
      </div>
      <p class="text-sm text-secondary font-mono mb-5">{stationSummary}</p>
      {#if onTrigger}
        <button onclick={onTrigger} disabled={triggering} class="btn btn-primary cursor-pointer">
          {#if triggering}
            <span class="animate-spin-slow inline-block">↻</span> Triggering...
          {:else}
            ▶ Trigger Agent Run
          {/if}
        </button>
      {/if}
    </div>
    <AgentPresenceStrip
      agents={agentPresence.agents}
      activeRuns={activeEmployees}
      onAgentClick={() => navigate('/agents')}
    />
  {/if}

  <!-- ==================== MAIN CONTENT: Activity + Kanban ==================== -->
  <div class="grid grid-cols-1 lg:grid-cols-5 gap-6">

    <!-- Left: Live Activity Feed (3 cols) -->
    <div class="lg:col-span-3 space-y-4">
      <div class="flex items-center justify-between">
        <h3 class="text-xs font-mono uppercase tracking-widest text-tertiary">Live Activity</h3>
        <button
          onclick={() => navigate('/agents')}
          class="text-xs text-violet hover:text-primary transition-colors font-mono cursor-pointer"
        >
          View All Comms →
        </button>
      </div>
      <LiveActivityFeed
        entries={agentPresence.conversationLog}
        maxHeight="420px"
      />

      <!-- Recent Outcomes (compact) -->
      <div class="space-y-2">
        <div class="flex items-center justify-between">
          <h3 class="text-xs font-mono uppercase tracking-widest text-tertiary">Recent Runs</h3>
          <button
            onclick={() => navigate('/runs')}
            class="text-xs text-violet hover:text-primary transition-colors font-mono cursor-pointer"
          >
            View All →
          </button>
        </div>
        <div class="space-y-1">
          {#each recentRuns.slice(0, 6) as run, i (run.id)}
            <button
              class="w-full flex items-center gap-4 px-4 py-2.5 rounded-xl
                     border border-transparent
                     hover:border-border transition-all duration-200
                     text-left cursor-pointer"
              style="background: rgba(14,14,22,0.3); backdrop-filter: blur(12px);"
              onclick={() => navigate(`/runs/${run.run_id}`)}
            >
              <span class="status-dot {run.verdict === 'APPROVE' ? 'online' : run.verdict === 'PR' ? 'online' : run.verdict === 'REJECT' ? 'error' : run.status === 'started' ? 'running' : 'offline'}"></span>
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2">
                  <span class="text-xs font-mono text-secondary truncate">{run.run_id?.slice(0, 16)}</span>
                  {#if run.issue_number}
                    <span class="text-xs text-tertiary">#{run.issue_number}</span>
                  {/if}
                </div>
              </div>
              {#if run.mode}
                <span class="badge {getModeBadge(run.mode)}">{run.mode}</span>
              {/if}
              {#if run.verdict}
                <span class="badge {getVerdictBadge(run.verdict)}">{run.verdict}</span>
              {:else if run.status === 'started'}
                <span class="badge badge-running">LIVE</span>
              {/if}
              <div class="flex items-center gap-3 text-[11px] font-mono text-tertiary">
                {#if run.tokens_total}
                  <span>{formatTokens(run.tokens_total)}</span>
                {/if}
                <span class="w-14 text-right">{timeAgo(run.started_at)}</span>
              </div>
            </button>
          {/each}
          {#if recentRuns.length === 0 && !loading}
            <div class="card p-8 text-center">
              <p class="text-secondary text-sm mb-3">No runs yet</p>
              {#if onTrigger}
                <button onclick={onTrigger} disabled={triggering} class="btn btn-primary cursor-pointer">
                  {triggering ? 'Triggering...' : 'Trigger First Run'}
                </button>
              {/if}
            </div>
          {/if}
        </div>
      </div>
    </div>

    <!-- Right: Project Board + Widgets (2 cols) -->
    <div class="lg:col-span-2 space-y-4">
      <!-- Compact Kanban -->
      <CompactKanban
        items={queueItems}
        onItemClick={(item) => navigate(`/queue/${item.id}`)}
      />

      <!-- Verdict Distribution -->
      {#if analyticsData?.verdict_distribution && analyticsData.verdict_distribution.length > 0}
        <div class="card p-4 space-y-3">
          <h3 class="text-xs font-mono uppercase tracking-widest text-tertiary">Verdicts (7d)</h3>
          <div class="space-y-2">
            {#each analyticsData.verdict_distribution as v}
              {@const pct = (v.count / verdictCounts.total) * 100}
              <div class="flex items-center gap-3 text-xs">
                <span class="w-16 font-mono text-secondary">{v.verdict}</span>
                <div class="flex-1 h-1.5 rounded-full bg-surface-2 overflow-hidden">
                  <div
                    class="h-full rounded-full transition-all duration-500"
                    style="width: {pct}%; background: {v.verdict === 'APPROVE' ? 'var(--color-emerald)' : v.verdict === 'PR' ? 'var(--color-indigo)' : v.verdict === 'REJECT' ? 'var(--color-rose)' : 'var(--color-tertiary)'}"
                  ></div>
                </div>
                <span class="w-8 text-right font-mono text-primary font-medium">{v.count}</span>
              </div>
            {/each}
          </div>
        </div>
      {/if}

      <!-- Trigger Run (when idle) -->
      {#if activeEmployees.length === 0 && onTrigger}
        <button
          onclick={onTrigger}
          disabled={triggering}
          class="w-full card card-interactive p-5 text-center group cursor-pointer"
        >
          <div class="text-violet text-lg mb-1 group-hover:glow-violet transition-shadow">▶</div>
          <div class="text-sm font-medium text-primary">{triggering ? 'Triggering...' : 'Trigger Agent Run'}</div>
          <div class="text-[11px] text-tertiary mt-1">Start the autonomous agent cycle</div>
        </button>
      {/if}
    </div>
  </div>

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
