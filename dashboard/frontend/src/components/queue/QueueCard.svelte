<script lang="ts">
  import type { QueueItem } from '../../lib/types';

  let {
    item,
    selected = false,
    onclick,
  }: {
    item: QueueItem;
    selected?: boolean;
    onclick?: () => void;
  } = $props();

  const priorityColors: Record<number, string> = {
    10: 'var(--color-reject)',
    7: 'var(--color-warning)',
    5: 'var(--color-info)',
    3: 'var(--color-text-muted)',
    0: 'var(--color-text-muted)',
  };

  function getPriorityColor(p: number): string {
    if (p >= 10) return priorityColors[10];
    if (p >= 7) return priorityColors[7];
    if (p >= 5) return priorityColors[5];
    return priorityColors[0];
  }

  let repoShort = $derived(item.project_repo?.split('/').pop() ?? '');
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
  class="glass rounded-lg p-3 cursor-pointer transition-all duration-fast hover:bg-surface-2/50
         {selected ? 'kb-selected' : ''}"
  {onclick}
>
  <!-- Title -->
  <div class="text-sm text-text font-medium mb-1 line-clamp-2">
    {#if item.issue_number}
      <span class="text-info">#{item.issue_number}</span>
    {/if}
    {item.issue_title ?? 'Untitled'}
  </div>

  <!-- Meta row -->
  <div class="flex items-center gap-2 text-[10px] text-text-muted">
    {#if repoShort}
      <span class="truncate max-w-[100px]">{repoShort}</span>
    {/if}
    {#if item.priority > 0}
      <span class="px-1 py-0.5 rounded" style="background: color-mix(in oklch, {getPriorityColor(item.priority)} 20%, transparent); color: {getPriorityColor(item.priority)}">
        P{item.priority}
      </span>
    {/if}
    {#if item.mode}
      <span class="uppercase tracking-wider">{item.mode}</span>
    {/if}
    {#if item.retry_count > 0}
      <span class="text-warning">retry {item.retry_count}</span>
    {/if}
    {#if item.complexity_score}
      <span>C{item.complexity_score}</span>
    {/if}
  </div>
</div>
