<script lang="ts">
  let {
    fromX = 0,
    fromY = 0,
    toX = 0,
    toY = 0,
    active = false,
  }: {
    fromX: number;
    fromY: number;
    toX: number;
    toY: number;
    active?: boolean;
  } = $props();

  let midY = $derived((fromY + toY) / 2);
  let d = $derived(`M${fromX},${fromY} C${fromX},${midY} ${toX},${midY} ${toX},${toY}`);
</script>

<path
  {d}
  fill="none"
  stroke={active ? 'var(--color-warning)' : 'var(--color-border)'}
  stroke-width={active ? 2 : 1}
  class={active ? 'dag-edge-active' : ''}
  marker-end="url(#arrow)"
/>
