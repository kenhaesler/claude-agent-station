<script lang="ts">
  let {
    label,
    value,
    trend,
    trendLabel,
    icon,
  }: {
    label: string;
    value: string | number;
    trend?: 'up' | 'down' | 'flat';
    trendLabel?: string;
    icon?: string;
  } = $props();

  const trendArrows: Record<string, string> = {
    up: '\u2191',
    down: '\u2193',
    flat: '\u2192',
  };

  const trendColors: Record<string, string> = {
    up: 'text-approve',
    down: 'text-reject',
    flat: 'text-text-muted',
  };
</script>

<div class="glass rounded-lg px-4 py-3 flex flex-col gap-1 min-w-0">
  <div class="flex items-center gap-1.5 text-text-muted text-xs">
    {#if icon}
      <span class="text-sm">{icon}</span>
    {/if}
    <span class="truncate">{label}</span>
  </div>
  <div class="data-readout text-xl text-text font-medium truncate">{value}</div>
  {#if trend}
    <div class="flex items-center gap-1 text-xs {trendColors[trend] ?? 'text-text-muted'}">
      <span>{trendArrows[trend] ?? ''}</span>
      {#if trendLabel}
        <span>{trendLabel}</span>
      {/if}
    </div>
  {/if}
</div>
