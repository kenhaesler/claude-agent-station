<script lang="ts">
  import { linearScale, niceDomain, formatCompact } from '../../lib/chart-utils';

  let {
    data = [],
    labels = [],
    colors = [],
    width = 400,
    height = 200,
    yFormat = formatCompact,
  }: {
    data: number[];
    labels?: string[];
    colors?: string[];
    width?: number;
    height?: number;
    yFormat?: (n: number) => string;
  } = $props();

  const pad = { top: 8, right: 12, bottom: 28, left: 40 };
  const defaultColor = 'var(--color-info)';

  let plotW = $derived(width - pad.left - pad.right);
  let plotH = $derived(height - pad.top - pad.bottom);

  let yDomain = $derived(niceDomain(data.length ? data : [0]));
  let yScale = $derived(linearScale(yDomain, [plotH, 0]));

  let barWidth = $derived(data.length > 0 ? Math.max(4, (plotW / data.length) * 0.7) : 0);
  let gap = $derived(data.length > 0 ? (plotW / data.length) : 0);

  let bars = $derived(
    data.map((v, i) => ({
      x: pad.left + gap * i + (gap - barWidth) / 2,
      y: pad.top + yScale(v),
      w: barWidth,
      h: plotH - yScale(v),
      color: colors[i] ?? defaultColor,
      label: labels[i] ?? '',
      value: v,
    }))
  );
</script>

<svg {width} {height} class="overflow-visible">
  <!-- Baseline -->
  <line
    x1={pad.left} y1={pad.top + plotH}
    x2={pad.left + plotW} y2={pad.top + plotH}
    stroke="var(--color-border-subtle)" stroke-width="1"
  />

  <!-- Bars -->
  {#each bars as bar, i}
    <rect
      x={bar.x} y={bar.y} width={bar.w} height={Math.max(0, bar.h)}
      fill={bar.color} rx="2" opacity="0.85"
    />
    {#if bar.label}
      <text
        x={bar.x + bar.w / 2} y={height - 6}
        fill="var(--color-text-muted)" font-size="9" text-anchor="middle"
      >{bar.label}</text>
    {/if}
  {/each}
</svg>
