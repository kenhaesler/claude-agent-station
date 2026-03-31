<script lang="ts">
  import { listProjects, createProject, deleteProject, updateProject } from '../lib/api';
  import { navigate } from '../lib/router.svelte';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import type { Project, AgentMode } from '../lib/types';
  import Modal from '../components/overlays/Modal.svelte';
  import EmptyState from '../components/data-display/EmptyState.svelte';
  import SkeletonLoader from '../components/data-display/SkeletonLoader.svelte';

  let projects = $state<Project[]>([]);
  let loading = $state(true);
  let showCreateModal = $state(false);
  let newRepo = $state('');
  let newMode = $state<AgentMode>('full');
  let newPriority = $state('medium');

  $effect(() => { loadProjects(); });

  async function loadProjects() {
    try { projects = await listProjects(); } catch { /* silent */ }
    loading = false;
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

  function getStatusBorder(p: Project): string {
    if (!p.enabled) return 'border-l-2 border-l-ghost';
    return 'border-l-2 border-l-emerald';
  }

  function getPriorityBadge(priority: string): string {
    if (priority === 'high') return 'badge-pending';
    if (priority === 'low') return '';
    return '';
  }
</script>

<div class="space-y-4 animate-fade-in">
  <div class="flex items-center justify-between">
    <h1 class="font-heading text-xl">Projects</h1>
    <button onclick={() => showCreateModal = true} class="btn btn-primary btn-sm">
      + Add Project
    </button>
  </div>

  {#if loading}
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {#each Array(3) as _}
        <div class="card p-4"><SkeletonLoader lines={3} /></div>
      {/each}
    </div>
  {:else if projects.length === 0}
    <div class="card">
      <EmptyState
        title="No projects yet"
        description="Add a GitHub repository to get started"
        icon="▤"
      />
    </div>
  {:else}
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {#each projects as project (project.id)}
        <div
          class="card p-4 {getStatusBorder(project)} hover:border-border-hover transition-all duration-200 cursor-pointer"
          style="{!project.enabled ? 'opacity: 0.6;' : ''}"
          onclick={() => navigate(`/projects/${project.id}`)}
          role="button"
          tabindex="0"
          onkeydown={(e) => e.key === 'Enter' && navigate(`/projects/${project.id}`)}
        >
          <div class="flex items-center justify-between mb-2">
            <span class="text-sm font-medium text-primary truncate flex-1 mr-2">
              {project.repo}
            </span>
            <button
              onclick={(e) => { e.stopPropagation(); toggleEnabled(project); }}
              class="w-8 h-4 rounded-full transition-colors shrink-0 cursor-pointer {project.enabled ? 'bg-emerald' : 'bg-surface-2'}"
              title={project.enabled ? 'Enabled — click to disable' : 'Disabled — click to enable'}
            >
              <span class="block w-3 h-3 rounded-full bg-white transition-transform {project.enabled ? 'translate-x-4' : 'translate-x-0.5'}"></span>
            </button>
          </div>
          <div class="flex items-center gap-2 flex-wrap">
            <span class="badge badge-{project.mode}">{project.mode}</span>
            <span class="text-[10px] font-mono text-tertiary">Priority: {project.priority}</span>
            <span class="text-[10px] font-mono text-tertiary">Branch: {project.branch}</span>
          </div>
          {#if !project.enabled}
            <div class="mt-2">
              <span class="badge badge-failed">Disabled</span>
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<Modal show={showCreateModal} onClose={() => showCreateModal = false} title="Add Project">
  <div class="space-y-3">
    <input
      bind:value={newRepo}
      placeholder="owner/repo"
      class="input"
    />
    <div class="flex gap-3">
      <select bind:value={newMode} class="input">
        <option value="full">Full</option>
        <option value="analyze">Analyze</option>
        <option value="plan">Plan</option>
        <option value="plan_only">Plan Only</option>
      </select>
      <select bind:value={newPriority} class="input">
        <option value="high">High</option>
        <option value="medium">Medium</option>
        <option value="low">Low</option>
      </select>
    </div>
    <button onclick={handleCreate} class="w-full btn btn-primary">Create</button>
  </div>
</Modal>
