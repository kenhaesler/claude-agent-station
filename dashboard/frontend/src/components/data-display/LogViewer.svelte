<script lang="ts">
  let { runId, logFile }: { runId: string; logFile: string | null } = $props();

  let lines = $state<string[]>([]);
  let connected = $state(false);
  let error = $state<string | null>(null);
  let container: HTMLDivElement | undefined = $state();
  let autoScroll = $state(true);

  $effect(() => {
    if (!logFile) return;

    lines = [];
    error = null;

    const apiKey = localStorage.getItem('station-api-key');
    const params = new URLSearchParams({ file: logFile, from_beginning: 'true' });
    if (apiKey) params.set('token', apiKey);

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${proto}//${location.host}/api/logs/stream?${params}`);

    ws.onopen = () => { connected = true; error = null; };
    ws.onmessage = (e) => {
      lines = [...lines, e.data];
      if (autoScroll && container) {
        requestAnimationFrame(() => {
          container!.scrollTop = container!.scrollHeight;
        });
      }
    };
    ws.onerror = () => { error = 'WebSocket connection failed'; };
    ws.onclose = () => { connected = false; };

    return () => ws.close();
  });
</script>

{#if !logFile}
  <div class="text-center py-12">
    <p class="text-sm text-tertiary">No log file path recorded for this run</p>
    <p class="text-xs text-ghost mt-2">Log paths are recorded for runs started after this update</p>
  </div>
{:else}
  <div class="space-y-3">
    <!-- Status bar -->
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-2">
        <span class="status-dot {connected ? 'running' : 'offline'}"></span>
        <span class="text-xs text-tertiary font-mono">{connected ? 'Streaming' : 'Disconnected'}</span>
      </div>
      <div class="flex items-center gap-3">
        <label class="flex items-center gap-1.5 cursor-pointer">
          <input type="checkbox" bind:checked={autoScroll} class="w-3 h-3 accent-violet rounded" />
          <span class="text-[10px] text-tertiary font-mono">Auto-scroll</span>
        </label>
        <span class="text-[10px] text-ghost font-mono truncate max-w-[300px]" title={logFile}>{logFile}</span>
      </div>
    </div>

    <!-- Log content -->
    <div
      bind:this={container}
      class="rounded-xl p-4 font-mono text-xs leading-relaxed max-h-[600px] overflow-auto"
      style="background: rgba(6, 6, 12, 0.6); backdrop-filter: blur(16px); border: 1px solid rgba(255,255,255,0.03);"
    >
      {#if error}
        <p class="text-rose">{error}</p>
      {:else if lines.length === 0}
        <p class="text-ghost animate-glow-pulse">Waiting for log data...</p>
      {:else}
        {#each lines as line, i}
          <div class="whitespace-pre-wrap break-all py-px hover:bg-white/[0.02] transition-colors"
               class:text-rose={line.includes('ERROR') || line.includes('error')}
               class:text-amber={line.includes('WARN') || line.includes('warn')}
               class:text-emerald={line.includes('OK') || line.includes('success')}
               class:text-secondary={!line.includes('ERROR') && !line.includes('WARN') && !line.includes('OK')}
          >{line}</div>
        {/each}
      {/if}
    </div>
  </div>
{/if}
