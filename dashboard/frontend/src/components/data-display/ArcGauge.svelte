<script lang="ts">
  import { arcPath, formatPercent } from '../../lib/chart-utils';

  let {
    value,
    size = 64,
    label,
    color = 'var(--color-info)',
  }: {
    value: number;
    size?: number;
    label?: string;
    color?: string;
  } = $props();

  const startAngle = -135;
  const totalSweep = 270;

  let cx = $derived(size / 2);
  let cy = $derived(size / 2);
  let r = $derived(size / 2 - 6);

  let bgArc = $derived(arcPath(cx, cy, r, startAngle, startAngle + totalSweep));
  let fgArc = $derived(
    value > 0
      ? arcPath(cx, cy, r, startAngle, startAngle + totalSweep * Math.min(value, 1))
      : ''
  );
  let pctText = $derived(formatPercent(value, 0));
</script>

<svg
  width={size}
  height={size}
  viewBox="0 0 {size} {size}"
  class="shrink-0"
  role="img"
  aria-label="{label ?? 'gauge'}: {pctText}"
>
  <!-- Background track -->
  <path
    d={bgArc}
    fill="none"
    stroke="var(--color-border)"
    stroke-width="5"
    stroke-linecap="round"
  />
  <!-- Value arc -->
  {#if fgArc}
    <path
      d={fgArc}
      fill="none"
      stroke={color}
      stroke-width="5"
      stroke-linecap="round"
    />
  {/if}
  <!-- Percentage text -->
  <text
    x={cx}
    y={cy + 1}
    text-anchor="middle"
    dominant-baseline="central"
    fill="var(--color-text)"
    class="font-data"
    font-size="{size * 0.2}px"
  >{pctText}</text>
  <!-- Label -->
  {#if label}
    <text
      x={cx}
      y={cy + size * 0.2}
      text-anchor="middle"
      dominant-baseline="central"
      fill="var(--color-text-muted)"
      font-size="{Math.max(8, size * 0.12)}px"
    >{label}</text>
  {/if}
</svg>
