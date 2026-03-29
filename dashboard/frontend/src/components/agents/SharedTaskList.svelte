<script lang="ts">
  import type { CoordinatorTask } from '../../lib/types';

  let {
    tasks = [],
  }: {
    tasks?: CoordinatorTask[];
  } = $props();

  function statusIcon(status: string): string {
    if (status === 'completed') return '\u2713';
    if (status === 'running') return '\u25CF';
    if (status === 'ready') return '\u25CB';
    if (status === 'pending') return '\u25CB';
    if (status === 'failed') return '\u2717';
    if (status === 'blocked') return '\u2298';
    return '\u25CB';
  }

  function statusColor(status: string): string {
    if (status === 'completed') return 'text-approve';
    if (status === 'running') return 'text-cyan animate-pulse';
    if (status === 'failed') return 'text-reject';
    if (status === 'blocked') return 'text-amber';
    return 'text-tertiary/40';
  }
</script>

<div class="flex flex-col h-full">
  <div class="flex items-center justify-between px-3 py-2 border-b border-border shrink-0">
    <span class="text-[10px] text-tertiary uppercase tracking-wider font-mono">Tasks</span>
    <span class="text-[10px] text-tertiary font-mono">
      {tasks.filter(t => t.status === 'completed').length}/{tasks.length}
    </span>
  </div>

  <div class="flex-1 overflow-y-auto px-3 py-2 space-y-1">
    {#each tasks as task}
      <div class="flex items-start gap-2 text-[11px] font-mono py-0.5" title={task.title}>
        <span class="shrink-0 {statusColor(task.status)}">{statusIcon(task.status)}</span>
        <span class="truncate {task.status === 'completed' ? 'text-tertiary/40 line-through' : 'text-secondary'}">
          {task.title}
        </span>
        {#if task.claimed_by}
          <span class="shrink-0 text-[9px] text-tertiary ml-auto">T{(task.claimed_by ?? 0)}</span>
        {/if}
      </div>
    {:else}
      <div class="text-[11px] font-mono text-tertiary italic text-center py-4">
        No tasks assigned
      </div>
    {/each}
  </div>
</div>
