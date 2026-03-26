<script lang="ts">
  import { untrack } from 'svelte';

  let {
    columns,
    rows,
    onRowClick,
    selectedIndex = -1,
  }: {
    columns: { key: string; label: string; sortable?: boolean; align?: 'left' | 'right' }[];
    rows: Record<string, any>[];
    onRowClick?: (row: any) => void;
    selectedIndex?: number;
  } = $props();

  let sortKey = $state('');
  let sortDir = $state<'asc' | 'desc'>('asc');
  let sel = $state(selectedIndex);

  // Sync external selectedIndex changes
  $effect(() => {
    sel = selectedIndex;
  });

  let sorted = $derived.by(() => {
    if (!sortKey) return rows;
    const dir = sortDir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = a[sortKey] ?? '';
      const bv = b[sortKey] ?? '';
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dir;
      return String(av).localeCompare(String(bv)) * dir;
    });
  });

  function toggleSort(key: string) {
    if (sortKey === key) {
      sortDir = sortDir === 'asc' ? 'desc' : 'asc';
    } else {
      sortKey = key;
      sortDir = 'asc';
    }
  }

  function handleKeydown(e: KeyboardEvent) {
    if (sorted.length === 0) return;
    if (e.key === 'j' || e.key === 'ArrowDown') {
      e.preventDefault();
      sel = Math.min(sel + 1, sorted.length - 1);
    } else if (e.key === 'k' || e.key === 'ArrowUp') {
      e.preventDefault();
      sel = Math.max(sel - 1, 0);
    } else if (e.key === 'Enter' && sel >= 0 && sel < sorted.length) {
      e.preventDefault();
      onRowClick?.(sorted[sel]);
    }
  }
</script>

<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div
  class="glass rounded-lg overflow-hidden"
  tabindex="0"
  onkeydown={handleKeydown}
  role="grid"
>
  <div class="overflow-x-auto">
    <table class="w-full text-sm">
      <thead>
        <tr class="border-b border-border-subtle">
          {#each columns as col}
            <th
              class="px-3 py-2 text-xs font-medium text-text-muted whitespace-nowrap
                     {col.align === 'right' ? 'text-right' : 'text-left'}
                     {col.sortable ? 'cursor-pointer select-none hover:text-text-dim' : ''}"
              onclick={() => col.sortable && toggleSort(col.key)}
            >
              {col.label}
              {#if col.sortable && sortKey === col.key}
                <span class="ml-0.5">{sortDir === 'asc' ? '\u25B2' : '\u25BC'}</span>
              {/if}
            </th>
          {/each}
        </tr>
      </thead>
      <tbody>
        {#each sorted as row, i}
          <tr
            class="border-b border-border-subtle/50 transition-colors duration-100
                   {onRowClick ? 'cursor-pointer hover:bg-surface-2' : ''}
                   {i === sel ? 'kb-selected bg-surface-2' : ''}"
            onclick={() => onRowClick?.(row)}
            role={onRowClick ? 'button' : undefined}
          >
            {#each columns as col}
              <td
                class="px-3 py-2 whitespace-nowrap text-text-dim
                       {col.align === 'right' ? 'text-right' : 'text-left'}"
              >{row[col.key] ?? '--'}</td>
            {/each}
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
</div>
