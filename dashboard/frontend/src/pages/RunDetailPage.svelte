<script lang="ts">
  import { getRun, getRunDiff, getRunFullContext, getRunLogs, getIntelligenceDecisions } from '../lib/api';
  import { navigate } from '../lib/router.svelte';
  import type { Run, DiffResult, RunFullContext, IntelligenceDecision } from '../lib/types';
  import Tabs from '../components/Tabs.svelte';
  import GlassCard from '../components/GlassCard.svelte';
  import Badge from '../components/Badge.svelte';
  import StatusBadge from '../components/StatusBadge.svelte';
  import EmployeeReport from '../components/EmployeeReport.svelte';
  import VerdictDetail from '../components/VerdictDetail.svelte';
  import PhaseTimeline from '../components/PhaseTimeline.svelte';
  import DiffViewer from '../components/DiffViewer.svelte';
  import CoordinatorDAG from '../components/CoordinatorDAG.svelte';
  import IntelligenceChip from '../components/IntelligenceChip.svelte';
  import RunReplay from '../components/RunReplay.svelte';
  import { getAgentName, getAgentColor } from '../lib/agent-presence.svelte';
  import { formatDuration, formatTokens, timeAgo } from '../lib/format';
  import type { RunPhase } from '../lib/workspace-renderer';

  interface Props {
    runId: string;
  }

  let { runId }: Props = $props();

  let run = $state<Run | null>(null);
  let fullContext = $state<RunFullContext | null>(null);
  let diff = $state<DiffResult | null>(null);
  let logLines = $state<Record<string, unknown>[]>([]);
  let decisions = $state<IntelligenceDecision[]>([]);
  let activeTab = $state('overview');
  let loading = $state(true);

  let isCompleted = $derived(run?.status === 'finished' || run?.status === 'error');

  let tabs = $derived([
    { id: 'overview', label: 'Overview' },
    { id: 'diff', label: 'Diff' },
    ...(isCompleted ? [{ id: 'replay', label: 'Replay' }] : []),
    { id: 'logs', label: 'Logs' },
    { id: 'coordinator', label: 'Coordinator' },
    { id: 'intelligence', label: 'Intelligence' },
  ]);

  async function loadData() {
    loading = true;
    try {
      const [runRes, ctxRes] = await Promise.allSettled([
        getRun(runId),
        getRunFullContext(runId),
      ]);
      if (runRes.status === 'fulfilled') run = runRes.value;
      if (ctxRes.status === 'fulfilled') fullContext = ctxRes.value;
    } catch { /* silent */ }
    loading = false;
  }

  async function loadDiff() {
    if (diff) return;
    try { diff = await getRunDiff(runId); } catch { /* silent */ }
  }

  async function loadLogs() {
    if (logLines.length > 0) return;
    try {
      const res = await getRunLogs(runId, 200);
      logLines = res.lines;
    } catch { /* silent */ }
  }

  async function loadDecisions() {
    if (decisions.length > 0) return;
    try { decisions = await getIntelligenceDecisions({ run_id: runId, limit: 50 }); } catch { /* silent */ }
  }

  $effect(() => {
    runId; // re-run on runId change
    loadData();
  });

  $effect(() => {
    if (activeTab === 'diff') loadDiff();
    if (activeTab === 'logs') loadLogs();
    if (activeTab === 'intelligence') loadDecisions();
  });

  let agentName = $derived(run ? getAgentName(run.employee_index, run.mode) : 'Unknown');
  let agentColor = $derived(getAgentColor(agentName));
  let phase = $derived<RunPhase>((() => {
    if (!run) return 'idle';
    if (run.status === 'finished' || run.status === 'error') return run.verdict ? 'executing_verdict' : 'idle';
    if (run.status === 'reviewing') return 'manager_review';
    if (run.status === 'running') return 'employee';
    return 'idle';
  })());
</script>

