<script lang="ts">
  import { getPlan, listProjects, approvePlan, rejectPlan, implementPlan } from '../lib/api';
  import { navigate } from '../lib/router.svelte';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import type { Plan, Project } from '../lib/types';
  import GlassCard from '../components/GlassCard.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';

  interface Props {
    planId: string;
  }

  let { planId }: Props = $props();

  let plan = $state<Plan | null>(null);
  let projects = $state<Project[]>([]);
  let loading = $state(true);

  function projectName(id: number): string {
    const p = projects.find(p => p.id === id);
    return p ? p.repo : `Project #${id}`;
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

  function scopeLabel(scope: string | null): string {
    switch (scope) {
      case 'small': return 'Small (1-2 files)';
      case 'medium': return 'Medium (3-5 files)';
      case 'large': return 'Large (6+ files)';
      default: return scope ?? 'Unknown';
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

  function formatDate(d: string | null): string {
    if (!d) return '-';
    return new Date(d).toLocaleString();
  }

  async function load() {
    loading = true;
    try {
      const id = parseInt(planId);
      const [p, projs] = await Promise.all([getPlan(id), listProjects()]);
      plan = p;
      projects = projs;
    } catch (e: any) {
      toastError(`Failed to load plan: ${e.message}`);
    } finally {
      loading = false;
    }
  }

  async function handleApprove() {
    if (!plan) return;
    try {
      plan = await approvePlan(plan.id);
      toastSuccess('Plan approved');
    } catch (e: any) {
      toastError(`Failed: ${e.message}`);
    }
  }

  async function handleReject() {
    if (!plan) return;
    try {
      plan = await rejectPlan(plan.id);
      toastSuccess('Plan rejected');
    } catch (e: any) {
      toastError(`Failed: ${e.message}`);
    }
  }

  async function handleImplement() {
    if (!plan) return;
    if (!confirm('This will trigger the agent to implement this plan. Continue?')) return;
    try {
      plan = await implementPlan(plan.id);
      toastSuccess('Implementation triggered! The agent will work on this plan.');
    } catch (e: any) {
      toastError(`Failed: ${e.message}`);
    }
  }

  $effect(() => {
    load();
  });
</script>

{#if loading}
  <LoadingSpinner />
{:else if !plan}
  <div class="text-text-dim text-center py-12">Plan not found.</div>
{:else}
  <div class="space-y-6">
    <!-- Back button + header -->
    <div class="flex items-center gap-3">
      <button onclick={() => navigate('/plans')} class="text-text-dim hover:text-text text-sm cursor-pointer">
        &larr; Back to Plans
      </button>
    </div>

    <!-- Title + status -->
    <div class="flex items-start justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-text">{plan.title}</h1>
        <div class="flex items-center gap-3 mt-2 text-sm text-text-dim">
          <span>{projectName(plan.project_id)}</span>
          {#if plan.issue_number}
            <span>Issue #{plan.issue_number}{plan.issue_title ? `: ${plan.issue_title}` : ''}</span>
          {/if}
        </div>
      </div>
      <span class="px-3 py-1 text-sm font-medium rounded border {statusColor(plan.status)}">
        {plan.status}
      </span>
    </div>

    <!-- Actions -->
    <div class="flex gap-2">
      {#if plan.status === 'approved' || plan.status === 'draft'}
        <button onclick={handleImplement} class="px-4 py-2 text-sm bg-accent-blue/20 text-accent-blue hover:bg-accent-blue/30 rounded-lg cursor-pointer font-medium">
          Implement Plan
        </button>
      {/if}
      {#if plan.status === 'draft' || plan.status === 'rejected'}
        <button onclick={handleApprove} class="px-4 py-2 text-sm bg-green-500/20 text-green-400 hover:bg-green-500/30 rounded-lg cursor-pointer">
          Approve Plan
        </button>
      {/if}
      {#if plan.status === 'draft' || plan.status === 'approved'}
        <button onclick={handleReject} class="px-4 py-2 text-sm bg-red-500/20 text-red-400 hover:bg-red-500/30 rounded-lg cursor-pointer">
          Reject Plan
        </button>
      {/if}
    </div>

    <!-- Metadata -->
    <GlassCard>
      <div class="p-4">
        <h2 class="text-sm font-semibold text-text mb-3">Details</h2>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <div>
            <span class="text-text-dim">Scope</span>
            <p class="text-text">{scopeLabel(plan.estimated_scope)}</p>
          </div>
          <div>
            <span class="text-text-dim">Created</span>
            <p class="text-text">{formatDate(plan.created_at)}</p>
          </div>
          <div>
            <span class="text-text-dim">Updated</span>
            <p class="text-text">{formatDate(plan.updated_at)}</p>
          </div>
          {#if plan.run_id}
            <div>
              <span class="text-text-dim">Created by Run</span>
              <button onclick={() => navigate(`/runs/${plan.run_id}`)} class="block text-accent-blue hover:underline cursor-pointer">
                {plan.run_id}
              </button>
            </div>
          {/if}
        </div>
      </div>
    </GlassCard>

    <!-- Description (Markdown content) -->
    {#if plan.description}
      <GlassCard>
        <div class="p-4">
          <h2 class="text-sm font-semibold text-text mb-3">Plan Description</h2>
          <div class="prose prose-invert prose-sm max-w-none text-text-dim whitespace-pre-wrap font-mono text-xs leading-relaxed bg-surface-2 rounded-lg p-4 overflow-x-auto">
            {plan.description}
          </div>
        </div>
      </GlassCard>
    {/if}

    <!-- Steps -->
    {#if parseSteps(plan.steps).length > 0}
      <GlassCard>
        <div class="p-4">
          <h2 class="text-sm font-semibold text-text mb-3">Implementation Steps</h2>
          <ol class="space-y-2">
            {#each parseSteps(plan.steps) as step, i}
              <li class="flex gap-3 text-sm">
                <span class="shrink-0 w-6 h-6 rounded-full bg-accent-blue/20 text-accent-blue flex items-center justify-center text-xs font-medium">
                  {i + 1}
                </span>
                <span class="text-text-dim pt-0.5">{step}</span>
              </li>
            {/each}
          </ol>
        </div>
      </GlassCard>
    {/if}

    <!-- Files Affected -->
    {#if parseFiles(plan.files_affected).length > 0}
      <GlassCard>
        <div class="p-4">
          <h2 class="text-sm font-semibold text-text mb-3">Files Affected ({parseFiles(plan.files_affected).length})</h2>
          <div class="flex flex-wrap gap-2">
            {#each parseFiles(plan.files_affected) as file}
              <span class="text-xs bg-surface-2 px-2 py-1 rounded font-mono text-text-dim">{file}</span>
            {/each}
          </div>
        </div>
      </GlassCard>
    {/if}
  </div>
{/if}
