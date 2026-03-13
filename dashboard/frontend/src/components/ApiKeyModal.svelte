<script lang="ts">
  import { getStoredApiKey, setStoredApiKey, clearStoredApiKey } from '../lib/api';

  interface Props {
    show: boolean;
    onClose: () => void;
  }

  let { show, onClose }: Props = $props();

  let inputValue = $state('');
  let hasKey = $derived(getStoredApiKey() !== null);

  function handleSave() {
    if (inputValue.trim()) {
      setStoredApiKey(inputValue.trim());
      inputValue = '';
      window.location.reload();
    }
  }

  function handleClear() {
    clearStoredApiKey();
    window.location.reload();
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') handleSave();
    if (e.key === 'Escape') onClose();
  }
</script>

{#if show}
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
    onkeydown={handleKeydown}
  >
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="absolute inset-0" onclick={onClose}></div>
    <div class="relative bg-surface border border-border rounded-lg shadow-xl p-6 w-full max-w-sm mx-4">
      <h2 class="text-base font-semibold text-text mb-2">API Key Required</h2>
      <p class="text-xs text-text-muted mb-4">
        This station requires authentication. Enter your STATION_API_KEY to continue.
      </p>

      {#if hasKey}
        <div class="flex items-center justify-between mb-4 p-2 rounded bg-white/5 border border-border">
          <span class="text-xs text-approve font-medium">Key is set</span>
          <button
            onclick={handleClear}
            class="px-2 py-1 text-xs rounded bg-red-600/20 text-red-400 hover:bg-red-600/30 cursor-pointer transition-colors"
          >
            Clear Key
          </button>
        </div>
      {/if}

      <input
        type="password"
        bind:value={inputValue}
        placeholder="Enter API key..."
        class="w-full px-3 py-2 text-sm rounded-md bg-bg border border-border text-text placeholder-text-dim focus:outline-none focus:border-accent-blue mb-4"
      />

      <div class="flex justify-end gap-2">
        <button
          onclick={onClose}
          class="px-3 py-1.5 text-xs rounded-md text-text-muted hover:text-text hover:bg-white/5 cursor-pointer transition-colors"
        >
          Cancel
        </button>
        <button
          onclick={handleSave}
          disabled={!inputValue.trim()}
          class="px-3 py-1.5 text-xs rounded-md font-medium text-white bg-accent-blue hover:opacity-90 cursor-pointer transition-all
            {!inputValue.trim() ? 'opacity-50 cursor-not-allowed' : 'active:scale-95'}"
        >
          Save
        </button>
      </div>
    </div>
  </div>
{/if}
