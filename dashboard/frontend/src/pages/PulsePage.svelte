<script lang="ts">
  import { agentPresence } from '../lib/agent-presence.svelte';
  import { getSystemStatus, getUsage, listProjects } from '../lib/api';
  import type { SystemStatus, UsageData, Project } from '../lib/types';
  import { navigate } from '../lib/router.svelte';
  import Timeline from '../components/Timeline.svelte';
  import ActiveWorkStrip from '../components/ActiveWorkStrip.svelte';
  import GlassCard from '../components/GlassCard.svelte';
  import MetricPanel from '../components/MetricPanel.svelte';
  import StatusOrb from '../components/StatusOrb.svelte';
  import IntelligencePanel from '../components/IntelligencePanel.svelte';
  import { formatTokens } from '../lib/format';

  let systemStatus = $state<SystemStatus | null>(null);
  let usage = $state<UsageData | null>(null);
  let projects = $state<Project[]>([]);

  async function loadData() {
    try {
      const [sysRes, usageRes, projRes] = await Promise.allSettled([
        getSystemStatus(),
        getUsage(),
        listProjects(),
      ]);
      if (sysRes.status === 'fulfilled') systemStatus = sysRes.value;
      if (usageRes.status === 'fulfilled') usage = usageRes.value;
      if (projRes.status === 'fulfilled') projects = projRes.value;
    } catch { /* silent */ }
  }

  $effect(() => {
    loadData();
    const interval = setInterval(loadData, 15000);
    return () => clearInterval(interval);
  });
</script>

<div class="space-y-4 animate-fade-in-up">
  <!-- Active Work Strip (visible only when agents working) -->
  <ActiveWorkStrip />

  <!-- Pending Decisions Banner -->
  {#if agentPresence.pendingDecisionCount > 0}
    <button
      onclick={() => navigate('/decide')}
      class="w-full glass border border-warning/30 rounded-lg px-4 py-2 flex items-center gap-3 cursor-pointer hover:bg-warning/5 transition-colors"
    >
      <div class="w-2 h-2 rounded-full bg-warning animate-pulse"></div>
      <span class="text-xs font-semibold text-warning">
        {agentPresence.pendingDecisionCount} pending decision{agentPresence.pendingDecisionCount !== 1 ? 's' : ''}
      </span>
      <span class="text-[10px] text-text-muted ml-auto">Click to review</span>
    </button>
  {/if}

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

  <!-- Activity Timeline + System Summary -->
  <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
    <!-- Activity Timeline -->
    <GlassCard class="p-4">
      <h2 class="text-sm font-semibold text-text mb-3">Activity Timeline</h2>
      <div class="max-h-[400px] overflow-auto">
        <Timeline maxItems={25} />
      </div>
    </GlassCard>

    <!-- System Summary -->
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
      </div>
    </GlassCard>
  </div>

  <!-- Intelligence (collapsible) -->
  <IntelligencePanel />
</div>
