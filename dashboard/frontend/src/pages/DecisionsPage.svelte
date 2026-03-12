<script lang="ts">
  import { listPlans, listProjects } from '../lib/api';
  import type { Plan, Project } from '../lib/types';
  import DecisionCard from '../components/DecisionCard.svelte';
  import GlassCard from '../components/GlassCard.svelte';

  interface Props {
    planId?: string | null;
  }

  let { planId = null }: Props = $props();

  let plans = $state<Plan[]>([]);
  let projects = $state<Project[]>([]);
  let total = $state(0);
  let loading = $state(true);
  let filterStatus = $state<string>('');

  let projectMap = $derived(new Map(projects.map(p => [p.id, p])));

  function getProjectRepo(projectId: number): string {
    const p = projectMap.get(projectId);
    return p ? p.repo.split('/').pop() ?? p.repo : `project-${projectId}`;
  }

  async function loadData() {
    loading = true;
    try {
      const [plansRes, projRes] = await Promise.allSettled([
        listPlans({ status: filterStatus || undefined, limit: 50 }),
        listProjects(),
      ]);
      if (plansRes.status === 'fulfilled') { plans = plansRes.value.plans; total = plansRes.value.total; }
      if (projRes.status === 'fulfilled') projects = projRes.value;
    } catch { /* silent */ }
    loading = false;
  }

  $effect(() => {
    filterStatus;
    loadData();
  });

  let draftPlans = $derived(plans.filter(p => p.status === 'draft'));
  let otherPlans = $derived(plans.filter(p => p.status !== 'draft'));

  // If planId is provided, auto-scroll (future enhancement)
</script>

<div class="space-y-4 animate-fade-in-up">
  <div class="flex items-center justify-between">
    <h1 class="text-lg font-semibold text-text">Decisions</h1>
    <span class="text-xs text-text-muted font-data">{total} plans</span>
  </div>

  <!-- Filter tabs -->
  <div class="flex items-center gap-1">
    {#each [
      { value: '', label: 'All' },
      { value: 'draft', label: 'Pending' },
      { value: 'approved', label: 'Approved' },
      { value: 'rejected', label: 'Rejected' },
      { value: 'implemented', label: 'Implemented' },
    ] as tab}
      <button
        onclick={() => filterStatus = tab.value}
        class="px-2.5 py-1 text-xs rounded-md cursor-pointer transition-colors
          {filterStatus === tab.value ? 'bg-surface-2 text-text font-medium' : 'text-text-dim hover:text-text hover:bg-white/[0.03]'}"
      >
        {tab.label}
        {#if tab.value === 'draft' && draftPlans.length > 0}
          <span class="ml-1 text-[10px] font-bold text-warning">{draftPlans.length}</span>
        {/if}
      </button>
    {/each}
  </div>

  <!-- Pending decisions (highlighted) -->
  {#if draftPlans.length > 0 && filterStatus !== 'approved' && filterStatus !== 'rejected' && filterStatus !== 'implemented'}
    <div class="space-y-2">
      <p class="text-xs text-warning font-medium">Needs your decision ({draftPlans.length})</p>
      {#each draftPlans as plan (plan.id)}
        <DecisionCard
          {plan}
          projectRepo={getProjectRepo(plan.project_id)}
          onAction={loadData}
        />
      {/each}
    </div>
  {/if}

  <!-- Other plans -->
  {#if loading && plans.length === 0}
    <div class="text-center py-8 text-text-muted text-sm">Loading...</div>
  {:else if otherPlans.length > 0 || (filterStatus && plans.length > 0)}
    <div class="space-y-2">
      {#if filterStatus !== 'draft' && otherPlans.length > 0}
        {#if !filterStatus}
          <p class="text-xs text-text-dim font-medium">History</p>
        {/if}
        {#each (filterStatus ? plans : otherPlans) as plan (plan.id)}
          <DecisionCard
            {plan}
            projectRepo={getProjectRepo(plan.project_id)}
            onAction={loadData}
          />
        {/each}
      {/if}
    </div>
  {:else if plans.length === 0 && !loading}
    <GlassCard class="p-6 text-center">
      <p class="text-sm text-text-muted">No plans yet. Plans are created when agents analyze issues.</p>
    </GlassCard>
  {/if}
</div>
