<script lang="ts">
  import type { Snippet } from 'svelte';

  let {
    open = false,
    onClose,
    title = '',
    width = 'w-96',
    children,
  }: {
    open: boolean;
    onClose: () => void;
    title?: string;
    width?: string;
    children: Snippet;
  } = $props();
</script>

{#if open}
  <!-- Backdrop -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="fixed inset-0 z-overlay bg-black/30" onclick={onClose}></div>

  <div class="fixed top-0 right-0 bottom-0 z-overlay {width} bg-surface-0 border-l border-border agent-panel-enter overflow-y-auto">
    <!-- Header -->
    <div class="flex items-center justify-between px-4 py-3 border-b border-border sticky top-0 bg-surface-0 z-10">
      <h3 class="text-sm font-semibold text-primary">{title}</h3>
      <button
        onclick={onClose}
        class="text-tertiary hover:text-primary transition-colors text-lg"
        aria-label="Close panel"
      >
        ×
      </button>
    </div>

    <!-- Content -->
    <div class="p-4">
      {@render children()}
    </div>
  </div>
{/if}

<svelte:window onkeydown={(e) => { if (open && e.key === 'Escape') onClose(); }} />
