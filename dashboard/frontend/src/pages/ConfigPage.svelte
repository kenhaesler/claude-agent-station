<script lang="ts">
  import { getConfig } from '../lib/api';
  import { toastError } from '../lib/toast.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';

  let config = $state<Record<string, unknown> | null>(null);
  let loading = $state(true);

  async function load() {
    try {
      config = await getConfig();
    } catch (e: any) {
      toastError(e.message);
    } finally {
      loading = false;
    }
  }

  $effect(() => { load(); });
</script>

<div class="space-y-6">
  <div class="flex items-center justify-between">
    <h1 class="text-2xl font-bold">Configuration</h1>
    <button onclick={load} class="px-3 py-1.5 text-sm bg-surface-2 rounded-lg text-text-dim hover:text-text cursor-pointer">
      Refresh
    </button>
  </div>

  {#if loading}
    <div class="flex justify-center py-12"><LoadingSpinner /></div>
  {:else if config}
    <div class="bg-surface rounded-xl border border-border p-5">
      <pre class="text-sm font-mono whitespace-pre-wrap overflow-x-auto text-text-dim">{JSON.stringify(config, null, 2)}</pre>
    </div>
    <p class="text-xs text-text-dim">
      Source: <code>manager-config.json</code>. Edit the file directly or manage projects via the Projects page.
    </p>
  {:else}
    <p class="text-text-dim">Unable to load configuration</p>
  {/if}
</div>
