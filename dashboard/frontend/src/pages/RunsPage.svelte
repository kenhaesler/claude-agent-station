<script lang="ts">
  import { listRuns } from '../lib/api';
  import { navigate } from '../lib/router.svelte';
  import { formatTokens, formatDuration, timeAgo } from '../lib/format';
  import type { Run } from '../lib/types';
  import SkeletonLoader from '../components/data-display/SkeletonLoader.svelte';
  import EmptyState from '../components/data-display/EmptyState.svelte';

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

  function getRowTint(run: Run): string {
    if (run.verdict === 'APPROVE' || run.verdict === 'PR') return 'background: rgba(46,125,50,0.03);';
    if (run.verdict === 'REJECT') return 'background: rgba(208,96,80,0.03);';
    if (run.status === 'started') return 'background: rgba(46,125,50,0.02);';
    return '';
  }

  const verdicts = ['', 'APPROVE', 'PR', 'REJECT'];
  const statuses = ['', 'started', 'finished', 'employee_done', 'reviewing'];
</script>

<div class="space-y-4 animate-fade-in">
  <!-- Header -->
  <div class="flex items-center justify-between">
    <div class="flex items-center gap-3">
      <h1 class="font-heading text-xl">Runs</h1>
      <span class="text-sm font-mono text-tertiary">{total} total</span>
    </div>
  </div>

  <!-- Filter Bar (compact inline) -->
  <div class="flex items-center gap-2 flex-wrap">
    <select bind:value={statusFilter} class="input text-xs py-1.5 px-3" style="width: auto; min-width: 120px;">
      <option value="">All Statuses</option>
      {#each statuses.slice(1) as s}<option value={s}>{s}</option>{/each}
    </select>

    <!-- Verdict pills -->
    <div class="flex items-center gap-1">
      <button
        class="badge cursor-pointer transition-opacity {verdictFilter === '' ? 'opacity-100' : 'opacity-40 hover:opacity-70'}"
        style="background: rgba(240,220,200,0.15); color: var(--color-secondary);"
        onclick={() => { verdictFilter = ''; offset = 0; }}
      >All</button>
      {#each verdicts.slice(1) as v}
        <button
          class="badge {getVerdictBadge(v)} cursor-pointer transition-opacity {verdictFilter === v ? 'opacity-100 ring-1 ring-[rgba(176,96,48,0.3)]' : 'opacity-50 hover:opacity-80'}"
          onclick={() => { verdictFilter = verdictFilter === v ? '' : v; offset = 0; }}
        >{v}</button>
      {/each}
    </div>

    {#if statusFilter || verdictFilter}
      <button onclick={() => { statusFilter = ''; verdictFilter = ''; offset = 0; }} class="text-xs text-tertiary hover:text-secondary transition-colors cursor-pointer font-mono">
        Clear filters
      </button>
    {/if}
  </div>

  <!-- Run Table -->
  <div class="card overflow-hidden">
    <table class="w-full text-sm">
      <thead>
        <tr class="text-[10px] font-mono uppercase tracking-widest text-tertiary" style="border-bottom: 1px solid rgba(240,220,200,0.20);">
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
            <tr style="border-bottom: 1px solid rgba(240,220,200,0.10);">
              {#each Array(9) as __}<td class="p-3"><div class="skeleton h-4 w-full"></div></td>{/each}
            </tr>
          {/each}
        {:else}
          {#each runs as run, i (run.id)}
            <tr
              class="hover:bg-surface-1/50 cursor-pointer transition-colors animate-slide-up stagger-{Math.min(i + 1, 6)}"
              style="{getRowTint(run)} border-bottom: 1px solid rgba(240,220,200,0.10);"
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
              <td class="p-3">{#if run.mode}<span class="badge {getModeBadge(run.mode)}">{run.mode}</span>{:else}<span class="text-xs text-ghost">-</span>{/if}</td>
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
          <tr><td colspan="9">
            <EmptyState title="No runs found" description="Try adjusting your filters" icon="▷" />
          </td></tr>
        {/if}
      </tbody>
    </table>
  </div>

  <!-- Pagination -->
  <div class="flex items-center justify-between">
    <span class="text-xs font-mono text-tertiary">
      {#if total > 0}
        Showing {offset + 1}-{Math.min(offset + limit, total)} of {total}
      {:else}
        No results
      {/if}
    </span>
    {#if totalPages > 1}
      <div class="flex items-center gap-2">
        <button onclick={() => offset = Math.max(0, offset - limit)} disabled={offset === 0} class="btn btn-ghost btn-sm text-xs disabled:opacity-30">Previous</button>
        <button onclick={() => offset = offset + limit} disabled={currentPage >= totalPages} class="btn btn-ghost btn-sm text-xs disabled:opacity-30">Next</button>
      </div>
    {/if}
  </div>
</div>
