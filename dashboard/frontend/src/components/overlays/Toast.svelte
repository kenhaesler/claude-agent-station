<script lang="ts">
  import { toasts, removeToast } from '../../lib/toast.svelte';

  const typeStyles: Record<string, { border: string; icon: string }> = {
    success: { border: 'border-l-emerald', icon: '✓' },
    error: { border: 'border-l-rose', icon: '✗' },
    warning: { border: 'border-l-amber', icon: '⚠' },
    info: { border: 'border-l-cyan', icon: 'ℹ' },
  };
</script>

{#if toasts.length > 0}
  <div class="fixed bottom-5 right-5 z-[100] flex flex-col gap-2 max-w-sm" role="status" aria-live="polite">
    {#each toasts as toast (toast.id)}
      {@const style = typeStyles[toast.type] ?? typeStyles.info}
      <button
        class="glass rounded-lg px-4 py-3 text-sm text-primary text-left border-l-2 {style.border}
               shadow-lg animate-slide-up cursor-pointer w-full flex items-start gap-2.5
               hover:bg-surface-1 transition-colors duration-150"
        onclick={() => removeToast(toast.id)}
      >
        <span class="text-xs mt-0.5 opacity-70">{style.icon}</span>
        <span>{toast.text}</span>
      </button>
    {/each}
  </div>
{/if}
