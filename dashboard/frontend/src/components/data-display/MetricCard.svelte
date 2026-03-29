<script lang="ts">
  let {
    label,
    value,
    sublabel = '',
    accent = 'default',
  }: {
    label: string;
    value: string | number;
    sublabel?: string;
    accent?: 'default' | 'cyan' | 'violet' | 'amber' | 'emerald' | 'rose' | 'indigo';
  } = $props();

  const accentStyles: Record<string, string> = {
    default: '',
    cyan: 'border-cyan/20 text-cyan',
    violet: 'border-violet/20 text-violet',
    amber: 'border-amber/20 text-amber',
    emerald: 'border-emerald/20 text-emerald',
    rose: 'border-rose/20 text-rose',
    indigo: 'border-indigo/20 text-indigo',
  };

  let valueColor = $derived(
    accent !== 'default' ? `text-${accent}` : 'text-primary'
  );
</script>

<div class="card px-4 py-3 flex flex-col gap-1 min-w-0 {accentStyles[accent]}">
  <div class="text-[10px] font-mono uppercase tracking-widest text-tertiary truncate">{label}</div>
  <div class="text-xl font-heading font-semibold {valueColor} truncate font-mono">{value}</div>
  {#if sublabel}
    <div class="text-[10px] text-tertiary font-mono truncate">{sublabel}</div>
  {/if}
</div>
