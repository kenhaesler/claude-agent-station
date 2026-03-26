<script lang="ts">
  import type { Snippet } from 'svelte';

  let {
    direction = 'vertical',
    initialSplit = 60,
    minSize = 100,
    top,
    bottom,
    left,
    right,
  }: {
    direction?: 'vertical' | 'horizontal';
    initialSplit?: number;
    minSize?: number;
    top?: Snippet;
    bottom?: Snippet;
    left?: Snippet;
    right?: Snippet;
  } = $props();

  let split = $state(initialSplit);
  let dragging = $state(false);
  let containerEl: HTMLDivElement | undefined = $state();

  function startDrag(e: MouseEvent) {
    dragging = true;
    e.preventDefault();
  }

  function onMove(e: MouseEvent) {
    if (!dragging || !containerEl) return;
    const rect = containerEl.getBoundingClientRect();
    if (direction === 'vertical') {
      const pct = ((e.clientY - rect.top) / rect.height) * 100;
      split = Math.max((minSize / rect.height) * 100, Math.min(100 - (minSize / rect.height) * 100, pct));
    } else {
      const pct = ((e.clientX - rect.left) / rect.width) * 100;
      split = Math.max((minSize / rect.width) * 100, Math.min(100 - (minSize / rect.width) * 100, pct));
    }
  }

  function stopDrag() {
    dragging = false;
  }
</script>

<svelte:window onmousemove={dragging ? onMove : undefined} onmouseup={dragging ? stopDrag : undefined} />

<div
  bind:this={containerEl}
  class="flex h-full {direction === 'vertical' ? 'flex-col' : 'flex-row'}"
>
  <!-- First pane -->
  <div style="flex: 0 0 {split}%; overflow: auto;">
    {#if direction === 'vertical' && top}
      {@render top()}
    {:else if left}
      {@render left()}
    {/if}
  </div>

  <!-- Divider -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="{direction === 'vertical' ? 'h-1 cursor-row-resize w-full' : 'w-1 cursor-col-resize h-full'}
           bg-border-subtle hover:bg-info/40 transition-colors shrink-0
           {dragging ? 'bg-info/60' : ''}"
    onmousedown={startDrag}
  ></div>

  <!-- Second pane -->
  <div style="flex: 1; overflow: auto;">
    {#if direction === 'vertical' && bottom}
      {@render bottom()}
    {:else if right}
      {@render right()}
    {/if}
  </div>
</div>
