<script lang="ts">
  import { portal } from '../../lib/portal';

  let {
    show = false,
    onClose,
  }: {
    show: boolean;
    onClose: () => void;
  } = $props();

  const shortcuts: { key: string; label: string }[] = [
    { key: '1', label: 'Command Center' },
    { key: '2', label: 'Runs' },
    { key: '3', label: 'Queue' },
    { key: '4', label: 'Projects' },
    { key: '5', label: 'Agent Teams' },
    { key: '6', label: 'Settings' },
    { key: '?', label: 'Toggle this overlay' },
    { key: 'Esc', label: 'Close overlays & modals' },
  ];

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
    use:portal
    class="fixed inset-0 z-modal flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-fade-in-up"
    onclick={handleBackdrop}
  >
    <div
      class="glass rounded-xl max-w-md w-full shadow-2xl"
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
    >
      <div class="flex items-center justify-between px-5 py-3 border-b border-border">
        <h2 class="text-sm font-semibold text-primary">Keyboard shortcuts</h2>
        <button
          onclick={onClose}
          class="text-tertiary hover:text-primary transition-colors text-lg leading-none"
          aria-label="Close"
        >
          ×
        </button>
      </div>
      <ul class="p-4 flex flex-col gap-2">
        {#each shortcuts as s}
          <li class="flex items-center justify-between gap-4 px-2 py-1.5">
            <span class="text-sm text-primary">{s.label}</span>
            <kbd
              class="inline-flex items-center px-2 py-0.5 rounded-md border border-border bg-surface text-xs font-mono text-secondary"
            >{s.key}</kbd>
          </li>
        {/each}
      </ul>
    </div>
  </div>
{/if}
