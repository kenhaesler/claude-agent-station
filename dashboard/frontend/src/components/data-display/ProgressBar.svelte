<script lang="ts">
  let {
    value,
    color = 'var(--color-info)',
    height = 4,
  }: {
    value: number;
    color?: string;
    height?: number;
  } = $props();

  let pct = $derived(Math.max(0, Math.min(1, value)) * 100);
</script>

<div
  class="w-full rounded-full overflow-hidden bg-surface-2"
  style="height: {height}px;"
  role="progressbar"
  aria-valuenow={Math.round(pct)}
  aria-valuemin={0}
  aria-valuemax={100}
>
  <div
    class="h-full rounded-full transition-all duration-300 ease-out"
    style="width: {pct}%; background: {color};"
  ></div>
</div>
