<script lang="ts">
  import { pauseRun, resumeRun, stopRun, pauseAll, resumeAll, getGlobalPause } from '../../lib/api';
  import { addToast } from '../../lib/toast.svelte';
  import { agentPresence } from '../../lib/agent-presence.svelte';

  let { runId = '' }: { runId: string } = $props();

  let pausing = $state(false);
  let stopping = $state(false);
  let globalPausing = $state(false);
  let runPaused = $derived(agentPresence.pausedRuns.has(runId));
  let globalPause = $derived(agentPresence.globalPause);

  // Load initial global pause state once.
  $effect(() => {
    (async () => {
      try {
        const s = await getGlobalPause();
        agentPresence.globalPause = s.global_pause;
      } catch { /* ignore */ }
    })();
  });

  async function onPauseRun() {
    if (!runId || pausing) return;
    pausing = true;
    try {
      if (runPaused) {
        await resumeRun(runId);
        addToast('success', `Resume requested for ${runId}`);
      } else {
        await pauseRun(runId);
        addToast('success', `Pause requested for ${runId}`);
      }
    } finally {
      pausing = false;
    }
  }

  async function onStopRun() {
    if (!runId || stopping) return;
    const ok = confirm(`Stop run ${runId}? The agent will halt after its next tool call and finish with status=interrupted.`);
    if (!ok) return;
    stopping = true;
    try {
      await stopRun(runId);
      addToast('success', `Stop requested for ${runId}`);
    } finally {
      stopping = false;
    }
  }

  async function onPauseAll() {
    if (globalPausing) return;
    const ok = globalPause
      ? true
      : confirm('Pause ALL runs? Every next tool call on every run will block until you approve it in the permission tray.');
    if (!ok) return;
    globalPausing = true;
    try {
      if (globalPause) {
        const s = await resumeAll();
        agentPresence.globalPause = s.global_pause;
        addToast('success', 'Global pause cleared');
      } else {
        const s = await pauseAll();
        agentPresence.globalPause = s.global_pause;
        addToast('success', 'All agents paused — approve each tool call via the tray');
      }
    } finally {
      globalPausing = false;
    }
  }
</script>

<div class="flex flex-wrap items-center gap-2 p-3 border-b border-border bg-surface-1">
  <div class="text-xs text-tertiary mr-2 uppercase tracking-wider">Intervene</div>

  <button
    type="button"
    onclick={onPauseRun}
    disabled={pausing || !runId}
    class="px-3 py-1.5 rounded text-xs font-medium transition-colors
           {runPaused
             ? 'bg-accent-yellow/30 text-accent-yellow hover:bg-accent-yellow/40'
             : 'bg-accent-yellow/15 text-accent-yellow hover:bg-accent-yellow/25'}
           disabled:opacity-40 disabled:cursor-not-allowed"
    title={runPaused ? 'Resume this run' : 'Route this run\'s next tool call to the tray'}
  >
    {#if pausing}
      …
    {:else if runPaused}
      ▶ Resume run
    {:else}
      ⏸ Pause run
    {/if}
  </button>

  <button
    type="button"
    onclick={onStopRun}
    disabled={stopping || !runId}
    class="px-3 py-1.5 rounded text-xs font-medium bg-reject/15 text-reject
           hover:bg-reject/25 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
    title="Interrupt this run cooperatively (finishes with status=interrupted)"
  >
    {stopping ? '…' : '⏹ Stop run'}
  </button>

  <div class="h-5 w-px bg-border mx-1"></div>

  <button
    type="button"
    onclick={onPauseAll}
    disabled={globalPausing}
    class="px-3 py-1.5 rounded text-xs font-medium transition-colors
           {globalPause
             ? 'bg-reject/25 text-reject hover:bg-reject/35 ring-1 ring-reject'
             : 'bg-reject/10 text-reject hover:bg-reject/20'}
           disabled:opacity-40 disabled:cursor-not-allowed"
    title={globalPause ? 'Release the global kill-switch' : 'Freeze every tool call on every run'}
  >
    {#if globalPausing}
      …
    {:else if globalPause}
      ▶ Resume all
    {:else}
      ⚠ Pause all agents
    {/if}
  </button>

  {#if globalPause}
    <span class="text-[10px] text-reject uppercase tracking-wider ml-2">
      Global pause active — approve every call in the tray
    </span>
  {:else if runPaused}
    <span class="text-[10px] text-accent-yellow uppercase tracking-wider ml-2">
      Run paused — next tool call will wait for approval
    </span>
  {/if}
</div>
