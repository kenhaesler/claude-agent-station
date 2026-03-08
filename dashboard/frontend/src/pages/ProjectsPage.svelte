<script lang="ts">
  import type { Project, ProjectCreate, ProjectUpdate } from '../lib/types';
  import { listProjects, createProject, updateProject, deleteProject } from '../lib/api';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import StatusBadge from '../components/StatusBadge.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';
  import EmptyState from '../components/EmptyState.svelte';
  import Modal from '../components/Modal.svelte';
  import ProjectForm from '../components/ProjectForm.svelte';

  let projects = $state<Project[]>([]);
  let loading = $state(true);
  let showModal = $state(false);
  let editingProject = $state<Project | null>(null);

  async function load() {
    try {
      projects = await listProjects();
    } catch (e: any) {
      toastError(`Failed to load: ${e.message}`);
    } finally {
      loading = false;
    }
  }

  function openCreate() {
    editingProject = null;
    showModal = true;
  }

  function openEdit(p: Project) {
    editingProject = p;
    showModal = true;
  }

  async function handleSubmit(data: ProjectCreate | ProjectUpdate) {
    try {
      if (editingProject) {
        await updateProject(editingProject.id, data as ProjectUpdate);
        toastSuccess('Project updated');
      } else {
        await createProject(data as ProjectCreate);
        toastSuccess('Project created');
      }
      showModal = false;
      await load();
    } catch (e: any) {
      toastError(e.message);
    }
  }

  async function handleDelete(p: Project) {
    if (!confirm(`Delete ${p.repo}?`)) return;
    try {
      await deleteProject(p.id);
      toastSuccess('Project deleted');
      await load();
    } catch (e: any) {
      toastError(e.message);
    }
  }

  $effect(() => { load(); });
</script>

<div class="space-y-6">
  <div class="flex items-center justify-between">
    <h1 class="text-2xl font-bold">Projects</h1>
    <button onclick={openCreate} class="px-4 py-2 bg-pr text-white rounded-lg text-sm font-medium hover:bg-pr/80 cursor-pointer">
      Add Project
    </button>
  </div>

  {#if loading}
    <div class="flex justify-center py-12"><LoadingSpinner /></div>
  {:else if projects.length === 0}
    <EmptyState message="No projects configured" />
  {:else}
    <div class="bg-surface rounded-xl border border-border overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border text-left text-text-dim">
            <th class="px-5 py-3 font-medium">Repository</th>
            <th class="px-5 py-3 font-medium">Mode</th>
            <th class="px-5 py-3 font-medium">Priority</th>
            <th class="px-5 py-3 font-medium">Branch</th>
            <th class="px-5 py-3 font-medium">Status</th>
            <th class="px-5 py-3 font-medium text-right">Actions</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-border">
          {#each projects as p}
            <tr class="hover:bg-surface-2/30 transition-colors">
              <td class="px-5 py-3 font-mono">{p.repo}</td>
              <td class="px-5 py-3"><StatusBadge value={p.mode} variant="mode" /></td>
              <td class="px-5 py-3"><StatusBadge value={p.priority} variant="status" /></td>
              <td class="px-5 py-3 text-text-dim">{p.branch}</td>
              <td class="px-5 py-3">
                <span class="w-2 h-2 rounded-full inline-block {p.enabled ? 'bg-approve' : 'bg-reject'}"></span>
                <span class="ml-1.5 text-text-dim">{p.enabled ? 'Enabled' : 'Disabled'}</span>
              </td>
              <td class="px-5 py-3 text-right space-x-2">
                <button onclick={() => openEdit(p)} class="text-info hover:underline cursor-pointer">Edit</button>
                <button onclick={() => handleDelete(p)} class="text-reject hover:underline cursor-pointer">Delete</button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}

  <Modal open={showModal} title={editingProject ? 'Edit Project' : 'Add Project'} onclose={() => showModal = false}>
    <ProjectForm project={editingProject} onsubmit={handleSubmit} oncancel={() => showModal = false} />
  </Modal>
</div>
