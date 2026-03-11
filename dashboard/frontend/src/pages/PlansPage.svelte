<script lang="ts">
  import { listPlans, listProjects, approvePlan, rejectPlan, deletePlan } from '../lib/api';
  import { navigate } from '../lib/router.svelte';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import type { Plan, Project } from '../lib/types';
  import GlassCard from '../components/GlassCard.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';
  import EmptyState from '../components/EmptyState.svelte';

  let plans = $state<Plan[]>([]);
  let projects = $state<Project[]>([]);
  let total = $state(0);
  let loading = $state(true);
  let filterStatus = $state('');
  let filterProject = $state(0);

  async function load() {
    loading = true;
    try {
      const [planRes, projRes] = await Promise.all([
        listPlans({
          limit: 50,
          status: filterStatus || undefined,
          project_id: filterProject || undefined,
        }),
        listProjects(),
      ]);
      plans = planRes.plans;
      total = planRes.total;
      projects = projRes;
    } catch (e: any) {
      toastError(`Failed to load plans: ${e.message}`);
    } finally {
      loading = false;
    }
  }

  function projectName(id: number): string {
    const p = projects.find(p => p.id === id);
    return p ? p.repo : `Project #${id}`;
  }

  function scopeColor(scope: string | null): string {
    switch (scope) {
      case 'small': return 'text-green-400';
      case 'medium': return 'text-yellow-400';
      case 'large': return 'text-red-400';
      default: return 'text-text-dim';
    }
  }

  function statusColor(status: string): string {
    switch (status) {
      case 'draft': return 'bg-blue-500/20 text-blue-400 border-blue-500/30';
      case 'approved': return 'bg-green-500/20 text-green-400 border-green-500/30';
      case 'implementing': return 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30';
      case 'completed': return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30';
      case 'rejected': return 'bg-red-500/20 text-red-400 border-red-500/30';
      default: return 'bg-gray-500/20 text-text-dim border-gray-500/30';
    }
  }

  async function handleApprove(plan: Plan) {
    try {
      await approvePlan(plan.id);
      toastSuccess(`Plan approved: ${plan.title}`);
      await load();
    } catch (e: any) {
      toastError(`Failed to approve: ${e.message}`);
    }
  }

  async function handleReject(plan: Plan) {
    try {
      await rejectPlan(plan.id);
      toastSuccess(`Plan rejected`);
      await load();
    } catch (e: any) {
      toastError(`Failed to reject: ${e.message}`);
    }
  }

  async function handleDelete(plan: Plan) {
    if (!confirm(`Delete plan "${plan.title}"?`)) return;
    try {
      await deletePlan(plan.id);
      toastSuccess('Plan deleted');
      await load();
    } catch (e: any) {
      toastError(`Failed to delete: ${e.message}`);
    }
  }

  function parseSteps(stepsJson: string | null): string[] {
    if (!stepsJson) return [];
    try { return JSON.parse(stepsJson); } catch { return []; }
  }

  function parseFiles(filesJson: string | null): string[] {
    if (!filesJson) return [];
    try { return JSON.parse(filesJson); } catch { return []; }
  }

  $effect(() => {
    load();
  });
</script>

