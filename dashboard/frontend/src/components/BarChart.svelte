<script lang="ts">
  interface BarData {
    label: string;
    value: number;
    color?: string;
  }

  interface Props {
    data: BarData[];
    limit?: number | null;
    title?: string;
    valueFormatter?: (v: number) => string;
    height?: number;
    barColor?: string;
  }

  let {
    data,
    limit = null,
    title = '',
    valueFormatter = (v: number) => v.toLocaleString(),
    height = 200,
    barColor = '#06b6d4',
  }: Props = $props();

  // Layout constants
  const PAD_LEFT = 50;
  const PAD_RIGHT = 16;
  const PAD_TOP = 8;
  const PAD_BOTTOM = 32;
  const BAR_GAP = 0.3; // fraction of bar width

  // Derived chart dimensions
  let chartW = $derived(Math.max(data.length * 48 + PAD_LEFT + PAD_RIGHT, 300));
  let chartH = $derived(height);
  let plotW = $derived(chartW - PAD_LEFT - PAD_RIGHT);
  let plotH = $derived(chartH - PAD_TOP - PAD_BOTTOM);

  // Scale
  let maxVal = $derived(Math.max(
    ...data.map(d => d.value),
    limit ?? 0,
    1
  ));
  let yScale = $derived(plotH / maxVal);
  let barWidth = $derived(data.length > 0 ? plotW / data.length : 0);
  let barInner = $derived(barWidth * (1 - BAR_GAP));

  // Y-axis gridlines (4 lines)
  let gridLines = $derived(
    [0.25, 0.5, 0.75, 1.0].map(frac => ({
      y: PAD_TOP + plotH - frac * plotH,
      label: valueFormatter(Math.round(frac * maxVal)),
    }))
  );

  // Limit line position
  let limitY = $derived(limit != null ? PAD_TOP + plotH - limit * yScale : null);

  // Unique ID for gradients
  let uid = $derived(`bar-${Math.random().toString(36).slice(2, 8)}`);

  // Tooltip state
  let hoveredIndex = $state<number | null>(null);
</script>

<div class="bar-chart-container w-full">
  {#if title}
    <div class="text-xs ai-text mb-2">{title}</div>
  {/if}

  <div class="overflow-x-auto">
    <svg width={chartW} {height} viewBox="0 0 {chartW} {chartH}" class="block">
      <defs>
        <!-- Bar gradient -->
        <linearGradient id="{uid}-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color={barColor} stop-opacity="0.9" />
          <stop offset="100%" stop-color={barColor} stop-opacity="0.3" />
        </linearGradient>
        <!-- Bar glow filter -->
        <filter id="{uid}-glow">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <!-- Limit line glow -->
        <filter id="{uid}-limit-glow">
          <feGaussianBlur stdDeviation="2" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
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

      <!-- Bars -->
      {#each data as item, i}
        {@const barH = Math.max(item.value * yScale, 1)}
        {@const x = PAD_LEFT + i * barWidth + (barWidth - barInner) / 2}
        {@const y = PAD_TOP + plotH - barH}
        {@const isHovered = hoveredIndex === i}
        {@const fill = item.color ?? `url(#${uid}-grad)`}
        {@const isOverLimit = limit != null && item.value > limit}

        <!-- Hover hitbox -->
        <rect
          x={PAD_LEFT + i * barWidth}
          y={PAD_TOP}
          width={barWidth}
          height={plotH + PAD_BOTTOM}
          fill="transparent"
          onmouseenter={() => hoveredIndex = i}
          onmouseleave={() => hoveredIndex = null}
          role="presentation"
        />

        <!-- Bar -->
        <rect
          {x}
          {y}
          width={barInner}
          height={barH}
          rx="2"
          fill={isOverLimit ? 'url(#' + uid + '-over)' : fill}
          opacity={isHovered ? 1 : 0.85}
          filter={isHovered ? `url(#${uid}-glow)` : 'none'}
          style="transition: opacity 0.2s ease, height 0.4s ease-out, y 0.4s ease-out"
        />

        <!-- Over-limit highlight -->
        {#if isOverLimit}
          <rect
            {x}
            {y}
            width={barInner}
            height={barH}
            rx="2"
            fill="rgba(239, 68, 68, 0.3)"
            style="transition: height 0.4s ease-out, y 0.4s ease-out"
          />
        {/if}

        <!-- Label -->
        <text
          x={x + barInner / 2}
          y={chartH - 6}
          text-anchor="middle"
          fill={isHovered ? 'rgba(226, 232, 240, 0.9)' : 'rgba(148, 163, 184, 0.6)'}
          font-size="9"
          font-family="'SF Mono', 'Cascadia Code', ui-monospace, monospace"
          style="transition: fill 0.2s ease"
        >
          {item.label}
        </text>

        <!-- Hover value tooltip -->
        {#if isHovered}
          <g>
            <rect
              x={x + barInner / 2 - 28}
              y={y - 22}
              width="56"
              height="18"
              rx="4"
              fill="rgba(15, 23, 42, 0.9)"
              stroke="rgba(6, 182, 212, 0.3)"
              stroke-width="0.5"
            />
            <text
              x={x + barInner / 2}
              y={y - 11}
              text-anchor="middle"
              dominant-baseline="central"
              fill="rgba(226, 232, 240, 0.95)"
              font-size="10"
              font-weight="600"
              font-family="'SF Mono', 'Cascadia Code', ui-monospace, monospace"
            >
              {valueFormatter(item.value)}
            </text>
          </g>
        {/if}
      {/each}

      <!-- Limit line -->
      {#if limitY != null && limit != null}
        <line
          x1={PAD_LEFT}
          y1={limitY}
          x2={chartW - PAD_RIGHT}
          y2={limitY}
          stroke="rgba(239, 68, 68, 0.6)"
          stroke-width="1.5"
          stroke-dasharray="6 4"
          filter="url(#{uid}-limit-glow)"
        />
        <text
          x={chartW - PAD_RIGHT + 2}
          y={limitY}
          dominant-baseline="central"
          fill="rgba(239, 68, 68, 0.8)"
          font-size="9"
          font-weight="600"
          font-family="'SF Mono', 'Cascadia Code', ui-monospace, monospace"
        >
          LIMIT
        </text>
      {/if}

      <!-- Bottom axis line -->
      <line
        x1={PAD_LEFT}
        y1={PAD_TOP + plotH}
        x2={chartW - PAD_RIGHT}
        y2={PAD_TOP + plotH}
        stroke="rgba(6, 182, 212, 0.12)"
        stroke-width="1"
      />
    </svg>
  </div>
</div>
