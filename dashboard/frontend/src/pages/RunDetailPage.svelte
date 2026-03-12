<script lang="ts">
  import type { Run, RunFullContext, DiffResult, CoordinatorTask, CoordinatorMessage } from '../lib/types';
  import { getRunFullContext, getRunDiff } from '../lib/api';
  import { formatDuration, formatTokens, formatDate } from '../lib/format';
  import { toastError } from '../lib/toast.svelte';
  import StatusBadge from '../components/StatusBadge.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';
  import VerdictDetail from '../components/VerdictDetail.svelte';
  import EmployeeReport from '../components/EmployeeReport.svelte';
  import DiffViewer from '../components/DiffViewer.svelte';
  import GlassCard from '../components/GlassCard.svelte';
  import PhaseTimeline from '../components/PhaseTimeline.svelte';
  import type { RunPhase } from '../lib/workspace-renderer';

  interface Props { runId: string; }
  let { runId }: Props = $props();

  let context = $state<RunFullContext | null>(null);
  let loading = $state(true);
  let diff = $state<DiffResult | null>(null);
  let diffLoading = $state(false);
  let diffExpanded = $state(false);
  let coordinatorExpanded = $state(true);
  let messagesExpanded = $state(false);
  let selectedTaskId = $state<string | null>(null);

  // Derived run for convenience
  let run = $derived(context?.run ?? null);

  // Derive run phase for the phase timeline
  let runPhase = $derived((): RunPhase => {
    if (!run) return 'idle';
    if (run.status === 'running') {
      if (context?.coordinator_tasks && context.coordinator_tasks.length > 0) {
        const hasRunning = context.coordinator_tasks.some(t => t.status === 'running');
        if (hasRunning) return 'employee';
        const allPending = context.coordinator_tasks.every(t => t.status === 'pending' || t.status === 'ready');
        if (allPending) return 'coordinating';
      }
      return 'employee';
    }
    if (run.status === 'reviewing') return 'manager_review';
    if (run.verdict) return 'executing_verdict';
    return 'idle';
  });

  let isRunActive = $derived(run?.status === 'running' || run?.status === 'reviewing');

  async function load(id: string) {
    loading = true;
    diff = null;
    diffExpanded = false;
    selectedTaskId = null;
    try {
      context = await getRunFullContext(id);
    } catch (e: any) {
      toastError(e.message);
    } finally {
      loading = false;
    }
  }

  async function loadDiff() {
    if (diffLoading || diff) return;
    diffLoading = true;
    try {
      diff = await getRunDiff(runId);
    } catch (e: any) {
      toastError('Failed to load diff: ' + e.message);
    } finally {
      diffLoading = false;
    }
  }

  function toggleDiff() {
    diffExpanded = !diffExpanded;
    if (diffExpanded && !diff && !diffLoading) {
      loadDiff();
    }
  }

  // Coordinator task helpers
  function statusColor(status: string): string {
    switch (status) {
      case 'completed': return 'text-green-400 bg-green-500/10 border-green-500/30';
      case 'running': return 'text-yellow-400 bg-yellow-500/10 border-yellow-500/30';
      case 'ready': return 'text-cyan-400 bg-cyan-500/10 border-cyan-500/30';
      case 'failed': return 'text-red-400 bg-red-500/10 border-red-500/30';
      case 'blocked': return 'text-orange-400 bg-orange-500/10 border-orange-500/30';
      default: return 'text-text-dim bg-white/5 border-white/10';
    }
  }

  function statusIcon(status: string): string {
    switch (status) {
      case 'completed': return '\u2713';
      case 'running': return '\u25C9';
      case 'ready': return '\u25CB';
      case 'failed': return '\u2715';
      case 'blocked': return '\u2298';
      default: return '\u00B7';
    }
  }

  function parseDeps(depsJson: string | null): string[] {
    if (!depsJson) return [];
    try { return JSON.parse(depsJson); } catch { return []; }
  }

  function parseJsonArray(json: string | null): string[] {
    if (!json) return [];
    try { return JSON.parse(json); } catch { return []; }
  }

  function taskDuration(start: string | null, end: string | null): string {
    if (!start) return '--';
    const s = new Date(start).getTime();
    const e = end ? new Date(end).getTime() : Date.now();
    const ms = e - s;
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
  }

  function msgTypeColor(type: string): string {
    switch (type) {
      case 'conflict': return 'text-red-400';
      case 'guidance': return 'text-cyan-400';
      case 'error': return 'text-red-400';
      default: return 'text-text-dim';
    }
  }

  // Auto-refresh for active runs
  $effect(() => {
    load(runId);
    let interval: ReturnType<typeof setInterval> | null = null;
    if (isRunActive) {
      interval = setInterval(() => load(runId), 5000);
    }
    return () => { if (interval) clearInterval(interval); };
  });
