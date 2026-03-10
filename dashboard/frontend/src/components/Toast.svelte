<script lang="ts">
  import { toasts } from '../lib/toast.svelte';

  const typeClasses: Record<string, string> = {
    success: 'border-approve/50 text-approve shadow-[0_0_12px_rgba(34,197,94,0.15)]',
    error: 'border-reject/50 text-reject shadow-[0_0_12px_rgba(239,68,68,0.15)]',
    info: 'border-info/50 text-info shadow-[0_0_12px_rgba(59,130,246,0.15)]',
  };
</script>

{#if toasts.length > 0}
  <div class="fixed bottom-4 right-4 flex flex-col gap-2 z-[60]">
    {#each toasts as t (t.id)}
      <div
        class="glass border rounded-lg px-4 py-2 text-sm max-w-sm animate-fade-in-up {typeClasses[t.type]}"
      >
        {t.text}
      </div>
    {/each}
  </div>
{/if}
