<script lang="ts">
  import type { QueueItem } from '../../lib/types';
  import { shortRepo } from '../../lib/format';

  let {
    items,
    onItemClick,
  }: {
    items: QueueItem[];
    onItemClick?: (item: QueueItem) => void;
  } = $props();

  interface Column {
    label: string;
    color: string;
    borderColor: string;
    states: string[];
  }

  const columns: Column[] = [
    { label: 'Queued', color: 'var(--color-amber)', borderColor: 'var(--color-amber)', states: ['pending', 'assigned', 'claimed', 'planning'] },
    { label: 'Active', color: 'var(--color-violet)', borderColor: 'var(--color-violet)', states: ['in_progress'] },
    { label: 'Review', color: 'var(--color-indigo)', borderColor: 'var(--color-indigo)', states: ['review', 'verifying'] },
    { label: 'Done', color: 'var(--color-emerald)', borderColor: 'var(--color-emerald)', states: ['approved', 'completed', 'rejected', 'escalated', 'paused', 'failed', 'cancelled'] },
  ];

  const MAX_VISIBLE = 3;

  function getColumnItems(col: Column): QueueItem[] {
    return items.filter(item => col.states.includes(item.state));
  }

  function getPriorityColor(priority: number): string {
    if (priority >= 4) return 'var(--color-rose)';
    if (priority >= 3) return 'var(--color-amber)';
    if (priority >= 2) return 'var(--color-indigo)';
    return 'var(--color-tertiary)';
  }

  function getItemTitle(item: QueueItem): string {
    if (item.issue_title) return item.issue_title;
    if (item.issue_number) return `#${item.issue_number}`;
    return `Task #${item.id}`;
  }

  function isBlocked(item: QueueItem): boolean {
    return ['rejected', 'escalated', 'paused', 'failed', 'cancelled'].includes(item.state);
  }
</script>

<div class="grid grid-cols-4 gap-2">
  {#each columns as col}
    {@const colItems = getColumnItems(col)}
    <div class="flex flex-col min-w-0">
      <!-- Column header -->
      <div
        class="flex items-center justify-between px-2 py-1.5 mb-2"
        style="border-top: 2px solid {col.borderColor};"
      >
        <span class="text-[10px] font-semibold text-secondary uppercase tracking-wider truncate">{col.label}</span>
        {#if colItems.length > 0}
          <span
            class="badge text-[9px] py-0 px-1.5 shrink-0 ml-1"
            style="background: {col.color}15; color: {col.color};"
          >
            {colItems.length}
          </span>
        {/if}
      </div>

      <!-- Column items -->
      <div class="flex flex-col gap-1.5">
        {#if colItems.length === 0}
          <div class="text-[10px] text-ghost font-mono text-center py-3 opacity-50">--</div>
        {:else}
          {#each colItems.slice(0, MAX_VISIBLE) as item (item.id)}
            <button
              class="card p-2 text-left cursor-pointer hover:border-border-hover transition-colors duration-100"
              title={getItemTitle(item)}
              onclick={() => onItemClick?.(item)}
            >
              <div class="flex items-start gap-1.5">
                <div
                  class="w-1.5 h-1.5 rounded-full mt-1.5 shrink-0"
                  style="background: {isBlocked(item) ? 'var(--color-rose)' : getPriorityColor(item.priority)};"
                ></div>
                <div class="min-w-0 flex-1">
                  <div class="text-xs text-primary truncate">{getItemTitle(item)}</div>
                  <div class="text-[10px] text-tertiary font-mono truncate mt-0.5">
                    {shortRepo(item.project_repo)}
                  </div>
                </div>
              </div>
            </button>
          {/each}

          {#if colItems.length > MAX_VISIBLE}
            <div class="text-[10px] text-tertiary font-mono text-center py-1">
              +{colItems.length - MAX_VISIBLE} more
            </div>
          {/if}
        {/if}
      </div>
    </div>
  {/each}
</div>
