<script lang="ts">
  import type { QueueItem, QueueStats } from '../lib/types';
  import { listQueue, getQueueStats, updateQueueItem, deleteQueueItem } from '../lib/api';
  import { toastError, toastSuccess } from '../lib/toast.svelte';
  import StatusBadge from '../components/StatusBadge.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import TimeAgo from '../components/TimeAgo.svelte';
  import GlassCard from '../components/GlassCard.svelte';

  let items = $state<QueueItem[]>([]);
  let total = $state(0);
  let stats = $state<QueueStats | null>(null);
  let loading = $state(true);
  let offset = $state(0);
  const limit = 20;

  let filterState = $state('');
  let filterProject = $state('');
  let expandedId = $state<number | null>(null);

  const stateLabels: [string, string][] = [
    ['pending', 'Pending'],
    ['assigned', 'Assigned'],
    ['in_progress', 'In Progress'],
    ['review', 'Review'],
    ['completed', 'Completed'],
    ['paused', 'Paused'],
    ['failed', 'Failed'],
  ];

  async function load() {
    loading = true;
    try {
      const [qRes, sRes] = await Promise.all([
        listQueue({
          limit,
          offset,
          state: filterState || undefined,
          project_repo: filterProject || undefined,
        }),
        getQueueStats(),
      ]);
      items = qRes.items;
      total = qRes.total;
      stats = sRes;
    } catch (e: any) {
      toastError(e.message);
    } finally {
      loading = false;
    }
  }

  function applyFilters() {
    offset = 0;
    load();
  }

  function prevPage() {
    offset = Math.max(0, offset - limit);
    load();
  }

  function nextPage() {
    if (offset + limit < total) {
      offset += limit;
      load();
    }
  }

  async function cancelItem(id: number) {
    try {
      await deleteQueueItem(id);
      toastSuccess('Item cancelled');
      await load();
    } catch (e: any) {
      toastError(e.message);
    }
  }

  async function requeueItem(id: number) {
    try {
      await updateQueueItem(id, { state: 'pending' });
      toastSuccess('Item re-queued');
      await load();
    } catch (e: any) {
      toastError(e.message);
    }
  }

  async function pauseItem(id: number) {
    try {
      await updateQueueItem(id, { state: 'paused' });
      toastSuccess('Item paused');
      await load();
    } catch (e: any) {
      toastError(e.message);
    }
  }

  function toggleExpand(id: number) {
    expandedId = expandedId === id ? null : id;
  }

  function tryParseJson(s: string | null): any {
    if (!s) return null;
    try { return JSON.parse(s); } catch { return s; }
  }

  // Unique projects for filter dropdown
  function toggleStateFilter(key: string) {
    filterState = filterState === key ? '' : key;
    applyFilters();
  }

  let uniqueProjects = $derived([...new Set(items.map(i => i.project_repo))].sort());

  $effect(() => { load(); });

  $effect(() => {
    const interval = setInterval(() => { load(); }, 15000);
    return () => clearInterval(interval);
  });
</script>

