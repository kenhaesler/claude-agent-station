<script lang="ts">
  import { LogWebSocket } from '../lib/ws';
  import { searchLogs } from '../lib/api';
  import type { LogSearchResult } from '../lib/types';
  import { toastError } from '../lib/toast.svelte';
  import LoadingSpinner from '../components/LoadingSpinner.svelte';
  import StatusOrb from '../components/StatusOrb.svelte';
  import GlassCard from '../components/GlassCard.svelte';

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

<div class="space-y-4 animate-fade-in-up">
  <div class="flex items-center justify-between">
    <h1 class="text-2xl font-bold">Logs</h1>
    <div class="flex items-center gap-2">
      <StatusOrb active={connected} />
      <span class="text-xs text-text-dim">{connected ? 'Connected' : 'Disconnected'}</span>
    </div>
  </div>

  <!-- Controls -->
  <div class="flex flex-wrap gap-3 items-center">
    <button
      onclick={switchToLive}
      class="px-3 py-1.5 text-sm rounded-lg cursor-pointer transition-all {mode === 'live' ? 'bg-gradient-to-r from-accent-blue to-accent-emerald text-white shadow-[0_0_12px_rgba(59,130,246,0.2)]' : 'glass text-text-dim hover:text-text'}"
    >
      Live
    </button>
    <button
      onclick={() => mode = 'search'}
      class="px-3 py-1.5 text-sm rounded-lg cursor-pointer transition-all {mode === 'search' ? 'bg-gradient-to-r from-accent-blue to-accent-emerald text-white shadow-[0_0_12px_rgba(59,130,246,0.2)]' : 'glass text-text-dim hover:text-text'}"
    >
      Search
    </button>

    {#if mode === 'live'}
      <button onclick={togglePause} class="px-3 py-1.5 text-sm glass rounded-lg text-text-dim hover:text-text cursor-pointer transition-colors">
        {paused ? 'Resume' : 'Pause'}
      </button>
      <button onclick={() => { lines = []; }} class="px-3 py-1.5 text-sm glass rounded-lg text-text-dim hover:text-text cursor-pointer transition-colors">
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
          class="flex-1 bg-white/[0.04] border border-border/50 rounded-lg px-3 py-1.5 text-sm text-text focus:outline-none focus:border-accent-blue/50 transition-colors"
        />
        <button type="submit" disabled={searching} class="px-3 py-1.5 bg-gradient-to-r from-accent-blue to-accent-emerald text-white rounded-lg text-sm cursor-pointer">
          {searching ? '...' : 'Search'}
        </button>
      </form>
    {/if}
  </div>

  {#if mode === 'live'}
    <!-- Live Log Viewer -->
    <GlassCard class="p-3 md:p-4 h-[calc(100vh-200px)] md:h-[calc(100vh-240px)] overflow-auto font-data text-xs leading-relaxed">
      <div bind:this={logContainer} class="h-full overflow-auto">
        {#if lines.length === 0}
          <p class="text-text-dim">Waiting for log data...</p>
        {:else}
          {#each lines as line}
            <div class="hover:bg-white/[0.02] py-0.5 break-all">{line}</div>
          {/each}
        {/if}
      </div>
    </GlassCard>
  {:else}
    <!-- Search Results -->
    {#if searching}
      <div class="flex justify-center py-8"><LoadingSpinner /></div>
    {:else if searchResults.length === 0}
      <p class="text-text-dim text-sm py-8 text-center">No results</p>
    {:else}
      <GlassCard class="overflow-hidden">
        <div class="divide-y divide-border/30">
          {#each searchResults as result}
            <div class="px-5 py-3">
              <div class="flex items-center gap-2 mb-1">
                <span class="text-xs text-text-dim font-data">{result.file}:{result.line}</span>
              </div>
              <pre class="text-xs whitespace-pre-wrap break-all text-text-dim">{result.content}</pre>
            </div>
          {/each}
        </div>
      </GlassCard>
    {/if}
  {/if}
</div>
