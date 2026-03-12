<script lang="ts">
  import type { CoordinatorTask } from '../lib/types';

  interface Props {
    tasks: CoordinatorTask[];
  }

  let { tasks }: Props = $props();

  const statusColors: Record<string, string> = {
    completed: 'bg-approve/30 text-approve',
    running: 'bg-info/30 text-info',
    pending: 'bg-warning/20 text-warning',
    ready: 'bg-warning/30 text-warning',
    failed: 'bg-reject/30 text-reject',
    blocked: 'bg-text-muted/20 text-text-muted',
  };

  function pillClass(status: string): string {
    return statusColors[status] ?? 'bg-surface-2 text-text-dim';
  }
</script>

{#if tasks.length > 0}
  <div class="flex flex-wrap gap-1">
    {#each tasks as task}
      <span
        class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-data {pillClass(task.status)}"
        title="{task.title} — {task.status}"
      >
        {task.title.length > 20 ? task.title.slice(0, 20) + '...' : task.title}
      </span>
    {/each}
  </div>
{/if}
