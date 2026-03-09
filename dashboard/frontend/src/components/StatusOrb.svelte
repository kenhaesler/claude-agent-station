<script lang="ts">
  interface Props {
    active: boolean;
    color?: string;
    size?: 'sm' | 'md' | 'lg';
  }

  let { active, color, size = 'sm' }: Props = $props();

  const sizeMap = { sm: 8, md: 12, lg: 16 };
  let s = $derived(sizeMap[size]);
  let fillColor = $derived(color ?? (active ? '#22c55e' : '#ef4444'));
</script>

<svg width={s} height={s} viewBox="0 0 16 16" class="shrink-0">
  {#if active}
    <circle cx="8" cy="8" r="7" fill="none" stroke={fillColor} stroke-width="1" opacity="0.3">
      <animate attributeName="r" values="5;7;5" dur="2s" repeatCount="indefinite" />
      <animate attributeName="opacity" values="0.4;0.1;0.4" dur="2s" repeatCount="indefinite" />
    </circle>
  {/if}
  <circle cx="8" cy="8" r="4" fill={fillColor} opacity={active ? 1 : 0.4} />
</svg>
