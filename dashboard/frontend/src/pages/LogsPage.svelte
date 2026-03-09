<script lang="ts">
  import { LogWebSocket } from '../lib/ws';
  import { searchLogs } from '../lib/api';
  import type { LogSearchResult } from '../lib/types';
  import { toastError } from '../lib/toast.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';

  let lines = $state<string[]>([]);
  let connected = $state(false);
  let paused = $state(false);
  let searchQuery = $state('');
  let searchResults = $state<LogSearchResult[]>([]);
  let searching = $state(false);
  let mode = $state<'live' | 'search'>('live');

  let logContainer: HTMLElement;
  let autoScroll = $state(true);

  let ws: LogWebSocket | null = null;

  function startWs() {
    ws?.disconnect();
    lines = [];
    ws = new LogWebSocket(
      '/api/logs/stream',
      (data) => {
        lines.push(data);
        if (lines.length > 2000) lines.splice(0, lines.length - 2000);
        if (autoScroll && logContainer) {
          requestAnimationFrame(() => {
            logContainer.scrollTop = logContainer.scrollHeight;
          });
        }
      },
      (status) => { connected = status; }
    );
    ws.connect();
  }

  function togglePause() {
    if (!ws) return;
    paused = !paused;
    if (paused) ws.pause();
    else ws.resume();
  }

  async function handleSearch() {
    if (!searchQuery.trim()) return;
    mode = 'search';
    searching = true;
    try {
      const res = await searchLogs(searchQuery);
      searchResults = res.results;
    } catch (e: any) {
      toastError(e.message);
    } finally {
      searching = false;
    }
  }

  function switchToLive() {
    mode = 'live';
    searchResults = [];
  }

  $effect(() => {
    startWs();
    return () => ws?.disconnect();
  });
</script>

<div class="space-y-4">
  <div class="flex items-center justify-between">
    <h1 class="text-2xl font-bold">Logs</h1>
    <div class="flex items-center gap-2">
      <span class="w-2 h-2 rounded-full {connected ? 'bg-approve' : 'bg-reject'}"></span>
      <span class="text-xs text-text-dim">{connected ? 'Connected' : 'Disconnected'}</span>
    </div>
  </div>

  <!-- Controls -->
  <div class="flex flex-wrap gap-3 items-center">
    <button
      onclick={switchToLive}
      class="px-3 py-1.5 text-sm rounded-lg cursor-pointer {mode === 'live' ? 'bg-pr text-white' : 'bg-surface-2 text-text-dim hover:text-text'}"
    >
      Live
    </button>
    <button
      onclick={() => mode = 'search'}
      class="px-3 py-1.5 text-sm rounded-lg cursor-pointer {mode === 'search' ? 'bg-pr text-white' : 'bg-surface-2 text-text-dim hover:text-text'}"
    >
      Search
    </button>

    {#if mode === 'live'}
      <button onclick={togglePause} class="px-3 py-1.5 text-sm bg-surface-2 rounded-lg text-text-dim hover:text-text cursor-pointer">
        {paused ? 'Resume' : 'Pause'}
      </button>
      <button onclick={() => { lines = []; }} class="px-3 py-1.5 text-sm bg-surface-2 rounded-lg text-text-dim hover:text-text cursor-pointer">
        Clear
      </button>
      <label class="flex items-center gap-1 text-xs text-text-dim">
        <input type="checkbox" bind:checked={autoScroll} />
        Auto-scroll
      </label>
    {/if}

    {#if mode === 'search'}
      <form onsubmit={(e) => { e.preventDefault(); handleSearch(); }} class="flex gap-2 flex-1">
        <input
          bind:value={searchQuery}
          placeholder="Search log content..."
          class="flex-1 bg-surface-2 border border-border rounded-lg px-3 py-1.5 text-sm text-text focus:outline-none focus:border-pr"
        />
        <button type="submit" disabled={searching} class="px-3 py-1.5 bg-pr text-white rounded-lg text-sm cursor-pointer">
          {searching ? '...' : 'Search'}
        </button>
      </form>
    {/if}
  </div>

  {#if mode === 'live'}
    <!-- Live Log Viewer -->
    <div
      bind:this={logContainer}
      class="bg-surface rounded-xl border border-border p-3 md:p-4 h-[calc(100vh-200px)] md:h-[calc(100vh-240px)] overflow-auto font-mono text-xs leading-relaxed"
    >
      {#if lines.length === 0}
        <p class="text-text-dim">Waiting for log data...</p>
      {:else}
        {#each lines as line}
          <div class="hover:bg-surface-2/30 py-0.5 break-all">{line}</div>
        {/each}
      {/if}
    </div>
  {:else}
    <!-- Search Results -->
    {#if searching}
      <div class="flex justify-center py-8"><LoadingSpinner /></div>
    {:else if searchResults.length === 0}
      <p class="text-text-dim text-sm py-8 text-center">No results</p>
    {:else}
      <div class="bg-surface rounded-xl border border-border overflow-hidden">
        <div class="divide-y divide-border">
          {#each searchResults as result}
            <div class="px-5 py-3">
              <div class="flex items-center gap-2 mb-1">
                <span class="text-xs text-text-dim font-mono">{result.file}:{result.line}</span>
              </div>
              <pre class="text-xs whitespace-pre-wrap break-all text-text-dim">{result.content}</pre>
            </div>
          {/each}
        </div>
      </div>
    {/if}
  {/if}
</div>
