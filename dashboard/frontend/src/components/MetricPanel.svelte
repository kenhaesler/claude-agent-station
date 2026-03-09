<script lang="ts">
  import GlassCard from './GlassCard.svelte';
  import AnimatedCounter from './AnimatedCounter.svelte';

  interface Props {
    label: string;
    value: number;
    format?: (n: number) => string;
    glow?: 'blue' | 'emerald' | 'purple' | 'red' | 'cyan' | 'none';
    subtitle?: string;
    class?: string;
  }

  let { label, value, format, glow = 'none', subtitle = '', class: className = '' }: Props = $props();
</script>

<GlassCard {glow} animated class="p-4 md:p-5 {className}">
  <p class="text-[10px] font-medium text-text-dim uppercase tracking-wider mb-2">{label}</p>
  <p class="text-xl md:text-2xl font-semibold text-text">
    <AnimatedCounter {value} {format} />
  </p>
  {#if subtitle}
    <p class="text-xs text-text-dim mt-1.5">{subtitle}</p>
  {/if}
</GlassCard>
