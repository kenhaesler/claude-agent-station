<script lang="ts">
  import { getRunFullContext, getRunDiff } from '../lib/api';
  import { formatCompact, formatDuration } from '../lib/chart-utils';
  import type { RunFullContext, DiffResult, CoordinatorTask } from '../lib/types';
  import DAGCanvas from '../components/dag/DAGCanvas.svelte';

  let { runId = '' }: { runId: string } = $props();

  let context = $state<RunFullContext | null>(null);
  let diff = $state<DiffResult | null>(null);
  let loading = $state(true);
  let activeTab = $state<'overview' | 'dag' | 'diff' | 'report'>('overview');

  $effect(() => {
    if (!runId) return;
    load();
  });

  async function load() {
    loading = true;
    try {
      const [ctxRes, diffRes] = await Promise.allSettled([
        getRunFullContext(runId),
        getRunDiff(runId),
      ]);
      if (ctxRes.status === 'fulfilled') context = ctxRes.value;
      if (diffRes.status === 'fulfilled') diff = diffRes.value;
    } catch { /* silent */ }
    loading = false;
  }

  let run = $derived(context?.run);
  let tasks = $derived(context?.coordinator_tasks ?? []);
  let teamSummary = $derived(context?.team_summary);

  let report = $derived.by(() => {
    if (!run?.employee_report) return null;
    try { return JSON.parse(run.employee_report); } catch { return null; }
  });

  let verdictDetail = $derived.by(() => {
    if (!run?.verdict_detail) return null;
    try { return JSON.parse(run.verdict_detail); } catch { return null; }
  });

  const verdictStyles: Record<string, string> = {
    APPROVE: 'bg-approve/20 text-approve',
    PR: 'bg-pr/20 text-pr',
    REJECT: 'bg-reject/20 text-reject',
  };
</script>

