<script lang="ts">
  interface Props {
    value: number;
    size?: number;
    color?: string;
    label?: string;
  }

  let { value, size = 48, color = '#6366f1', label }: Props = $props();

  const strokeWidth = 3;
  let radius = $derived((size - strokeWidth) / 2);
  let circumference = $derived(radius * Math.PI * 1.5); // 270° arc
  let offset = $derived(circumference - (Math.min(value, 100) / 100) * circumference);
  let center = $derived(size / 2);
  // Start at 135° (bottom-left), sweep 270°
  const startAngle = 135;
  const endAngle = 405;
  function polarToCartesian(cx: number, r: number, angle: number) {
    const rad = (angle * Math.PI) / 180;
    return { x: cx + r * Math.cos(rad), y: cx + r * Math.sin(rad) };
  }
  let start = $derived(polarToCartesian(center, radius, startAngle));
  let end = $derived(polarToCartesian(center, radius, endAngle));
  let arcPath = $derived(`M ${start.x} ${start.y} A ${radius} ${radius} 0 1 1 ${end.x} ${end.y}`);
</script>

<svg width={size} height={size} viewBox="0 0 {size} {size}" class="shrink-0">
  <defs></defs>
  <!-- Background arc -->
  <path
    d={arcPath}
    fill="none"
    stroke="rgba(148, 163, 184, 0.1)"
    stroke-width={strokeWidth}
    stroke-linecap="round"
  />
  <!-- Value arc -->
  <path
    d={arcPath}
    fill="none"
    stroke={color}
    stroke-width={strokeWidth}
    stroke-linecap="round"
    stroke-dasharray={circumference}
    stroke-dashoffset={offset}
    style="transition: stroke-dashoffset 0.6s ease-out"
  />
  <!-- Center text -->
  <text
    x={center}
    y={center}
    text-anchor="middle"
    dominant-baseline="central"
    fill={color}
    font-size={size * 0.22}
    font-family="'SF Mono', 'Cascadia Code', ui-monospace, monospace"
    font-weight="600"
  >
    {Math.round(value)}%
  </text>
  {#if label}
    <text
      x={center}
      y={center + size * 0.18}
      text-anchor="middle"
      fill="rgb(148, 163, 184)"
      font-size={size * 0.12}
      text-transform="uppercase"
      letter-spacing="0.15em"
    >
      {label}
    </text>
  {/if}
</svg>
