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
      <div
        class="glass rounded-lg px-4 py-3 text-sm text-primary text-left border-l-2 {style.border}
               shadow-lg animate-slide-up w-full flex items-start gap-2.5"
        role="alert"
      >
        <button
          type="button"
          class="flex items-start gap-2.5 flex-1 text-left cursor-pointer
                 hover:bg-surface-1 transition-colors duration-150 -mx-2 -my-1 px-2 py-1 rounded"
          onclick={() => removeToast(toast.id)}
          aria-label="Dismiss notification"
        >
          <span class="text-xs mt-0.5 opacity-70">{style.icon}</span>
          <span>{toast.text}</span>
        </button>
        {#if toast.action}
          <a
            href={toast.action.href}
            class="text-xs underline underline-offset-2 mt-0.5 whitespace-nowrap hover:text-primary"
            data-testid="toast-action-link"
            onclick={() => removeToast(toast.id)}
          >
            {toast.action.label}
          </a>
        {/if}
      </div>
    {/each}
  </div>
{/if}
