<script lang="ts">
  import type { Run, SystemStatus, UsageData } from '../lib/types';
  import type { RunPhase } from '../lib/workspace-renderer';
  import GlassCard from './GlassCard.svelte';
  import StatusOrb from './StatusOrb.svelte';
  import AnimatedCounter from './AnimatedCounter.svelte';
  import ActivitySparkline from './ActivitySparkline.svelte';
  import { formatTokens } from '../lib/format';
  import { liveActivity } from '../lib/live-activity.svelte';

  interface Props {
    latestRun: Run | null;
    systemStatus: SystemStatus | null;
    usage: UsageData | null;
    phase: RunPhase;
    activeProject: string | null;
  }

  let { latestRun, systemStatus, usage, phase, activeProject }: Props = $props();

  let isRunning = $derived(phase !== 'idle');

  let employeePhaseColor = $derived(
    phase === 'employee' ? '#3b82f6'
    : phase === 'manager_review' ? '#f59e0b'
    : phase === 'executing_verdict' ? '#10b981'
    : '#64748b'
  );

  let phaseLabel = $derived(
    phase === 'employee' ? 'Working'
    : phase === 'manager_review' ? 'In Review'
    : phase === 'executing_verdict' ? 'Executing'
    : 'Standby'
  );

  let managerPhaseLabel = $derived(
    phase === 'manager_review' ? 'Reviewing'
    : phase === 'executing_verdict' ? 'Directing'
    : phase === 'employee' ? 'Monitoring'
    : 'Idle'
  );

  let nextTrigger = $derived(systemStatus?.timer.next_trigger ?? null);
  let sessionsToday = $derived(usage?.sessions_used ?? 0);
</script>

<div class="grid grid-cols-1 md:grid-cols-2 gap-3 animate-fade-in-up">
  <!-- Employee Card -->
  <GlassCard glow={isRunning ? 'blue' : 'none'}>
    <div class="p-3">
      <div class="flex items-center justify-between mb-2">
        <div class="flex items-center gap-2">
          <StatusOrb active={isRunning} color={employeePhaseColor} size="md" />
          <div>
            <span class="text-xs font-medium">Employee</span>
            <span class="text-[10px] text-text-dim ml-1.5">{phaseLabel}</span>
          </div>
        </div>
        {#if isRunning}
          <ActivitySparkline data={liveActivity.sparklineData} color={employeePhaseColor} />
        {/if}
      </div>

      {#if isRunning && activeProject}
        <div class="text-[11px] text-text-dim mb-1.5 truncate">
          Working on <span class="text-accent-blue font-medium">{activeProject}</span>
        </div>
      {/if}

      {#if liveActivity.currentTool}
        <div class="text-[10px] font-data text-accent-cyan truncate mb-2">
          {liveActivity.currentTool.name}: {liveActivity.currentTool.summary}
        </div>
      {:else if isRunning}
        <div class="text-[10px] font-data text-text-dim mb-2 animate-pulse-glow">
          Thinking...
        </div>
      {:else}
        <div class="text-[10px] text-text-dim mb-2">No active task</div>
      {/if}

      <div class="flex items-center gap-4 text-[10px] text-text-dim">
        <div>
          <span class="mr-1">Turns:</span>
          <span class="font-data text-text">
            <AnimatedCounter value={liveActivity.turnCount} />
          </span>
        </div>
        <div>
          <span class="mr-1">Tokens:</span>
          <span class="font-data text-text">
            <AnimatedCounter value={liveActivity.tokensBurned} format={formatTokens} />
          </span>
        </div>
      </div>
    </div>
  </GlassCard>

  <!-- Manager Card -->
  <GlassCard glow={phase === 'manager_review' ? 'purple' : 'none'}>
    <div class="p-3">
      <div class="flex items-center justify-between mb-2">
        <div class="flex items-center gap-2">
          <StatusOrb
            active={systemStatus?.service.active ?? false}
            color={phase === 'manager_review' ? '#f59e0b' : phase === 'executing_verdict' ? '#10b981' : undefined}
            size="md"
          />
          <div>
            <span class="text-xs font-medium">Manager</span>
            <span class="text-[10px] text-text-dim ml-1.5">{managerPhaseLabel}</span>
          </div>
        </div>
      </div>

      <div class="space-y-1 text-[10px] text-text-dim">
        {#if nextTrigger}
          <div>
            Next run: <span class="text-text font-data">{nextTrigger}</span>
          </div>
        {/if}
        <div>
          Sessions today: <span class="text-text font-data">{sessionsToday}</span>
        </div>
        {#if latestRun?.verdict}
          <div>
            Last verdict: <span class="font-medium {latestRun.verdict === 'APPROVE' ? 'text-approve' : latestRun.verdict === 'REJECT' ? 'text-reject' : 'text-pr'}">{latestRun.verdict}</span>
          </div>
        {/if}
      </div>
    </div>
  </GlassCard>
</div>
