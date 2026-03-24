<script lang="ts">
  import { listProjects } from '../lib/api';
  import type { Project } from '../lib/types';
  import GlassCard from '../components/GlassCard.svelte';
  import IntegrationPanel from '../components/IntegrationPanel.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';
  import EmptyState from '../components/EmptyState.svelte';

  let projects = $state<Project[]>([]);
  let selectedRepo = $state<string>('');
  let loading = $state(true);

  async function loadProjects() {
    try {
      const res = await listProjects();
      projects = res.filter(p => p.enabled);
      // Auto-select first project if none selected
      if (!selectedRepo && projects.length > 0) {
        selectedRepo = projects[0].repo;
      }
    } catch { /* silent */ }
    loading = false;
  }

  $effect(() => {
    loadProjects();
  });
</script>

<div class="space-y-4 animate-fade-in-up">
  <!-- Page Header -->
  <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
    <div>
      <h1 class="text-lg font-semibold text-text">Integration Branch</h1>
      <p class="text-xs text-text-muted mt-0.5">Feature pipeline from dev to main</p>
    </div>

    <!-- Project Selector -->
    {#if projects.length > 0}
      <select
        bind:value={selectedRepo}
        class="px-3 py-1.5 text-xs font-medium bg-surface border border-border rounded-lg text-text focus:outline-none focus:border-info transition-colors appearance-none cursor-pointer"
      >
        {#each projects as project (project.id)}
          <option value={project.repo}>{project.repo}</option>
        {/each}
      </select>
    {/if}
  </div>

  <!-- Main Content -->
  {#if loading}
    <div class="flex items-center justify-center py-16">
      <LoadingSpinner />
    </div>
  {:else if projects.length === 0}
    <EmptyState message="No enabled projects found" />
  {:else}
    <IntegrationPanel projectRepo={selectedRepo} />
  {/if}

  <!-- Integration Stats -->
  {#if selectedRepo && !loading}
    <GlassCard class="p-4">
      <h2 class="text-sm font-semibold text-text mb-3">Pipeline Overview</h2>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div class="text-center">
          <div class="text-2xl font-bold font-data text-info">
            <svg class="w-5 h-5 mx-auto mb-1 text-info" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M10 3v14M6 7l4-4 4 4" />
            </svg>
          </div>
          <p class="text-[10px] text-text-muted">Features merge to dev branch</p>
        </div>
        <div class="text-center">
          <div class="text-2xl font-bold font-data text-approve">
            <svg class="w-5 h-5 mx-auto mb-1 text-approve" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M5 10l3 3 7-7" />
            </svg>
          </div>
          <p class="text-[10px] text-text-muted">Tests & validation run on dev</p>
        </div>
        <div class="text-center">
          <div class="text-2xl font-bold font-data text-warning">
            <svg class="w-5 h-5 mx-auto mb-1 text-warning" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 10h14M13 6l4 4-4 4" />
            </svg>
          </div>
          <p class="text-[10px] text-text-muted">Validated features promote to main</p>
        </div>
        <div class="text-center">
          <div class="text-2xl font-bold font-data text-accent-purple">
            <svg class="w-5 h-5 mx-auto mb-1 text-accent-purple" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M6 3h8l3 4-7 10-7-10z" />
            </svg>
          </div>
          <p class="text-[10px] text-text-muted">Stable code lands on main branch</p>
        </div>
      </div>
    </GlassCard>
  {/if}
</div>
