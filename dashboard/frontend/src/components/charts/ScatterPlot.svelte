<script lang="ts">
  import { linearScale, formatPercent } from '../../lib/chart-utils';

  let {
    points = [],
    width = 300,
    height = 300,
    xLabel = '',
    yLabel = '',
    showDiagonal = true,
    color = 'var(--color-info)',
  }: {
    points: { x: number; y: number; label?: string; size?: number }[];
    width?: number;
    height?: number;
    xLabel?: string;
    yLabel?: string;
    showDiagonal?: boolean;
    color?: string;
  } = $props();

  const pad = { top: 12, right: 12, bottom: 30, left: 36 };

  let plotW = $derived(width - pad.left - pad.right);
  let plotH = $derived(height - pad.top - pad.bottom);

  let xScale = $derived(linearScale([0, 1], [0, plotW]));
  let yScale = $derived(linearScale([0, 1], [plotH, 0]));

  let dots = $derived(
    points.map(p => ({
      cx: pad.left + xScale(p.x),
      cy: pad.top + yScale(p.y),
      r: p.size ?? 5,
      label: p.label,
    }))
  );
</script>

<svg {width} {height} class="overflow-visible">
  <!-- Grid -->
  {#each [0, 0.25, 0.5, 0.75, 1] as tick}
    <line
      x1={pad.left} y1={pad.top + yScale(tick)}
      x2={pad.left + plotW} y2={pad.top + yScale(tick)}
      stroke="var(--color-border-subtle)" stroke-width="0.5"
    />
    <text x={pad.left - 4} y={pad.top + yScale(tick) + 3} fill="var(--color-text-muted)" font-size="8" text-anchor="end" class="font-data">
      {formatPercent(tick, 0)}
    </text>
    <text x={pad.left + xScale(tick)} y={height - 8} fill="var(--color-text-muted)" font-size="8" text-anchor="middle" class="font-data">
      {formatPercent(tick, 0)}
    </text>
  {/each}

  <!-- Diagonal reference line (perfect calibration) -->
  {#if showDiagonal}
    <line
      x1={pad.left} y1={pad.top + plotH}
      x2={pad.left + plotW} y2={pad.top}
      stroke="var(--color-text-muted)" stroke-width="1" stroke-dasharray="4 3" opacity="0.4"
    />
  {/if}

  <!-- Dots -->
  {#each dots as dot}
    <circle cx={dot.cx} cy={dot.cy} r={dot.r} fill={color} opacity="0.7" />
  {/each}

  <!-- Axis labels -->
  {#if xLabel}
    <text x={pad.left + plotW / 2} y={height - 1} fill="var(--color-text-muted)" font-size="9" text-anchor="middle">{xLabel}</text>
  {/if}
  {#if yLabel}
    <text x={8} y={pad.top + plotH / 2} fill="var(--color-text-muted)" font-size="9" text-anchor="middle" transform="rotate(-90, 8, {pad.top + plotH / 2})">{yLabel}</text>
  {/if}
</svg>
