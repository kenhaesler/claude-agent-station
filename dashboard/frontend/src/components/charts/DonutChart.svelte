<script lang="ts">
  import { arcPath } from '../../lib/chart-utils';

  let {
    segments = [],
    size = 160,
    thickness = 20,
    centerLabel = '',
    centerValue = '',
  }: {
    segments: { value: number; color: string; label: string }[];
    size?: number;
    thickness?: number;
    centerLabel?: string;
    centerValue?: string;
  } = $props();

  let total = $derived(segments.reduce((s, seg) => s + seg.value, 0) || 1);
  let cx = $derived(size / 2);
  let cy = $derived(size / 2);
  let r = $derived((size - thickness) / 2 - 2);

  let arcs = $derived.by(() => {
    let angle = 0;
    return segments.map(seg => {
      const sweep = (seg.value / total) * 360;
      const start = angle;
      angle += sweep;
      return {
        ...seg,
        d: sweep > 0.5 ? arcPath(cx, cy, r, start, start + Math.max(sweep - 0.5, 0.1)) : '',
        percent: Math.round((seg.value / total) * 100),
      };
    });
  });
</script>

<div class="inline-flex flex-col items-center gap-2">
  <svg width={size} height={size}>
    <!-- Background ring -->
    <circle {cx} {cy} {r} fill="none" stroke="var(--color-border-subtle)" stroke-width={thickness} />

    <!-- Segments -->
    {#each arcs as arc}
      {#if arc.d}
        <path d={arc.d} fill="none" stroke={arc.color} stroke-width={thickness} stroke-linecap="round" />
      {/if}
    {/each}

    <!-- Center text -->
    {#if centerValue}
      <text x={cx} y={cy - 4} fill="var(--color-text)" font-size="18" font-weight="600" text-anchor="middle" class="font-data">{centerValue}</text>
    {/if}
    {#if centerLabel}
      <text x={cx} y={cy + 14} fill="var(--color-text-muted)" font-size="10" text-anchor="middle">{centerLabel}</text>
    {/if}
  </svg>

  <!-- Legend -->
  {#if segments.length > 0}
    <div class="flex flex-wrap gap-x-3 gap-y-1 justify-center">
      {#each arcs as arc}
        <div class="flex items-center gap-1 text-xs text-text-dim">
          <span class="w-2 h-2 rounded-full" style="background:{arc.color}"></span>
          <span>{arc.label}</span>
          <span class="text-text-muted data-readout">{arc.percent}%</span>
        </div>
      {/each}
    </div>
  {/if}
</div>
