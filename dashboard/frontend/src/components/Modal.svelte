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

  function portal(node: HTMLElement) {
    document.body.appendChild(node);
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
    class="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-[999]"
    role="dialog"
    aria-modal="true"
    aria-labelledby="modal-title"
    tabindex="-1"
    onclick={onBackdropClick}
    onkeydown={onKeydown}
  >
    <div class="glass rounded-xl shadow-2xl w-full max-w-lg mx-4 animate-fade-in-up border border-border/50">
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
