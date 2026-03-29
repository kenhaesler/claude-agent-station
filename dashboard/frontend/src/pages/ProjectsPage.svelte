<script lang="ts">
  import { listProjects, createProject, deleteProject, updateProject } from '../lib/api';
  import { navigate } from '../lib/router.svelte';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import type { Project, AgentMode } from '../lib/types';
  import Modal from '../components/overlays/Modal.svelte';

  let projects = $state<Project[]>([]);
  let showCreateModal = $state(false);
  let newRepo = $state('');
  let newMode = $state<AgentMode>('full');
  let newPriority = $state('medium');

  $effect(() => { loadProjects(); });

  async function loadProjects() {
    try { projects = await listProjects(); } catch { /* silent */ }
  }

  async function handleCreate() {
    if (!newRepo.trim()) return;
    try {
      await createProject({ repo: newRepo.trim(), mode: newMode, priority: newPriority });
      toastSuccess('Project created');
      showCreateModal = false;
      newRepo = '';
      loadProjects();
    } catch (e: any) { toastError(e.message); }
  }

  async function toggleEnabled(p: Project) {
    try {
      await updateProject(p.id, { enabled: !p.enabled });
      p.enabled = !p.enabled;
    } catch (e: any) { toastError(e.message); }
  }
</script>

<div class="space-y-4 animate-fade-in-up">
  <div class="flex items-center justify-between">
    <h1 class="text-lg font-semibold text-primary">Projects</h1>
    <button
      onclick={() => showCreateModal = true}
      class="px-3 py-1.5 rounded-lg text-xs font-medium bg-accent-blue/20 text-accent-blue hover:bg-accent-blue/30 transition-colors"
    >+ Add Project</button>
  </div>

  <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
    {#each projects as project (project.id)}
      <div class="glass rounded-lg p-4 hover:bg-surface-2/30 transition-colors">
        <div class="flex items-center justify-between mb-2">
          <button onclick={() => navigate(`/projects/${project.id}`)} class="text-sm font-medium text-primary hover:text-indigo transition-colors truncate text-left">
            {project.repo}
          </button>
          <button
            onclick={() => toggleEnabled(project)}
            class="w-8 h-4 rounded-full transition-colors {project.enabled ? 'bg-status-active' : 'bg-surface-3'}"
            title={project.enabled ? 'Enabled' : 'Disabled'}
          >
            <span class="block w-3 h-3 rounded-full bg-text transition-transform {project.enabled ? 'translate-x-4' : 'translate-x-0.5'}"></span>
          </button>
        </div>
        <div class="flex items-center gap-2 text-[10px] text-tertiary">
          <span class="uppercase">{project.mode}</span>
          <span>Priority: {project.priority}</span>
          <span>Branch: {project.branch}</span>
        </div>
        {#if !project.enabled}
          <div class="mt-1 text-[10px] text-tertiary opacity-50">Disabled</div>
        {/if}
      </div>
    {/each}
  </div>
</div>

<Modal show={showCreateModal} onClose={() => showCreateModal = false} title="Add Project">
  <div class="space-y-3">
    <input
      bind:value={newRepo}
      placeholder="owner/repo"
      class="w-full px-3 py-2 rounded-lg bg-void text-primary text-sm border border-border focus:border-border-focus outline-none"
    />
    <div class="flex gap-3">
      <select bind:value={newMode} class="flex-1 px-3 py-2 rounded-lg bg-void text-primary text-sm border border-border focus:border-border-focus outline-none">
        <option value="full">Full</option>
        <option value="analyze">Analyze</option>
        <option value="plan">Plan</option>
        <option value="plan_only">Plan Only</option>
      </select>
      <select bind:value={newPriority} class="flex-1 px-3 py-2 rounded-lg bg-void text-primary text-sm border border-border focus:border-border-focus outline-none">
        <option value="high">High</option>
        <option value="medium">Medium</option>
        <option value="low">Low</option>
      </select>
    </div>
    <button onclick={handleCreate} class="w-full px-4 py-2 rounded-lg text-sm font-medium bg-accent-blue text-white hover:bg-accent-blue/80 transition-colors">Create</button>
  </div>
</Modal>
