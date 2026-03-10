<script lang="ts">
  import type { Run, SystemStatus, UsageData, Project } from '../lib/types';
  import { getLatestRun, getSystemStatus, listRuns, getUsage, listProjects } from '../lib/api';
  import { formatDuration, formatTokens } from '../lib/format';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import {
    connect as connectLiveActivity,
    disconnect as disconnectLiveActivity,
    liveActivity,
  } from '../lib/live-activity.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';
  import AgentWorkspace from '../components/AgentWorkspace.svelte';
  import MetricPanel from '../components/MetricPanel.svelte';
  import GlassCard from '../components/GlassCard.svelte';
  import ActivityFeed from '../components/ActivityFeed.svelte';
  import ScanLine from '../components/ScanLine.svelte';
  import EventTicker from '../components/EventTicker.svelte';
  import PhaseTimeline from '../components/PhaseTimeline.svelte';
  import LiveAgentFeed from '../components/LiveAgentFeed.svelte';
  import AgentStatusCards from '../components/AgentStatusCards.svelte';
  import ArcGauge from '../components/ArcGauge.svelte';
  import type { RunPhase } from '../lib/workspace-renderer';

  let latest = $state<Run | null>(null);
  let recentRuns = $state<Run[]>([]);
  let system = $state<SystemStatus | null>(null);
  let usage = $state<UsageData | null>(null);
  let projects = $state<Project[]>([]);
  let projectMap = $state<Record<number, string>>({});
  let loading = $state(true);

  async function load() {
    try {
      const [latestRes, runsRes, sysRes, usageRes, projRes] = await Promise.allSettled([
        getLatestRun(),
        listRuns({ limit: 10 }),
        getSystemStatus(),
        getUsage(),
        listProjects(),
      ]);
      if (latestRes.status === 'fulfilled') latest = latestRes.value;
      if (runsRes.status === 'fulfilled') recentRuns = runsRes.value.runs;
      if (sysRes.status === 'fulfilled') system = sysRes.value;
      if (usageRes.status === 'fulfilled') usage = usageRes.value;
      if (projRes.status === 'fulfilled') {
        projects = projRes.value;
        const map: Record<number, string> = {};
        for (const p of projRes.value) map[p.id] = p.repo;
        projectMap = map;
      }
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    load();
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  });

  // Connect/disconnect LiveActivityStore
  $effect(() => {
    connectLiveActivity();
    return () => disconnectLiveActivity();
  });

  let totalTokens = $derived(recentRuns.reduce((sum, r) => sum + (r.tokens_total ?? 0), 0));
  let avgTokens = $derived(recentRuns.length > 0 ? totalTokens / recentRuns.length : 0);

  // Derive run phase
  let runPhase = $derived((): RunPhase => {
    const runningRuns = recentRuns.filter(r => r.status === 'running');
    if (runningRuns.length === 0) return 'idle';
    const hasManager = runningRuns.some(r => r.mode === 'manager');
    const hasVerdict = runningRuns.some(r => r.verdict != null);
    if (hasVerdict) return 'executing_verdict';
    if (hasManager) return 'manager_review';
    return 'employee';
  });

  let isRunActive = $derived(runPhase() !== 'idle');

  // Derive active project name
  let activeProject = $derived(() => {
    const running = recentRuns.find(r => r.status === 'running');
    if (!running?.project_id) return null;
    return projectMap[running.project_id] ?? null;
  });

  // Current tool summary for canvas overlay
  let toolSummary = $derived(
    liveActivity.currentTool ? `${liveActivity.currentTool.name}: ${liveActivity.currentTool.summary}` : null
  );
</script>

<div class="space-y-5">
  {#if loading}
    <div class="flex justify-center py-12"><LoadingSpinner /></div>
  {:else}
    <!-- Page Header -->
    <div class="flex items-center justify-between animate-fade-in-up">
      <h1 class="ai-text hud-sweep-line !text-sm font-bold pb-1">Command Center</h1>
    </div>

    <!-- Live Event Ticker (SSE-powered) -->
    <div class="animate-fade-in-up border-l-2 border-l-accent-cyan/20 rounded-lg">
      <EventTicker onRefresh={load} />
    </div>

    <!-- Phase Timeline — only when run is active -->
    {#if isRunActive}
      <PhaseTimeline
        phase={runPhase()}
        startedAt={latest?.started_at ?? null}
      />
    {/if}

    <!-- Agent Workspace Visualization -->
    <div class="relative glass rounded-lg overflow-hidden animate-fade-in-up" style="height: clamp(280px, 50vh, 500px)">
      <ScanLine />
      <AgentWorkspace
        {projects}
        runs={recentRuns}
        latestRun={latest}
        systemStatus={system}
        {usage}
        activityIntensity={liveActivity.activityIntensity}
        currentToolSummary={toolSummary}
      />
    </div>

    <!-- Live Agent Feed — when run is active -->
    {#if isRunActive}
      <LiveAgentFeed />
    {/if}

    <!-- Agent Status Cards — always visible -->
    <AgentStatusCards
      latestRun={latest}
      systemStatus={system}
      {usage}
      phase={runPhase()}
      activeProject={activeProject()}
    />

    <!-- Metric Panels -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4">
      <MetricPanel
        label="Service"
        value={system?.service.active ? 1 : 0}
        format={(n) => n >= 0.5 ? 'Active' : 'Offline'}
        glow={system?.service.active ? 'emerald' : 'red'}
        subtitle={system?.timer.next_trigger ? `Next: ${system.timer.next_trigger}` : undefined}
        class="stagger-1"
      />
      <MetricPanel
        label="Latest Run"
        value={latest?.duration_ms ?? 0}
        format={(n) => n > 0 ? formatDuration(n) : 'No runs'}
        glow={latest?.verdict === 'APPROVE' ? 'emerald' : latest?.verdict === 'REJECT' ? 'red' : 'blue'}
        subtitle={latest?.verdict ?? undefined}
        class="stagger-2"
      />
      <MetricPanel
        label="Total Tokens"
        value={totalTokens}
        format={(n) => formatTokens(n)}
        glow="purple"
        subtitle="{recentRuns.length} runs, avg {formatTokens(avgTokens)}"
        class="stagger-3"
      />
      <GlassCard glow={usage && usage.usage_percent > 80 ? 'red' : 'cyan'} animated class="p-4 md:p-5 stagger-4 flex flex-col items-center justify-center">
        <p class="ai-text font-medium mb-2">Usage</p>
        <ArcGauge
          value={usage?.usage_percent ?? 0}
          size={64}
          color={usage && usage.usage_percent > 80 ? '#ef4444' : '#06b6d4'}
        />
        {#if usage}
          <p class="text-xs text-text-dim mt-1.5">{usage.sessions_used}/{usage.session_limit_24h} sessions</p>
        {/if}
      </GlassCard>
    </div>

    <!-- Activity Feed -->
    <div class="animate-fade-in-up stagger-5">
      <ActivityFeed runs={recentRuns} {projectMap} />
    </div>
  {/if}
</div>
