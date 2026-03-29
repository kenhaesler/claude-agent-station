<script lang="ts">
  import { linePath, areaPath, linearScale, niceDomain, niceTicks, formatCompact } from '../../lib/chart-utils';

  let {
    data = [],
    width = 400,
    height = 200,
    color = 'var(--color-info)',
    fillOpacity = 0.1,
    showArea = true,
    showDots = false,
    showGrid = true,
    xLabels = [],
    yFormat = formatCompact,
    smooth = true,
  }: {
    data: number[];
    width?: number;
    height?: number;
    color?: string;
    fillOpacity?: number;
    showArea?: boolean;
    showDots?: boolean;
    showGrid?: boolean;
    xLabels?: string[];
    yFormat?: (n: number) => string;
    smooth?: boolean;
  } = $props();

  const pad = { top: 8, right: 12, bottom: 24, left: 40 };

  let plotW = $derived(width - pad.left - pad.right);
  let plotH = $derived(height - pad.top - pad.bottom);

  let yDomain = $derived(niceDomain(data));
  let yScale = $derived(linearScale(yDomain, [plotH, 0]));
  let xScale = $derived(linearScale([0, Math.max(1, data.length - 1)], [0, plotW]));

  let points = $derived(
    data.map((v, i) => ({ x: pad.left + xScale(i), y: pad.top + yScale(v) }))
  );

  let yTicks = $derived(niceTicks(yDomain, 4));
</script>

<svg {width} {height} class="overflow-visible">
  <!-- Grid lines -->
  {#if showGrid}
    {#each yTicks as tick}
      <line
        x1={pad.left} y1={pad.top + yScale(tick)}
        x2={pad.left + plotW} y2={pad.top + yScale(tick)}
        stroke="var(--color-border-subtle)" stroke-width="1" stroke-dasharray="2 3"
      />
      <text
        x={pad.left - 6} y={pad.top + yScale(tick) + 3}
        fill="var(--color-text-muted)" font-size="9" text-anchor="end" class="font-mono"
      >{yFormat(tick)}</text>
    {/each}
  {/if}

  <!-- Area fill -->
  {#if showArea && points.length > 1}
    <path
      d={areaPath(points, pad.top + plotH, smooth)}
      fill={color} opacity={fillOpacity}
    />
  {/if}

  <!-- Line -->
  {#if points.length > 1}
    <path
      d={linePath(points, smooth)}
      fill="none" stroke={color} stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
    />
  {/if}

  <!-- Dots -->
  {#if showDots}
    {#each points as p, i}
      <circle cx={p.x} cy={p.y} r="3" fill={color} />
    {/each}
  {/if}

  <!-- X labels -->
  {#if xLabels.length > 0}
    {#each xLabels as label, i}
      {#if i % Math.ceil(xLabels.length / 6) === 0 || i === xLabels.length - 1}
        <text
          x={pad.left + xScale(i)} y={height - 4}
          fill="var(--color-text-muted)" font-size="9" text-anchor="middle"
        >{label}</text>
      {/if}
    {/each}
  {/if}
</svg>
