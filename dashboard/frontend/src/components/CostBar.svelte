<script lang="ts">
  import { formatCost } from '../lib/format';

  interface Props {
    cost: number | null;
    maxCost?: number;
    label?: string;
  }

  let { cost, maxCost = 1, label = '' }: Props = $props();

  let pct = $derived(cost != null ? Math.min((cost / maxCost) * 100, 100) : 0);
  let barColor = $derived(
    cost != null && cost > maxCost * 0.8 ? 'bg-reject' :
    cost != null && cost > maxCost * 0.5 ? 'bg-warning' : 'bg-pr'
  );
</script>

<div class="flex items-center gap-3">
  {#if label}
    <span class="text-xs text-text-dim w-24 truncate">{label}</span>
  {/if}
  <div class="flex-1 h-3 bg-surface-2 rounded-full overflow-hidden">
    <div class="h-full rounded-full transition-all duration-500 {barColor}" style:width="{pct}%"></div>
  </div>
  <span class="text-xs text-text-dim w-16 text-right">{formatCost(cost)}</span>
</div>
