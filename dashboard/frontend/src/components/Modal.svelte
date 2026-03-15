<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    open: boolean;
    title: string;
    onclose: () => void;
    /** Size variant */
    size?: 'sm' | 'md' | 'lg' | 'xl';
    children: Snippet;
  }

  let { open, title, onclose, size = 'md', children }: Props = $props();

  const sizeClasses: Record<string, string> = {
    sm: 'max-w-sm',
    md: 'max-w-lg',
    lg: 'max-w-2xl',
    xl: 'max-w-4xl',
  };

  function onBackdropClick(e: MouseEvent) {
    if (e.target === e.currentTarget) onclose();
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') onclose();
    // Focus trap
    if (e.key === 'Tab' && dialogEl) {
      const focusable = dialogEl.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }
  }

  let dialogEl: HTMLDivElement | undefined = $state(undefined);

  function portal(node: HTMLElement) {
    document.body.appendChild(node);
    // Auto-focus the dialog
    const focusable = node.querySelector<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    focusable?.focus();
    return {
      destroy() {
        node.remove();
      }
    };
  }
</script>

{#if open}
  <div
    use:portal
    class="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-modal"
    role="dialog"
    aria-modal="true"
    aria-labelledby="modal-title"
    tabindex="-1"
    onclick={onBackdropClick}
    onkeydown={onKeydown}
  >
    <div
      bind:this={dialogEl}
      class="glass rounded-xl shadow-2xl w-full {sizeClasses[size]} mx-4 animate-fade-in-up border border-border/50"
    >
      <div class="flex items-center justify-between px-5 py-4 border-b border-border/50">
        <h2 id="modal-title" class="text-lg font-semibold">{title}</h2>
        <button onclick={onclose} class="text-text-dim hover:text-text text-xl cursor-pointer" aria-label="Close">&times;</button>
      </div>
      <div class="px-5 py-4">
        {@render children()}
      </div>
    </div>
  </div>
{/if}
