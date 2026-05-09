<!--
  Auth-prompt modal. Listens for the global ``station-auth-required`` event
  (dispatched by lib/api.ts and lib/ws.ts on 401) and prompts the operator
  for the API key configured server-side via STATION_API_KEY. The key is
  persisted to localStorage under ``station-api-key`` and the page is
  reloaded so all in-flight subscriptions reconnect with the new bearer.

  Without this, enabling auth on the dashboard locks the SPA out — the 401
  event fires into the void with no listener.
-->
<script lang="ts">
  import { onMount } from 'svelte';
  import Modal from './Modal.svelte';
  import { getStoredApiKey, setStoredApiKey, clearStoredApiKey } from '../../lib/api';

  let show = $state(false);
  let value = $state('');
  let invalid = $state(false);

  // Guard: only respond to the first ``station-auth-required`` per session
  // window. Without this a single bad-auth state can fire dozens of events
  // (one per polling endpoint), each spawning a new modal.
  let dismissed = $state(false);

  function open() {
    if (show || dismissed) return;
    value = getStoredApiKey() ?? '';
    invalid = !!value;  // we got here with a stored key, so it's wrong
    show = true;
  }

  function close() {
    show = false;
    dismissed = true;
  }

  function save(e?: Event) {
    e?.preventDefault();
    const key = value.trim();
    if (!key) return;
    setStoredApiKey(key);
    // Hard reload so SSE / WS / polling all reconnect with the new bearer.
    window.location.reload();
  }

  function signOut() {
    clearStoredApiKey();
    value = '';
    invalid = false;
  }

  onMount(() => {
    const handler = () => open();
    window.addEventListener('station-auth-required', handler);
    return () => window.removeEventListener('station-auth-required', handler);
  });
</script>

<Modal {show} onClose={close} title="API key required" maxWidth="max-w-md">
  <form onsubmit={save} class="flex flex-col gap-3">
    <p class="text-xs text-secondary leading-snug">
      The dashboard is configured with <code class="font-mono">STATION_API_KEY</code>.
      Paste it below to authenticate the SPA. The key is stored in this
      browser's <code class="font-mono">localStorage</code> and sent as a
      bearer token on every request.
    </p>

    {#if invalid}
      <p class="text-xs text-red-400 leading-snug">
        The key currently stored is invalid (server returned 401). Replace it
        below or sign out and reload.
      </p>
    {/if}

    <label class="flex flex-col gap-1">
      <span class="text-xs text-tertiary">API key</span>
      <input
        type="password"
        bind:value
        autocomplete="off"
        spellcheck="false"
        autofocus
        placeholder="bf244927…"
        class="bg-bg-elevated border border-border rounded px-2 py-1 text-sm font-mono text-primary"
      />
    </label>

    <div class="flex items-center justify-between gap-2 pt-1">
      <button
        type="button"
        onclick={signOut}
        class="text-xs text-tertiary hover:text-primary transition-colors"
      >
        Clear stored key
      </button>
      <div class="flex gap-2">
        <button
          type="button"
          onclick={close}
          class="px-3 py-1 text-xs text-secondary hover:text-primary transition-colors"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={!value.trim()}
          class="px-3 py-1 text-xs bg-cyan text-void rounded font-medium disabled:opacity-40 disabled:cursor-not-allowed hover:opacity-90 transition-opacity"
        >
          Save & reload
        </button>
      </div>
    </div>
  </form>
</Modal>
