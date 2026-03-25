<script lang="ts">
  import type { Run, RunFullContext, CoordinatorTask } from '../lib/types';
  import { getRunFullContext, getRunDiff } from '../lib/api';
  import type { DiffResult } from '../lib/types';
  import StatusBadge from './StatusBadge.svelte';
  import StatusOrb from './StatusOrb.svelte';
  import GlassCard from './GlassCard.svelte';
  import TaskDAGMini from './TaskDAGMini.svelte';
  import PhaseTimeline from './PhaseTimeline.svelte';
  import EmployeeReport from './EmployeeReport.svelte';
  import VerdictDetail from './VerdictDetail.svelte';
  import DiffViewer from './DiffViewer.svelte';
  import IntelligenceChip from './IntelligenceChip.svelte';
  import type { RunPhase } from '../lib/workspace-renderer';
  import { formatDuration, formatTokens, timeAgo } from '../lib/format';
  import { agentPresence, getAgentName, getAgentColor } from '../lib/agent-presence.svelte';

  interface Props {
    run: Run;
    expanded?: boolean;
    projectRepo?: string;
  }

  let { run, expanded = false, projectRepo }: Props = $props();

  let level = $state(0);

  $effect(() => {
    if (expanded) level = 1;
  });
  let fullContext = $state<RunFullContext | null>(null);
  let diff = $state<DiffResult | null>(null);
  let loadingContext = $state(false);
  let loadingDiff = $state(false);

  let isActive = $derived(run.status === 'running' || run.status === 'reviewing' || run.status === 'plan_reviewing');

  async function expand() {
    if (level === 0) {
      level = 1;
      if (!fullContext) await loadFullContext();
    } else if (level === 1) {
      level = 0;
    }
  }

  async function showDetails() {
    level = 2;
    if (!diff) await loadDiff();
  }

  async function loadFullContext() {
    loadingContext = true;
    try {
      fullContext = await getRunFullContext(run.run_id);
    } catch { /* silent */ }
    loadingContext = false;
  }

  async function loadDiff() {
    loadingDiff = true;
    try {
      diff = await getRunDiff(run.run_id);
    } catch { /* silent */ }
    loadingDiff = false;
  }

  let agentName = $derived(getAgentName(run.employee_index, run.mode));
  let agentColor = $derived(getAgentColor(agentName));

  let verdictColor = $derived(
    run.verdict === 'APPROVE' ? 'text-approve' :
    run.verdict === 'REJECT' ? 'text-reject' :
    run.verdict === 'PR' ? 'text-pr' :
    run.verdict === 'SKIP' ? 'text-text-dim' : ''
  );

  let phase = $derived<RunPhase>((() => {
    if (run.status === 'finished' || run.status === 'error') {
      return run.verdict ? 'executing_verdict' : 'idle';
    }
    if (run.status === 'plan_reviewing') return 'plan_review';
    if (run.status === 'reviewing') return 'manager_review';
    if (run.status === 'running') return 'employee';
    if (run.status === 'coordinating') return 'coordinating';
    return 'idle';
  })());
</script>

