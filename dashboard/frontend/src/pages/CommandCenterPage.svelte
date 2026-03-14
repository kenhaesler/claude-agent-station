<script lang="ts">
  import { agentPresence } from '../lib/agent-presence.svelte';
  import { getSystemStatus, getUsage, listRuns, listProjects, getAnalytics } from '../lib/api';
  import type { SystemStatus, UsageData, Run, Project, AnalyticsData } from '../lib/types';
  import AgentWorkspace from '../components/AgentWorkspace.svelte';
  import NarrativeFeed from '../components/NarrativeFeed.svelte';
  import MetricPanel from '../components/MetricPanel.svelte';
  import GlassCard from '../components/GlassCard.svelte';
  import StatusOrb from '../components/StatusOrb.svelte';
  import { formatTokens, formatDuration } from '../lib/format';

  let systemStatus = $state<SystemStatus | null>(null);
  let usage = $state<UsageData | null>(null);
  let recentRuns = $state<Run[]>([]);
  let projects = $state<Project[]>([]);
  let analytics = $state<AnalyticsData | null>(null);
  let showAnalytics = $state(false);

  async function loadData() {
    try {
      const [sysRes, usageRes, runsRes, projRes] = await Promise.allSettled([
        getSystemStatus(),
        getUsage(),
        listRuns({ limit: 5 }),
        listProjects(),
      ]);
      if (sysRes.status === 'fulfilled') systemStatus = sysRes.value;
      if (usageRes.status === 'fulfilled') usage = usageRes.value;
      if (runsRes.status === 'fulfilled') recentRuns = runsRes.value.runs;
      if (projRes.status === 'fulfilled') projects = projRes.value;
    } catch { /* silent */ }
  }

  async function loadAnalytics() {
    if (analytics) return;
    try {
      analytics = await getAnalytics({ days: 7 });
    } catch { /* silent */ }
  }

  function toggleAnalytics() {
    showAnalytics = !showAnalytics;
    if (showAnalytics && !analytics) loadAnalytics();
  }

  $effect(() => {
    loadData();
    const interval = setInterval(loadData, 15000);
    return () => clearInterval(interval);
  });

  let totalRuns = $derived(analytics?.total_runs ?? recentRuns.length);
  let totalTokens = $derived(analytics?.total_tokens ?? 0);
  let verdictRate = $derived(() => {
    if (!analytics?.verdict_distribution) return 0;
    const approved = analytics.verdict_distribution.find(v => v.verdict === 'APPROVE')?.count ?? 0;
    const total = analytics.verdict_distribution.reduce((sum, v) => sum + v.count, 0);
    return total > 0 ? Math.round((approved / total) * 100) : 0;
  });
</script>

