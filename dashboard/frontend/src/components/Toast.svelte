<script lang="ts">
  import { toasts } from '../lib/toast.svelte';

  const typeClasses: Record<string, string> = {
    success: 'border-approve bg-approve/10 text-approve',
    error: 'border-reject bg-reject/10 text-reject',
    info: 'border-info bg-info/10 text-info',
  };
</script>

{#if toasts.length > 0}
  <div class="fixed bottom-4 right-4 flex flex-col gap-2 z-50">
    {#each toasts as t (t.id)}
      <div class="border rounded-lg px-4 py-2 text-sm shadow-lg max-w-sm {typeClasses[t.type]}">
        {t.text}
      </div>
    {/each}
  </div>
{/if}
