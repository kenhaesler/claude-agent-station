<script lang="ts">
  import { listRuns } from '../lib/api';
  import { navigate } from '../lib/router.svelte';
  import { formatTokens, formatDuration, timeAgo } from '../lib/format';
  import type { Run } from '../lib/types';

  let runs = $state<Run[]>([]);
  let total = $state(0);
  let loading = $state(true);
  let offset = $state(0);
  const limit = 25;

  // Filters
  let statusFilter = $state<string>('');
  let verdictFilter = $state<string>('');

  async function loadRuns() {
    loading = true;
    try {
      const params: Record<string, unknown> = { limit, offset };
      if (statusFilter) params.status = statusFilter;
      if (verdictFilter) params.verdict = verdictFilter;
      const res = await listRuns(params);
      runs = res.runs;
      total = res.total;
    } catch (e) {
      console.error('Failed to load runs:', e);
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    statusFilter; verdictFilter; offset;
    loadRuns();
  });

  let totalPages = $derived(Math.ceil(total / limit));
  let currentPage = $derived(Math.floor(offset / limit) + 1);

  function getVerdictBadge(verdict: string | null): string {
    const map: Record<string, string> = { 'APPROVE': 'badge-approve', 'PR': 'badge-pr', 'REJECT': 'badge-reject' };
    return verdict ? map[verdict] ?? '' : '';
  }

  function getModeBadge(mode: string | null): string {
    return mode ? `badge-${mode}` : '';
  }

  function getStatusDot(run: Run): string {
    if (run.status === 'started') return 'running';
    if (run.verdict === 'APPROVE' || run.verdict === 'PR') return 'online';
    if (run.verdict === 'REJECT') return 'error';
    return 'offline';
  }

  const statuses = ['', 'started', 'finished', 'employee_done', 'reviewing'];
  const verdicts = ['', 'APPROVE', 'PR', 'REJECT'];
</script>

<div class="space-y-4 animate-fade-in">
  <div class="flex items-center justify-between">
    <h1 class="font-heading text-xl">Runs</h1>
    <span class="text-xs font-mono text-tertiary">{total} total</span>
  </div>

  <!-- Filter Bar -->
  <div class="flex items-center gap-3 flex-wrap">
    <select bind:value={statusFilter} class="input w-auto text-xs">
      <option value="">All Statuses</option>
      {#each statuses.slice(1) as s}<option value={s}>{s}</option>{/each}
    </select>
    <select bind:value={verdictFilter} class="input w-auto text-xs">
      <option value="">All Verdicts</option>
      {#each verdicts.slice(1) as v}<option value={v}>{v}</option>{/each}
    </select>
    <button onclick={() => { statusFilter = ''; verdictFilter = ''; offset = 0; }} class="btn btn-ghost btn-sm text-xs">Clear</button>
  </div>

  <!-- Run Table -->
  <div class="card overflow-hidden">
    <table class="w-full text-sm">
      <thead>
        <tr class="border-b border-border text-[10px] font-mono uppercase tracking-widest text-tertiary">
          <th class="text-left p-3 w-8"></th>
          <th class="text-left p-3">Run ID</th>
          <th class="text-left p-3">Issue</th>
          <th class="text-left p-3">Mode</th>
          <th class="text-left p-3">Model</th>
          <th class="text-left p-3">Verdict</th>
          <th class="text-right p-3">Tokens</th>
          <th class="text-right p-3">Duration</th>
          <th class="text-right p-3">Started</th>
        </tr>
      </thead>
      <tbody>
        {#if loading}
          {#each Array(5) as _}
            <tr class="border-b border-border/50">
              {#each Array(9) as __}<td class="p-3"><div class="skeleton h-4 w-full"></div></td>{/each}
            </tr>
          {/each}
        {:else}
          {#each runs as run, i (run.id)}
            <tr
              class="border-b border-border/50 hover:bg-surface-1/50 cursor-pointer transition-colors animate-slide-up stagger-{Math.min(i + 1, 6)}"
              onclick={() => navigate(`/runs/${run.run_id}`)}
              role="button"
              tabindex="0"
              onkeydown={(e) => e.key === 'Enter' && navigate(`/runs/${run.run_id}`)}
            >
              <td class="p-3"><span class="status-dot {getStatusDot(run)}"></span></td>
              <td class="p-3"><span class="font-mono text-xs text-secondary">{run.run_id?.slice(0, 16)}</span></td>
              <td class="p-3">
                {#if run.issue_number}<span class="text-xs text-primary">#{run.issue_number}</span>
                {:else}<span class="text-xs text-ghost">-</span>{/if}
              </td>
              <td class="p-3">{#if run.mode}<span class="badge {getModeBadge(run.mode)}">{run.mode}</span>{/if}</td>
              <td class="p-3"><span class="text-xs font-mono text-tertiary">{run.model?.split('-').pop() ?? '-'}</span></td>
              <td class="p-3">
                {#if run.verdict}<span class="badge {getVerdictBadge(run.verdict)}">{run.verdict}</span>
                {:else if run.status === 'started'}<span class="badge badge-running">LIVE</span>
                {:else}<span class="text-xs text-ghost">{run.status}</span>{/if}
              </td>
              <td class="p-3 text-right"><span class="font-mono text-xs text-secondary">{run.tokens_total ? formatTokens(run.tokens_total) : '-'}</span></td>
              <td class="p-3 text-right"><span class="font-mono text-xs text-secondary">{run.duration_ms ? formatDuration(run.duration_ms) : '-'}</span></td>
              <td class="p-3 text-right"><span class="font-mono text-xs text-tertiary">{timeAgo(run.started_at)}</span></td>
            </tr>
          {/each}
        {/if}
        {#if !loading && runs.length === 0}
          <tr><td colspan="9" class="p-12 text-center text-secondary">No runs found</td></tr>
        {/if}
      </tbody>
    </table>
  </div>

  <!-- Pagination -->
  {#if totalPages > 1}
    <div class="flex items-center justify-between">
      <span class="text-xs font-mono text-tertiary">Page {currentPage} of {totalPages}</span>
      <div class="flex items-center gap-2">
        <button onclick={() => offset = Math.max(0, offset - limit)} disabled={offset === 0} class="btn btn-ghost btn-sm text-xs disabled:opacity-30">Previous</button>
        <button onclick={() => offset = offset + limit} disabled={currentPage >= totalPages} class="btn btn-ghost btn-sm text-xs disabled:opacity-30">Next</button>
      </div>
    </div>
  {/if}
</div>