<div class="space-y-6">
  <div class="flex items-center justify-between">
    <h1 class="text-2xl font-bold text-text">Plans</h1>
    <span class="text-sm text-text-dim">{total} total</span>
  </div>

  <!-- Filters -->
  <div class="flex gap-3 flex-wrap">
    <select
      bind:value={filterStatus}
      onchange={load}
      class="bg-surface-2 border border-border rounded-lg px-3 py-1.5 text-sm text-text"
    >
      <option value="">All statuses</option>
      <option value="draft">Draft</option>
      <option value="approved">Approved</option>
      <option value="implementing">Implementing</option>
      <option value="completed">Completed</option>
      <option value="rejected">Rejected</option>
    </select>
    <select
      bind:value={filterProject}
      onchange={load}
      class="bg-surface-2 border border-border rounded-lg px-3 py-1.5 text-sm text-text"
    >
      <option value={0}>All projects</option>
      {#each projects as proj}
        <option value={proj.id}>{proj.repo}</option>
      {/each}
    </select>
  </div>

  {#if loading}
    <LoadingSpinner />
  {:else if plans.length === 0}
    <EmptyState>No plans found. Set a project to "plan" mode to generate implementation plans.</EmptyState>
  {:else}
    <div class="space-y-4">
      {#each plans as plan}
        <GlassCard>
          <div class="p-4 space-y-3">
            <!-- Header -->
            <div class="flex items-start justify-between gap-3">
              <div class="flex-1 min-w-0">
                <button
                  onclick={() => navigate(`/plans/${plan.id}`)}
                  class="text-left cursor-pointer hover:text-accent-blue transition-colors"
                >
                  <h3 class="text-lg font-semibold text-text truncate">{plan.title}</h3>
                </button>
                <div class="flex items-center gap-2 mt-1 text-sm text-text-dim">
                  <span>{projectName(plan.project_id)}</span>
                  {#if plan.issue_number}
                    <span>#{plan.issue_number}</span>
                  {/if}
                  {#if plan.estimated_scope}
                    <span class={scopeColor(plan.estimated_scope)}>{plan.estimated_scope}</span>
                  {/if}
                </div>
              </div>
              <span class="shrink-0 px-2 py-0.5 text-xs font-medium rounded border {statusColor(plan.status)}">
                {plan.status}
              </span>
            </div>

            <!-- Issue title -->
            {#if plan.issue_title}
              <p class="text-sm text-text-dim">Issue: {plan.issue_title}</p>
            {/if}

            <!-- Steps preview -->
            {#if parseSteps(plan.steps).length > 0}
              <div class="text-sm text-text-dim">
                <span class="font-medium text-text">{parseSteps(plan.steps).length} steps</span>
                <span class="mx-1">-</span>
                <span class="truncate">{parseSteps(plan.steps)[0]}</span>
              </div>
            {/if}

            <!-- Files affected -->
            {#if parseFiles(plan.files_affected).length > 0}
              <div class="flex flex-wrap gap-1">
                {#each parseFiles(plan.files_affected).slice(0, 5) as file}
                  <span class="text-xs bg-surface-2 px-2 py-0.5 rounded font-mono text-text-dim">{file}</span>
                {/each}
                {#if parseFiles(plan.files_affected).length > 5}
                  <span class="text-xs text-text-dim">+{parseFiles(plan.files_affected).length - 5} more</span>
                {/if}
              </div>
            {/if}

            <!-- Actions -->
            <div class="flex items-center gap-2 pt-2 border-t border-border/30">
              <button
                onclick={() => navigate(`/plans/${plan.id}`)}
                class="px-3 py-1 text-xs text-text-dim hover:text-text bg-surface-2 rounded cursor-pointer"
              >
                View Details
              </button>
              {#if plan.status === 'draft' || plan.status === 'rejected'}
                <button
                  onclick={() => handleApprove(plan)}
                  class="px-3 py-1 text-xs text-green-400 hover:text-green-300 bg-green-500/10 rounded cursor-pointer"
                >
                  Approve
                </button>
              {/if}
              {#if plan.status === 'draft' || plan.status === 'approved'}
                <button
                  onclick={() => handleReject(plan)}
                  class="px-3 py-1 text-xs text-red-400 hover:text-red-300 bg-red-500/10 rounded cursor-pointer"
                >
                  Reject
                </button>
              {/if}
              <button
                onclick={() => handleDelete(plan)}
                class="ml-auto px-3 py-1 text-xs text-red-400/60 hover:text-red-400 cursor-pointer"
              >
                Delete
              </button>
            </div>
          </div>
        </GlassCard>
      {/each}
    </div>
  {/if}
</div>
