<script lang="ts">
  import { listRuns } from '../lib/api';
  import { navigate } from '../lib/router.svelte';
  import { formatCompact, formatDuration } from '../lib/chart-utils';
  import type { Run } from '../lib/types';

  let runs = $state<Run[]>([]);
  let total = $state(0);
  let offset = $state(0);
  let loading = $state(true);
  let statusFilter = $state('');
  let verdictFilter = $state('');
  const limit = 30;

  async function loadRuns() {
    loading = true;
    try {
      const params: any = { limit, offset };
      if (statusFilter) params.status = statusFilter;
      if (verdictFilter) params.verdict = verdictFilter;
      const res = await listRuns(params);
      runs = res.runs;
      total = res.total;
    } catch { /* silent */ }
    loading = false;
  }

  $effect(() => {
    loadRuns();
  });

  // Reload when filters change
  $effect(() => {
    statusFilter; verdictFilter;
    offset = 0;
    loadRuns();
  });

  const verdictStyles: Record<string, string> = {
    APPROVE: 'bg-approve/20 text-approve',
    PR: 'bg-pr/20 text-pr',
    REJECT: 'bg-reject/20 text-reject',
    SKIP: 'bg-surface-2 text-text-muted',
  };
</script>

<div class="space-y-4 animate-fade-in-up">
  <div class="flex items-center justify-between">
    <h1 class="text-lg font-semibold text-text">Runs</h1>
    <div class="flex items-center gap-2">
      <select bind:value={statusFilter} class="bg-surface text-text-dim text-xs px-2 py-1.5 rounded border border-border-subtle focus:border-focus outline-none">
        <option value="">All statuses</option>
        <option value="running">Running</option>
        <option value="success">Success</option>
        <option value="failed">Failed</option>
      </select>
      <select bind:value={verdictFilter} class="bg-surface text-text-dim text-xs px-2 py-1.5 rounded border border-border-subtle focus:border-focus outline-none">
        <option value="">All verdicts</option>
        <option value="APPROVE">Approve</option>
        <option value="PR">PR</option>
        <option value="REJECT">Reject</option>
      </select>
    </div>
  </div>

  <!-- Runs table -->
  <div class="glass rounded-lg overflow-hidden">
    <table class="w-full text-sm">
      <thead>
        <tr class="border-b border-border-subtle text-xs text-text-muted">
          <th class="px-4 py-2 text-left font-medium">Run ID</th>
          <th class="px-4 py-2 text-left font-medium">Mode</th>
          <th class="px-4 py-2 text-left font-medium">Status</th>
          <th class="px-4 py-2 text-left font-medium">Verdict</th>
          <th class="px-4 py-2 text-right font-medium">Tokens</th>
          <th class="px-4 py-2 text-right font-medium">Turns</th>
          <th class="px-4 py-2 text-right font-medium">Duration</th>
        </tr>
      </thead>
      <tbody>
        {#each runs as run (run.id)}
          <tr
            class="border-b border-border-subtle/50 hover:bg-surface-2/30 cursor-pointer transition-colors"
            onclick={() => navigate(`/runs/${run.run_id}`)}
          >
            <td class="px-4 py-2 text-text font-mono text-xs">{run.run_id}</td>
            <td class="px-4 py-2 text-text-dim capitalize">{run.mode ?? '-'}</td>
            <td class="px-4 py-2">
              <span class="text-xs {run.status === 'running' ? 'text-status-active' : run.status === 'failed' ? 'text-reject' : 'text-text-dim'}">
                {run.status ?? '-'}
              </span>
            </td>
            <td class="px-4 py-2">
              {#if run.verdict}
                <span class="text-[10px] px-1.5 py-0.5 rounded font-medium {verdictStyles[run.verdict] ?? ''}">
                  {run.verdict}
                </span>
              {:else}
                <span class="text-text-muted">-</span>
              {/if}
            </td>
            <td class="px-4 py-2 text-right text-text-dim data-readout text-xs">{run.tokens_total ? formatCompact(run.tokens_total) : '-'}</td>
            <td class="px-4 py-2 text-right text-text-dim data-readout text-xs">{run.turns ?? '-'}</td>
            <td class="px-4 py-2 text-right text-text-dim data-readout text-xs">{run.duration_ms ? formatDuration(run.duration_ms) : '-'}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>

  <!-- Pagination -->
  {#if total > limit}
    <div class="flex items-center justify-between text-xs text-text-muted">
      <span>Showing {offset + 1}-{Math.min(offset + limit, total)} of {total}</span>
      <div class="flex gap-2">
        <button
          disabled={offset === 0}
          onclick={() => { offset = Math.max(0, offset - limit); loadRuns(); }}
          class="px-3 py-1 rounded bg-surface hover:bg-surface-2 disabled:opacity-30 transition-colors"
        >← Prev</button>
        <button
          disabled={offset + limit >= total}
          onclick={() => { offset += limit; loadRuns(); }}
          class="px-3 py-1 rounded bg-surface hover:bg-surface-2 disabled:opacity-30 transition-colors"
        >Next →</button>
      </div>
    </div>
  {/if}
</div>
