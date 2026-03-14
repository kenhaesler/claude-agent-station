<script lang="ts">
  import { listRuns, listQueue, listProjects } from '../lib/api';
  import type { Run, QueueItem, Project } from '../lib/types';
  import RunCard from '../components/RunCard.svelte';
  import GlassCard from '../components/GlassCard.svelte';
  import StatusBadge from '../components/StatusBadge.svelte';
  import IntelligenceChip from '../components/IntelligenceChip.svelte';
  import RunFilters from '../components/RunFilters.svelte';
  import { timeAgo } from '../lib/format';

  interface Props {
    runId?: string | null;
  }

  let { runId = null }: Props = $props();

  let runs = $state<Run[]>([]);
  let queue = $state<QueueItem[]>([]);
  let projects = $state<Project[]>([]);
  let total = $state(0);
  let loading = $state(true);
  let offset = $state(0);
  let limit = 25;

  // Filters
  let filterProjectId = $state<number | undefined>(undefined);
  let filterStatus = $state<string | undefined>(undefined);
  let filterVerdict = $state<string | undefined>(undefined);

  let projectMap = $derived(new Map(projects.map(p => [p.id, p])));

  function getProjectRepo(projectId: number | null): string {
    if (!projectId) return 'unknown';
    const p = projectMap.get(projectId);
    return p ? p.repo.split('/').pop() ?? p.repo : `project-${projectId}`;
  }

  async function loadData() {
    loading = true;
    try {
      const [runsRes, queueRes, projRes] = await Promise.allSettled([
        listRuns({
          limit,
          offset,
          project_id: filterProjectId,
          status: filterStatus,
          verdict: filterVerdict,
        }),
        listQueue({ limit: 10 }),
        listProjects(),
      ]);
      if (runsRes.status === 'fulfilled') { runs = runsRes.value.runs; total = runsRes.value.total; }
      if (queueRes.status === 'fulfilled') queue = queueRes.value.items;
      if (projRes.status === 'fulfilled') projects = projRes.value;
    } catch { /* silent */ }
    loading = false;
  }

  $effect(() => {
    // Re-run when filters change
    filterProjectId;
    filterStatus;
    filterVerdict;
    offset;
    loadData();
  });

  // Auto-refresh
  $effect(() => {
    const interval = setInterval(loadData, 15000);
    return () => clearInterval(interval);
  });

  function handleFilterChange(e: { projectId?: number; status?: string; verdict?: string }) {
    filterProjectId = e.projectId;
    filterStatus = e.status;
    filterVerdict = e.verdict;
    offset = 0;
  }

  let activeQueue = $derived(queue.filter(q => q.state === 'assigned' || q.state === 'in_progress'));
  let pendingQueue = $derived(queue.filter(q => q.state === 'pending'));
</script>

<div class="space-y-4 animate-fade-in-up">
  <div class="flex items-center justify-between">
    <h1 class="text-lg font-semibold text-text">Work Stream</h1>
    <span class="text-xs text-text-muted font-data">{total} runs</span>
  </div>

  <!-- Queue summary (if items in queue) -->
  {#if activeQueue.length > 0 || pendingQueue.length > 0}
    <GlassCard class="p-3" glow="blue">
      <div class="flex items-center gap-2 mb-2">
        <span class="text-xs font-semibold text-text">Queue</span>
        <span class="text-[10px] text-text-muted font-data">{activeQueue.length} active, {pendingQueue.length} pending</span>
      </div>
      <div class="space-y-1">
        {#each [...activeQueue, ...pendingQueue].slice(0, 5) as item}
          <div class="flex items-center justify-between text-xs py-0.5">
            <div class="flex items-center gap-2 min-w-0">
              <StatusBadge value={item.state} />
              <span class="text-text-dim truncate">{item.project_repo?.split('/').pop() ?? 'unknown'}</span>
              {#if item.issue_number}
                <span class="text-text-muted font-data">#{item.issue_number}</span>
              {/if}
            </div>
            {#if item.mode}
              <IntelligenceChip type="success-rate" mode={item.mode} />
            {/if}
            <span class="text-text-muted text-[10px]">{timeAgo(item.created_at)}</span>
          </div>
        {/each}
      </div>
    </GlassCard>
  {/if}

  <!-- Filters -->
  <RunFilters {projects} onChange={handleFilterChange} />

  <!-- Run list -->
  {#if loading && runs.length === 0}
    <div class="text-center py-8 text-text-muted text-sm">Loading...</div>
  {:else if runs.length === 0}
    <div class="text-center py-8 text-text-muted text-sm">No runs found.</div>
  {:else}
    <div class="space-y-2">
      {#each runs as run (run.run_id)}
        <RunCard
          {run}
          expanded={runId === run.run_id}
          projectRepo={getProjectRepo(run.project_id)}
        />
      {/each}
    </div>

    <!-- Pagination -->
    {#if total > limit}
      <div class="flex items-center justify-center gap-3 pt-2">
        <button
          onclick={() => offset = Math.max(0, offset - limit)}
          disabled={offset === 0}
          class="px-3 py-1 text-xs rounded border border-border text-text-dim hover:bg-surface-2 cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
        >
          Previous
        </button>
        <span class="text-xs text-text-muted font-data">{offset + 1}–{Math.min(offset + limit, total)} of {total}</span>
        <button
          onclick={() => offset += limit}
          disabled={offset + limit >= total}
          class="px-3 py-1 text-xs rounded border border-border text-text-dim hover:bg-surface-2 cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
        >
          Next
        </button>
      </div>
    {/if}
  {/if}
</div>