<div class="space-y-6 animate-fade-in-up">
  <h1 class="text-2xl font-bold">Task Queue</h1>

  <!-- Stats bar -->
  {#if stats}
    <div class="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
      {#each stateLabels as [key, label]}
        {@const count = stats.by_state[key] ?? 0}
        <button
          onclick={() => toggleStateFilter(key)}
          class="glass-hud rounded-lg px-3 py-2 text-center cursor-pointer transition-all hover:bg-white/[0.04]
            {filterState === key ? 'ring-1 ring-accent-cyan/50' : ''}"
        >
          <div class="text-lg font-bold font-data">{count}</div>
          <div class="text-xs text-text-dim">{label}</div>
        </button>
      {/each}
    </div>
  {/if}

  <!-- Filters -->
  <div class="flex flex-wrap gap-3 items-end">
    <div>
      <label class="block text-xs text-text-dim mb-1">State</label>
      <select bind:value={filterState} onchange={applyFilters} class="glass px-3 py-1.5 rounded text-sm bg-surface text-text cursor-pointer">
        <option value="">All</option>
        {#each stateLabels as [key, label]}
          <option value={key}>{label}</option>
        {/each}
      </select>
    </div>
    <div>
      <label class="block text-xs text-text-dim mb-1">Project</label>
      <select bind:value={filterProject} onchange={applyFilters} class="glass px-3 py-1.5 rounded text-sm bg-surface text-text cursor-pointer">
        <option value="">All</option>
        {#each uniqueProjects as proj}
          <option value={proj}>{proj}</option>
        {/each}
      </select>
    </div>
  </div>

  {#if loading}
    <div class="flex justify-center py-12"><LoadingSpinner /></div>
  {:else if items.length === 0}
    <EmptyState message="No queue items match the filters" />
  {:else}
    <GlassCard class="overflow-hidden overflow-x-auto">
      <table class="w-full text-sm min-w-[640px]">
        <thead>
          <tr class="border-b border-border/50 text-left text-text-dim">
            <th class="px-3 md:px-5 py-3 font-medium">ID</th>
            <th class="px-3 md:px-5 py-3 font-medium">Project</th>
            <th class="px-3 md:px-5 py-3 font-medium">Issue</th>
            <th class="px-3 md:px-5 py-3 font-medium">State</th>
            <th class="px-3 md:px-5 py-3 font-medium">Priority</th>
            <th class="px-3 md:px-5 py-3 font-medium hidden md:table-cell">Employee</th>
            <th class="px-3 md:px-5 py-3 font-medium hidden md:table-cell">Retries</th>
            <th class="px-3 md:px-5 py-3 font-medium hidden sm:table-cell">Updated</th>
            <th class="px-3 md:px-5 py-3 font-medium">Actions</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-border/30">
          {#each items as item}
            <tr
              class="hover:bg-white/[0.02] transition-colors cursor-pointer"
              onclick={() => toggleExpand(item.id)}
            >
              <td class="px-3 md:px-5 py-3 font-data text-xs">#{item.id}</td>
              <td class="px-3 md:px-5 py-3 text-text-dim truncate max-w-[140px]">{item.project_repo}</td>
              <td class="px-3 md:px-5 py-3">
                {#if item.issue_number}
                  <span class="text-text-dim">#{item.issue_number}</span>
                  {#if item.issue_title}
                    <span class="text-xs text-text-dim/60 ml-1 truncate inline-block max-w-[120px] align-bottom">{item.issue_title}</span>
                  {/if}
                {:else}
                  <span class="text-text-dim/40">-</span>
                {/if}
              </td>
              <td class="px-3 md:px-5 py-3"><StatusBadge value={item.state} variant="status" /></td>
              <td class="px-3 md:px-5 py-3 font-data">{item.priority}</td>
              <td class="px-3 md:px-5 py-3 hidden md:table-cell font-data">{item.assigned_to ?? '-'}</td>
              <td class="px-3 md:px-5 py-3 hidden md:table-cell font-data">{item.retry_count}/{item.max_retries}</td>
              <td class="px-3 md:px-5 py-3 hidden sm:table-cell"><TimeAgo date={item.updated_at} /></td>
              <td class="px-3 md:px-5 py-3">
                <div class="flex gap-1" onclick={(e) => e.stopPropagation()}>
                  {#if item.state === 'pending' || item.state === 'paused'}
                    <button onclick={() => cancelItem(item.id)} class="px-2 py-0.5 text-xs glass rounded cursor-pointer hover:bg-reject/10 hover:text-reject transition-colors">Cancel</button>
                  {/if}
                  {#if item.state === 'failed' || item.state === 'rejected'}
                    <button onclick={() => requeueItem(item.id)} class="px-2 py-0.5 text-xs glass rounded cursor-pointer hover:bg-info/10 hover:text-info transition-colors">Re-queue</button>
                  {/if}
                  {#if item.state === 'assigned' || item.state === 'in_progress'}
                    <button onclick={() => pauseItem(item.id)} class="px-2 py-0.5 text-xs glass rounded cursor-pointer hover:bg-warning/10 hover:text-warning transition-colors">Pause</button>
                  {/if}
                </div>
              </td>
            </tr>
            {#if expandedId === item.id}
              <tr>
                <td colspan="9" class="px-5 py-4 bg-surface-2/30">
                  <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
                    <div>
                      <h4 class="font-medium text-text-dim mb-1">Details</h4>
                      <div class="space-y-1">
                        <div><span class="text-text-dim/60">Run ID:</span> <span class="font-data">{item.run_id ?? 'none'}</span></div>
                        <div><span class="text-text-dim/60">Created:</span> <TimeAgo date={item.created_at} /></div>
                        {#if item.assigned_at}<div><span class="text-text-dim/60">Assigned:</span> <TimeAgo date={item.assigned_at} /></div>{/if}
                        {#if item.started_at}<div><span class="text-text-dim/60">Started:</span> <TimeAgo date={item.started_at} /></div>{/if}
                        {#if item.completed_at}<div><span class="text-text-dim/60">Completed:</span> <TimeAgo date={item.completed_at} /></div>{/if}
                        {#if item.error_message}<div class="text-reject"><span class="text-text-dim/60">Error:</span> {item.error_message}</div>{/if}
                      </div>
                    </div>
                    <div>
                      {#if item.employee_report}
                        <h4 class="font-medium text-text-dim mb-1">Employee Report</h4>
                        <pre class="text-xs bg-surface/50 rounded p-2 overflow-auto max-h-40 whitespace-pre-wrap">{JSON.stringify(tryParseJson(item.employee_report), null, 2)}</pre>
                      {/if}
                      {#if item.manager_feedback}
                        <h4 class="font-medium text-text-dim mb-1 mt-2">Manager Feedback</h4>
                        <pre class="text-xs bg-surface/50 rounded p-2 overflow-auto max-h-40 whitespace-pre-wrap">{JSON.stringify(tryParseJson(item.manager_feedback), null, 2)}</pre>
                      {/if}
                      {#if item.context}
                        <h4 class="font-medium text-text-dim mb-1 mt-2">Context</h4>
                        <pre class="text-xs bg-surface/50 rounded p-2 overflow-auto max-h-40 whitespace-pre-wrap">{JSON.stringify(tryParseJson(item.context), null, 2)}</pre>
                      {/if}
                    </div>
                  </div>
                </td>
              </tr>
            {/if}
          {/each}
        </tbody>
      </table>
    </GlassCard>

    <!-- Pagination -->
    <div class="flex items-center justify-between text-xs md:text-sm text-text-dim">
      <span>{offset + 1}-{Math.min(offset + limit, total)} of {total}</span>
      <div class="flex gap-2">
        <button onclick={prevPage} disabled={offset === 0} class="px-3 py-1 glass rounded disabled:opacity-50 cursor-pointer hover:bg-white/[0.03] transition-colors">Prev</button>
        <button onclick={nextPage} disabled={offset + limit >= total} class="px-3 py-1 glass rounded disabled:opacity-50 cursor-pointer hover:bg-white/[0.03] transition-colors">Next</button>
      </div>
    </div>
  {/if}
</div>
