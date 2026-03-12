<script lang="ts">
  interface DataPoint {
    label: string;
    value: number;
  }

  interface Props {
    data: DataPoint[];
    title?: string;
    color?: string;
    fillOpacity?: number;
    height?: number;
    valueFormatter?: (v: number) => string;
    showDots?: boolean;
  }

  let {
    data,
    title = '',
    color = '#6366f1',
    fillOpacity = 0.15,
    height = 180,
    valueFormatter = (v: number) => v.toLocaleString(),
    showDots = true,
  }: Props = $props();

  // Layout
  const PAD_LEFT = 44;
  const PAD_RIGHT = 16;
  const PAD_TOP = 12;
  const PAD_BOTTOM = 32;

  let chartW = $derived(Math.max(data.length * 52 + PAD_LEFT + PAD_RIGHT, 300));
  let chartH = $derived(height);
  let plotW = $derived(chartW - PAD_LEFT - PAD_RIGHT);
  let plotH = $derived(chartH - PAD_TOP - PAD_BOTTOM);

  let maxVal = $derived(Math.max(...data.map(d => d.value), 1));
  let minVal = $derived(Math.min(...data.map(d => d.value), 0));
  let range = $derived(maxVal - minVal || 1);

  // Point positions
  let points = $derived(
    data.map((d, i) => ({
      x: PAD_LEFT + (data.length > 1 ? (i / (data.length - 1)) * plotW : plotW / 2),
      y: PAD_TOP + plotH - ((d.value - minVal) / range) * plotH,
      ...d,
    }))
  );

  // SVG path for the line
  let linePath = $derived(() => {
    if (points.length < 2) return '';
    return points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
  });

  // SVG path for the filled area
  let areaPath = $derived(() => {
    if (points.length < 2) return '';
    const baseline = PAD_TOP + plotH;
    const line = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
    return `${line} L ${points[points.length - 1].x.toFixed(1)} ${baseline} L ${points[0].x.toFixed(1)} ${baseline} Z`;
  });

  // Grid lines (4 horizontal)
  let gridLines = $derived(
    [0.25, 0.5, 0.75, 1.0].map(frac => ({
      y: PAD_TOP + plotH - frac * plotH,
      label: valueFormatter(Math.round(minVal + frac * range)),
    }))
  );

  let uid = $derived(`line-${Math.random().toString(36).slice(2, 8)}`);
  let hoveredIndex = $state<number | null>(null);
</script>

<div class="line-chart-container w-full">
  {#if title}
    <div class="text-xs font-medium text-text-dim uppercase tracking-wide mb-2">{title}</div>
  {/if}

  <div class="overflow-x-auto">
    <svg width={chartW} {height} viewBox="0 0 {chartW} {chartH}" class="block">
      <defs>
        <!-- Area gradient -->
        <linearGradient id="{uid}-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color={color} stop-opacity={fillOpacity} />
          <stop offset="100%" stop-color={color} stop-opacity="0" />
        </linearGradient>
      </defs>

      <!-- Grid lines -->
      {#each gridLines as line}
        <line
          x1={PAD_LEFT}
          y1={line.y}
          x2={chartW - PAD_RIGHT}
          y2={line.y}
          stroke="rgba(148, 163, 184, 0.08)"
          stroke-width="1"
        />
        <text
          x={PAD_LEFT - 6}
          y={line.y}
          text-anchor="end"
          dominant-baseline="central"
          fill="rgba(148, 163, 184, 0.5)"
          font-size="9"
          font-family="'SF Mono', 'Cascadia Code', ui-monospace, monospace"
        >
          {line.label}
        </text>
      {/each}

      <!-- Filled area -->
      {#if areaPath()}
        <path d={areaPath()} fill="url(#{uid}-fill)" />
      {/if}

      <!-- Line -->
      {#if linePath()}
        <path
          d={linePath()}
          fill="none"
          stroke={color}
          stroke-width="2"
          stroke-linejoin="round"
          stroke-linecap="round"
          style="transition: d 0.4s ease-out"
        />
      {/if}

      <!-- Hover crosshair + dots -->
      {#each points as pt, i}
        <!-- Invisible hover hitbox -->
        <rect
          x={pt.x - (plotW / data.length) / 2}
          y={PAD_TOP}
          width={plotW / data.length}
          height={plotH + PAD_BOTTOM}
          fill="transparent"
          onmouseenter={() => hoveredIndex = i}
          onmouseleave={() => hoveredIndex = null}
          role="presentation"
        />

        <!-- Vertical crosshair on hover -->
        {#if hoveredIndex === i}
          <line
            x1={pt.x}
            y1={PAD_TOP}
            x2={pt.x}
            y2={PAD_TOP + plotH}
            stroke="rgba(148, 163, 184, 0.2)"
            stroke-width="1"
            stroke-dasharray="3 3"
          />
        {/if}

        <!-- Data dots -->
        {#if showDots}
          <circle
            cx={pt.x}
            cy={pt.y}
            r={hoveredIndex === i ? 5 : 3}
            fill={hoveredIndex === i ? color : 'rgba(15, 23, 42, 0.8)'}
            stroke={color}
            stroke-width={hoveredIndex === i ? 2 : 1.5}
            style="transition: r 0.15s ease, fill 0.15s ease"
          />
        {/if}

        <!-- Hover tooltip -->
        {#if hoveredIndex === i}
          <g>
            <rect
              x={pt.x - 30}
              y={pt.y - 26}
              width="60"
              height="18"
              rx="4"
              fill="rgba(15, 23, 42, 0.9)"
              stroke="rgba(148, 163, 184, 0.3)"
              stroke-width="0.5"
            />
            <text
              x={pt.x}
              y={pt.y - 15}
              text-anchor="middle"
              dominant-baseline="central"
              fill="rgba(226, 232, 240, 0.95)"
              font-size="10"
              font-weight="600"
              font-family="'SF Mono', 'Cascadia Code', ui-monospace, monospace"
            >
              {valueFormatter(pt.value)}
            </text>
          </g>
        {/if}

        <!-- X-axis label -->
        <text
          x={pt.x}
          y={chartH - 6}
          text-anchor="middle"
          fill={hoveredIndex === i ? 'rgba(226, 232, 240, 0.9)' : 'rgba(148, 163, 184, 0.6)'}
          font-size="9"
          font-family="'SF Mono', 'Cascadia Code', ui-monospace, monospace"
          style="transition: fill 0.2s ease"
        >
          {pt.label}
        </text>
      {/each}

      <!-- Bottom axis line -->
      <line
        x1={PAD_LEFT}
        y1={PAD_TOP + plotH}
        x2={chartW - PAD_RIGHT}
        y2={PAD_TOP + plotH}
        stroke="rgba(148, 163, 184, 0.12)"
        stroke-width="1"
      />
    </svg>
  </div>
</div>
