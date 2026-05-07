<script lang="ts">
  import { listProjects, createProject, deleteProject, updateProject, listGitHubRepos, listGitHubBranches } from '../lib/api';
  import { navigate } from '../lib/router.svelte';
  import { toastSuccess, toastError } from '../lib/toast.svelte';
  import type { Project, AgentMode, AutonomyLevel } from '../lib/types';
  import type { GitHubRepo, GitHubBranch } from '../lib/api';
  import Modal from '../components/overlays/Modal.svelte';
  import EmptyState from '../components/data-display/EmptyState.svelte';
  import SkeletonLoader from '../components/data-display/SkeletonLoader.svelte';
  import AutonomyBadge from '../components/badges/AutonomyBadge.svelte';
  import VisionChat from '../components/vision/VisionChat.svelte';

  let projects = $state<Project[]>([]);
  let loading = $state(true);
  let showCreateModal = $state(false);
  let wizardStep = $state<1 | 2>(1);
  let savedProjectId = $state<number | null>(null);
  let newRepo = $state('');
  let newBranch = $state('main');
  let newMode = $state<AgentMode>('full');
  let newPriority = $state('medium');

  // Repos pulled from GitHub via the configured auth (App or PAT). The
  // dropdown shows these; users can also pick "Custom…" to type a repo
  // name by hand (e.g. for a repo the App isn't installed on yet).
  let repos = $state<GitHubRepo[]>([]);
  let reposLoading = $state(false);
  let useCustomRepo = $state(false);

  // Branches for the currently-selected repo. Fetched lazily when newRepo
  // changes. Empty list = no GitHub auth, manual-repo mode, or fetch
  // failed → UI degrades to a free-text branch input.
  let branches = $state<GitHubBranch[]>([]);
  let branchesLoading = $state(false);

  // When the user picks a repo from the dropdown, fetch its branches and
  // pre-select the default. In manual-repo mode we leave the branch field
  // alone — there's no way to know the default without an extra round-trip
  // and users typing a custom repo usually know which branch they want.
  $effect(() => {
    if (!showCreateModal || useCustomRepo || !newRepo) {
      branches = [];
      return;
    }
    const picked = repos.find(r => r.full_name === newRepo);
    if (!picked) return;
    newBranch = picked.default_branch;
    loadBranches(picked.full_name);
  });

  async function loadBranches(repo: string) {
    branchesLoading = true;
    try {
      const res = await listGitHubBranches(repo);
      branches = res.branches;
    } catch {
      branches = [];
    } finally {
      branchesLoading = false;
    }
  }

  $effect(() => { loadProjects(); });

  async function loadProjects() {
    try { projects = await listProjects(); } catch { /* silent */ }
    loading = false;
  }

  async function loadRepos() {
    reposLoading = true;
    try {
      const res = await listGitHubRepos();
      // Filter out repos already added as projects so the dropdown only
      // shows things the user can actually create.
      const existing = new Set(projects.map(p => p.repo));
      repos = res.repos.filter(r => !existing.has(r.full_name));
      // If GitHub returned nothing, fall straight to custom-input mode.
      useCustomRepo = repos.length === 0;
    } catch {
      repos = [];
      useCustomRepo = true;
    } finally {
      reposLoading = false;
    }
  }

  function openCreateModal() {
    showCreateModal = true;
    wizardStep = 1;
    savedProjectId = null;
    newRepo = '';
    newBranch = 'main';
    useCustomRepo = false;
    branches = [];
    loadRepos();
  }

  function closeWizard() {
    showCreateModal = false;
    wizardStep = 1;
    savedProjectId = null;
    newRepo = '';
    newBranch = 'main';
    loadProjects();
  }

  async function handleCreate() {
    if (!newRepo.trim()) return;
    try {
      const created = await createProject({
        repo: newRepo.trim(),
        branch: newBranch.trim() || 'main',
        mode: newMode,
        priority: newPriority,
      });
      toastSuccess('Project created');
      savedProjectId = created.id;
      wizardStep = 2;
    } catch (e: any) { toastError(e.message); }
  }

  async function toggleEnabled(p: Project) {
    try {
      await updateProject(p.id, { enabled: !p.enabled });
      p.enabled = !p.enabled;
    } catch (e: any) { toastError(e.message); }
  }

  async function setAutonomy(p: Project, next: AutonomyLevel) {
    if (p.autonomy_level === next) return;
    const prev = p.autonomy_level;
    p.autonomy_level = next;   // optimistic
    try {
      await updateProject(p.id, { autonomy_level: next });
      toastSuccess(`Autonomy: ${prev} \u2192 ${next}`);
    } catch (e: any) {
      p.autonomy_level = prev; // rollback
      toastError(e.message ?? 'Failed to update autonomy');
    }
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
    <button onclick={openCreateModal} class="btn btn-primary btn-sm">
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

          <!-- Autonomy selector (ADR-0001) -->
          <!-- svelte-ignore a11y_click_events_have_key_events -->
          <!-- svelte-ignore a11y_no_static_element_interactions -->
          <div
            class="mt-3 flex items-center gap-2"
            onclick={(e) => e.stopPropagation()}
          >
            <span class="text-[10px] font-mono uppercase tracking-widest text-tertiary">Autonomy</span>
            <AutonomyBadge level={project.autonomy_level} size="xs" />
            <select
              class="input text-xs py-1 px-2 ml-auto"
              style="width: auto; min-width: 90px;"
              value={project.autonomy_level ?? 'assisted'}
              onchange={(e) => setAutonomy(project, (e.currentTarget as HTMLSelectElement).value as AutonomyLevel)}
            >
              <option value="manual">manual</option>
              <option value="assisted">assisted</option>
              <option value="auto">auto</option>
            </select>
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

<Modal show={showCreateModal} onClose={closeWizard} title={wizardStep === 1 ? 'Add Project' : 'Project Vision'}>
  {#if wizardStep === 1}
    <div class="space-y-3">
      {#if reposLoading}
        <div class="text-xs text-tertiary py-2">Loading repos from GitHub…</div>
      {:else if !useCustomRepo && repos.length > 0}
        <select bind:value={newRepo} class="input" data-testid="repo-select">
          <option value="" disabled>Pick a repo…</option>
          {#each repos as r (r.full_name)}
            <option value={r.full_name}>
              {r.full_name}{r.private ? ' (private)' : ''}
            </option>
          {/each}
        </select>
        <button
          type="button"
          onclick={() => { useCustomRepo = true; newRepo = ''; }}
          class="text-xs text-tertiary hover:text-secondary underline"
        >Or enter a repo manually</button>
      {:else}
        <input
          bind:value={newRepo}
          placeholder="owner/repo"
          class="input"
          data-testid="repo-input"
        />
        {#if repos.length > 0}
          <button
            type="button"
            onclick={() => { useCustomRepo = false; newRepo = ''; }}
            class="text-xs text-tertiary hover:text-secondary underline"
          >Pick from your GitHub repos</button>
        {:else}
          <div class="text-[11px] text-tertiary">
            No repos found via GitHub auth. Connect a GitHub App or PAT in
            <a href="/settings?tab=auth" class="text-accent-orange underline">Settings → Auth</a>
            to populate this list automatically.
          </div>
        {/if}
      {/if}

      <div>
        <label for="branch-input" class="text-[11px] uppercase tracking-widest text-tertiary block mb-1">
          Branch
        </label>
        {#if branchesLoading}
          <div class="text-xs text-tertiary py-2">Loading branches…</div>
        {:else if branches.length > 0}
          <select id="branch-input" bind:value={newBranch} class="input" data-testid="branch-select">
            {#each branches as b (b.name)}
              <option value={b.name}>
                {b.name}{b.protected ? ' (protected)' : ''}
              </option>
            {/each}
          </select>
        {:else}
          <input
            id="branch-input"
            bind:value={newBranch}
            placeholder="main"
            class="input"
            data-testid="branch-input"
          />
        {/if}
      </div>

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

      <div class="flex justify-end gap-2 mt-4">
        <button type="button" onclick={closeWizard} class="btn btn-ghost btn-sm">Cancel</button>
        <button type="button" onclick={handleCreate} disabled={!newRepo.trim() || !newBranch.trim()} class="btn btn-primary btn-sm">Next →</button>
      </div>
    </div>
  {:else if wizardStep === 2 && savedProjectId !== null}
    <p class="text-xs text-tertiary mb-3">
      Step 2 of 2 — Define the project's vision so Claude knows the end goal.
    </p>
    <VisionChat
      projectId={savedProjectId}
      onApproved={closeWizard}
      onCancelled={closeWizard}
    />
    <div class="flex justify-start mt-3">
      <button type="button" onclick={closeWizard} class="btn btn-ghost btn-sm text-xs">
        Skip for now
      </button>
    </div>
  {/if}
</Modal>
