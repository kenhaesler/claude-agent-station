<script lang="ts">
  import { getStoredApiKey, setStoredApiKey, clearStoredApiKey } from '../../lib/api';
  import { toastSuccess } from '../../lib/toast.svelte';
  import Modal from './Modal.svelte';

  let {
    show = false,
    onClose,
  }: {
    show: boolean;
    onClose: () => void;
  } = $props();

  let apiKey = $state(getStoredApiKey() ?? '');

  function handleSave() {
    if (apiKey.trim()) {
      setStoredApiKey(apiKey.trim());
      toastSuccess('API key saved');
    } else {
      clearStoredApiKey();
      toastSuccess('API key cleared');
    }
    onClose();
    window.location.reload();
  }
</script>

<Modal {show} {onClose} title="API Key">
  <div class="flex flex-col gap-4">
    <p class="text-sm text-text-dim">
      Enter your Station API key to authenticate with the dashboard backend.
    </p>
    <input
      type="password"
      bind:value={apiKey}
      placeholder="sk-..."
      class="w-full px-3 py-2 rounded-lg bg-bg text-text text-sm border border-border
             focus:border-focus focus:outline-none transition-colors"
      onkeydown={(e) => e.key === 'Enter' && handleSave()}
    />
    <div class="flex gap-2 justify-end">
      <button
        onclick={onClose}
        class="px-4 py-2 rounded-lg text-sm text-text-dim hover:text-text hover:bg-surface-2 transition-colors"
      >
        Cancel
      </button>
      <button
        onclick={handleSave}
        class="px-4 py-2 rounded-lg text-sm font-medium bg-accent-blue text-white hover:bg-accent-blue/80 transition-colors"
      >
        Save
      </button>
    </div>
  </div>
</Modal>
