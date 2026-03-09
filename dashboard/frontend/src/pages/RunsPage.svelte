<script lang="ts">
  import type { Run, Project } from '../lib/types';
  import { listRuns, listProjects } from '../lib/api';
  import { formatDuration, formatCost } from '../lib/format';
  import { toastError } from '../lib/toast.svelte';
  import StatusBadge from '../components/StatusBadge.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import TimeAgo from '../components/TimeAgo.svelte';
  import RunFilters from '../components/RunFilters.svelte';
  import GlassCard from '../components/GlassCard.svelte';

  let runs = $state<Run[]>([]);
  let total = $state(0);
  let projects = $state<Project[]>([]);
  let loading = $state(true);
  let offset = $state(0);
  const limit = 20;

  let verdict = $state('');
  let status = $state('');
  let projectId = $state('');

  async function load() {
    loading = true;
    try {
      const [runsRes, projectsRes] = await Promise.all([
        listRuns({
          limit,
          offset,
          verdict: verdict || undefined,
          status: status || undefined,
          project_id: projectId ? Number(projectId) : undefined,
        }),
        listProjects(),
      ]);
      runs = runsRes.runs;
      total = runsRes.total;
      projects = projectsRes;
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

  $effect(() => { load(); });

  let projectMap = $derived(Object.fromEntries(projects.map(p => [p.id, p.repo])));
</script>

<div class="space-y-6 animate-fade-in-up">
  <h1 class="text-2xl font-bold">Runs</h1>

  <RunFilters bind:verdict bind:status bind:projectId {projects} onchange={applyFilters} />

  {#if loading}
    <div class="flex justify-center py-12"><LoadingSpinner /></div>
  {:else if runs.length === 0}
    <EmptyState message="No runs match the filters" />
  {:else}
    <GlassCard class="overflow-hidden overflow-x-auto">
      <table class="w-full text-sm min-w-[480px]">
        <thead>
          <tr class="border-b border-border/50 text-left text-text-dim">
            <th class="px-3 md:px-5 py-3 font-medium">Run ID</th>
            <th class="px-3 md:px-5 py-3 font-medium hidden sm:table-cell">Project</th>
            <th class="px-3 md:px-5 py-3 font-medium">Verdict</th>
            <th class="px-3 md:px-5 py-3 font-medium hidden md:table-cell">Mode</th>
            <th class="px-3 md:px-5 py-3 font-medium">Duration</th>
            <th class="px-3 md:px-5 py-3 font-medium">Cost</th>
            <th class="px-3 md:px-5 py-3 font-medium hidden sm:table-cell">Started</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-border/30">
          {#each runs as run}
            <tr class="hover:bg-white/[0.02] transition-colors cursor-pointer" onclick={() => window.location.hash = `/runs/${run.run_id}`}>
              <td class="px-3 md:px-5 py-3 font-data text-xs truncate max-w-[100px]">{run.run_id.slice(-8)}</td>
              <td class="px-3 md:px-5 py-3 text-text-dim hidden sm:table-cell truncate max-w-[120px]">{run.project_id ? (projectMap[run.project_id] ?? `#${run.project_id}`) : '-'}</td>
              <td class="px-3 md:px-5 py-3"><StatusBadge value={run.verdict} /></td>
              <td class="px-3 md:px-5 py-3 hidden md:table-cell"><StatusBadge value={run.mode} variant="mode" /></td>
              <td class="px-3 md:px-5 py-3 text-text-dim">{formatDuration(run.duration_ms)}</td>
              <td class="px-3 md:px-5 py-3 text-text-dim font-data">{formatCost(run.cost_usd)}</td>
              <td class="px-3 md:px-5 py-3 hidden sm:table-cell"><TimeAgo date={run.started_at} /></td>
            </tr>
          {/each}
        </tbody>
      </table>
    </GlassCard>

    <!-- Pagination -->
    <div class="flex items-center justify-between text-xs md:text-sm text-text-dim">
      <span>{offset + 1}-{Math.min(offset + limit, total)} of {total}</span>
      <div class="flex gap-2">
        <button onclick={prevPage} disabled={offset === 0} class="px-3 py-1 glass rounded disabled:opacity-30 cursor-pointer hover:bg-white/[0.03] transition-colors">Prev</button>
        <button onclick={nextPage} disabled={offset + limit >= total} class="px-3 py-1 glass rounded disabled:opacity-30 cursor-pointer hover:bg-white/[0.03] transition-colors">Next</button>
      </div>
    </div>
  {/if}
</div>
