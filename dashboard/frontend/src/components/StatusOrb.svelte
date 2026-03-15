<script lang="ts">
  interface Props {
    active: boolean;
    color?: string;
    size?: 'sm' | 'md' | 'lg';
  }

  let { active, color, size = 'sm' }: Props = $props();

  const sizeMap = { sm: 8, md: 12, lg: 16 };
  let s = $derived(sizeMap[size]);
  let fillColor = $derived(color ?? (active ? 'var(--color-status-active)' : 'var(--color-status-inactive)'));
</script>

<span
  class="inline-block rounded-full shrink-0"
  style:width="{s}px"
  style:height="{s}px"
  style:background={fillColor}
  style:opacity={active ? 1 : 0.4}
></span>
