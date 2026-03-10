<script lang="ts">
  import type { Run, SystemStatus, UsageData, Project, CoordinatorTask } from '../lib/types';
  import { getLatestRun, getSystemStatus, listRuns, getUsage, listProjects, getCoordinatorTasks } from '../lib/api';
  import { formatDuration, formatTokens } from '../lib/format';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import {
    connect as connectLiveActivity,
    disconnect as disconnectLiveActivity,
    reset as resetLiveActivity,
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
  let coordinatorTasks = $state<CoordinatorTask[]>([]);
  let loading = $state(true);

  async function load() {
    try {
      const [latestRes, runsRes, sysRes, usageRes, projRes, coordRes] = await Promise.allSettled([
        getLatestRun(),
        listRuns({ limit: 10 }),
        getSystemStatus(),
        getUsage(),
        listProjects(),
        getCoordinatorTasks(),
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
      if (coordRes.status === 'fulfilled') {
        // Only show coordinator tasks for the current active run
        const activeRunIds = new Set(recentRuns.filter(r => r.status === 'running' || r.status === 'reviewing').map(r => r.run_id));
        coordinatorTasks = activeRunIds.size > 0
          ? coordRes.value.filter(t => activeRunIds.has(t.run_id))
          : [];
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

  // Derive run phase — detect coordinator activity
  // "running" = employees working, "reviewing" = manager review phase
  let runPhase = $derived((): RunPhase => {
    const activeRuns = recentRuns.filter(r => r.status === 'running' || r.status === 'reviewing');
    if (activeRuns.length === 0) return 'idle';

    const hasReviewing = activeRuns.some(r => r.status === 'reviewing');
    const hasManager = activeRuns.some(r => r.mode === 'manager');
    const hasVerdict = activeRuns.some(r => r.verdict != null);

    if (hasVerdict) return 'executing_verdict';
    if (hasReviewing || hasManager) return 'manager_review';

    // Detect coordinating phase: tasks exist and some are pending/ready (not yet all running)
    const hasCoordTasks = coordinatorTasks.length > 0;
    if (hasCoordTasks) {
      const hasRunning = coordinatorTasks.some(t => t.status === 'running');
      const hasPending = coordinatorTasks.some(t => t.status === 'pending' || t.status === 'ready');
      if (!hasRunning || hasPending) return 'coordinating';
    }
    return 'employee';
  });

  let isRunActive = $derived(runPhase() !== 'idle');

  // Derive active project name
  let activeProject = $derived(() => {
    const running = recentRuns.find(r => r.status === 'running' || r.status === 'reviewing');
    if (!running?.project_id) return null;
    return projectMap[running.project_id] ?? null;
  });

  // Clear stale live activity data when run finishes
  let prevPhase = $state<string>('idle');
  $effect(() => {
    const cur = runPhase();
    if (prevPhase !== 'idle' && cur === 'idle') {
      resetLiveActivity();
    }
    prevPhase = cur;
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

    <!-- Coordinator Task Status — when coordinating -->
    {#if coordinatorTasks.length > 0 && isRunActive}
      {@const running = coordinatorTasks.filter(t => t.status === 'running')}
      {@const completed = coordinatorTasks.filter(t => t.status === 'completed')}
      {@const failed = coordinatorTasks.filter(t => t.status === 'failed')}
      {@const pending = coordinatorTasks.filter(t => t.status === 'pending' || t.status === 'ready')}
      {@const blocked = coordinatorTasks.filter(t => t.status === 'blocked')}
      <div class="glass rounded-lg px-4 py-3 animate-fade-in-up">
        <div class="flex items-center gap-3 mb-2">
          <span class="text-accent-purple text-xs font-medium">Coordinator</span>
          <span class="text-text-dim text-[10px]">{coordinatorTasks.length} tasks in DAG</span>
          <a href="#/coordinator" class="ml-auto text-[10px] text-accent-cyan hover:underline">View DAG</a>
        </div>
        <div class="flex gap-2 flex-wrap">
          {#each coordinatorTasks as task}
            {@const color = task.status === 'completed' ? 'bg-green-500/20 border-green-500/30 text-green-400'
              : task.status === 'running' ? 'bg-yellow-500/20 border-yellow-500/30 text-yellow-400'
              : task.status === 'failed' ? 'bg-red-500/20 border-red-500/30 text-red-400'
              : task.status === 'blocked' ? 'bg-orange-500/20 border-orange-500/30 text-orange-400'
              : task.status === 'ready' ? 'bg-cyan-500/20 border-cyan-500/30 text-cyan-400'
              : 'bg-white/5 border-white/10 text-text-dim'}
            <div class="px-2 py-1 rounded border text-[10px] {color} flex items-center gap-1.5">
              <span class="font-medium truncate max-w-[140px]">{task.title}</span>
              {#if task.employee_index != null}
                <span class="opacity-60">E{task.employee_index}</span>
              {/if}
            </div>
          {/each}
        </div>
        {#if failed.length > 0}
          <div class="mt-1.5 text-[10px] text-red-400">
            {failed.length} task{failed.length !== 1 ? 's' : ''} failed{blocked.length > 0 ? `, ${blocked.length} blocked` : ''}
          </div>
        {/if}
      </div>
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
        overridePhase={runPhase()}
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