<div class="space-y-4 animate-fade-in-up">
  <!-- Back button -->
  <button onclick={() => navigate('/stream')} class="text-xs text-text-muted hover:text-text-dim cursor-pointer flex items-center gap-1">
    <span>&larr;</span> Back to Work Stream
  </button>

  {#if loading && !run}
    <div class="text-center py-12 text-text-muted">Loading run...</div>
  {:else if run}
    <!-- Run header -->
    <div class="flex items-center gap-3 flex-wrap">
      <span class="text-sm font-semibold" style="color: {agentColor}">{agentName}</span>
      <span class="text-xs text-text-muted font-data">{run.run_id.slice(0, 12)}</span>
      {#if run.issue_number}
        <span class="text-xs text-text-muted font-data">#{run.issue_number}</span>
      {/if}
      <StatusBadge value={run.verdict ?? run.status} />
      <span class="text-xs text-text-muted">{timeAgo(run.started_at)}</span>
      {#if fullContext?.queue_item && fullContext.queue_item.escalation_rung > 0}
        <IntelligenceChip type="escalation" rung={fullContext.queue_item.escalation_rung} />
      {/if}
    </div>

    <!-- Metrics -->
    <div class="flex flex-wrap gap-x-4 gap-y-1 text-xs">
      <span class="text-text-dim">Turns: <span class="text-text font-data">{run.turns ?? '-'}</span></span>
      <span class="text-text-dim">Tokens: <span class="text-text font-data">{formatTokens(run.tokens_total)}</span></span>
      <span class="text-text-dim">Duration: <span class="text-text font-data">{formatDuration(run.duration_ms)}</span></span>
      <span class="text-text-dim">Mode: <span class="text-text font-data">{run.mode ?? '-'}</span></span>
      <span class="text-text-dim">Model: <span class="text-text font-data">{run.model ?? '-'}</span></span>
    </div>

    <!-- Tabs -->
    <Tabs {tabs} {activeTab} onTabChange={(id) => activeTab = id} />

    <!-- Tab panels -->
    <div role="tabpanel" id="tabpanel-{activeTab}" aria-labelledby="tab-{activeTab}">
      {#if activeTab === 'overview'}
        <div class="space-y-4">
          {#if run.started_at}
            <PhaseTimeline {phase} startedAt={run.started_at} />
          {/if}

          {#if run.employee_report}
            <GlassCard class="p-4">
              <h3 class="text-xs font-semibold text-text-muted uppercase tracking-wider mb-2">Teammate Report</h3>
              <div class="text-xs text-text-dim leading-relaxed">
                <EmployeeReport report={run.employee_report} />
              </div>
            </GlassCard>
          {/if}

          {#if run.verdict_detail}
            <GlassCard class="p-4">
              <h3 class="text-xs font-semibold text-text-muted uppercase tracking-wider mb-2">Verdict Detail</h3>
              <div class="text-xs text-text-dim leading-relaxed">
                <VerdictDetail detail={run.verdict_detail} />
              </div>
            </GlassCard>
          {/if}
        </div>

      {:else if activeTab === 'diff'}
        {#if diff && diff.files.length > 0}
          <div class="space-y-2">
            <p class="text-xs text-text-muted">{diff.total_files} files, +{diff.total_additions} -{diff.total_deletions}</p>
            <DiffViewer {diff} />
          </div>
        {:else}
          <div class="text-center py-8 text-xs text-text-muted">No diff available</div>
        {/if}

      {:else if activeTab === 'replay'}
        <RunReplay {runId} />

      {:else if activeTab === 'logs'}
        <div class="space-y-1 max-h-[600px] overflow-auto">
          {#if logLines.length === 0}
            <div class="text-center py-8 text-xs text-text-muted">No log lines available</div>
          {:else}
            {#each logLines as line, i}
              <div class="text-[11px] font-data text-text-dim py-0.5 px-2 hover:bg-white/[0.02] rounded break-all">
                <span class="text-text-muted mr-2">{i + 1}</span>
                {JSON.stringify(line).slice(0, 200)}
              </div>
            {/each}
          {/if}
        </div>

      {:else if activeTab === 'coordinator'}
        <div class="space-y-4">
          {#if fullContext?.coordinator_tasks && fullContext.coordinator_tasks.length > 0}
            <CoordinatorDAG tasks={fullContext.coordinator_tasks} />
          {:else}
            <div class="text-center py-8 text-xs text-text-muted">No coordinator tasks for this run</div>
          {/if}

          {#if fullContext?.coordinator_messages && fullContext.coordinator_messages.length > 0}
            <GlassCard class="p-4">
              <h3 class="text-xs font-semibold text-text-muted uppercase tracking-wider mb-2">Messages ({fullContext.coordinator_messages.length})</h3>
              <div class="space-y-1 max-h-[300px] overflow-auto">
                {#each fullContext.coordinator_messages as msg}
                  <div class="text-xs py-1 flex gap-2">
                    <span class="text-text-muted font-data shrink-0">{msg.direction === 'outbound' ? '→' : '←'}</span>
                    <span class="text-text-dim">{msg.content}</span>
                  </div>
                {/each}
              </div>
            </GlassCard>
          {/if}
        </div>

      {:else if activeTab === 'intelligence'}
        <div class="space-y-2">
          {#if decisions.length === 0}
            <div class="text-center py-8 text-xs text-text-muted">No intelligence decisions for this run</div>
          {:else}
            {#each decisions as decision}
              <GlassCard class="p-3">
                <div class="flex items-center gap-2 mb-1">
                  <Badge label={decision.event_type} variant="info" />
                  <span class="text-[10px] text-text-muted">{timeAgo(decision.created_at)}</span>
                </div>
                <pre class="text-[11px] text-text-dim font-data whitespace-pre-wrap break-all">{decision.event_data}</pre>
              </GlassCard>
            {/each}
          {/if}
        </div>
      {/if}
    </div>
  {:else}
    <div class="text-center py-12 text-text-muted">Run not found</div>
  {/if}
</div>