</script>

<div class="space-y-5 animate-fade-in-up">
  <div class="flex items-center gap-3 min-w-0">
    <a href="#/runs" class="text-text-dim hover:text-text shrink-0">&larr; Runs</a>
    <h1 class="text-lg md:text-2xl font-bold font-data truncate">{runId}</h1>
    {#if context?.project_repo}
      <span class="text-xs text-text-dim hidden sm:inline">{context.project_repo}</span>
    {/if}
  </div>

  {#if loading && !context}
    <div class="flex justify-center py-12"><LoadingSpinner /></div>
  {:else if run}
    <!-- Phase Timeline (always visible for active runs, shows completed state for finished runs) -->
    {#if isRunActive || run.status === 'success' || run.status === 'failed'}
      <PhaseTimeline
        phase={isRunActive ? runPhase() : 'idle'}
        startedAt={run.started_at}
      />
    {/if}

    <!-- Metadata Grid -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4">
      <GlassCard glow={run.verdict === 'APPROVE' ? 'emerald' : run.verdict === 'REJECT' ? 'red' : 'purple'} class="p-4">
        <span class="text-xs text-text-dim">Verdict</span>
        <div class="mt-1"><StatusBadge value={run.verdict} /></div>
      </GlassCard>
      <GlassCard glow="blue" class="p-4">
        <span class="text-xs text-text-dim">Status</span>
        <div class="mt-1"><StatusBadge value={run.status} variant="status" /></div>
      </GlassCard>
      <GlassCard class="p-4">
        <span class="text-xs text-text-dim">Duration</span>
        <p class="mt-1 font-medium">{formatDuration(run.duration_ms)}</p>
      </GlassCard>
      <GlassCard class="p-4">
        <span class="text-xs text-text-dim">Tokens</span>
        <p class="mt-1 font-medium font-data">{formatTokens(run.tokens_total)}</p>
      </GlassCard>
    </div>

    <!-- Details Table -->
    <GlassCard class="overflow-hidden overflow-x-auto">
      <table class="w-full text-sm min-w-[320px]">
        <tbody class="divide-y divide-border/30">
          <tr>
            <td class="px-5 py-3 text-text-dim w-40">Mode</td>
            <td class="px-5 py-3">{run.mode ?? '-'}</td>
          </tr>
          <tr>
            <td class="px-5 py-3 text-text-dim">Model</td>
            <td class="px-5 py-3 font-data text-xs">{run.model ?? '-'}</td>
          </tr>
          <tr>
            <td class="px-5 py-3 text-text-dim">Branch</td>
            <td class="px-5 py-3">{run.branch ?? '-'}</td>
          </tr>
          <tr>
            <td class="px-5 py-3 text-text-dim">Issue</td>
            <td class="px-5 py-3">
              {#if run.issue_number && context?.project_repo}
                <a href="https://github.com/{context.project_repo}/issues/{run.issue_number}" target="_blank" rel="noopener" class="text-accent-cyan hover:underline">
                  #{run.issue_number}
                </a>
              {:else}
                {run.issue_number ?? '-'}
              {/if}
            </td>
          </tr>
          <tr>
            <td class="px-5 py-3 text-text-dim">Turns</td>
            <td class="px-5 py-3">{run.turns ?? '-'}</td>
          </tr>
          <tr>
            <td class="px-5 py-3 text-text-dim">Tokens (Input / Output)</td>
            <td class="px-5 py-3 font-data">{formatTokens(run.tokens_input)} / {formatTokens(run.tokens_output)}</td>
          </tr>
          <tr>
            <td class="px-5 py-3 text-text-dim">Started</td>
            <td class="px-5 py-3">{formatDate(run.started_at)}</td>
          </tr>
          <tr>
            <td class="px-5 py-3 text-text-dim">Finished</td>
            <td class="px-5 py-3">{formatDate(run.finished_at)}</td>
          </tr>
          {#if run.concurrent_group_id}
            <tr>
              <td class="px-5 py-3 text-text-dim">Parallel Batch</td>
              <td class="px-5 py-3 font-data text-xs">
                <span class="inline-flex items-center gap-1.5">
                  <span class="w-1.5 h-1.5 rounded-full bg-purple-400/60"></span>
                  {run.concurrent_group_id}
                  {#if run.employee_index != null}
                    <span class="text-purple-400/60">&middot; Employee #{run.employee_index}</span>
                  {/if}
                </span>
              </td>
            </tr>
          {/if}
        </tbody>
      </table>
    </GlassCard>

    <!-- Related Queue Item (AC2: queue items link to run) -->
    {#if context?.queue_item}
      {@const qi = context.queue_item}
      <GlassCard glow="amber" class="p-5">
        <h2 class="font-semibold mb-3 flex items-center gap-2">
          <svg class="w-4 h-4 text-accent-amber" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 4h14M3 8h14M3 12h14M3 16h14" /><rect x="14" y="3" width="4" height="3" rx="0.5" />
          </svg>
          Queue Item
          <span class="text-xs font-normal px-2 py-0.5 rounded {qi.state === 'completed' ? 'bg-green-500/20 text-green-400' : qi.state === 'failed' ? 'bg-red-500/20 text-red-400' : qi.state === 'in_progress' ? 'bg-yellow-500/20 text-yellow-400' : 'bg-white/10 text-text-dim'}">{qi.state}</span>
        </h2>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
          <div>
            <span class="text-text-dim text-xs block mb-0.5">Project</span>
            <span class="text-text">{qi.project_repo}</span>
          </div>
          {#if qi.issue_number}
            <div>
              <span class="text-text-dim text-xs block mb-0.5">Issue</span>
              <span class="text-text">#{qi.issue_number} {qi.issue_title ?? ''}</span>
            </div>
          {/if}
          <div>
            <span class="text-text-dim text-xs block mb-0.5">Priority</span>
            <span class="text-text">{qi.priority}</span>
          </div>
          <div>
            <span class="text-text-dim text-xs block mb-0.5">Retries</span>
            <span class="text-text">{qi.retry_count}/{qi.max_retries}</span>
          </div>
        </div>
        {#if qi.error_message}
          <div class="mt-3 p-2 rounded bg-red-500/10 border border-red-500/20 text-red-400 text-xs">{qi.error_message}</div>
        {/if}
      </GlassCard>
    {/if}

    <!-- Related Plan (AC2: plans link to run) -->
    {#if context?.plan}
      {@const plan = context.plan}
      <GlassCard glow="blue" class="p-5">
        <h2 class="font-semibold mb-3 flex items-center gap-2">
          <svg class="w-4 h-4 text-accent-blue" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 4h12M4 8h12M4 12h8" /><path d="M13 12l2 2 4-4" stroke-width="2" />
          </svg>
          Implementation Plan
          <span class="text-xs font-normal px-2 py-0.5 rounded bg-white/10 text-text-dim">{plan.status}</span>
        </h2>
        <div class="space-y-2 text-sm">
          <div>
            <a href="#/plans/{plan.id}" class="text-accent-cyan hover:underline font-medium">{plan.title}</a>
          </div>
          {#if plan.description}
            <p class="text-text-dim text-xs line-clamp-3">{plan.description}</p>
          {/if}
          <div class="flex gap-4 text-xs text-text-dim">
            {#if plan.estimated_scope}
              <span>Scope: {plan.estimated_scope}</span>
            {/if}
            {#if plan.issue_number}
              <span>Issue #{plan.issue_number}</span>
            {/if}
          </div>
        </div>
      </GlassCard>
    {/if}

    <!-- Coordinator Tasks (AC2 + AC4: integrated into run detail) -->
    {#if context?.coordinator_tasks && context.coordinator_tasks.length > 0}
      {@const tasks = context.coordinator_tasks}
      {@const completedCount = tasks.filter(t => t.status === 'completed').length}
      {@const failedCount = tasks.filter(t => t.status === 'failed').length}
      <GlassCard glow="purple" class="p-5">
        <button
          class="w-full flex items-center justify-between"
          onclick={() => coordinatorExpanded = !coordinatorExpanded}
        >
          <h2 class="font-semibold flex items-center gap-2">
            <svg class="w-4 h-4 text-purple-400" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="10" cy="6" r="2.5" /><circle cx="5" cy="15" r="2.5" /><circle cx="15" cy="15" r="2.5" /><line x1="10" y1="8.5" x2="6.5" y2="12.5" /><line x1="10" y1="8.5" x2="13.5" y2="12.5" />
            </svg>
            Coordinator Tasks
            <span class="text-xs font-data text-text-dim">
              {completedCount}/{tasks.length} done
              {#if failedCount > 0}
                <span class="text-red-400">&middot; {failedCount} failed</span>
              {/if}
            </span>
          </h2>
          <span class="text-text-dim text-sm transition-transform {coordinatorExpanded ? 'rotate-90' : ''}">&#9654;</span>
        </button>

        {#if coordinatorExpanded}
          <div class="mt-4 space-y-2">
            {#each tasks as task}
              {@const deps = parseDeps(task.depends_on)}
              {@const isSelected = selectedTaskId === task.id}
              <button
                class="w-full text-left p-3 rounded-lg border transition-all duration-200 cursor-pointer
                  {isSelected ? 'ring-1 ring-accent-cyan border-accent-cyan/50 bg-accent-cyan/5' : statusColor(task.status)}"
                onclick={() => selectedTaskId = isSelected ? null : task.id}
              >
                <div class="flex items-center gap-2 mb-1">
                  <span class="text-xs font-bold w-5 h-5 rounded-full flex items-center justify-center border {statusColor(task.status)}">
                    {statusIcon(task.status)}
                  </span>
                  <span class="text-sm font-medium truncate flex-1 text-text">{task.title}</span>
                  <span class="text-text-dim text-xs shrink-0 transition-transform duration-200" class:rotate-180={isSelected}>
                    &#9660;
                  </span>
                </div>
                <div class="flex items-center gap-3 text-xs opacity-70 ml-7">
                  <span class="capitalize">{task.status}</span>
                  {#if task.employee_index != null}
                    <span class="px-1.5 py-0.5 rounded bg-white/10 text-text-dim">E{task.employee_index}</span>
                  {/if}
                  {#if deps.length > 0}
                    <span class="text-text-dim">{deps.length} dep{deps.length > 1 ? 's' : ''}</span>
                  {/if}
                  {#if task.started_at}
                    <span class="text-text-dim ml-auto">{taskDuration(task.started_at, task.finished_at)}</span>
                  {/if}
                </div>

                <!-- Expanded task detail -->
                {#if isSelected}
                  <div class="mt-3 pt-3 border-t border-hud-line space-y-2 text-xs" onclick={(e: MouseEvent) => e.stopPropagation()}>
                    {#if task.description}
                      <p class="text-text-dim">{task.description}</p>
                    {/if}
                    {#if task.result_summary}
                      <div class="p-2 rounded bg-green-500/5 border border-green-500/20 text-green-400">{task.result_summary}</div>
                    {/if}
                    {#if task.error_message}
                      <div class="p-2 rounded bg-red-500/10 border border-red-500/20 text-red-400 font-mono whitespace-pre-wrap">{task.error_message}</div>
                    {/if}
                    {#if task.branch}
                      <div><span class="text-text-dim">Branch:</span> <span class="font-mono text-text">{task.branch}</span></div>
                    {/if}
                    {#if task.touched_files}
                      {@const files = parseJsonArray(task.touched_files)}
                      {#if files.length > 0}
                        <div>
                          <span class="text-text-dim">Files ({files.length}):</span>
                          <div class="flex flex-wrap gap-1 mt-1">
                            {#each files as f}
                              <span class="font-mono px-1.5 py-0.5 rounded bg-white/5 text-text-dim">{f}</span>
                            {/each}
                          </div>
                        </div>
                      {/if}
                    {/if}
                    <div class="flex gap-4">
                      {#if task.started_at}
                        <span><span class="text-text-dim">Started:</span> {new Date(task.started_at).toLocaleString()}</span>
                      {/if}
                      {#if task.finished_at}
                        <span><span class="text-text-dim">Finished:</span> {new Date(task.finished_at).toLocaleString()}</span>
                      {/if}
                    </div>
                  </div>
                {/if}
              </button>
            {/each}
          </div>
        {/if}
      </GlassCard>
    {/if}

    <!-- Coordinator Messages -->
    {#if context?.coordinator_messages && context.coordinator_messages.length > 0}
      <GlassCard class="p-5">
        <button
          class="w-full flex items-center justify-between"
          onclick={() => messagesExpanded = !messagesExpanded}
        >
          <h2 class="font-semibold flex items-center gap-2">
            Coordinator Messages
            <span class="text-xs font-data text-text-dim">{context.coordinator_messages.length}</span>
          </h2>
          <span class="text-text-dim text-sm transition-transform {messagesExpanded ? 'rotate-90' : ''}">&#9654;</span>
        </button>
        {#if messagesExpanded}
          <div class="mt-3 divide-y divide-border/20 max-h-60 overflow-auto">
            {#each context.coordinator_messages as msg}
              <div class="py-2 flex items-start gap-3 text-xs">
                <span class="{msgTypeColor(msg.message_type)} shrink-0">{msg.message_type}</span>
                <span class="text-text-dim shrink-0">
                  {msg.direction === 'to_employee' ? '\u2192' : '\u2190'} E{msg.employee_index ?? '?'}
                </span>
                <span class="text-text flex-1">{msg.content}</span>
                {#if msg.created_at}
                  <span class="text-text-dim shrink-0">{new Date(msg.created_at).toLocaleTimeString()}</span>
                {/if}
              </div>
            {/each}
          </div>
        {/if}
      </GlassCard>
    {/if}

    <!-- Employee Report -->
    <GlassCard class="p-5">
      <h2 class="font-semibold mb-3">Employee Report</h2>
      <EmployeeReport report={run.employee_report} />
    </GlassCard>

    <!-- Verdict Detail -->
    <GlassCard class="p-5">
      <h2 class="font-semibold mb-3">Verdict Detail</h2>
      <VerdictDetail detail={run.verdict_detail} />
    </GlassCard>

    <!-- Code Changes -->
    {#if run.branch}
      <GlassCard class="p-5">
        <button
          class="w-full flex items-center justify-between"
          onclick={toggleDiff}
        >
          <h2 class="font-semibold flex items-center gap-2">
            Code Changes
            {#if diff && diff.total_files > 0}
              <span class="text-xs font-data text-text-dim">
                {diff.total_files} file{diff.total_files !== 1 ? 's' : ''}
                {#if diff.total_additions > 0}
                  <span class="text-emerald-400">+{diff.total_additions}</span>
                {/if}
                {#if diff.total_deletions > 0}
                  <span class="text-red-400">-{diff.total_deletions}</span>
                {/if}
              </span>
            {/if}
          </h2>
          <span class="text-text-dim text-sm transition-transform {diffExpanded ? 'rotate-90' : ''}">&#9654;</span>
        </button>
        {#if diffExpanded}
          <div class="mt-4">
            {#if diffLoading}
              <div class="flex justify-center py-6"><LoadingSpinner /></div>
            {:else if diff}
              <DiffViewer {diff} />
            {/if}
          </div>
        {/if}
      </GlassCard>
    {/if}

    <!-- Action Links -->
    <div class="flex gap-3 flex-wrap">
      <a href="#/logs?run={run.run_id}" class="px-4 py-2 glass rounded-lg text-sm hover:bg-white/[0.03] transition-colors">
        View Logs
      </a>
      {#if context?.plan}
        <a href="#/plans/{context.plan.id}" class="px-4 py-2 glass rounded-lg text-sm hover:bg-white/[0.03] transition-colors">
          View Plan
        </a>
      {/if}
      {#if run.concurrent_group_id}
        <a href="#/runs?group={run.concurrent_group_id}" class="px-4 py-2 glass rounded-lg text-sm hover:bg-white/[0.03] transition-colors">
          View Parallel Runs
        </a>
      {/if}
    </div>
  {:else}
    <p class="text-text-dim">Run not found</p>
  {/if}
</div>
