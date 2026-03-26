<script lang="ts">
  import { linePath, linearScale, niceDomain } from '../../lib/chart-utils';

  let {
    values,
    width = 80,
    height = 24,
    color = 'var(--color-info)',
  }: {
    values: number[];
    width?: number;
    height?: number;
    color?: string;
  } = $props();

  const pad = 2;

  let path = $derived.by(() => {
    if (values.length < 2) return '';
    const domain = niceDomain(values);
    const xScale = linearScale([0, values.length - 1], [pad, width - pad]);
    const yScale = linearScale(domain, [height - pad, pad]);
    const points = values.map((v, i) => ({ x: xScale(i), y: yScale(v) }));
    return linePath(points, true);
  });
</script>

<svg
  {width}
  {height}
  viewBox="0 0 {width} {height}"
  class="inline-block shrink-0"
  role="img"
  aria-label="sparkline"
>
  {#if path}
    <path
      d={path}
      fill="none"
      stroke={color}
      stroke-width="1.5"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
  {/if}
</svg>
