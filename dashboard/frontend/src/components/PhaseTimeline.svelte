<script lang="ts">
  import type { RunPhase } from '../lib/workspace-renderer';

  interface Props {
    phase: RunPhase;
    startedAt: string | null;
  }

  let { phase, startedAt }: Props = $props();

  const phases = [
    { key: 'coordinating', label: 'Coordinating', color: 'rgb(168, 85, 247)' },
    { key: 'employee', label: 'Employees Working', color: 'rgb(59, 130, 246)' },
    { key: 'manager_review', label: 'Review', color: 'rgb(245, 158, 11)' },
    { key: 'executing_verdict', label: 'Verdict', color: 'rgb(16, 185, 129)' },
    { key: 'complete', label: 'Complete', color: 'rgb(34, 197, 94)' },
  ];

  // Map RunPhase to phase index
  function getPhaseIndex(p: RunPhase): number {
    switch (p) {
      case 'idle': return -1;
      case 'coordinating': return 0;
      case 'employee': return 1;
      case 'manager_review': return 2;
      case 'executing_verdict': return 3;
      default: return -1;
    }
  }

  let activeIndex = $derived(getPhaseIndex(phase));

  // Elapsed time counter
  let elapsed = $state(0);
  $effect(() => {
    if (!startedAt) { elapsed = 0; return; }
    const start = new Date(startedAt).getTime();
    elapsed = Math.max(0, Math.floor((Date.now() - start) / 1000));
    const interval = setInterval(() => {
      elapsed = Math.max(0, Math.floor((Date.now() - start) / 1000));
    }, 1000);
    return () => clearInterval(interval);
  });

  function formatElapsed(s: number): string {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    if (m > 0) return `${m}m ${sec}s`;
    return `${sec}s`;
  }
</script>

<div class="glass rounded-lg px-4 py-3 animate-fade-in-up">
  <div class="flex items-center gap-1">
    {#each phases as p, i}
      {@const isCompleted = i < activeIndex}
      {@const isActive = i === activeIndex}
      {@const isFuture = i > activeIndex}

      <!-- Segment -->
      <div class="flex-1 h-2 rounded-full relative overflow-hidden {isFuture ? 'opacity-30' : ''}"  >
        <!-- Background -->
        <div class="absolute inset-0 rounded-full"
          style="background: {isCompleted || isActive ? p.color : 'rgba(71, 85, 105, 0.3)'}; opacity: {isCompleted ? 0.8 : isActive ? 0.6 : 1}"
        ></div>

        <!-- Active shimmer -->
      </div>

      <!-- Connector dot between segments -->
      {#if i < phases.length - 1}
        <div class="w-1.5 h-1.5 rounded-full shrink-0"
          style="background: {isCompleted ? p.color : 'rgba(71, 85, 105, 0.3)'}"
        ></div>
      {/if}
    {/each}
  </div>

  <!-- Labels -->
  <div class="flex justify-between mt-1.5">
    <div class="flex gap-3">
      {#each phases as p, i}
        {@const isActive = i === activeIndex}
        <span class="text-[10px] {isActive ? 'text-text font-medium' : 'text-text-dim'}"
          style={isActive ? `color: ${p.color}` : ''}
        >
          {p.label}
        </span>
      {/each}
    </div>
    {#if startedAt && activeIndex >= 0}
      <span class="text-[10px] text-text-dim font-data tabular-nums">
        {formatElapsed(elapsed)}
      </span>
    {/if}
  </div>
</div>
