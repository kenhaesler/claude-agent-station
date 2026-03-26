<script lang="ts">
  import type { QueueItem } from '../../lib/types';
  import QueueCard from './QueueCard.svelte';
  import type { Snippet } from 'svelte';

  let {
    title = '',
    color = 'var(--color-text-muted)',
    items = [],
    onItemClick,
  }: {
    title: string;
    color?: string;
    items: QueueItem[];
    onItemClick?: (item: QueueItem) => void;
  } = $props();
</script>

<div class="flex flex-col min-w-[240px] max-w-[300px] shrink-0">
  <!-- Column header -->
  <div class="flex items-center gap-2 px-3 py-2 mb-2">
    <span class="w-2 h-2 rounded-full" style="background: {color}"></span>
    <span class="text-xs font-medium text-text-dim uppercase tracking-wider">{title}</span>
    <span class="text-[10px] text-text-muted data-readout ml-auto">{items.length}</span>
  </div>

  <!-- Cards -->
  <div class="flex flex-col gap-2 overflow-y-auto flex-1 px-1 pb-2">
    {#each items as item (item.id)}
      <QueueCard {item} onclick={() => onItemClick?.(item)} />
    {/each}

    {#if items.length === 0}
      <div class="text-center text-xs text-text-muted py-6 opacity-50">Empty</div>
    {/if}
  </div>
</div>