<div class="space-y-4 animate-fade-in-up">
  {#if loading}
    <div class="text-sm text-text-muted">Loading run {runId}...</div>
  {:else if !run}
    <div class="text-sm text-text-muted">Run not found</div>
  {:else}
    <!-- Header -->
    <div class="flex items-center justify-between">
      <div>
        <h1 class="text-lg font-semibold text-text font-mono">{run.run_id}</h1>
        <div class="flex items-center gap-3 text-xs text-text-muted mt-1">
          {#if run.mode}<span class="capitalize">{run.mode}</span>{/if}
          {#if run.model}<span>{run.model}</span>{/if}
          {#if context?.project_repo}<span>{context.project_repo}</span>{/if}
        </div>
      </div>
      <div class="flex items-center gap-2">
        {#if run.status === 'running'}
          <span class="text-xs px-2 py-1 rounded bg-status-active/20 text-status-active animate-pulse">LIVE</span>
        {/if}
        {#if run.verdict}
          <span class="text-xs px-2 py-1 rounded font-medium {verdictStyles[run.verdict] ?? 'bg-surface-2 text-text-muted'}">
            {run.verdict}
          </span>
        {/if}
      </div>
    </div>

    <!-- Metrics strip -->
    <div class="flex items-center gap-6 text-xs text-text-dim data-readout">
      {#if run.tokens_total}<span>{formatCompact(run.tokens_total)} tokens</span>{/if}
      {#if run.turns}<span>{run.turns} turns</span>{/if}
      {#if run.duration_ms}<span>{formatDuration(run.duration_ms)}</span>{/if}
      {#if run.issue_number}<span class="text-info">Issue #{run.issue_number}</span>{/if}
    </div>

    <!-- Tabs -->
    <div class="flex gap-1 border-b border-border-subtle">
      {#each ['overview', 'dag', 'diff', 'report'] as tab}
        <button
          class="px-3 py-2 text-xs font-medium transition-colors
                 {activeTab === tab ? 'text-text border-b-2 border-accent-blue' : 'text-text-muted hover:text-text-dim'}"
          onclick={() => activeTab = tab as any}
        >
          {tab.charAt(0).toUpperCase() + tab.slice(1)}
          {#if tab === 'dag' && tasks.length > 0}
            <span class="ml-1 text-text-muted">({tasks.length})</span>
          {/if}
          {#if tab === 'diff' && diff}
            <span class="ml-1 text-text-muted">({diff.total_files})</span>
          {/if}
        </button>
      {/each}
    </div>

    <!-- Tab content -->
    {#if activeTab === 'overview'}
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <!-- Team summary -->
        {#if teamSummary}
          <div class="glass rounded-lg p-4">
            <h3 class="text-xs font-semibold text-text-dim mb-3 uppercase tracking-wider">Team: {teamSummary.team_name}</h3>
            <div class="space-y-2">
              {#each teamSummary.teammates as mate}
                <div class="flex items-center justify-between text-xs">
                  <span class="text-text">{mate.name}</span>
                  <span class="text-text-muted capitalize">{mate.status}</span>
                </div>
              {/each}
            </div>
            <div class="mt-3 text-xs text-text-muted">
              {teamSummary.tasks_completed}/{teamSummary.tasks_total} tasks done
            </div>
          </div>
        {/if}

        <!-- Verdict detail -->
        {#if verdictDetail}
          <div class="glass rounded-lg p-4">
            <h3 class="text-xs font-semibold text-text-dim mb-3 uppercase tracking-wider">Verdict Detail</h3>
            <pre class="text-xs text-text-dim whitespace-pre-wrap overflow-auto max-h-64 font-data">{JSON.stringify(verdictDetail, null, 2)}</pre>
          </div>
        {/if}
      </div>

    {:else if activeTab === 'dag'}
      <div class="glass rounded-lg h-96">
        <DAGCanvas {tasks} />
      </div>

    {:else if activeTab === 'diff'}
      {#if diff && diff.files.length > 0}
        <div class="space-y-3">
          <div class="text-xs text-text-muted">
            {diff.total_files} files, <span class="text-approve">+{diff.total_additions}</span> <span class="text-reject">-{diff.total_deletions}</span>
          </div>
          {#each diff.files as file}
            <div class="glass rounded-lg overflow-hidden">
              <div class="flex items-center justify-between px-3 py-2 bg-surface-2/50 border-b border-border-subtle text-xs">
                <span class="text-text font-mono">{file.filename}</span>
                <span>
                  <span class="text-approve">+{file.additions}</span>
                  <span class="text-reject ml-1">-{file.deletions}</span>
                </span>
              </div>
              <div class="overflow-x-auto text-xs font-data">
                {#each file.hunks as hunk}
                  <div class="text-text-muted px-3 py-0.5 bg-surface-2/30">{hunk.header}</div>
                  {#each hunk.lines as line}
                    <div class="px-3 py-0 whitespace-pre
                      {line.type === 'add' ? 'bg-approve/5 text-approve' :
                       line.type === 'remove' ? 'bg-reject/5 text-reject' :
                       'text-text-dim'}">
                      <span class="text-text-muted w-8 inline-block text-right mr-2 select-none">{line.old_line ?? ''}</span>
                      <span class="text-text-muted w-8 inline-block text-right mr-2 select-none">{line.new_line ?? ''}</span>
                      {line.content}
                    </div>
                  {/each}
                {/each}
              </div>
            </div>
          {/each}
        </div>
      {:else}
        <div class="text-sm text-text-muted text-center py-8">No diff available</div>
      {/if}

    {:else if activeTab === 'report'}
      {#if report}
        <div class="glass rounded-lg p-4">
          <pre class="text-xs text-text-dim whitespace-pre-wrap overflow-auto font-data">{JSON.stringify(report, null, 2)}</pre>
        </div>
      {:else}
        <div class="text-sm text-text-muted text-center py-8">No report available</div>
      {/if}
    {/if}
  {/if}
</div>
