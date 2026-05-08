<script lang="ts">
  import { getProject, updateProject, deleteProject, listRuns } from '../lib/api';
  import { navigate } from '../lib/router.svelte';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import { formatCompact, formatDuration } from '../lib/chart-utils';
  import type { Project, Run } from '../lib/types';
  import Toggle from '../components/forms/Toggle.svelte';
  import VisionTab from '../components/vision/VisionTab.svelte';

  let { projectId = '' }: { projectId: string } = $props();

  let activeTab = $state<'overview' | 'vision' | 'runs'>('overview');

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
    <div class="text-sm text-tertiary">Loading...</div>
  {:else if !project}
    <div class="text-sm text-tertiary">Project not found</div>
  {:else}
    <div class="flex items-center justify-between">
      <h1 class="text-lg font-semibold text-primary">{project.repo}</h1>
      <button onclick={handleDelete} class="text-xs text-reject hover:text-reject/80 transition-colors">Delete</button>
    </div>

    <!-- Tab strip -->
    <div class="flex gap-1" style="border-bottom: 1px solid var(--color-border);">
      {#each ['overview', 'vision', 'runs'] as t}
        <button
          class="px-4 py-2.5 text-xs font-medium capitalize transition-colors cursor-pointer"
          style="{activeTab === t ? 'color: var(--color-primary); border-bottom: 2px solid var(--color-violet);' : 'color: var(--color-tertiary); border-bottom: 2px solid transparent;'}"
          onclick={() => activeTab = t as 'overview' | 'vision' | 'runs'}
        >{t}</button>
      {/each}
    </div>

    {#if activeTab === 'overview'}
      <!-- Config -->
      <div class="glass rounded-lg p-4 space-y-4">
        <h2 class="text-xs font-semibold text-secondary uppercase tracking-wider">Configuration</h2>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label class="text-xs text-tertiary mb-1 block">Mode</label>
            <select
              value={project.mode}
              onchange={(e) => { project!.mode = (e.target as HTMLSelectElement).value as any; save('mode', project!.mode); }}
              class="w-full px-3 py-2 rounded-lg bg-void text-primary text-sm border border-border focus:border-border-focus outline-none"
            >
              <option value="full">Full</option>
              <option value="analyze">Analyze</option>
              <option value="plan">Plan</option>
              <option value="plan_only">Plan Only</option>
            </select>
          </div>
          <div>
            <label class="text-xs text-tertiary mb-1 block">Priority</label>
            <select
              value={project.priority}
              onchange={(e) => { project!.priority = (e.target as HTMLSelectElement).value as any; save('priority', project!.priority); }}
              class="w-full px-3 py-2 rounded-lg bg-void text-primary text-sm border border-border focus:border-border-focus outline-none"
            >
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </div>
          <div>
            <label class="text-xs text-tertiary mb-1 block">Branch</label>
            <input
              value={project.branch}
              onchange={(e) => { project!.branch = (e.target as HTMLInputElement).value; save('branch', project!.branch); }}
              class="w-full px-3 py-2 rounded-lg bg-void text-primary text-sm border border-border focus:border-border-focus outline-none"
            />
          </div>
          <div class="flex items-end">
            <Toggle checked={project.enabled} label="Enabled"
              onchange={(v) => { project!.enabled = v; save('enabled', v); }} />
          </div>
        </div>
      </div>
    {:else if activeTab === 'vision'}
      <VisionTab {project} />
    {:else if activeTab === 'runs'}
      <!-- Recent runs -->
      <div class="glass rounded-lg p-4">
        <h2 class="text-xs font-semibold text-secondary uppercase tracking-wider mb-3">Recent Runs</h2>
        {#if runs.length > 0}
          <div class="space-y-1">
            {#each runs as run}
              <button
                class="w-full flex items-center justify-between px-3 py-2 rounded hover:bg-surface-2/50 text-xs transition-colors"
                onclick={() => navigate(`/runs/${run.run_id}`)}
              >
                <span class="text-primary font-mono">{run.run_id}</span>
                <div class="flex items-center gap-3 text-tertiary">
                  {#if run.verdict}
                    <span class="{run.verdict === 'APPROVE' ? 'text-approve' : run.verdict === 'REJECT' ? 'text-reject' : 'text-pr'}">{run.verdict}</span>
                  {/if}
                  {#if run.tokens_total}<span class="font-mono">{formatCompact(run.tokens_total)}</span>{/if}
                </div>
              </button>
            {/each}
          </div>
        {:else}
          <div class="text-sm text-tertiary text-center py-4">No runs</div>
        {/if}
      </div>
    {/if}
  {/if}
</div>
