<script lang="ts">
  import type { Run, SystemStatus, UsageData, Project } from '../lib/types';
  import { getLatestRun, getSystemStatus, listRuns, getUsage, listProjects } from '../lib/api';
  import { formatDuration, formatTokens } from '../lib/format';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';
  import AgentWorkspace from '../components/AgentWorkspace.svelte';
  import MetricPanel from '../components/MetricPanel.svelte';
  import ActivityFeed from '../components/ActivityFeed.svelte';
  import ScanLine from '../components/ScanLine.svelte';

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

  let totalTokens = $derived(recentRuns.reduce((sum, r) => sum + (r.tokens_total ?? 0), 0));
  let avgTokens = $derived(recentRuns.length > 0 ? totalTokens / recentRuns.length : 0);
</script>

<div class="space-y-5">
  {#if loading}
    <div class="flex justify-center py-12"><LoadingSpinner /></div>
  {:else}
    <!-- Agent Workspace Visualization -->
    <div class="relative glass rounded-xl overflow-hidden animate-fade-in-up" style="height: clamp(280px, 50vh, 500px)">
      <ScanLine />
      <AgentWorkspace
        {projects}
        runs={recentRuns}
        latestRun={latest}
        systemStatus={system}
        {usage}
      />
    </div>

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
      <MetricPanel
        label="Usage"
        value={usage?.usage_percent ?? 0}
        format={(n) => `${Math.round(n)}%`}
        glow={usage && usage.usage_percent > 80 ? 'red' : 'emerald'}
        subtitle={usage ? `${usage.sessions_used}/${usage.session_limit_24h} sessions` : undefined}
        class="stagger-4"
      />
    </div>

    <!-- Activity Feed -->
    <div class="animate-fade-in-up stagger-5">
      <ActivityFeed runs={recentRuns} {projectMap} />
    </div>
  {/if}
</div>
