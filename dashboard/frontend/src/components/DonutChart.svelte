<script lang="ts">
  interface Segment {
    label: string;
    value: number;
    color: string;
  }

  interface Props {
    segments: Segment[];
    size?: number;
    thickness?: number;
    title?: string;
    centerLabel?: string;
    centerValue?: string;
  }

  let {
    segments,
    size = 180,
    thickness = 24,
    title = '',
    centerLabel = '',
    centerValue = '',
  }: Props = $props();

  let center = $derived(size / 2);
  let radius = $derived((size - thickness) / 2 - 4);
  let circumference = $derived(2 * Math.PI * radius);
  let total = $derived(segments.reduce((s, seg) => s + seg.value, 0));

  // Build arc segments with offsets
  let arcs = $derived(() => {
    let offset = 0;
    return segments
      .filter(s => s.value > 0)
      .map(seg => {
        const frac = total > 0 ? seg.value / total : 0;
        const dashLen = frac * circumference;
        const gapLen = circumference - dashLen;
        const rotation = (offset / total) * 360 - 90; // start at top
        offset += seg.value;
        return {
          ...seg,
          frac,
          dashLen,
          gapLen,
          rotation,
          pct: Math.round(frac * 100),
        };
      });
  });

  // Unique ID for filters
  let uid = $derived(`donut-${Math.random().toString(36).slice(2, 8)}`);

  let hoveredIndex = $state<number | null>(null);
</script>

<div class="donut-chart-container flex flex-col items-center gap-3">
  {#if title}
    <div class="text-xs font-medium text-text-dim uppercase tracking-wide">{title}</div>
  {/if}

  <div class="relative">
    <svg width={size} height={size} viewBox="0 0 {size} {size}">
      <defs></defs>

      <!-- Background ring -->
      <circle
        cx={center}
        cy={center}
        r={radius}
        fill="none"
        stroke="rgba(148, 163, 184, 0.06)"
        stroke-width={thickness}
      />

      <!-- Segments -->
      {#each arcs() as arc, i}
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          stroke={arc.color}
          stroke-width={hoveredIndex === i ? thickness + 4 : thickness}
          stroke-dasharray="{arc.dashLen} {arc.gapLen}"
          stroke-dashoffset="0"
          stroke-linecap="butt"
          transform="rotate({arc.rotation} {center} {center})"
          opacity={hoveredIndex != null && hoveredIndex !== i ? 0.4 : 0.85}
          style="transition: opacity 0.2s ease, stroke-width 0.2s ease"
          onmouseenter={() => hoveredIndex = i}
          onmouseleave={() => hoveredIndex = null}
          role="presentation"
        />
      {/each}

      <!-- Inner glow ring -->
      <circle
        cx={center}
        cy={center}
        r={radius - thickness / 2 - 2}
        fill="none"
        stroke="rgba(148, 163, 184, 0.04)"
        stroke-width="1"
      />

      <!-- Center text -->
      {#if centerValue}
        <text
          x={center}
          y={centerLabel ? center - 6 : center}
          text-anchor="middle"
          dominant-baseline="central"
          fill="rgba(226, 232, 240, 0.95)"
          font-size={size * 0.13}
          font-weight="700"
          font-family="'SF Mono', 'Cascadia Code', ui-monospace, monospace"
        >
          {centerValue}
        </text>
      {/if}
      {#if centerLabel}
        <text
          x={center}
          y={center + size * 0.09}
          text-anchor="middle"
          dominant-baseline="central"
          fill="rgba(148, 163, 184, 0.6)"
          font-size={size * 0.065}
          text-transform="uppercase"
          letter-spacing="0.15em"
        >
          {centerLabel}
        </text>
      {/if}

      <!-- Hovered tooltip -->
      {#if hoveredIndex != null}
        {@const arc = arcs()[hoveredIndex]}
        <text
          x={center}
          y={center + size * 0.32}
          text-anchor="middle"
          dominant-baseline="central"
          fill={arc.color}
          font-size="11"
          font-weight="600"
          font-family="'SF Mono', 'Cascadia Code', ui-monospace, monospace"
        >
          {arc.label}: {arc.pct}%
        </text>
      {/if}
    </svg>
  </div>

  <!-- Legend -->
  <div class="flex flex-wrap justify-center gap-x-4 gap-y-1.5">
    {#each arcs() as arc, i}
      <div
        class="flex items-center gap-1.5 cursor-default"
        onmouseenter={() => hoveredIndex = i}
        onmouseleave={() => hoveredIndex = null}
        role="listitem"
      >
        <span
          class="inline-block w-2 h-2 rounded-full shrink-0"
          style="background: {arc.color}"
        ></span>
        <span class="text-[10px] text-text-dim font-data">
          {arc.label}
          <span class="text-text/70">{arc.pct}%</span>
        </span>
      </div>
    {/each}
  </div>
</div>
