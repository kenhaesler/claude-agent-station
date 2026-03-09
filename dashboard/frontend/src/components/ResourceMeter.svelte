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
    displayPct > 60 ? 'bg-warning' : 'bg-accent-emerald'
  );
  let glowColor = $derived(
    displayPct > 80 ? 'shadow-[0_0_6px_rgba(239,68,68,0.2)]' :
    displayPct > 60 ? '' : 'shadow-[0_0_6px_rgba(16,185,129,0.15)]'
  );
</script>

<div class="space-y-1.5">
  <div class="flex justify-between text-xs">
    <span class="text-text-dim">{label}</span>
    <span class="text-text font-data">{value != null ? `${value.toFixed(1)} ${unit}` : '-'}</span>
  </div>
  <div class="h-1.5 bg-white/[0.04] rounded-full overflow-hidden">
    <div class="h-full rounded-full transition-all duration-700 ease-out {barColor} {glowColor}" style:width="{displayPct}%"></div>
  </div>
</div>
