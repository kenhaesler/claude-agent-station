<script lang="ts">
  import { searchCommands, executeCommand, getCommands, type Command } from '../../lib/command-registry';

  let {
    open = false,
    onclose,
  }: {
    open: boolean;
    onclose: () => void;
  } = $props();

  let query = $state('');
  let selectedIndex = $state(0);
  let inputEl: HTMLInputElement | undefined = $state();

  let results = $derived(
    query.trim()
      ? searchCommands(query.trim())
      : getCommands().filter(c => c.category === 'navigation')
  );

  $effect(() => {
    if (open) {
      query = '';
      selectedIndex = 0;
      // Focus input on next tick
      setTimeout(() => inputEl?.focus(), 10);
    }
  });

  // Reset selection when results change
  $effect(() => {
    if (results) selectedIndex = 0;
  });

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      onclose();
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      selectedIndex = Math.min(selectedIndex + 1, results.length - 1);
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      selectedIndex = Math.max(selectedIndex - 1, 0);
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      const cmd = results[selectedIndex];
      if (cmd) {
        executeCommand(cmd.id);
        onclose();
      }
      return;
    }
  }

  function runCommand(cmd: Command) {
    executeCommand(cmd.id);
    onclose();
  }

  const categoryLabels: Record<string, string> = {
    navigation: 'Navigate',
    actions: 'Actions',
    view: 'View',
    system: 'System',
  };
</script>

{#if open}
  <!-- Backdrop -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="fixed inset-0 z-modal bg-black/40 backdrop-blur-sm" onclick={onclose}></div>

  <div class="fixed top-[15%] left-1/2 -translate-x-1/2 z-modal w-full max-w-lg">
    <div class="glass rounded-xl shadow-2xl overflow-hidden border border-border">
      <!-- Search input -->
      <div class="flex items-center gap-2 px-4 py-3 border-b border-border-subtle">
        <span class="text-text-muted text-sm">⌘</span>
        <input
          bind:this={inputEl}
          bind:value={query}
          onkeydown={handleKeydown}
          placeholder="Type a command..."
          class="flex-1 bg-transparent text-sm text-text placeholder:text-text-muted outline-none"
          type="text"
          role="combobox"
          aria-expanded="true"
          aria-controls="palette-results"
          aria-activedescendant={results[selectedIndex]?.id}
        />
        <kbd class="text-[10px] text-text-muted bg-surface-2 px-1.5 py-0.5 rounded">esc</kbd>
      </div>

      <!-- Results -->
      <div id="palette-results" class="max-h-72 overflow-y-auto py-1" role="listbox">
        {#if results.length === 0}
          <div class="px-4 py-6 text-center text-sm text-text-muted">No commands found</div>
        {:else}
          {#each results as cmd, i (cmd.id)}
            <!-- svelte-ignore a11y_click_events_have_key_events -->
            <div
              id={cmd.id}
              class="flex items-center justify-between px-4 py-2 cursor-pointer text-sm
                     {i === selectedIndex ? 'bg-surface-2 text-text' : 'text-text-dim hover:bg-surface'}"
              role="option"
              aria-selected={i === selectedIndex}
              onclick={() => runCommand(cmd)}
              onmouseenter={() => selectedIndex = i}
            >
              <span>{cmd.title}</span>
              <div class="flex items-center gap-2">
                {#if cmd.shortcut}
                  <kbd class="text-[10px] text-text-muted bg-surface-2 px-1.5 py-0.5 rounded font-mono">{cmd.shortcut}</kbd>
                {/if}
                <span class="text-[10px] text-text-muted">{categoryLabels[cmd.category] ?? cmd.category}</span>
              </div>
            </div>
          {/each}
        {/if}
      </div>
    </div>
  </div>
{/if}
