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
    <!-- Outer radar ping -->
    <circle cx="8" cy="8" r="7" fill="none" stroke={fillColor} stroke-width="0.5" opacity="0.15">
      <animate attributeName="r" values="4;8;4" dur="3s" repeatCount="indefinite" />
      <animate attributeName="opacity" values="0.2;0;0.2" dur="3s" repeatCount="indefinite" />
    </circle>
    <!-- Inner pulse ring -->
    <circle cx="8" cy="8" r="7" fill="none" stroke={fillColor} stroke-width="1" opacity="0.3">
      <animate attributeName="r" values="5;7;5" dur="2s" repeatCount="indefinite" />
      <animate attributeName="opacity" values="0.4;0.1;0.4" dur="2s" repeatCount="indefinite" />
    </circle>
  {/if}
  <circle cx="8" cy="8" r="4" fill={fillColor} opacity={active ? 1 : 0.4} />
</svg>
