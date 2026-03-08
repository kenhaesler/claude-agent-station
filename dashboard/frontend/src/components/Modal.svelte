<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    open: boolean;
    title: string;
    onclose: () => void;
    children: Snippet;
  }

  let { open, title, onclose, children }: Props = $props();

  function onBackdropClick(e: MouseEvent) {
    if (e.target === e.currentTarget) onclose();
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') onclose();
  }
</script>

{#if open}
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="fixed inset-0 bg-black/60 flex items-center justify-center z-40"
    onclick={onBackdropClick}
    onkeydown={onKeydown}
  >
    <div class="bg-surface border border-border rounded-xl shadow-2xl w-full max-w-lg mx-4">
      <div class="flex items-center justify-between px-5 py-4 border-b border-border">
        <h2 class="text-lg font-semibold">{title}</h2>
        <button onclick={onclose} class="text-text-dim hover:text-text text-xl cursor-pointer">&times;</button>
      </div>
      <div class="px-5 py-4">
        {@render children()}
      </div>
    </div>
  </div>
{/if}
