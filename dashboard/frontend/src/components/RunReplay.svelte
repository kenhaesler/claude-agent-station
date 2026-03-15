<script lang="ts">
  import { ReplayController, type ReplayStatus, type ReplayEvent } from '../lib/replay-controller';
  import { formatDuration } from '../lib/format';

  interface Props {
    runId: string;
  }

  let { runId }: Props = $props();

  let controller: ReplayController | null = $state(null);
  let status = $state<ReplayStatus>({
    state: 'idle',
    currentTime: 0,
    totalDuration: 0,
    speed: 25,
    eventIndex: 0,
    totalEvents: 0,
    progress: 0,
  });
  let replayLog = $state<string[]>([]);
  let timelineEl: HTMLDivElement | undefined = $state(undefined);

  $effect(() => {
    controller = new ReplayController({
      onStatusChange: (s) => { status = s; },
      onLogEvent: (evt) => {
        const label = evt.type === 'assistant_tool_use'
          ? `Tool: ${evt.toolName}`
          : evt.type === 'assistant_thinking'
          ? 'Thinking...'
          : evt.type === 'assistant_text'
          ? `Text: ${(evt.text ?? '').slice(0, 50)}`
          : evt.type === 'result'
          ? `Result: ${evt.resultStatus}`
          : evt.type;
        replayLog = [...replayLog.slice(-50), label];
      },
    });

    controller.load(runId);

    return () => {
      controller?.destroy();
      controller = null;
    };
  });

  function handleTimelineClick(e: MouseEvent) {
    if (!timelineEl || !controller) return;
    const rect = timelineEl.getBoundingClientRect();
    const progress = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    controller.scrubTo(progress);
  }

  function handleKeydown(e: KeyboardEvent) {
    if (!controller) return;
    if (e.key === ' ') { e.preventDefault(); controller.togglePlayPause(); }
    if (e.key === 'ArrowRight') { e.preventDefault(); controller.stepForward(); }
    if (e.key === 'ArrowLeft') { e.preventDefault(); controller.stepBackward(); }
    if (e.key === '[') { e.preventDefault(); controller.cycleSpeed(); }
    if (e.key === ']') { e.preventDefault(); controller.cycleSpeed(); }
  }

  let events = $derived((controller as import('../lib/replay-controller').ReplayController | null)?.getEvents() ?? []);

  // Build density heatmap for timeline
  let densityBuckets = $derived((() => {
    if (events.length === 0 || status.totalDuration === 0) return [];
    const BUCKETS = 60;
    const bucketWidth = status.totalDuration / BUCKETS;
    const counts = new Array(BUCKETS).fill(0);
    for (const evt of events) {
      const idx = Math.min(Math.floor(evt.compressedTime / bucketWidth), BUCKETS - 1);
      counts[idx]++;
    }
    const max = Math.max(1, ...counts);
    return counts.map(c => c / max);
  })());
</script>

<svelte:window onkeydown={handleKeydown} />

<div class="space-y-3">
  {#if status.state === 'loading'}
    <div class="text-center py-8 text-xs text-text-muted">Loading replay data...</div>
  {:else if status.totalEvents === 0}
    <div class="text-center py-8 text-xs text-text-muted">No replay data available for this run</div>
  {:else}
    <!-- Timeline bar -->
    <div class="glass rounded-lg p-3 border border-border/50">
      <!-- Density heatmap + playhead -->
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <div
        bind:this={timelineEl}
        class="relative h-8 rounded cursor-pointer bg-surface-2 overflow-hidden"
        onclick={handleTimelineClick}
        role="slider"
        aria-label="Replay timeline"
        aria-valuenow={Math.round(status.progress * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
        tabindex="0"
      >
        <!-- Density bars -->
        <div class="absolute inset-0 flex items-end">
          {#each densityBuckets as density, i}
            <div
              class="flex-1 bg-info/30"
              style="height: {Math.max(2, density * 100)}%"
            ></div>
          {/each}
        </div>

        <!-- Playhead -->
        <div
          class="absolute top-0 bottom-0 w-0.5 bg-text z-10 transition-none"
          style="left: {status.progress * 100}%"
        >
          <div class="absolute -top-1 -left-1 w-2.5 h-2.5 rounded-full bg-text"></div>
        </div>

        <!-- Progress fill -->
        <div
          class="absolute top-0 bottom-0 left-0 bg-info/10"
          style="width: {status.progress * 100}%"
        ></div>
      </div>

      <!-- Controls row -->
      <div class="flex items-center justify-between mt-2">
        <div class="flex items-center gap-2">
          <!-- Play/Pause -->
          <button
            onclick={() => controller?.togglePlayPause()}
            class="w-7 h-7 flex items-center justify-center rounded-md bg-surface-2 text-text hover:bg-surface-3 cursor-pointer transition-colors"
            title={status.state === 'playing' ? 'Pause' : 'Play'}
          >
            {#if status.state === 'playing'}
              <svg class="w-3.5 h-3.5" viewBox="0 0 16 16" fill="currentColor">
                <rect x="3" y="2" width="4" height="12" rx="1" />
                <rect x="9" y="2" width="4" height="12" rx="1" />
              </svg>
            {:else}
              <svg class="w-3.5 h-3.5" viewBox="0 0 16 16" fill="currentColor">
                <path d="M4 2l10 6-10 6z" />
              </svg>
            {/if}
          </button>

          <!-- Step backward/forward -->
          <button
            onclick={() => controller?.stepBackward()}
            class="w-6 h-6 flex items-center justify-center rounded text-text-dim hover:text-text cursor-pointer text-xs"
            title="Step backward"
          >⏮</button>
          <button
            onclick={() => controller?.stepForward()}
            class="w-6 h-6 flex items-center justify-center rounded text-text-dim hover:text-text cursor-pointer text-xs"
            title="Step forward"
          >⏭</button>

          <!-- Speed -->
          <button
            onclick={() => controller?.cycleSpeed()}
            class="px-2 py-1 text-[10px] font-data text-text-dim hover:text-text rounded border border-border-subtle cursor-pointer"
            title="Change speed"
          >
            {status.speed}x
          </button>
        </div>

        <!-- Time display -->
        <div class="text-[10px] text-text-muted font-data">
          {formatDuration(status.currentTime)} / {formatDuration(status.totalDuration)}
          <span class="ml-2">{status.eventIndex}/{status.totalEvents} events</span>
        </div>
      </div>
    </div>

    <!-- Replay log feed -->
    <div class="glass rounded-lg p-3 border border-border/50 max-h-[200px] overflow-auto">
      <h4 class="text-[10px] text-text-muted uppercase tracking-wider mb-2">Replay Feed</h4>
      {#if replayLog.length === 0}
        <p class="text-xs text-text-muted">Press play to start replay</p>
      {:else}
        <div class="space-y-0.5">
          {#each replayLog as line, i}
            <div class="text-[11px] font-data text-text-dim {i === replayLog.length - 1 ? 'text-text' : ''}">
              {line}
            </div>
          {/each}
        </div>
      {/if}
    </div>

    <!-- Keyboard hints -->
    <div class="flex items-center gap-3 text-[10px] text-text-muted">
      <span><kbd class="px-1 py-0.5 rounded border border-border-subtle">Space</kbd> Play/Pause</span>
      <span><kbd class="px-1 py-0.5 rounded border border-border-subtle">←/→</kbd> Step</span>
      <span><kbd class="px-1 py-0.5 rounded border border-border-subtle">[/]</kbd> Speed</span>
    </div>
  {/if}
</div>
