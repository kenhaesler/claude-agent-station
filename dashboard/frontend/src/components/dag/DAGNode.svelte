<script lang="ts">
  let {
    id = '',
    label = '',
    status = 'pending',
    x = 0,
    y = 0,
    selected = false,
    onclick,
  }: {
    id: string;
    label: string;
    status: string;
    x: number;
    y: number;
    selected?: boolean;
    onclick?: () => void;
  } = $props();

  const W = 160;
  const H = 48;

  const statusColors: Record<string, { bg: string; border: string; text: string }> = {
    pending: { bg: 'var(--color-surface-2)', border: 'var(--color-border)', text: 'var(--color-text-muted)' },
    ready: { bg: 'color-mix(in oklch, var(--color-info) 15%, var(--color-surface))', border: 'var(--color-info)', text: 'var(--color-info)' },
    running: { bg: 'color-mix(in oklch, var(--color-warning) 15%, var(--color-surface))', border: 'var(--color-warning)', text: 'var(--color-warning)' },
    completed: { bg: 'color-mix(in oklch, var(--color-approve) 15%, var(--color-surface))', border: 'var(--color-approve)', text: 'var(--color-approve)' },
    failed: { bg: 'color-mix(in oklch, var(--color-reject) 15%, var(--color-surface))', border: 'var(--color-reject)', text: 'var(--color-reject)' },
    blocked: { bg: 'var(--color-surface)', border: 'var(--color-text-muted)', text: 'var(--color-text-muted)' },
  };

  let colors = $derived(statusColors[status] ?? statusColors.pending);
</script>

<g class="cursor-pointer" onclick={onclick} role="button" tabindex="0" onkeydown={(e) => e.key === 'Enter' && onclick?.()}>
  <rect
    {x} {y} width={W} height={H} rx="8"
    fill={colors.bg} stroke={colors.border} stroke-width={selected ? 2 : 1}
  />
  {#if status === 'running'}
    <rect {x} {y} width={W} height={H} rx="8" fill="none" stroke={colors.border} stroke-width="2" opacity="0.4">
      <animate attributeName="opacity" values="0.4;0.1;0.4" dur="1.5s" repeatCount="indefinite" />
    </rect>
  {/if}
  <text x={x + 10} y={y + 20} fill="var(--color-text)" font-size="11" font-weight="500">
    {label.length > 22 ? label.slice(0, 20) + '...' : label}
  </text>
  <text x={x + 10} y={y + 36} fill={colors.text} font-size="9" text-transform="uppercase" class="font-data">
    {status}
  </text>
</g>
