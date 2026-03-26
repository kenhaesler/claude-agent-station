<script lang="ts">
  import { linePath, areaPath, linearScale, niceDomain, formatCompact } from '../../lib/chart-utils';

  let {
    used = [],
    budget = 0,
    labels = [],
    width = 400,
    height = 180,
  }: {
    used: number[];
    budget?: number;
    labels?: string[];
    width?: number;
    height?: number;
  } = $props();

  const pad = { top: 8, right: 12, bottom: 24, left: 44 };

  let plotW = $derived(width - pad.left - pad.right);
  let plotH = $derived(height - pad.top - pad.bottom);

  let maxVal = $derived(Math.max(budget, ...used, 1));
  let yDomain = $derived([0, maxVal] as [number, number]);
  let yScale = $derived(linearScale(yDomain, [plotH, 0]));
  let xScale = $derived(linearScale([0, Math.max(1, used.length - 1)], [0, plotW]));

  let points = $derived(
    used.map((v, i) => ({ x: pad.left + xScale(i), y: pad.top + yScale(v) }))
  );

  let budgetY = $derived(budget > 0 ? pad.top + yScale(budget) : -1);

  let usagePercent = $derived(
    budget > 0 && used.length > 0
      ? Math.round((used[used.length - 1] / budget) * 100)
      : 0
  );

  let budgetColor = $derived(
    usagePercent > 90 ? 'var(--color-reject)' :
    usagePercent > 70 ? 'var(--color-warning)' :
    'var(--color-approve)'
  );
</script>

<svg {width} {height} class="overflow-visible">
  <!-- Budget line -->
  {#if budgetY >= 0}
    <line
      x1={pad.left} y1={budgetY}
      x2={pad.left + plotW} y2={budgetY}
      stroke="var(--color-warning)" stroke-width="1" stroke-dasharray="4 3" opacity="0.6"
    />
    <text
      x={pad.left + plotW + 4} y={budgetY + 3}
      fill="var(--color-warning)" font-size="9" opacity="0.7"
    >limit</text>
  {/if}

  <!-- Area -->
  {#if points.length > 1}
    <path
      d={areaPath(points, pad.top + plotH, true)}
      fill={budgetColor} opacity="0.1"
    />
    <path
      d={linePath(points, true)}
      fill="none" stroke={budgetColor} stroke-width="2" stroke-linecap="round"
    />
  {/if}

  <!-- Current value dot -->
  {#if points.length > 0}
    {@const last = points[points.length - 1]}
    <circle cx={last.x} cy={last.y} r="4" fill={budgetColor} />
    <text
      x={last.x} y={last.y - 8}
      fill={budgetColor} font-size="10" text-anchor="middle" class="font-data"
    >{formatCompact(used[used.length - 1])}</text>
  {/if}

  <!-- X labels -->
  {#each labels as label, i}
    {#if i % Math.ceil(labels.length / 5) === 0 || i === labels.length - 1}
      <text
        x={pad.left + xScale(i)} y={height - 4}
        fill="var(--color-text-muted)" font-size="9" text-anchor="middle"
      >{label}</text>
    {/if}
  {/each}

  <!-- Y axis -->
  <text x={pad.left - 6} y={pad.top + 3} fill="var(--color-text-muted)" font-size="9" text-anchor="end" class="font-data">
    {formatCompact(maxVal)}
  </text>
  <text x={pad.left - 6} y={pad.top + plotH + 3} fill="var(--color-text-muted)" font-size="9" text-anchor="end" class="font-data">
    0
  </text>
</svg>