<div class="space-y-4 animate-fade-in-up">
  <!-- Agent Network Canvas (hero) -->
  <GlassCard class="overflow-hidden">
    <div class="h-[320px] md:h-[400px]">
      <AgentWorkspace {systemStatus} {usage} />
    </div>
  </GlassCard>

  <!-- Quick metrics row -->
  <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
    <MetricPanel
      label="Active Agents"
      value={agentPresence.agents.filter(a => a.status === 'active' || a.status === 'thinking').length}
      glow={agentPresence.phase !== 'idle' ? 'blue' : 'none'}
      subtitle={agentPresence.phase !== 'idle' ? `Phase: ${agentPresence.phase.replace('_', ' ')}` : 'Idle'}
    />
    <MetricPanel
      label="Turns"
      value={agentPresence.turnCount}
      glow="none"
      subtitle="Current session"
    />
    <MetricPanel
      label="Tokens Burned"
      value={agentPresence.tokensBurned}
      format={formatTokens}
      glow="none"
      subtitle="Current session"
    />
    <MetricPanel
      label="Projects"
      value={projects.length}
      glow="none"
      subtitle="{projects.filter(p => p.enabled).length} enabled"
    />
  </div>

  <!-- Narrative feed + system summary -->
  <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
    <!-- Narrative feed -->
    <GlassCard class="p-4">
      <h2 class="text-sm font-semibold text-text mb-3">Activity Feed</h2>
      <div class="max-h-[300px] overflow-auto">
        <NarrativeFeed maxItems={15} />
      </div>
    </GlassCard>

    <!-- System summary -->
    <GlassCard class="p-4">
      <h2 class="text-sm font-semibold text-text mb-3">System</h2>
      <div class="space-y-2.5">
        <div class="flex items-center justify-between">
          <span class="text-xs text-text-dim">Service</span>
          <div class="flex items-center gap-1.5">
            <StatusOrb active={systemStatus?.service.active ?? false} />
            <span class="text-xs text-text">{systemStatus?.service.active ? 'Running' : 'Stopped'}</span>
          </div>
        </div>
        <div class="flex items-center justify-between">
          <span class="text-xs text-text-dim">Timer</span>
          <span class="text-xs text-text font-data">{systemStatus?.timer.active ? 'Active' : 'Inactive'}</span>
        </div>
        {#if systemStatus?.timer.next_trigger}
          <div class="flex items-center justify-between">
            <span class="text-xs text-text-dim">Next run</span>
            <span class="text-xs text-text font-data">{systemStatus.timer.next_trigger}</span>
          </div>
        {/if}
        <div class="flex items-center justify-between">
          <span class="text-xs text-text-dim">Usage</span>
          <span class="text-xs text-text font-data">{usage ? `${Math.round(usage.usage_percent)}%` : '-'}</span>
        </div>
        <div class="flex items-center justify-between">
          <span class="text-xs text-text-dim">Sessions</span>
          <span class="text-xs text-text font-data">{usage ? `${usage.sessions_used}/${usage.plan_limit ?? '-'}` : '-'}</span>
        </div>
        {#if systemStatus?.resources}
          <div class="flex items-center justify-between">
            <span class="text-xs text-text-dim">Memory</span>
            <span class="text-xs text-text font-data">
              {systemStatus.resources.memory_used_mb && systemStatus.resources.memory_total_mb
                ? `${Math.round(systemStatus.resources.memory_used_mb / 1024 * 10) / 10}/${Math.round(systemStatus.resources.memory_total_mb / 1024 * 10) / 10} GB`
                : '-'}
            </span>
          </div>
          <div class="flex items-center justify-between">
            <span class="text-xs text-text-dim">Load</span>
            <span class="text-xs text-text font-data">{systemStatus.resources.load_avg?.map(l => l.toFixed(1)).join(' / ') ?? '-'}</span>
          </div>
        {/if}

        <!-- Recent runs -->
        {#if recentRuns.length > 0}
          <hr class="border-border-subtle" />
          <p class="text-xs text-text-dim font-medium">Recent Runs</p>
          {#each recentRuns.slice(0, 3) as run}
            <a href="#/stream/{run.run_id}" class="flex items-center justify-between text-xs hover:bg-white/[0.02] rounded px-1 py-0.5 no-underline">
              <span class="text-text-dim truncate max-w-[160px]">
                {run.run_id.slice(0, 8)}
                {#if run.issue_number}#{ run.issue_number}{/if}
              </span>
              <span class="font-data {run.verdict === 'APPROVE' ? 'text-approve' : run.verdict === 'REJECT' ? 'text-reject' : 'text-text-muted'}">
                {run.verdict ?? run.status ?? '-'}
              </span>
            </a>
          {/each}
        {/if}
      </div>
    </GlassCard>
  </div>

  <!-- Analytics (collapsible) -->
  <button
    onclick={toggleAnalytics}
    class="w-full text-left text-xs text-text-dim hover:text-text-dim cursor-pointer flex items-center gap-1.5 py-1"
  >
    <span class="text-[10px]">{showAnalytics ? '▾' : '▸'}</span>
    Analytics (7 days)
  </button>

  {#if showAnalytics && analytics}
    <div class="grid grid-cols-3 gap-3 animate-fade-in-up">
      <MetricPanel
        label="Total Runs"
        value={analytics.total_runs}
        glow="blue"
      />
      <MetricPanel
        label="Tokens (7d)"
        value={analytics.total_tokens}
        format={formatTokens}
        glow="purple"
      />
      <MetricPanel
        label="Approval Rate"
        value={verdictRate()}
        format={(n) => `${n}%`}
        glow="emerald"
      />
    </div>
  {/if}
</div>
