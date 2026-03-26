<script lang="ts">
  import { getProject, updateProject, deleteProject, listRuns } from '../lib/api';
  import { navigate } from '../lib/router.svelte';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import { formatCompact, formatDuration } from '../lib/chart-utils';
  import type { Project, Run } from '../lib/types';
  import Toggle from '../components/forms/Toggle.svelte';

  let { projectId = '' }: { projectId: string } = $props();

  let project = $state<Project | null>(null);
  let runs = $state<Run[]>([]);
  let loading = $state(true);

  $effect(() => {
    if (!projectId) return;
    load();
  });

  async function load() {
    loading = true;
    try {
      const id = parseInt(projectId);
      const [pRes, rRes] = await Promise.allSettled([
        getProject(id),
        listRuns({ project_id: id, limit: 20 }),
      ]);
      if (pRes.status === 'fulfilled') project = pRes.value;
      if (rRes.status === 'fulfilled') runs = rRes.value.runs;
    } catch { /* silent */ }
    loading = false;
  }

  async function save(field: string, value: any) {
    if (!project) return;
    try {
      await updateProject(project.id, { [field]: value });
      toastSuccess('Updated');
    } catch (e: any) { toastError(e.message); }
  }

  async function handleDelete() {
    if (!project || !confirm(`Delete project ${project.repo}?`)) return;
    try {
      await deleteProject(project.id);
      toastSuccess('Deleted');
      navigate('/projects');
    } catch (e: any) { toastError(e.message); }
  }
</script>

<div class="space-y-6 animate-fade-in-up max-w-3xl">
  {#if loading}
    <div class="text-sm text-text-muted">Loading...</div>
  {:else if !project}
    <div class="text-sm text-text-muted">Project not found</div>
  {:else}
    <div class="flex items-center justify-between">
      <h1 class="text-lg font-semibold text-text">{project.repo}</h1>
      <button onclick={handleDelete} class="text-xs text-reject hover:text-reject/80 transition-colors">Delete</button>
    </div>

    <!-- Config -->
    <div class="glass rounded-lg p-4 space-y-4">
      <h2 class="text-xs font-semibold text-text-dim uppercase tracking-wider">Configuration</h2>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label class="text-xs text-text-muted mb-1 block">Mode</label>
          <select
            value={project.mode}
            onchange={(e) => { project!.mode = (e.target as HTMLSelectElement).value; save('mode', project!.mode); }}
            class="w-full px-3 py-2 rounded-lg bg-bg text-text text-sm border border-border focus:border-focus outline-none"
          >
            <option value="full">Full</option>
            <option value="analyze">Analyze</option>
            <option value="plan">Plan</option>
            <option value="plan_only">Plan Only</option>
          </select>
        </div>
        <div>
          <label class="text-xs text-text-muted mb-1 block">Priority</label>
          <select
            value={project.priority}
            onchange={(e) => { project!.priority = (e.target as HTMLSelectElement).value; save('priority', project!.priority); }}
            class="w-full px-3 py-2 rounded-lg bg-bg text-text text-sm border border-border focus:border-focus outline-none"
          >
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </div>
        <div>
          <label class="text-xs text-text-muted mb-1 block">Branch</label>
          <input
            value={project.branch}
            onchange={(e) => { project!.branch = (e.target as HTMLInputElement).value; save('branch', project!.branch); }}
            class="w-full px-3 py-2 rounded-lg bg-bg text-text text-sm border border-border focus:border-focus outline-none"
          />
        </div>
        <div class="flex items-end">
          <Toggle checked={project.enabled} label="Enabled"
            onchange={() => { project!.enabled = !project!.enabled; save('enabled', project!.enabled); }} />
        </div>
      </div>
    </div>

    <!-- Recent runs -->
    <div class="glass rounded-lg p-4">
      <h2 class="text-xs font-semibold text-text-dim uppercase tracking-wider mb-3">Recent Runs</h2>
      {#if runs.length > 0}
        <div class="space-y-1">
          {#each runs as run}
            <button
              class="w-full flex items-center justify-between px-3 py-2 rounded hover:bg-surface-2/50 text-xs transition-colors"
              onclick={() => navigate(`/runs/${run.run_id}`)}
            >
              <span class="text-text font-mono">{run.run_id}</span>
              <div class="flex items-center gap-3 text-text-muted">
                {#if run.verdict}
                  <span class="{run.verdict === 'APPROVE' ? 'text-approve' : run.verdict === 'REJECT' ? 'text-reject' : 'text-pr'}">{run.verdict}</span>
                {/if}
                {#if run.tokens_total}<span class="data-readout">{formatCompact(run.tokens_total)}</span>{/if}
              </div>
            </button>
          {/each}
        </div>
      {:else}
        <div class="text-sm text-text-muted text-center py-4">No runs</div>
      {/if}
    </div>
  {/if}
</div>
