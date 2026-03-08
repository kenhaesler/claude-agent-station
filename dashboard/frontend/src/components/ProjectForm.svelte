<script lang="ts">
  import type { Project, ProjectCreate, ProjectUpdate } from '../lib/types';

  interface Props {
    project?: Project | null;
    onsubmit: (data: ProjectCreate | ProjectUpdate) => void;
    oncancel: () => void;
  }

  let { project = null, onsubmit, oncancel }: Props = $props();

  let repo = $state('');
  let priority = $state('medium');
  let mode = $state('full');
  let branch = $state('main');
  let enabled = $state(true);

  $effect(() => {
    repo = project?.repo ?? '';
    priority = project?.priority ?? 'medium';
    mode = project?.mode ?? 'full';
    branch = project?.branch ?? 'main';
    enabled = project?.enabled ?? true;
  });

  function handleSubmit(e: Event) {
    e.preventDefault();
    if (project) {
      onsubmit({ priority, mode, branch, enabled } as ProjectUpdate);
    } else {
      onsubmit({ repo, priority, mode, branch, enabled } as ProjectCreate);
    }
  }
</script>

<form onsubmit={handleSubmit} class="space-y-4">
  {#if !project}
    <div>
      <label for="repo" class="block text-sm text-text-dim mb-1">Repository (org/repo)</label>
      <input
        id="repo"
        bind:value={repo}
        required
        placeholder="owner/repo"
        class="w-full bg-surface-2 border border-border rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-pr"
      />
    </div>
  {/if}

  <div class="grid grid-cols-2 gap-4">
    <div>
      <label for="priority" class="block text-sm text-text-dim mb-1">Priority</label>
      <select id="priority" bind:value={priority} class="w-full bg-surface-2 border border-border rounded-lg px-3 py-2 text-sm text-text">
        <option value="high">High</option>
        <option value="medium">Medium</option>
        <option value="low">Low</option>
      </select>
    </div>
    <div>
      <label for="mode" class="block text-sm text-text-dim mb-1">Mode</label>
      <select id="mode" bind:value={mode} class="w-full bg-surface-2 border border-border rounded-lg px-3 py-2 text-sm text-text">
        <option value="full">Full</option>
        <option value="analyze">Analyze</option>
      </select>
    </div>
  </div>

  <div>
    <label for="branch" class="block text-sm text-text-dim mb-1">Branch</label>
    <input
      id="branch"
      bind:value={branch}
      class="w-full bg-surface-2 border border-border rounded-lg px-3 py-2 text-sm text-text focus:outline-none focus:border-pr"
    />
  </div>

  <label class="flex items-center gap-2 text-sm">
    <input type="checkbox" bind:checked={enabled} class="rounded" />
    Enabled
  </label>

  <div class="flex justify-end gap-3 pt-2">
    <button type="button" onclick={oncancel} class="px-4 py-2 text-sm text-text-dim hover:text-text cursor-pointer">
      Cancel
    </button>
    <button type="submit" class="px-4 py-2 bg-pr text-white rounded-lg text-sm font-medium hover:bg-pr/80 cursor-pointer">
      {project ? 'Update' : 'Create'}
    </button>
  </div>
</form>
