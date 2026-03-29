<script lang="ts">
  import type { Snippet } from 'svelte';

  let {
    show = false,
    onClose,
    title = '',
    maxWidth = 'max-w-lg',
    children,
  }: {
    show: boolean;
    onClose: () => void;
    title?: string;
    maxWidth?: string;
    children: Snippet;
  } = $props();

  function handleBackdrop(e: MouseEvent) {
    if (e.target === e.currentTarget) onClose();
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') onClose();
  }
</script>

<svelte:window onkeydown={show ? handleKeydown : undefined} />

{#if show}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="fixed inset-0 z-modal flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-fade-in-up"
    onclick={handleBackdrop}
  >
    <div class="glass rounded-xl {maxWidth} w-full shadow-2xl" role="dialog" aria-modal="true" aria-label={title}>
      {#if title}
        <div class="flex items-center justify-between px-5 py-3 border-b border-border">
          <h2 class="text-sm font-semibold text-primary">{title}</h2>
          <button
            onclick={onClose}
            class="text-tertiary hover:text-primary transition-colors text-lg leading-none"
            aria-label="Close"
          >
            ×
          </button>
        </div>
      {/if}
      <div class="p-5">
        {@render children()}
      </div>
    </div>
  </div>
{/if}
