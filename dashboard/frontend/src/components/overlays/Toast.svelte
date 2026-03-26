<script lang="ts">
  import { toasts } from '../../lib/toast.svelte';

  const typeColors: Record<string, string> = {
    success: 'border-l-approve',
    error: 'border-l-reject',
    info: 'border-l-info',
  };

  function dismiss(id: number) {
    const idx = toasts.findIndex(t => t.id === id);
    if (idx !== -1) toasts.splice(idx, 1);
  }
</script>

{#if toasts.length > 0}
  <div class="fixed bottom-4 right-4 z-modal flex flex-col gap-2 max-w-sm" role="status" aria-live="polite">
    {#each toasts as toast (toast.id)}
      <button
        class="glass rounded-lg px-4 py-3 text-sm text-text text-left border-l-2 {typeColors[toast.type] ?? 'border-l-info'}
               shadow-xl animate-slide-in-right cursor-pointer w-full"
        onclick={() => dismiss(toast.id)}
      >
        {toast.text}
      </button>
    {/each}
  </div>
{/if}
