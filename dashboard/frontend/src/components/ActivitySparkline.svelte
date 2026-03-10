<script lang="ts">
  interface Props {
    data: number[];
    color?: string;
  }

  let { data, color = '#3b82f6' }: Props = $props();

  const W = 100;
  const H = 20;
  const MAX_POINTS = 60;

  let points = $derived(() => {
    const d = data.length > MAX_POINTS ? data.slice(-MAX_POINTS) : data;
    if (d.length < 2) return '';
    const step = W / (d.length - 1);
    return d.map((v, i) => `${(i * step).toFixed(1)},${(H - v * H).toFixed(1)}`).join(' ');
  });

  let areaPoints = $derived(() => {
    const d = data.length > MAX_POINTS ? data.slice(-MAX_POINTS) : data;
    if (d.length < 2) return '';
    const step = W / (d.length - 1);
    const linePoints = d.map((v, i) => `${(i * step).toFixed(1)},${(H - v * H).toFixed(1)}`);
    return `0,${H} ${linePoints.join(' ')} ${W},${H}`;
  });

  let gradientId = $derived(`sparkline-grad-${color.replace('#', '')}`);
</script>

<svg width={W} height={H} viewBox="0 0 {W} {H}" class="shrink-0">
  <defs>
    <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color={color} stop-opacity="0.3" />
      <stop offset="100%" stop-color={color} stop-opacity="0" />
    </linearGradient>
  </defs>

  {#if areaPoints()}
    <polygon points={areaPoints()} fill="url(#{gradientId})" />
    <polyline points={points()} fill="none" stroke={color} stroke-width="1.5" stroke-linejoin="round" />
  {/if}
</svg>
