<script lang="ts">
  import { arcPath } from '../../lib/chart-utils';

  let {
    level = 'GREEN',
    usagePercent = 0,
    compact = false,
  }: {
    level: string;
    usagePercent?: number;
    compact?: boolean;
  } = $props();

  const colorMap: Record<string, string> = {
    GREEN: 'var(--color-bp-green)',
    YELLOW: 'var(--color-bp-yellow)',
    RED: 'var(--color-bp-red)',
    BLACK: 'var(--color-bp-black)',
  };

  let color = $derived(colorMap[level] ?? colorMap.GREEN);
  let size = $derived(compact ? 32 : 64);
  let r = $derived(size / 2 - 4);
  let cx = $derived(size / 2);
  let cy = $derived(size / 2);
  let sweepAngle = $derived(Math.min(usagePercent / 100, 1) * 270);
</script>

<div class="inline-flex flex-col items-center gap-1" title="Backpressure: {level} ({Math.round(usagePercent)}%)">
  <svg width={size} height={size}>
    <!-- Background arc -->
    <path
      d={arcPath(cx, cy, r, 0, 270)}
      fill="none" stroke="var(--color-border-subtle)" stroke-width={compact ? 3 : 5} stroke-linecap="round"
    />
    <!-- Value arc -->
    {#if sweepAngle > 1}
      <path
        d={arcPath(cx, cy, r, 0, sweepAngle)}
        fill="none" stroke={color} stroke-width={compact ? 3 : 5} stroke-linecap="round"
      />
    {/if}
    {#if !compact}
      <text x={cx} y={cy + 4} fill={color} font-size="14" font-weight="600" text-anchor="middle" class="font-mono">
        {Math.round(usagePercent)}%
      </text>
    {/if}
  </svg>
  {#if !compact}
    <span class="text-[10px] uppercase tracking-wider font-medium" style="color: {color}">{level}</span>
  {/if}
</div>
