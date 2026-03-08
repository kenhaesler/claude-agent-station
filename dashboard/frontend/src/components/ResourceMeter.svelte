<script lang="ts">
  interface Props {
    label: string;
    value: number | null;
    max: number;
    unit: string;
    invert?: boolean;
  }

  let { label, value, max, unit, invert = false }: Props = $props();

  let pct = $derived(value != null ? Math.min((value / max) * 100, 100) : 0);
  let displayPct = $derived(invert ? 100 - pct : pct);
  let barColor = $derived(
    displayPct > 80 ? 'bg-reject' :
    displayPct > 60 ? 'bg-warning' : 'bg-approve'
  );
</script>

<div class="space-y-1">
  <div class="flex justify-between text-xs">
    <span class="text-text-dim">{label}</span>
    <span class="text-text">{value != null ? `${value.toFixed(1)} ${unit}` : '-'}</span>
  </div>
  <div class="h-2 bg-surface-2 rounded-full overflow-hidden">
    <div class="h-full rounded-full transition-all duration-500 {barColor}" style:width="{displayPct}%"></div>
  </div>
</div>