<!-- Level 0: Collapsed row (48px) -->
<div class="border border-border rounded-lg overflow-hidden bg-surface transition-all duration-200 {isActive ? 'border-l-2 border-l-info' : ''}">
  <button
    onclick={expand}
    class="w-full flex items-center gap-3 px-3 py-2.5 text-left cursor-pointer hover:bg-white/[0.02] transition-colors"
  >
    <!-- Status orb -->
    <StatusOrb active={isActive} color={isActive ? 'var(--color-info)' : run.verdict === 'APPROVE' ? 'var(--color-approve)' : run.verdict === 'REJECT' ? 'var(--color-reject)' : undefined} />

    <!-- Agent name + project -->
    <div class="flex items-center gap-2 min-w-0 flex-1">
      <span class="text-xs font-semibold" style="color: {agentColor}">{agentName}</span>
      <span class="text-xs text-text-dim truncate">{projectRepo ?? `project-${run.project_id}`}</span>
      {#if run.issue_number}
        <span class="text-xs text-text-muted font-data">#{run.issue_number}</span>
      {/if}
    </div>

    <!-- Duration -->
    <span class="text-xs text-text-muted font-data hidden sm:inline">{formatDuration(run.duration_ms)}</span>

    <!-- Verdict badge -->
    <StatusBadge value={run.verdict ?? run.status} />

    <!-- Expand indicator -->
    <span class="text-text-muted text-xs">{level === 0 ? '▸' : '▾'}</span>
  </button>

  <!-- Level 1: Expanded summary -->
  {#if level >= 1}
    <div class="px-3 pb-3 border-t border-border-subtle animate-fade-in-up">
      {#if loadingContext}
        <div class="py-4 text-center text-xs text-text-muted">Loading...</div>
      {:else}
        <div class="space-y-3 pt-3">
          <!-- Metrics row -->
          <div class="flex flex-wrap gap-x-4 gap-y-1 text-xs">
            <span class="text-text-dim">Turns: <span class="text-text font-data">{run.turns ?? '-'}</span></span>
            <span class="text-text-dim">Tokens: <span class="text-text font-data">{formatTokens(run.tokens_total)}</span></span>
            <span class="text-text-dim">Duration: <span class="text-text font-data">{formatDuration(run.duration_ms)}</span></span>
            <span class="text-text-dim">Started: <span class="text-text">{timeAgo(run.started_at)}</span></span>
          </div>

          <!-- Intelligence: escalation info -->
          {#if fullContext?.queue_item && fullContext.queue_item.escalation_rung > 0}
            <IntelligenceChip type="escalation" rung={fullContext.queue_item.escalation_rung} />
          {/if}

          <!-- Teammate report -->
          {#if run.employee_report}
            <div>
              <p class="text-[10px] text-text-muted uppercase tracking-wider mb-1">Report</p>
              <div class="text-xs text-text-dim leading-relaxed max-h-[200px] overflow-auto">
                <EmployeeReport report={run.employee_report} />
              </div>
            </div>
          {/if}

          <!-- Coordinator tasks (mini DAG) -->
          {#if fullContext?.coordinator_tasks && fullContext.coordinator_tasks.length > 0}
            <div>
              <p class="text-[10px] text-text-muted uppercase tracking-wider mb-1">Tasks</p>
              <TaskDAGMini tasks={fullContext.coordinator_tasks} />
            </div>
          {/if}

          <!-- Phase timeline -->
          {#if run.started_at}
            <PhaseTimeline
              {phase}
              startedAt={run.started_at}
            />
          {/if}

          <!-- Show Details button -->
          <button
            onclick={showDetails}
            class="text-xs text-info hover:underline cursor-pointer"
          >
            {level === 2 ? 'Hide details' : 'Show details'}
          </button>
        </div>
      {/if}
    </div>
  {/if}

  <!-- Level 2: Full details -->
  {#if level >= 2}
    <div class="px-3 pb-3 border-t border-border-subtle animate-fade-in-up">
      <!-- Diff viewer -->
      {#if loadingDiff}
        <div class="py-4 text-center text-xs text-text-muted">Loading diff...</div>
      {:else if diff && diff.files.length > 0}
        <div class="mt-3">
          <p class="text-[10px] text-text-muted uppercase tracking-wider mb-2">Changes ({diff.total_files} files, +{diff.total_additions} -{diff.total_deletions})</p>
          <DiffViewer {diff} />
        </div>
      {/if}

      <!-- Verdict detail -->
      {#if run.verdict_detail}
        <div class="mt-3">
          <p class="text-[10px] text-text-muted uppercase tracking-wider mb-1">Verdict Detail</p>
          <div class="text-xs text-text-dim leading-relaxed">
            <VerdictDetail detail={run.verdict_detail} />
          </div>
        </div>
      {/if}

      <!-- Coordinator messages -->
      {#if fullContext?.coordinator_messages && fullContext.coordinator_messages.length > 0}
        <div class="mt-3">
          <p class="text-[10px] text-text-muted uppercase tracking-wider mb-1">Messages ({fullContext.coordinator_messages.length})</p>
          <div class="space-y-1 max-h-[200px] overflow-auto">
            {#each fullContext.coordinator_messages as msg}
              <div class="text-xs py-1 flex gap-2">
                <span class="text-text-muted font-data shrink-0">{msg.direction === 'outbound' ? '→' : '←'}</span>
                <span class="text-text-dim">{msg.content}</span>
              </div>
            {/each}
          </div>
        </div>
      {/if}
    </div>
  {/if}
</div>
