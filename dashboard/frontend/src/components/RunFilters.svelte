<script lang="ts">
  import type { Project } from '../lib/types';

  interface Props {
    verdict: string;
    status: string;
    projectId: string;
    projects: Project[];
    onchange: () => void;
  }

  let { verdict = $bindable(), status = $bindable(), projectId = $bindable(), projects, onchange }: Props = $props();
</script>

<div class="flex flex-wrap gap-3">
  <select
    bind:value={verdict}
    onchange={onchange}
    class="bg-surface-2 border border-border rounded-lg px-3 py-2 text-sm text-text"
  >
    <option value="">All Verdicts</option>
    <option value="APPROVE">APPROVE</option>
    <option value="REJECT">REJECT</option>
    <option value="OPEN_PR">OPEN_PR</option>
  </select>

  <select
    bind:value={status}
    onchange={onchange}
    class="bg-surface-2 border border-border rounded-lg px-3 py-2 text-sm text-text"
  >
    <option value="">All Statuses</option>
    <option value="completed">Completed</option>
    <option value="running">Running</option>
    <option value="failed">Failed</option>
  </select>

  <select
    bind:value={projectId}
    onchange={onchange}
    class="bg-surface-2 border border-border rounded-lg px-3 py-2 text-sm text-text"
  >
    <option value="">All Projects</option>
    {#each projects as p}
      <option value={String(p.id)}>{p.repo}</option>
    {/each}
  </select>
</div>
